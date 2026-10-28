"""Windows lifetime regression: terminate a controller, then check its tree."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
from modules import process_guard


class GuardChecks(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock()
        self.api.CreateJobObjectW.return_value = 123
        self.api.SetInformationJobObject.return_value = True
        self.api.AssignProcessToJobObject.return_value = True
        self.proc = MagicMock()
        self.proc._handle, self.proc.pid = 321, 456
        for target, name, value in (
                (process_guard, "_job_handles", []),
                (process_guard, "os", SimpleNamespace(name="nt"))):
            item = patch.object(target, name, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(process_guard, "_windows_api", return_value=self.api)
        item.start()
        self.addCleanup(item.stop)
        item = patch.object(process_guard.subprocess, "Popen", return_value=self.proc)
        self.popen = item.start()
        self.addCleanup(item.stop)
        item = patch.object(process_guard, "_resume_primary_thread")
        self.resume = item.start()
        self.addCleanup(item.stop)

    def test_suspended_assignment_before_resume_and_non_inheritable_job(self):
        result = process_guard.launch_guarded_process(["python", "main.py"], creationflags=0x200)
        self.assertIs(result, self.proc)
        self.assertEqual(self.popen.call_args.kwargs["creationflags"], 0x204)
        self.api.CreateJobObjectW.assert_called_once_with(None, None)
        self.api.AssignProcessToJobObject.assert_called_once_with(123, 321)
        self.resume.assert_called_once_with(self.api, 456)
        _, kind, info, size = self.api.SetInformationJobObject.call_args.args
        self.assertEqual(kind, 9)
        self.assertEqual(info._obj.BasicLimitInformation.LimitFlags, 0x2000)
        self.assertEqual(size, ctypes.sizeof(process_guard._ExtendedLimitInformation))
        self.assertEqual(result._hub_job_handle, 123)
        self.assertEqual(process_guard._job_handles, [123])
        self.api.CloseHandle.assert_not_called()

    def test_create_failure_does_not_publish_handle(self):
        self.api.CreateJobObjectW.return_value = 0
        with patch.object(process_guard, "_failure", return_value=OSError("create failed")):
            with self.assertRaisesRegex(OSError, "create failed"):
                process_guard.launch_guarded_process(["python"])
        self.assertEqual(process_guard._job_handles, [])
        self.api.CloseHandle.assert_not_called()
        self.api.AssignProcessToJobObject.assert_not_called()
        self.popen.assert_not_called()

    def test_configuration_failure_closes_only_unassigned_job(self):
        self.api.SetInformationJobObject.return_value = False
        with patch.object(process_guard, "_failure", return_value=OSError("configure failed")):
            with self.assertRaisesRegex(OSError, "configure failed"):
                process_guard.launch_guarded_process(["python"])
        self.api.CloseHandle.assert_called_once_with(123)
        self.api.AssignProcessToJobObject.assert_not_called()
        self.popen.assert_not_called()
        self.assertEqual(process_guard._job_handles, [])

    def test_assignment_failure_kills_suspended_process_and_closes_job(self):
        self.api.AssignProcessToJobObject.return_value = False
        with patch.object(process_guard, "_failure", return_value=OSError("assignment failed")):
            with self.assertRaisesRegex(OSError, "assignment failed"):
                process_guard.launch_guarded_process(["python"])
        self.api.CloseHandle.assert_called_once_with(123)
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once_with(timeout=5)
        self.resume.assert_not_called()
        self.assertEqual(process_guard._job_handles, [])

    def test_resume_failure_kills_process_and_closes_job(self):
        self.resume.side_effect = OSError("resume failed")
        with self.assertRaisesRegex(OSError, "resume failed"):
            process_guard.launch_guarded_process(["python"])
        self.proc.kill.assert_called_once()
        self.api.CloseHandle.assert_called_once_with(123)
        self.assertEqual(process_guard._job_handles, [])

    def test_spawn_failure_closes_unused_job(self):
        self.popen.side_effect = OSError("spawn failed")
        with self.assertRaisesRegex(OSError, "spawn failed"):
            process_guard.launch_guarded_process(["python"])
        self.api.CloseHandle.assert_called_once_with(123)
        self.api.AssignProcessToJobObject.assert_not_called()
        self.assertEqual(process_guard._job_handles, [])

    def test_other_platform_does_not_load_windows_api(self):
        with patch.object(process_guard, "os", SimpleNamespace(name="posix")):
            self.assertIs(process_guard.launch_guarded_process(["python"], cwd="app"), self.proc)
        process_guard._windows_api.assert_not_called()
        self.popen.assert_called_once_with(["python"], cwd="app")


class ThreadChecks(unittest.TestCase):
    def test_oldest_owned_thread_resumes_and_all_native_handles_close(self):
        api = MagicMock()
        api.CreateToolhelp32Snapshot.return_value = 10
        rows = iter([(101, 456), (102, 456), (103, 999)])

        def next_thread(snapshot, pointer):
            row = next(rows, None)
            if row is None:
                return False
            pointer._obj.th32ThreadID, pointer._obj.th32OwnerProcessID = row
            return True

        api.Thread32First.side_effect = next_thread
        api.Thread32Next.side_effect = next_thread
        api.OpenThread.side_effect = [41, 42]

        def times(handle, created, *args):
            created._obj.dwLowDateTime = {41: 20, 42: 10}[handle]
            return True

        api.GetThreadTimes.side_effect = times
        api.ResumeThread.return_value = 1
        process_guard._resume_primary_thread(api, 456)
        api.ResumeThread.assert_called_once_with(42)
        self.assertEqual([call.args[0] for call in api.CloseHandle.call_args_list], [41, 42, 10])

    def test_missing_owned_thread_closes_snapshot_and_never_resumes(self):
        api = MagicMock()
        api.CreateToolhelp32Snapshot.return_value = 10
        api.Thread32First.return_value = False
        with self.assertRaisesRegex(OSError, "No se encontró"):
            process_guard._resume_primary_thread(api, 456)
        api.CloseHandle.assert_called_once_with(10)
        api.ResumeThread.assert_not_called()


def _fixture(role, folder):
    folder = Path(folder)
    report = {"pid": os.getpid()}
    if role == "controller":
        # Record the real interpreter PID before setup, also on setup failure.
        (folder / "controller.json").write_text(json.dumps(report))
        child = process_guard.launch_guarded_process(
            [sys.executable, __file__, "--fixture", "child", str(folder)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sibling = subprocess.Popen(
            [sys.executable, __file__, "--fixture", "unprotected", str(folder)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        report.update(spawned_pid=child.pid, sibling_pid=sibling.pid)
    elif role == "child":
        child = subprocess.Popen(
            [sys.executable, __file__, "--fixture", "grandchild", str(folder)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        report["spawned_pid"] = child.pid
    else:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        report["port"] = listener.getsockname()[1]
    (folder / f"{role}.json").write_text(json.dumps(report))
    while True:
        time.sleep(1)


@unittest.skipUnless(os.name == "nt", "Windows process jobs")
class WindowsTreeChecks(unittest.TestCase):
    def test_abrupt_parent_death_closes_managed_tree_and_preserves_unprotected_sibling(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        parent = ROOT / ".cache" / "process-guard-tests"
        parent.mkdir(parents=True, exist_ok=True)
        handles = {}
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            folder = Path(directory)
            with (folder / "controller.log").open("w", encoding="utf-8") as log:
                controller = subprocess.Popen(
                    [sys.executable, __file__, "--fixture", "controller", str(folder)],
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            records = {}
            try:
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    for role in ("controller", "child", "grandchild", "unprotected"):
                        try:
                            records[role] = json.loads((folder / f"{role}.json").read_text())
                        except (OSError, ValueError):
                            continue
                    if len(records) == 4:
                        break
                    if controller.poll() is not None:
                        self.fail((folder / "controller.log").read_text(encoding="utf-8"))
                    time.sleep(0.03)
                self.assertEqual(len(records), 4, "fixture did not become ready")
                pids = {controller.pid}
                for row in records.values():
                    pids.add(row["pid"])
                    if "spawned_pid" in row:
                        pids.add(row["spawned_pid"])
                    if "sibling_pid" in row:
                        pids.add(row["sibling_pid"])
                for pid in pids:
                    handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x0001, False, pid)
                    self.assertTrue(handle, f"cannot open fixture PID {pid}")
                    handles[pid] = handle
                port = records["grandchild"]["port"]
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    pass
                # Kill the actual interpreter, not merely uv's venv trampoline.
                self.assertTrue(kernel.TerminateProcess(handles[records["controller"]["pid"]], 99))
                sibling_pids = {records["unprotected"]["pid"], records["controller"]["sibling_pid"]}
                for pid, handle in handles.items():
                    if pid in sibling_pids:
                        continue
                    self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0,
                                     f"fixture PID {pid} survived controller death")
                for pid in sibling_pids:
                    self.assertEqual(kernel.WaitForSingleObject(handles[pid], 0), 0x102,
                                     f"unprotected sibling PID {pid} was terminated")
                with socket.create_connection(("127.0.0.1", records["unprotected"]["port"]), timeout=2):
                    pass
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
            finally:
                # Touch only the exact fixture PIDs, retaining handles against reuse.
                for role in ("controller", "child", "grandchild", "unprotected"):
                    try:
                        row = json.loads((folder / f"{role}.json").read_text())
                    except (OSError, ValueError):
                        continue
                    for key in ("pid", "spawned_pid", "sibling_pid"):
                        pid = row.get(key)
                        if pid and pid not in handles:
                            handle = kernel.OpenProcess(0x100000 | 0x0001, False, pid)
                            if handle:
                                handles[pid] = handle
                for handle in handles.values():
                    if kernel.WaitForSingleObject(handle, 0) != 0:
                        kernel.TerminateProcess(handle, 100)
                        kernel.WaitForSingleObject(handle, 5000)
                    kernel.CloseHandle(handle)
                if controller.poll() is None:
                    controller.kill()
                controller.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fixture":
        _fixture(sys.argv[2], sys.argv[3])
    else:
        unittest.main(verbosity=2)
