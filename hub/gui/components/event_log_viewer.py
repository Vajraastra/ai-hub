"""
EventLogViewer — Tab independiente del log de eventos operacionales del Hub.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel
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

        hint = QLabel("Se actualiza automáticamente cada 10 segundos. Los eventos más recientes aparecen al final.")
        hint.setStyleSheet("color: #555588; font-size: 11px; font-style: italic;")
        layout.addWidget(hint)

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.refresh_log)
        self._timer.start()

        self.refresh_log()

    def refresh_log(self):
        content = state.read_event_log(max_lines=200)
        self._log_view.setPlainText(content if content else "(Sin eventos registrados aún)")
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
