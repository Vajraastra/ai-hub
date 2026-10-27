"""CPU checks for shared headless backend ownership; no child processes start."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "hub-webui"))
with patch("modules.model_organizer.sync_all_app_configs"), \
        patch("modules.model_organizer.sync_krita_priority"), \
        patch("modules.storage_manager.create_model_dirs"):
    import hub_bridge
from modules import app_launcher


class Timer:
    def __init__(self, interval, callback):
        self.interval, self.callback = interval, callback
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Fire cancelled timers too, to model an already executing callback.
        self.callback()


class State:
    def __init__(self, folder):
        self._lock = threading.Lock()
        self.busy_apps, self.running_apps = {}, {}
        self.registry_apps = {"comfyui": {"id": "comfyui", "default_port": 8188,
                                           "auto_open_browser": True}}
        self.installed_apps = {"comfyui": {"dir": str(folder)}}
        self.config_file = folder / "hub.json"
        self.config_file.write_text(json.dumps({"paths": {}, "cuda": {}}))

    def get_port_override(self, app_id):
        return None

    def get_user_extra_args(self, app_id):
        return []

    def set_busy(self, app_id, status):
        self.busy_apps[app_id] = status

    def clear_busy(self, app_id):
        self.busy_apps.pop(app_id, None)

    def set_running(self, app_id, proc):
        self.running_apps[app_id] = proc

    def clear_running(self, app_id):
        self.running_apps.pop(app_id, None)

    def log_event(self, *args):
        pass


class BackendChecks(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".cache" / "backend-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.folder = Path(self.temp.name)
        with patch.object(hub_bridge.HubBridge, "_run_startup_sync"):
            self.bridge = hub_bridge.HubBridge()
        self.bridge._state = State(self.folder)
        self.timer_patch = patch.object(hub_bridge.threading, "Timer", Timer)
        self.timer_patch.start()
        self.stop_patch = patch.object(self.bridge, "stop", return_value=True)
        self.stop = self.stop_patch.start()
        self.up_patch = patch.object(self.bridge, "is_backend_up", return_value=False)
        self.up = self.up_patch.start()

    def tearDown(self):
        self.up_patch.stop()
        self.stop_patch.stop()
        self.timer_patch.stop()
        self.temp.cleanup()

    def running(self, owners=("page-one",)):
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        self.bridge._state.running_apps["comfyui"] = proc
        self.bridge._backend_autostarted.add("comfyui")
        self.bridge._backend_owners["comfyui"] = set(owners)
        return proc

    def timer(self):
        return self.bridge._backend_release_timers["comfyui"]

    def test_last_owner_only_releases_auto_backend(self):
        self.running()
        self.assertTrue(self.bridge.launch_backend("comfyui", "page-two"))
        self.assertTrue(self.bridge.release_backend("comfyui", "page-one"))
        self.assertNotIn("comfyui", self.bridge._backend_release_timers)
        self.bridge.release_backend("comfyui", "page-two")
        self.timer().fire()
        self.stop.assert_called_once_with("comfyui")

    def test_manual_or_external_backend_is_never_stopped_by_release(self):
        self.up.return_value = True
        self.assertTrue(self.bridge.launch_backend("comfyui", "page"))
        self.assertNotIn("comfyui", self.bridge._backend_autostarted)
        self.assertTrue(self.bridge.release_backend("comfyui", "page"))
        self.stop.assert_not_called()
        self.assertFalse(self.bridge._backend_release_timers)

    def test_reload_cancels_even_a_stale_timer_callback(self):
        self.running()
        self.bridge.release_backend("comfyui", "page-one")
        stale = self.timer()
        self.assertTrue(self.bridge.launch_backend("comfyui", "new-page"))
        self.assertTrue(stale.cancelled)
        stale.fire()
        self.stop.assert_not_called()

    def test_release_during_startup_is_honored_when_process_appears(self):
        self.bridge._backend_autostarted.add("comfyui")
        self.bridge._backend_launching.add("comfyui")
        self.bridge._backend_owners["comfyui"] = {"page"}
        self.bridge.release_backend("comfyui", "page", grace=1)
        self.timer().fire()
        self.stop.assert_not_called()
        self.bridge._state.running_apps["comfyui"] = MagicMock(spec=subprocess.Popen)
        self.bridge._backend_process_started("comfyui")
        self.timer().fire()
        self.stop.assert_called_once_with("comfyui")

    def test_failed_launch_releases_all_ownership_and_timers(self):
        with patch.object(self.bridge, "launch", return_value=False):
            self.assertFalse(self.bridge.launch_backend("comfyui", "page"))
        self.assertFalse(self.bridge._backend_owners)
        self.assertFalse(self.bridge._backend_autostarted)
        self.assertFalse(self.bridge._backend_launching)
        self.assertFalse(self.bridge._backend_release_timers)

    def test_thread_start_failure_does_not_leave_a_busy_backend_lease(self):
        with patch.object(hub_bridge.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start thread")
            self.assertFalse(self.bridge.launch_backend("comfyui", "page"))
        self.assertFalse(self.bridge._backend_owners)
        self.assertFalse(self.bridge._backend_autostarted)
        self.assertFalse(self.bridge._backend_launching)
        self.assertFalse(self.bridge._state.busy_apps)

    def test_acquisition_rejects_a_backend_already_stopping(self):
        self.running()
        self.bridge._state.busy_apps["comfyui"] = "stopping"
        self.assertFalse(self.bridge.launch_backend("comfyui", "new-page"))
        self.assertEqual(self.bridge._backend_owners["comfyui"], {"page-one"})

    def test_manual_dashboard_adopts_existing_backend_without_restart(self):
        proc = self.running()
        with patch.object(hub_bridge.threading, "Thread") as thread:
            self.assertTrue(self.bridge.launch("comfyui", open_browser=True))
            self.assertEqual(thread.call_args.kwargs["args"], ("comfyui", 8188, proc))
        self.assertNotIn("comfyui", self.bridge._backend_autostarted)
        self.bridge.release_backend("comfyui", "page-one")
        self.stop.assert_not_called()

    def test_busy_guard_retries_and_runs_outside_owner_lock(self):
        self.running()
        called = []

        def busy():
            called.append(self.bridge._backend_lock._is_owned())
            return False

        self.bridge.set_backend_idle_guard("comfyui", busy)
        self.bridge.release_backend("comfyui", "page-one")
        self.timer().fire()
        self.assertEqual(called, [False])
        self.assertEqual(self.timer().interval, 2.0)
        self.stop.assert_not_called()
        self.bridge.set_backend_idle_guard("comfyui", lambda: True)
        self.timer().fire()
        self.stop.assert_called_once_with("comfyui")

    def test_guard_reacquisition_prevents_stop(self):
        self.running()

        def acquire():
            self.bridge.launch_backend("comfyui", "new-page")
            return True

        self.bridge.set_backend_idle_guard("comfyui", acquire)
        self.bridge.release_backend("comfyui", "page-one")
        self.timer().fire()
        self.stop.assert_not_called()
        self.assertEqual(self.bridge._backend_owners["comfyui"], {"new-page"})

    def test_uncertain_queue_is_preserved(self):
        self.running()
        self.bridge.set_backend_idle_guard("comfyui", MagicMock(side_effect=OSError("offline")))
        self.bridge.release_backend("comfyui", "page-one")
        self.timer().fire()
        self.assertEqual(self.timer().interval, 2.0)
        self.stop.assert_not_called()

    def test_dashboard_close_preserves_active_backend_but_shutdown_forces_it(self):
        proc = self.running()
        with patch.object(hub_bridge, "_interrupt_tree") as interrupt:
            self.bridge.stop_all_running(preserve_backends=True)
            interrupt.assert_not_called()
            interrupt.side_effect = lambda _: setattr(proc.poll, "return_value", 0)
            with patch.object(hub_bridge.time, "sleep"):
                self.bridge.stop_all_running(wait_secs=0.1)
            interrupt.assert_called_once_with(proc)

    def test_dashboard_close_rechecks_owner_after_idle_guard(self):
        proc = self.running(owners=())

        def acquire_during_check():
            self.bridge.launch_backend("comfyui", "new-page")
            return True

        self.bridge.set_backend_idle_guard("comfyui", acquire_during_check)
        with patch.object(hub_bridge, "_interrupt_tree") as interrupt:
            self.bridge.stop_all_running(wait_secs=0, preserve_backends=True)
            interrupt.assert_not_called()
        self.assertEqual(self.bridge._backend_owners["comfyui"], {"new-page"})

    def startup_patches(self, launcher, prepare=None):
        stack = ExitStack()
        stack.enter_context(patch("modules.cuda_guardian.build_env_overrides", return_value={}))
        stack.enter_context(patch("modules.app_installer.apply_post_install_patches",
                                  side_effect=prepare or (lambda *args: {"failed": []})))
        stack.enter_context(patch.object(hub_bridge, "_is_port_in_use", return_value=False))
        stack.enter_context(patch("modules.app_launcher.launch_app", launcher))
        return stack

    def test_final_shutdown_fences_a_launch_still_in_preparation(self):
        self.bridge._backend_launching.add("comfyui")
        self.bridge._backend_autostarted.add("comfyui")
        self.bridge._backend_owners["comfyui"] = {"page"}
        self.bridge._state.set_busy("comfyui", "launching")

        def shutdown_during_prepare(*args):
            self.assertFalse(self.bridge._state.running_apps)
            self.bridge.stop_all_running(wait_secs=0)
            return {"failed": []}

        launcher = MagicMock()
        with self.startup_patches(launcher, shutdown_during_prepare):
            self.bridge._launch_thread("comfyui", open_browser=False)
        launcher.assert_not_called()
        self.assertFalse(self.bridge._state.running_apps)
        self.assertFalse(self.bridge._state.busy_apps)
        self.assertFalse(self.bridge._backend_launching)
        self.assertFalse(self.bridge.launch("comfyui"))
        self.assertFalse(self.bridge.launch_backend("comfyui", "late-page"))

    def test_dashboard_close_during_preparation_does_not_fence_launch(self):
        proc = MagicMock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.stdout = []
        proc.wait.return_value = 0
        launcher = MagicMock(return_value=proc)

        def dashboard_close(*args):
            self.bridge.stop_all_running(wait_secs=0, preserve_backends=True)
            return {"failed": []}

        with self.startup_patches(launcher, dashboard_close):
            self.bridge._launch_thread("comfyui", open_browser=False)
        launcher.assert_called_once()
        self.assertFalse(self.bridge._shutdown_requested)

    def test_shutdown_cannot_miss_a_process_between_spawn_and_registration(self):
        entered, allow_return, stopped = threading.Event(), threading.Event(), threading.Event()
        shutdown_started, shutdown_done = threading.Event(), threading.Event()
        proc = MagicMock(spec=subprocess.Popen)
        proc.pid = 12346
        proc.stdout = []
        proc.poll.side_effect = lambda: 0 if stopped.is_set() else None
        proc.wait.side_effect = lambda timeout=None: stopped.wait(timeout or 2)
        self.bridge._backend_launching.add("comfyui")
        self.bridge._backend_autostarted.add("comfyui")
        self.bridge._backend_owners["comfyui"] = {"page"}

        def spawn(*args, **kwargs):
            entered.set()
            if not allow_return.wait(2):
                raise RuntimeError("test did not release mocked spawn")
            return proc

        def shutdown():
            shutdown_started.set()
            self.bridge.stop_all_running(wait_secs=0.1)
            shutdown_done.set()

        with self.startup_patches(MagicMock(side_effect=spawn)), \
                patch.object(hub_bridge, "_interrupt_tree", side_effect=lambda _: stopped.set()) as interrupt:
            worker = threading.Thread(target=self.bridge._launch_thread, args=("comfyui", False))
            closer = threading.Thread(target=shutdown)
            worker.start()
            self.assertTrue(entered.wait(2))
            closer.start()
            self.assertTrue(shutdown_started.wait(2))
            try:
                # Shutdown must wait until the returned Popen has been registered.
                self.assertFalse(shutdown_done.wait(0.05))
            finally:
                allow_return.set()
                worker.join(3)
                closer.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(closer.is_alive())
            interrupt.assert_called_once_with(proc)
        self.assertTrue(shutdown_done.is_set())
        self.assertTrue(stopped.is_set())
        self.assertFalse(self.bridge._state.running_apps)

    def test_headless_startup_does_not_kill_a_port_won_by_an_external_process(self):
        self.bridge._backend_autostarted.add("comfyui")
        self.bridge._backend_launching.add("comfyui")
        self.bridge._backend_owners["comfyui"] = {"page"}
        with patch("modules.cuda_guardian.build_env_overrides", return_value={}), \
                patch("modules.app_installer.apply_post_install_patches", return_value={"failed": []}), \
                patch.object(hub_bridge, "_is_port_in_use", return_value=True), \
                patch.object(hub_bridge, "_kill_processes_on_port") as kill_port, \
                patch("modules.app_launcher.launch_app") as launch:
            self.bridge._launch_thread("comfyui", open_browser=False)
        launch.assert_not_called()
        kill_port.assert_not_called()
        self.assertFalse(self.bridge._backend_autostarted)
        self.assertFalse(self.bridge._backend_launching)

    def test_windows_headless_launcher_has_no_console_flag(self):
        runtime = SimpleNamespace(env_dir=str(self.folder))
        proc = MagicMock()
        with patch.object(app_launcher, "build_runtime_env", return_value={}), \
                patch.object(app_launcher, "resolve_python_runtime", return_value=runtime), \
                patch.object(app_launcher, "apply_python_runtime_env"), \
                patch.object(app_launcher, "_get_launch_command", return_value=["python", "main.py"]), \
                patch.object(app_launcher.subprocess, "Popen", return_value=proc) as popen:
            result = app_launcher.launch_app({}, str(self.folder), {}, async_mode=True,
                                            capture_output=True, headless=True)
            self.assertIs(result, proc)
            flags = popen.call_args.kwargs["creationflags"]
            self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
            app_launcher.launch_app({}, str(self.folder), {}, async_mode=True, capture_output=True)
            self.assertFalse(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
