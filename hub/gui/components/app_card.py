from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, 
                               QPushButton, QWidget, QSizePolicy)
from PySide6.QtCore import Qt

# Status labels and colors aligned with the cyberpunk palette
_STATUS_DISPLAY = {
    "running":       ("🟢 Corriendo",          "#54EFEA"),  # fluorescent cyan
    "installed":     ("🔵 Instalada",          "#51CCDC"),  # robin egg blue
    "installing":    ("⏳ Instalando...",      "#EC00F0"),  # phlox
    "updating":      ("⏳ Actualizando...",    "#EC00F0"),  # phlox
    "uninstalling":  ("⏳ Desinstalando...",   "#EC00F0"),  # phlox
    "stopping":      ("⏹️ Deteniendo...",      "#EC00F0"),  # phlox
    "not_installed": ("⚪ No instalada",        "#555588"),  # dim violet
}

class AppCard(QFrame):
    def __init__(self, app_id: str, app_cfg: dict, status: str,
                 on_action_callback, any_running: bool = False, parent=None):
        """
        app_id:            Identifier of this app.
        app_cfg:           Config dict from registry.
        status:            Current status string for this app.
        on_action_callback: Called with (action, app_id).
        any_running:       True if ANY app is currently running or stopping.
                           Used to disable the Launch button on non-running cards.
        """
        super().__init__(parent)
        self.app_id = app_id
        self.app_cfg = app_cfg
        self.status = status
        self.on_action_callback = on_action_callback
        self.any_running = any_running

        self.setObjectName("card")
        self.build_ui()

    def _trigger_action(self, action: str):
        """Forward action to main window callback, discarding any Qt signal extras."""
        if self.on_action_callback:
            self.on_action_callback(action, self.app_id)

    def build_ui(self):
        name = self.app_cfg.get("name", self.app_id)
        desc = self.app_cfg.get("description", "")
        port = self.app_cfg.get("default_port", "")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        # ── Left side: Info ──────────────────────────────────────────────
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        title_lbl = QLabel(name)
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")

        desc_lbl = QLabel(desc)
        desc_lbl.setStyleSheet("color: #aaaaaa; font-size: 13px;")
        desc_lbl.setWordWrap(True)

        info_layout.addWidget(title_lbl)
        info_layout.addWidget(desc_lbl)

        if port:
            port_lbl = QLabel(f"Puerto: {port}")
            port_lbl.setStyleSheet("color: #777777; font-size: 11px;")
            info_layout.addWidget(port_lbl)

        layout.addLayout(info_layout)
        layout.addStretch()

        # ── Right side: Status + Buttons ─────────────────────────────────
        actions_layout = QVBoxLayout()
        actions_layout.setSpacing(8)
        actions_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Status label
        label_text, label_color = _STATUS_DISPLAY.get(
            self.status, ("⚪ Desconocido", "#757575")
        )
        status_lbl = QLabel(label_text)
        status_lbl.setAlignment(Qt.AlignRight)
        status_lbl.setStyleSheet(f"color: {label_color}; font-weight: bold;")
        actions_layout.addWidget(status_lbl)

        # Button row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        is_busy = self.status in ("installing", "updating", "uninstalling", "stopping")

        if self.status == "running":
            # The running app only shows "Detener"
            btn_stop = QPushButton("Detener")
            btn_stop.setObjectName("stop_btn")
            btn_stop.clicked.connect(lambda checked=False: self._trigger_action("stop"))
            btn_layout.addWidget(btn_stop)

        elif self.status == "installed":
            btn_launch = QPushButton("Lanzar")
            btn_launch.setObjectName("success_btn")
            btn_launch.clicked.connect(lambda checked=False: self._trigger_action("launch"))

            # Block Launch if another app is already running or stopping
            if self.any_running:
                btn_launch.setEnabled(False)
                btn_launch.setToolTip(
                    "Otra aplicación está corriendo o deteniéndose.\n"
                    "Detén la app activa antes de lanzar esta."
                )

            btn_update = QPushButton("Actualizar")
            btn_update.setObjectName("outline_btn")
            btn_update.clicked.connect(lambda checked=False: self._trigger_action("update"))

            btn_uninstall = QPushButton("Desinstalar")
            btn_uninstall.setObjectName("danger_outline_btn")
            btn_uninstall.clicked.connect(lambda checked=False: self._trigger_action("uninstall"))

            btn_settings = QPushButton("⚙️")
            btn_settings.setObjectName("outline_btn")
            btn_settings.setToolTip("Configurar flags y opciones de esta app")
            btn_settings.setFixedWidth(36)
            btn_settings.clicked.connect(lambda checked=False: self._trigger_action("settings"))

            btn_layout.addWidget(btn_launch)
            btn_layout.addWidget(btn_update)
            btn_layout.addWidget(btn_uninstall)
            btn_layout.addWidget(btn_settings)

        elif is_busy:
            # Show disabled placeholder so the card keeps its height
            placeholder = QPushButton("En progreso...")
            placeholder.setObjectName("outline_btn")
            placeholder.setEnabled(False)
            btn_layout.addWidget(placeholder)

        else:  # not_installed
            btn_install = QPushButton("Instalar")
            btn_install.setObjectName("install_btn")
            btn_install.clicked.connect(lambda checked=False: self._trigger_action("install"))
            btn_layout.addWidget(btn_install)

        btn_container = QWidget()
        btn_container.setLayout(btn_layout)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addWidget(btn_container)

        layout.addLayout(actions_layout)
