"""
EventLogViewer — Tab independiente del log de eventos operacionales del Hub.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QLineEdit
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont

from gui.state import state


class EventLogViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)

        # Header row
        header = QHBoxLayout()
        title = QLabel("📋 Log de Eventos Operacionales")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #54EFEA;")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("🔄 Actualizar")
        refresh_btn.setObjectName("outline_btn")
        refresh_btn.setFixedWidth(130)
        refresh_btn.clicked.connect(self.refresh_log)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # Search/filter bar
        filter_row = QHBoxLayout()
        filter_lbl = QLabel("Filtrar:")
        filter_lbl.setStyleSheet("color: #888aaa; font-size: 12px;")
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Buscar en el log... (ej: LAUNCH, comfyui, ERROR)")
        self._filter_edit.setStyleSheet("""
            QLineEdit {
                background: #1A0040;
                color: #e0e0ff;
                border: 1px solid #3D1B7B;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #54EFEA; }
        """)
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(filter_lbl)
        filter_row.addWidget(self._filter_edit)
        layout.addLayout(filter_row)

        # Log text area
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Monospace", 10))
        self._log_view.setStyleSheet("""
            QTextEdit {
                background: #0F0023;
                color: #54EFEA;
                border: 1px solid #3D1B7B;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        layout.addWidget(self._log_view)

        hint = QLabel("Se actualiza automáticamente cada 3 segundos. Los eventos más recientes aparecen al final.")
        hint.setStyleSheet("color: #555588; font-size: 11px; font-style: italic;")
        layout.addWidget(hint)

        # Full log content in memory for filtering
        self._full_content = ""

        # Auto-refresh timer — 3s para mayor responsividad
        self._timer = QTimer(self)
        self._timer.setInterval(3_000)
        self._timer.timeout.connect(self.refresh_log)
        self._timer.start()

        self.refresh_log()

    def refresh_log(self):
        self._full_content = state.read_event_log(max_lines=300)
        if not self._full_content:
            self._full_content = "(Sin eventos registrados aún)"
        self._apply_filter(self._filter_edit.text())

    def _apply_filter(self, text: str):
        """Filtra las líneas del log que contengan el texto buscado."""
        if not text.strip():
            filtered = self._full_content
        else:
            term = text.lower()
            lines = self._full_content.splitlines()
            filtered = "\n".join(l for l in lines if term in l.lower())
            if not filtered:
                filtered = f"(Sin resultados para '{text}')"

        self._log_view.setPlainText(filtered)
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
