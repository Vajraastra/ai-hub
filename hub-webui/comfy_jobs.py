"""Admission control for the Hub's single process / asyncio event loop.

Reserve before the first await in a generating endpoint. Spawn its background
work here so returning HTTP 200 does not release the reservation. Read-only
routes and CPU-only merges do not reserve ComfyUI. External clients and other
Hub processes are outside this in-process coordinator.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import signature
import threading

from fastapi import HTTPException


@dataclass(eq=False)
class _Lease:
    owner: str
    users: int = 1


_active: _Lease | None = None
_context: ContextVar[_Lease | None] = ContextVar("comfy_job", default=None)
_state_lock = threading.RLock()


def _release(lease: _Lease):
    global _active
    with _state_lock:
        lease.users -= 1
        if lease.users == 0 and _active is lease:
            _active = None


def reserve_backend_shutdown(owner: str = "ComfyUI se está cerrando"):
    """Atomically block new Hub jobs while a timer thread stops the backend.

    Return an idempotent release callback, or None if a job/control already
    owns admission. The callback may run from any thread. No ContextVar is
    installed: a shutdown reservation cannot spawn GPU work.
    """
    global _active
    with _state_lock:
        if _active is not None:
            return None
        lease = _active = _Lease(owner)
    released = False

    def release():
        nonlocal released
        with _state_lock:
            if not released:
                released = True
                _release(lease)

    return release


def _busy():
    return HTTPException(409, f"ComfyUI ocupado por {_active.owner}; espera a que termine.")


def exclusive(owner: str, *, control: bool = False):
    """Fail immediately if busy; a control can only pin its owner's lease.

    Pinning controls prevents a finishing job from handing the backend to
    another module while an interrupt request is still in flight.
    """
    def decorate(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            global _active
            with _state_lock:
                if control:
                    if _active is None:
                        raise HTTPException(409, "No hay un trabajo de este módulo que interrumpir.")
                    if _active.owner != owner:
                        raise _busy()
                    lease = _active
                    lease.users += 1
                else:
                    if _active is not None:
                        raise _busy()
                    lease = _active = _Lease(owner)
            token = _context.set(lease)
            try:
                return await func(*args, **kwargs)
            finally:
                _context.reset(token)
                _release(lease)

        # Preserve FastAPI request models, including postponed annotations.
        wrapped.__signature__ = signature(func, eval_str=True)
        return wrapped
    return decorate


def spawn(coro) -> asyncio.Task:
    """Carry the request reservation through completion, error or cancellation.

    A done callback also runs when cancellation happens before the first step
    of the coroutine (where a coroutine-level finally would never execute).
    Callers retain their existing task sets and job/status bookkeeping.
    """
    lease = _context.get()
    if lease is not None:
        with _state_lock:
            expired = _active is not lease
            if not expired:
                lease.users += 1
        if expired:
            coro.close()
            raise RuntimeError("Expired ComfyUI reservation")
    try:
        task = asyncio.create_task(coro)
    except BaseException:
        coro.close()
        if lease is not None:
            _release(lease)
        raise
    if lease is not None:
        task.add_done_callback(lambda _: _release(lease))
    return task
