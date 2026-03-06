"""
AppCard — Tarjeta de aplicación individual para el AI Hub.
"""
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel,
                               QPushButton, QWidget)
from PySide6.QtCore import Qt

_STATUS_DISPLAY = {
    "running":       ("● Corriendo",        "#54EFEA"),
    "installed":     ("● Instalada",        "#51CCDC"),
    "launching":     ("⏳ Iniciando...",    "#EC00F0"),
    "installing":    ("⏳ Instalando...",   "#EC00F0"),
    "updating":      ("⏳ Actualizando...", "#EC00F0"),
    "uninstalling":  ("⏳ Desinstalando…",  "#EC00F0"),
    "stopping":      ("⏹ Deteniendo...",   "#EC00F0"),
    "not_installed": ("○ No instalada",     "#555588"),
}


class AppCard(QFrame):
    def __init__(self, app_id: str, app_cfg: dict, status: str,
                 on_action_callback, any_running: bool = False, parent=None):
        super().__init__(parent)
        self.app_id = app_id
        self.app_cfg = app_cfg
        self.status = status
        self.on_action_callback = on_action_callback
        self.any_running = any_running
        self.setObjectName("card")
        self.build_ui()

    def _trigger_action(self, action: str):
        if self.on_action_callback:
            self.on_action_callback(action, self.app_id)

    def build_ui(self):
        name = self.app_cfg.get("name", self.app_id)
        desc = self.app_cfg.get("description", "")
        port = self.app_cfg.get("default_port", "")
        launch_type = self.app_cfg.get("launch_type", "")

        # Determine app type badge
        if port:
            type_badge = "🌐 WebUI"
            type_color = "#51CCDC"
        elif launch_type == "python" and not port:
            type_badge = "🖥 Desktop"
            type_color = "#888aaa"
        else:
            type_badge = ""
            type_color = ""

        # Main horizontal layout
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)

        # ── Left: App info ────────────────────────────────────────────────
        info = QVBoxLayout()
        info.setSpacing(3)

        # Title row: name + type badge
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(name)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #e0e0ff;")
        title_row.addWidget(title_lbl)

        if type_badge:
            badge_lbl = QLabel(type_badge)
            badge_lbl.setStyleSheet(
                f"font-size: 10px; color: {type_color}; "
                f"border: 1px solid {type_color}; border-radius: 3px; "
                f"padding: 1px 5px; font-weight: bold;"
            )
            title_row.addWidget(badge_lbl)

        title_row.addStretch()
        info.addLayout(title_row)

        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: #888aaa; font-size: 12px;")
            desc_lbl.setWordWrap(True)
            info.addWidget(desc_lbl)

        if port:
            port_lbl = QLabel(f"Puerto: {port}")
            port_lbl.setStyleSheet("color: #555588; font-size: 11px;")
            info.addWidget(port_lbl)

        outer.addLayout(info, stretch=1)

        # ── Right: Status + Buttons (all added directly — no wrapper QWidget)
        right = QVBoxLayout()
        right.setSpacing(6)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Status label
        label_text, label_color = _STATUS_DISPLAY.get(
            self.status, ("○ Desconocido", "#757575")
        )
        status_lbl = QLabel(label_text)
        status_lbl.setAlignment(Qt.AlignRight)
        status_lbl.setStyleSheet(
            f"color: {label_color}; font-weight: bold; font-size: 12px;"
        )
        right.addWidget(status_lbl)

        is_busy = self.status in (
            "launching", "installing", "updating", "uninstalling", "stopping"
        )

        if self.status == "running":
            # Stop button added DIRECTLY to right layout — no intermediate wrapper
            btn_stop = QPushButton("⏹ Detener")
            btn_stop.setObjectName("stop_btn")
            btn_stop.clicked.connect(
                lambda checked=False: self._trigger_action("stop")
            )
            right.addWidget(btn_stop, alignment=Qt.AlignRight)

        elif self.status == "installed":
            blocked = self.any_running
            is_internal = self.app_cfg.get("is_internal", False)

            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            btn_row.setContentsMargins(0, 0, 0, 0)

            btn_launch = QPushButton("▶ Lanzar")
            btn_launch.setObjectName("success_btn")
            btn_launch.setEnabled(not blocked)
            if blocked:
                btn_launch.setToolTip("Otra app está activa. Detén la app activa primero.")
            btn_launch.clicked.connect(
                lambda checked=False: self._trigger_action("launch")
            )
            btn_row.addWidget(btn_launch)

            if not is_internal:
                btn_update = QPushButton("↑ Actualizar")
                btn_update.setObjectName("outline_btn")
                btn_update.clicked.connect(
                    lambda checked=False: self._trigger_action("update")
                )

                btn_uninstall = QPushButton("✕ Desinstalar")
                btn_uninstall.setObjectName("danger_outline_btn")
                btn_uninstall.clicked.connect(
                    lambda checked=False: self._trigger_action("uninstall")
                )

                btn_settings = QPushButton("⚙ Opciones")
                btn_settings.setObjectName("outline_btn")
                btn_settings.setToolTip("Configurar flags y opciones")
                btn_settings.clicked.connect(
                    lambda checked=False: self._trigger_action("settings")
                )

                btn_row.addWidget(btn_update)
                btn_row.addWidget(btn_uninstall)
                btn_row.addWidget(btn_settings)

            right.addLayout(btn_row)

        elif is_busy:
            placeholder = QPushButton("En progreso...")
            placeholder.setObjectName("outline_btn")
            placeholder.setEnabled(False)
            right.addWidget(placeholder, alignment=Qt.AlignRight)

        else:  # not_installed
            btn_install = QPushButton("↓ Instalar")
            btn_install.setObjectName("install_btn")
            btn_install.clicked.connect(
                lambda checked=False: self._trigger_action("install")
            )
            right.addWidget(btn_install, alignment=Qt.AlignRight)

        outer.addLayout(right)
