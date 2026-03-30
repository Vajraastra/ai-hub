"""
HubSettings — Widget para la tab "Hub" del AI Hub.
Muestra y permite editar:
  - Carpeta de modelos
  - Carpeta de outputs
  - Información del sistema (expandida)
  - Log de eventos operacionales
"""
import os

import threading
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFileDialog, QMessageBox, QProgressDialog
)
from PySide6.QtCore import Qt

from gui.state import state
from gui.theme import GROUPBOX_STYLE


class HubSettings(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(20)

        layout.addWidget(self._build_paths_group())
        layout.addWidget(self._build_vault_group())
        layout.addWidget(self._build_sysinfo_group())
        layout.addStretch()

    def _build_vault_group(self) -> QGroupBox:
        box = QGroupBox("📦 Model Vault Settings")
        box.setStyleSheet(GROUPBOX_STYLE)
        layout = QVBoxLayout(box)
        layout.setSpacing(12)

        row = QHBoxLayout()
        lbl = QLabel("Civitai API Key:")
        lbl.setFixedWidth(100)
        lbl.setStyleSheet("color: #e0e0ff;")

        self._civitai_key_edit = QLineEdit(state.get_civitai_key())
        self._civitai_key_edit.setEchoMode(QLineEdit.Password)
        self._civitai_key_edit.setPlaceholderText(
            "Tu API Key de Civitai (opcional)"
        )
        self._civitai_key_edit.setStyleSheet("""
            QLineEdit {
                background: #1A0040;
                color: #54EFEA;
                border: 1px solid #3D1B7B;
                border-radius: 4px;
                padding: 4px 8px;
            }
        """)

        # Botón toggle para mostrar/ocultar la key
        toggle_btn = QPushButton("👁")
        toggle_btn.setFixedWidth(34)
        toggle_btn.setToolTip("Mostrar/ocultar API Key")
        toggle_btn.setStyleSheet(
            "QPushButton { background: #3D1B7B; color: #e0e0ff; border-radius: 4px; }"
            "QPushButton:hover { background: #600DB5; }"
        )
        toggle_btn.setCheckable(True)
        def _toggle_visibility(checked):
            mode = QLineEdit.Normal if checked else QLineEdit.Password
            self._civitai_key_edit.setEchoMode(mode)
        toggle_btn.toggled.connect(_toggle_visibility)

        row.addWidget(lbl)
        row.addWidget(self._civitai_key_edit)
        row.addWidget(toggle_btn)
        layout.addLayout(row)

        save_btn = QPushButton("💾 Guardar Ajustes Vault")
        save_btn.setObjectName("install_btn")
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._save_vault_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignLeft)

        return box

    def _save_vault_settings(self):
        key = self._civitai_key_edit.text().strip()
        state.set_civitai_key(key)
        QMessageBox.information(
            self, "Guardado",
            "✅ Ajustes del Model Vault guardados correctamente."
        )

    # ------------------------------------------------------------------ #
    # Paths group                                                          #
    # ------------------------------------------------------------------ #

    def _build_paths_group(self) -> QGroupBox:
        box = QGroupBox("📁 Rutas de Almacenamiento")
        box.setStyleSheet(GROUPBOX_STYLE)
        layout = QVBoxLayout(box)
        layout.setSpacing(12)

        paths = state.get_paths()

        self._models_edit = self._path_row(
            layout, "Modelos:", paths.get("models", "No configurado")
        )
        self._outputs_edit = self._path_row(
            layout, "Outputs:", paths.get("outputs", "No configurado")
        )

        btn_row = QHBoxLayout()

        save_btn = QPushButton("💾 Guardar Rutas")
        save_btn.setObjectName("install_btn")
        save_btn.setFixedWidth(160)
        save_btn.clicked.connect(self._save_paths)
        btn_row.addWidget(save_btn)

        refresh_btn = QPushButton("🔄 Regenerar paths de modelos")
        refresh_btn.setObjectName("outline_btn")
        refresh_btn.setToolTip(
            "Vuelve a escanear la carpeta de modelos y actualiza los configs\n"
            "de todas las apps instaladas (YAML de ComfyUI, args de Forge...)\n\n"
            "Úsalo después de descargar modelos nuevos o reorganizar carpetas."
        )
        refresh_btn.clicked.connect(self._refresh_model_paths)
        btn_row.addWidget(refresh_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

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
        browse_btn.clicked.connect(
            lambda checked=False, e=edit: self._browse_dir(e)
        )

        row.addWidget(lbl)
        row.addWidget(edit)
        row.addWidget(browse_btn)
        parent_layout.addLayout(row)
        return edit

    def _browse_dir(self, edit: QLineEdit):
        current = edit.text()
        start = current if os.path.isdir(current) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta", start
        )
        if chosen:
            edit.setText(chosen)

    def _save_paths(self):
        models_dir = self._models_edit.text().strip()
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
            models_dir=models_dir or None,
            outputs_dir=outputs_dir or None,
        )

        if models_dir:
            self._run_refresh(models_dir, silent=True)

        QMessageBox.information(self, "Guardado", "✅ Rutas guardadas correctamente.")

    def _refresh_model_paths(self):
        """Regenera los configs de modelos de forma asincrónica con indicador de progreso."""
        models_dir = state.get_paths().get("models", "")
        if not models_dir or not os.path.isdir(models_dir):
            QMessageBox.warning(
                self, "Sin ruta de modelos",
                "Primero configura y guarda la carpeta de modelos."
            )
            return

        progress = QProgressDialog(
            "Regenerando paths de modelos...", None, 0, 0, self
        )
        progress.setWindowTitle("Actualizando")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        result = {"count": 0}

        def _worker():
            result["count"] = self._run_refresh(models_dir, silent=False)

        def _on_done():
            progress.close()
            QMessageBox.information(
                self, "Paths actualizados",
                f"✅ Configs regenerados para {result['count']} app(s).\n\n"
                "Reinicia las apps activas para que carguen los nuevos paths."
            )

        def _run():
            _worker()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, _on_done)

        threading.Thread(target=_run, daemon=True).start()

    def _run_refresh(self, models_dir: str, silent: bool = False) -> int:
        """Regenera model paths para todas las apps instaladas. Retorna count."""
        try:
            from modules.storage_manager import build_app_model_args
            refreshed = 0
            for app_id, app_info in state.installed_apps.items():
                app_cfg = state.registry_apps.get(app_id, {})
                app_dir = app_info.get("dir", "")
                if app_dir and app_cfg.get("model_map"):
                    build_app_model_args(app_cfg, models_dir, app_dir)
                    refreshed += 1
            if refreshed and not silent:
                print(f"[settings] Model paths refreshed for {refreshed} app(s).")
            return refreshed
        except Exception as e:
            print(f"[settings] Error refreshing model paths: {e}")
            return 0

    # ------------------------------------------------------------------ #
    # System info group                                                    #
    # ------------------------------------------------------------------ #

    def _build_sysinfo_group(self) -> QGroupBox:
        box = QGroupBox("🖥️ Información del Sistema")
        box.setStyleSheet(GROUPBOX_STYLE)
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        # Nota informativa
        readonly_lbl = QLabel("ℹ️  Solo lectura — detectado automáticamente al iniciar")
        readonly_lbl.setStyleSheet("color: #555588; font-size: 11px; font-style: italic;")
        layout.addWidget(readonly_lbl)

        info = state.sys_info
        rows = [
            ("GPU:",            info.get("gpu_name", "N/D")),
            ("CUDA Tag:",       info.get("cuda_tag", "N/D")),
            ("CUDA Max:",       info.get("max_cuda", "N/D")),
            ("PyTorch Target:", info.get("torch_version", "N/D")),
            ("Espacio Libre:",  info.get("disk_free", "N/D")),
        ]
        for label, value in rows:
            row_lbl = QLabel(
                f"<span style='color:#aaaacc'>{label}</span> "
                f"<span style='color:#54EFEA; font-weight:bold'>{value}</span>"
            )
            layout.addWidget(row_lbl)

        return box
