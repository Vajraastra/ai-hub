"""
Estado de sesión del Painter — una sesión global en memoria.
Guarda: imagen current, historial undo/redo, preview pendiente.
"""
from image_utils import MAX_HISTORY


class PainterSession:
    def __init__(self):
        self._current:  bytes | None = None   # PNG bytes — imagen aceptada
        self._preview:  bytes | None = None   # PNG bytes — resultado pendiente de aceptar/rechazar
        self._history:  list[bytes]  = []     # undo stack
        self._redo:     list[bytes]  = []     # redo stack

    # ── Propiedades ───────────────────────────────────────────────────────

    @property
    def current(self) -> bytes | None:
        return self._current

    @property
    def preview(self) -> bytes | None:
        return self._preview

    @property
    def has_current(self) -> bool:
        return self._current is not None

    @property
    def history_size(self) -> int:
        return len(self._history)

    @property
    def redo_size(self) -> int:
        return len(self._redo)

    # ── Ciclo accept / reject ─────────────────────────────────────────────

    def set_preview(self, image_bytes: bytes):
        """Llamado cuando ComfyUI retorna un resultado — queda pendiente de aprobación."""
        self._preview = image_bytes

    def accept(self) -> bytes:
        """
        Acepta el preview: se convierte en current.
        Retorna los bytes del nuevo current.
        Lanza ValueError si no hay preview.
        """
        if self._preview is None:
            raise ValueError("No hay preview pendiente para aceptar")
        if self._current is not None:
            self._history.append(self._current)
            if len(self._history) > MAX_HISTORY:
                self._history.pop(0)
        self._current = self._preview
        self._preview = None
        self._redo.clear()
        return self._current

    def reject(self):
        """Descarta el preview. Current no cambia."""
        self._preview = None

    # ── Undo / Redo ───────────────────────────────────────────────────────

    def undo(self) -> bytes:
        """
        Restaura el estado anterior.
        Retorna los bytes del nuevo current.
        Lanza ValueError si no hay historial.
        """
        if not self._history:
            raise ValueError("No hay más pasos para deshacer")
        if self._current is not None:
            self._redo.append(self._current)
        self._current = self._history.pop()
        return self._current

    def redo(self) -> bytes:
        """
        Rehace el último undo.
        Retorna los bytes del nuevo current.
        Lanza ValueError si no hay redo disponible.
        """
        if not self._redo:
            raise ValueError("No hay pasos para rehacer")
        if self._current is not None:
            self._history.append(self._current)
        self._current = self._redo.pop()
        return self._current

    def clear(self):
        """Reinicia la sesión completa."""
        self._current = None
        self._preview = None
        self._history.clear()
        self._redo.clear()

    def state_dict(self) -> dict:
        return {
            "has_current":  self.has_current,
            "history_size": self.history_size,
            "redo_size":    self.redo_size,
        }


# Instancia global — una sesión por servidor
session = PainterSession()
