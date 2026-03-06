"""
HubSettings — Widget para la tab "Hub" del AI Hub.
Muestra y permite editar:
  - Carpeta de modelos
  - Carpeta de outputs
  - Información del sistema (expandida)
  - Log de eventos operacionales
"""
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt

from gui.state import state


class HubSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(20)

        layout.addWidget(self._build_paths_group())
        layout.addWidget(self._build_sysinfo_group())
        layout.addStretch()

    # ------------------------------------------------------------------ #
    # Paths group                                                          #
    # ------------------------------------------------------------------ #

    def _build_paths_group(self) -> QGroupBox:
        box = QGroupBox("📁 Rutas de Almacenamiento")
        box.setStyleSheet("""
            QGroupBox {
                color: #54EFEA;
                font-size: 14px;
                font-weight: bold;
                border: 1px solid #3D1B7B;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; padding: 0 6px; }
        """)
        layout = QVBoxLayout(box)
        layout.setSpacing(12)

        paths = state.get_paths()

        self._models_edit = self._path_row(
            layout, "Modelos:", paths.get("models", "No configurado")
        )
        self._outputs_edit = self._path_row(
            layout, "Outputs:", paths.get("outputs", "No configurado")
        )

        save_btn = QPushButton("💾 Guardar Rutas")
        save_btn.setObjectName("install_btn")
        save_btn.setFixedWidth(160)
        save_btn.clicked.connect(self._save_paths)
        layout.addWidget(save_btn, alignment=Qt.AlignLeft)

        return box

    def _path_row(self, parent_layout, label_text: str, value: str) -> QLineEdit:
        """Helper: adds a label + editable path + browse button row."""
        row = QHBoxLayout()

        lbl = QLabel(label_text)
        lbl.setFixedWidth(80)
        lbl.setStyleSheet("color: #e0e0ff;")

        edit = QLineEdit(value)
        edit.setStyleSheet("""
            QLineEdit {
                background: #1A0040;
                color: #54EFEA;
                border: 1px solid #3D1B7B;
                border-radius: 4px;
                padding: 4px 8px;
                font-family: monospace;
            }
        """)

        browse_btn = QPushButton("📁")
        browse_btn.setFixedWidth(36)
        browse_btn.setStyleSheet("""
            QPushButton { background: #3D1B7B; color: #e0e0ff; border-radius: 4px; }
            QPushButton:hover { background: #600DB5; }
        """)
        browse_btn.clicked.connect(lambda checked=False, e=edit: self._browse_dir(e))

        row.addWidget(lbl)
        row.addWidget(edit)
        row.addWidget(browse_btn)
        parent_layout.addLayout(row)
        return edit

    def _browse_dir(self, edit: QLineEdit):
        current = edit.text()
        start = current if os.path.isdir(current) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", start)
        if chosen:
            edit.setText(chosen)

    def _save_paths(self):
        models_dir  = self._models_edit.text().strip()
        outputs_dir = self._outputs_edit.text().strip()

        errors = []
        if models_dir and not os.path.isdir(models_dir):
            errors.append(f"La carpeta de modelos no existe:\n{models_dir}")
        if outputs_dir and not os.path.isdir(outputs_dir):
            errors.append(f"La carpeta de outputs no existe:\n{outputs_dir}")

        if errors:
            QMessageBox.warning(self, "Rutas inválidas", "\n\n".join(errors))
            return

        state.set_paths(
            models_dir=models_dir  or None,
            outputs_dir=outputs_dir or None,
        )
        QMessageBox.information(self, "Guardado", "✅ Rutas guardadas correctamente.")

    # ------------------------------------------------------------------ #
    # System info group                                                    #
    # ------------------------------------------------------------------ #

    def _build_sysinfo_group(self) -> QGroupBox:
        box = QGroupBox("🖥️ Información del Sistema")
        box.setStyleSheet("""
            QGroupBox {
                color: #54EFEA;
                font-size: 14px;
                font-weight: bold;
                border: 1px solid #3D1B7B;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; padding: 0 6px; }
        """)
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        info = state.sys_info
        rows = [
            ("GPU:",              info.get("gpu_name", "N/D")),
            ("CUDA Tag:",         info.get("cuda_tag", "N/D")),
            ("CUDA Max:",         info.get("max_cuda", "N/D")),
            ("PyTorch Target:",   info.get("torch_version", "N/D")),
            ("Espacio Libre:",    info.get("disk_free", "N/D")),
        ]
        for label, value in rows:
            row_lbl = QLabel(
                f"<span style='color:#aaaacc'>{label}</span> "
                f"<span style='color:#54EFEA; font-weight:bold'>{value}</span>"
            )
            layout.addWidget(row_lbl)

        return box

