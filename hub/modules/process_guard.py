"""Keep only managed apps inside the Hub's lifetime, including abrupt exits.

Each managed app starts suspended, enters its own Windows job, then resumes.
The Hub holds that job's sole non-inheritable handle until its process exits.
The Hub, its browser, and independently launched tools do not enter the job.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
import threading


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_CREATE_SUSPENDED = 0x00000004
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_THREAD_QUERY_INFORMATION = 0x0040
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_job_handles = []
_lock = threading.Lock()


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _windows_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Thread32First", "Thread32Next"):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
        function.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.GetThreadTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetThreadTimes.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    return kernel


def _failure(operation):
    code = ctypes.get_last_error()
    return ctypes.WinError(code, f"No se pudo {operation}: {ctypes.FormatError(code).strip()}")


def _resume_primary_thread(kernel, pid):
    snapshot = kernel.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        raise _failure("buscar el hilo de la aplicación")
    primary, oldest = None, None
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                handle = kernel.OpenThread(
                    _THREAD_SUSPEND_RESUME | _THREAD_QUERY_INFORMATION,
                    False, entry.th32ThreadID)
                if handle:
                    created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
                    if kernel.GetThreadTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                             ctypes.byref(kernel_time), ctypes.byref(user_time)):
                        stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
                        # Usually only the primary exists. Creation time also
                        # handles supplementary threads injected during startup.
                        if oldest is None or stamp < oldest:
                            if primary is not None:
                                kernel.CloseHandle(primary)
                            primary, oldest = handle, stamp
                            handle = None
                    if handle is not None:
                        kernel.CloseHandle(handle)
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        if primary is None:
            raise OSError("No se encontró el hilo inicial de la aplicación suspendida.")
        count = kernel.ResumeThread(primary)
        if count == 0xFFFFFFFF:
            raise _failure("reanudar la aplicación protegida")
        if count != 1:
            raise OSError(f"Estado inesperado del hilo inicial de la aplicación: {count}.")
    finally:
        if primary is not None:
            kernel.CloseHandle(primary)
        kernel.CloseHandle(snapshot)


def launch_guarded_process(cmd, **kwargs):
    """Launch one managed app without allowing its first instruction to escape.

    Windows 8+ supports nested jobs when the Hub itself already belongs to one.
    Fail before resuming if the enclosing job prevents assignment. Job handles
    outlive Popen references, so dropping a finished launcher cannot orphan its
    still-running descendants. Other platforms retain normal Popen behavior.
    """
    if os.name != "nt":
        return subprocess.Popen(cmd, **kwargs)
    kernel = _windows_api()
    # NULL SECURITY_ATTRIBUTES creates a handle children cannot inherit.
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _failure("crear la protección de procesos de la aplicación")
    proc = None
    try:
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
                job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info)):
            raise _failure("configurar el cierre de los procesos de la aplicación")
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CREATE_SUSPENDED
        proc = subprocess.Popen(cmd, **kwargs)
        if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
            raise _failure("asignar la aplicación a su protección de procesos")
        _resume_primary_thread(kernel, proc.pid)
        proc._hub_job_handle = job
        with _lock:
            _job_handles.append(job)
        return proc
    except BaseException:
        try:
            if proc is not None:
                try:
                    proc.kill()
                except OSError:
                    pass
        finally:
            kernel.CloseHandle(job)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise
