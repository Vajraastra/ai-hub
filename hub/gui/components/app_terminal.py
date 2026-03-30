"""
AppTerminal — Panel de salida de terminal en tiempo real.
Muestra el stdout/stderr de la app activa, con limpieza de códigos ANSI.
Se conecta a workers.signals.log_message(app_id, line).
"""
import re

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                                QTextEdit, QLabel, QPushButton)
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtCore import Qt, QTimer, Slot


# Elimina secuencias ANSI de color/cursor y carriage returns
_ANSI_RE = re.compile(
    r'\x1b(?:'
    r'\[[0-9;]*[A-Za-z]'   # CSI: colores, movimiento de cursor
    r'|\][^\x07]*\x07'     # OSC: títulos de ventana, etc.
    r'|[()][AB012]'        # Character set
    r'|[=>~MDE]'           # Otros
    r')'
)


def _strip_ansi(text: str) -> str:
    text = _ANSI_RE.sub('', text)
    return text.replace('\r', '')


class AppTerminal(QWidget):
    """
    Widget de salida de terminal. Recibe líneas via append_line(app_id, line)
    y solo muestra las del app actualmente activo.
    """
    # Límite de líneas en el buffer pendiente antes de descartar las más antiguas
    MAX_PENDING = 500
    # Límite de líneas visibles en el QTextEdit
    MAX_DOC_LINES = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_app_id = None
        self._pending_lines: list[str] = []   # buffer para batching

        # Timer de batching: vuelca líneas acumuladas cada 50ms en el hilo GUI
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(50)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._flush_timer.start()

        # ── Output area (se crea primero para que el botón pueda referenciarlo)
        self._output = QTextEdit()
        self._output.setMinimumHeight(180)
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Monospace", 9))
        self._output.setStyleSheet("""
            QTextEdit {
                background: #07000F;
                color: #B8C0D0;
                border: none;
                padding: 8px 12px;
                selection-background-color: #2A0A5E;
            }
        """)

        # ── Header ──────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(28)
        header.setStyleSheet("background: #0D0025; border-top: 1px solid #2A0A5E;")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(12, 0, 8, 0)
        header_row.setSpacing(8)

        self._title_lbl = QLabel("▸ Terminal  —  sin app activa")
        self._title_lbl.setStyleSheet(
            "color: #555588; font-size: 11px; font-weight: bold; font-family: monospace;"
        )
        header_row.addWidget(self._title_lbl)
        header_row.addStretch()

        clear_btn = QPushButton("limpiar")
        clear_btn.setFixedSize(54, 18)
        clear_btn.setStyleSheet("""
            QPushButton {
                font-size: 10px; padding: 0 4px;
                background: transparent;
                border: 1px solid #2A0A5E;
                color: #444466;
                border-radius: 3px;
            }
            QPushButton:hover { border-color: #54EFEA; color: #54EFEA; }
        """)
        clear_btn.clicked.connect(self._output.clear)
        header_row.addWidget(clear_btn)

        # ── Layout ──────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._output)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def set_active_app(self, app_id: str, app_name: str):
        """Prepara el panel para una nueva app: limpia buffer y actualiza el título."""
        self._current_app_id = app_id
        self._pending_lines.clear()
        self._title_lbl.setText(f"▸ Terminal  —  {app_name}")
        self._title_lbl.setStyleSheet(
            "color: #54EFEA; font-size: 11px; font-weight: bold; font-family: monospace;"
        )
        self._output.clear()

    def on_app_stopped(self, app_id: str):
        """Marca el panel como inactivo cuando la app se detiene."""
        if app_id == self._current_app_id:
            self._title_lbl.setText(
                f"▸ Terminal  —  {self._title_lbl.text().split('—')[-1].strip()}  (detenida)"
            )
            self._title_lbl.setStyleSheet(
                "color: #888aaa; font-size: 11px; font-weight: bold; font-family: monospace;"
            )

    @Slot(str, str)
    def append_line(self, app_id: str, raw_line: str):
        """Acumula líneas en el buffer; el timer las vuelca cada 50ms."""
        if app_id != self._current_app_id:
            return
        clean = _strip_ansi(raw_line)
        if clean:
            self._pending_lines.append(clean)
            # Si el buffer supera el límite, descartar líneas antiguas
            if len(self._pending_lines) > self.MAX_PENDING:
                self._pending_lines = self._pending_lines[-self.MAX_PENDING:]

    def _trim_document(self):
        """Elimina las primeras líneas si el documento supera MAX_DOC_LINES."""
        doc = self._output.document()
        if doc.blockCount() > self.MAX_DOC_LINES:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            # Seleccionar las líneas excedentes desde el inicio
            excess = doc.blockCount() - self.MAX_DOC_LINES
            for _ in range(excess):
                cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()

    def _flush_pending(self):
        """Vuelca el buffer acumulado al QTextEdit en un solo repaint."""
        if not self._pending_lines:
            return
        block = '\n'.join(self._pending_lines) + '\n'
        self._pending_lines.clear()
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._output.setTextCursor(cursor)
        self._output.insertPlainText(block)
        self._trim_document()
        self._output.verticalScrollBar().setValue(
            self._output.verticalScrollBar().maximum()
        )
