"""
AppCard — Tarjeta de aplicación individual para el AI Hub.

Diseño: widget persistente. Solo el panel derecho (botones + status) se
reconstruye al llamar update_status(). El panel izquierdo (nombre, desc,
puerto) es estático y nunca se toca después de _build_ui().
"""
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel,
                               QPushButton, QProgressBar, QWidget)
from PySide6.QtCore import Qt

_STATUS_DISPLAY = {
    "running":       ("● Corriendo",        "#54EFEA"),
    "installed":     ("● Instalada",        "#51CCDC"),
    "launching":     ("⏳ Iniciando...",    "#EC00F0"),
    "installing":    ("⏳ Instalando...",   "#EC00F0"),
    "updating":      ("⏳ Actualizando...", "#EC00F0"),
    "uninstalling":  ("⏹ Desinstalando…",  "#EC00F0"),
    "stopping":      ("⏹ Deteniendo...",   "#EC00F0"),
    "not_installed": ("○ No instalada",     "#555588"),
}

# Texto del botón placeholder según estado busy
_BUSY_LABELS = {
    "launching":    "Iniciando...",
    "installing":   "Instalando...",
    "updating":     "Actualizando...",
    "uninstalling": "Desinstalando...",
    "stopping":     "Deteniendo...",
}

_PROGRESS_BAR_STYLE = """
    QProgressBar {
        border: none;
        border-radius: 3px;
        background: #1A0040;
        max-height: 4px;
    }
    QProgressBar::chunk {
        background: #600DB5;
        border-radius: 3px;
    }
"""


class AppCard(QFrame):
    def __init__(self, app_id: str, app_cfg: dict, status: str,
                 on_action_callback, any_running: bool = False,
                 running_proc=None, parent=None):
        super().__init__(parent)
        self.app_id          = app_id
        self.app_cfg         = app_cfg
        self.status          = status
        self.any_running     = any_running
        self.running_proc    = running_proc   # subprocess.Popen | None
        self.on_action_callback = on_action_callback
        self.setObjectName("card")
        self._build_ui()

    def _trigger_action(self, action: str):
        if self.on_action_callback:
            self.on_action_callback(action, self.app_id)

    # ------------------------------------------------------------------ #
    # Construcción inicial                                                 #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        name        = self.app_cfg.get("name", self.app_id)
        desc        = self.app_cfg.get("description", "")
        port        = self.app_cfg.get("default_port", "")
        launch_type = self.app_cfg.get("launch_type", "")

        if port:
            type_badge, type_color = "🌐 WebUI",   "#51CCDC"
        elif launch_type == "python" and not port:
            type_badge, type_color = "🖥 Desktop", "#888aaa"
        else:
            type_badge, type_color = "", ""

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(14, 12, 14, 12)
        self._outer.setSpacing(12)

        # ── Panel izquierdo: info estática ────────────────────────────
        info = QVBoxLayout()
        info.setSpacing(3)

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

        self._outer.addLayout(info, stretch=1)

        # ── Panel derecho: contenedor mutable ────────────────────────
        self._right_widget = QWidget()
        self._right_layout = QVBoxLayout(self._right_widget)
        self._right_layout.setSpacing(6)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._outer.addWidget(self._right_widget)

        self._populate_right(self.status, self.any_running, self.running_proc)

    # ------------------------------------------------------------------ #
    # Actualización de estado                                             #
    # ------------------------------------------------------------------ #

    def update_status(self, status: str, any_running: bool, running_proc=None):
        """Actualiza solo el panel derecho sin destruir el widget."""
        if (status == self.status
                and any_running == self.any_running
                and running_proc == self.running_proc):
            return

        self.status       = status
        self.any_running  = any_running
        self.running_proc = running_proc

        while self._right_layout.count():
            item = self._right_layout.takeAt(0)
            if item.widget():
                # setParent(None) lo saca del árbol de hijos inmediatamente,
                # antes del deleteLater() diferido. Evita que findChildren()
                # devuelva widgets "fantasma" del estado anterior.
                w = item.widget()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                # Sub-layouts (btn_row): limpiar sus hijos primero
                sub_layout = item.layout()
                while sub_layout.count():
                    sub = sub_layout.takeAt(0)
                    if sub.widget():
                        sub.widget().setParent(None)
                        sub.widget().deleteLater()

        self._populate_right(status, any_running, running_proc)

    def _populate_right(self, status: str, any_running: bool, running_proc=None):
        """Construye el contenido del panel derecho según el estado actual."""
        label_text, label_color = _STATUS_DISPLAY.get(
            status, ("○ Desconocido", "#757575")
        )
        status_lbl = QLabel(label_text)
        status_lbl.setAlignment(Qt.AlignRight)
        status_lbl.setStyleSheet(
            f"color: {label_color}; font-weight: bold; font-size: 12px;"
        )
        self._right_layout.addWidget(status_lbl)

        is_busy     = status in _BUSY_LABELS
        is_internal = self.app_cfg.get("is_internal", False)

        if status == "running":
            # Info de proceso activo: puerto + PID
            meta_parts = []
            port = self.app_cfg.get("default_port")
            if port:
                meta_parts.append(f":{port}")
            if running_proc and running_proc.pid:
                meta_parts.append(f"PID {running_proc.pid}")
            if meta_parts:
                meta_lbl = QLabel("  ".join(meta_parts))
                meta_lbl.setAlignment(Qt.AlignRight)
                meta_lbl.setStyleSheet("color: #555588; font-size: 11px; font-family: monospace;")
                self._right_layout.addWidget(meta_lbl)

            btn_stop = QPushButton("⏹ Detener")
            btn_stop.setObjectName("stop_btn")
            btn_stop.clicked.connect(lambda: self._trigger_action("stop"))
            self._right_layout.addWidget(btn_stop, alignment=Qt.AlignRight)

        elif status == "installed":
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            btn_row.setContentsMargins(0, 0, 0, 0)

            btn_launch = QPushButton("▶ Lanzar")
            btn_launch.setObjectName("success_btn")
            btn_launch.setEnabled(not any_running)
            if any_running:
                btn_launch.setToolTip("Otra app está activa. Detén la app activa primero.")
            btn_launch.clicked.connect(lambda: self._trigger_action("launch"))
            btn_row.addWidget(btn_launch)

            if not is_internal:
                btn_update = QPushButton("↑ Actualizar")
                btn_update.setObjectName("outline_btn")
                btn_update.clicked.connect(lambda: self._trigger_action("update"))

                btn_uninstall = QPushButton("✕ Desinstalar")
                btn_uninstall.setObjectName("danger_outline_btn")
                btn_uninstall.clicked.connect(lambda: self._trigger_action("uninstall"))

                btn_settings = QPushButton("⚙ Opciones")
                btn_settings.setObjectName("outline_btn")
                btn_settings.setToolTip("Configurar flags y opciones")
                btn_settings.clicked.connect(lambda: self._trigger_action("settings"))

                btn_row.addWidget(btn_update)
                btn_row.addWidget(btn_uninstall)
                btn_row.addWidget(btn_settings)

            self._right_layout.addLayout(btn_row)

        elif is_busy:
            placeholder = QPushButton(_BUSY_LABELS.get(status, "En progreso..."))
            placeholder.setObjectName("outline_btn")
            placeholder.setEnabled(False)
            self._right_layout.addWidget(placeholder, alignment=Qt.AlignRight)

            # Barra de progreso indeterminada para operaciones largas
            if status in ("installing", "updating", "uninstalling"):
                bar = QProgressBar()
                bar.setRange(0, 0)   # indeterminado
                bar.setFixedHeight(4)
                bar.setStyleSheet(_PROGRESS_BAR_STYLE)
                bar.setTextVisible(False)
                self._right_layout.addWidget(bar)

        else:  # not_installed
            btn_install = QPushButton("↓ Instalar")
            btn_install.setObjectName("install_btn")
            btn_install.clicked.connect(lambda: self._trigger_action("install"))
            self._right_layout.addWidget(btn_install, alignment=Qt.AlignRight)
