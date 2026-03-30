"""
AppCard — Tarjeta de aplicación individual para el AI Hub.

Diseño (Fase 4 — rediseño completo):
  ┌─────────────────────────────────────────────────────────────────┐
  │ [ícono]  Nombre App               [badge tipo]  ● Estado        │
  │          Descripción breve              🔌 puerto  🔑 PID XXXXX │
  │                          [▶ Lanzar]  [↑ Act.]  [⚙]  [✕]        │
  └─────────────────────────────────────────────────────────────────┘

Solo el panel derecho (botones + status + meta) se reconstruye en
update_status(). El ícono y la info estática no se tocan.
"""
import os

from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel,
                               QPushButton, QProgressBar, QWidget)
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt
from gui import theme

# Texto del botón placeholder según estado busy
_BUSY_LABELS = {
    "launching":    "Iniciando...",
    "installing":   "Instalando...",
    "updating":     "Actualizando...",
    "uninstalling": "Desinstalando...",
    "stopping":     "Deteniendo...",
}

_PROGRESS_BAR_STYLE = f"""
    QProgressBar {{
        border: none;
        border-radius: 2px;
        background: {theme.BG_SURFACE};
        max-height: 3px;
    }}
    QProgressBar::chunk {{
        background: {theme.ACCENT_VIOLET};
        border-radius: 2px;
    }}
"""

# Altura fija de cada card para consistencia visual
_CARD_HEIGHT = 90

# Directorio de íconos — apps/{app_id}/icon.png (relativo a HUB_ROOT)
_HUB_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ICONS_BASE = os.path.join(os.path.dirname(_HUB_DIR), "apps")


def _load_icon(app_id: str, size: int = 48) -> QLabel:
    """
    Carga apps/{app_id}/icon.png si existe.
    Fallback: placeholder con la inicial del nombre en un cuadrado de color.
    """
    icon_path = os.path.join(_ICONS_BASE, app_id, "icon.png")
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    lbl.setAlignment(Qt.AlignCenter)
    if os.path.isfile(icon_path):
        px = QPixmap(icon_path).scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        lbl.setPixmap(px)
    else:
        # Placeholder: inicial del app_id en caja con color accent
        initial = app_id[0].upper()
        lbl.setText(initial)
        lbl.setFont(QFont("sans-serif", size // 3, QFont.Bold))
        lbl.setStyleSheet(
            f"background-color: {theme.ACCENT_VIOLET}; "
            f"color: {theme.TEXT_PRIMARY}; "
            f"border-radius: {size // 6}px;"
        )
    return lbl


class AppCard(QFrame):
    def __init__(self, app_id: str, app_cfg: dict, status: str,
                 on_action_callback, any_running: bool = False,
                 running_proc=None, parent=None):
        super().__init__(parent)
        self.app_id             = app_id
        self.app_cfg            = app_cfg
        self.status             = status
        self.any_running        = any_running
        self.running_proc       = running_proc
        self.on_action_callback = on_action_callback
        self.setObjectName("card")
        self.setFixedHeight(_CARD_HEIGHT)
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
            type_badge, type_color = "🌐 WebUI",   theme.ACCENT_CYAN_2
        elif launch_type == "python" and not port:
            type_badge, type_color = "🖥 Desktop", theme.TEXT_SECONDARY
        elif launch_type == "npm":
            type_badge, type_color = "🌐 WebUI",   theme.ACCENT_CYAN_2
        else:
            type_badge, type_color = "", ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 14, 8)
        outer.setSpacing(12)

        # ── Ícono de la app ───────────────────────────────────────────
        icon_lbl = _load_icon(self.app_id, size=48)
        outer.addWidget(icon_lbl, alignment=Qt.AlignVCenter)

        # ── Info estática (columna central) ───────────────────────────
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        info_col.setContentsMargins(0, 0, 0, 0)

        # Fila 1: nombre + badge de tipo
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.setContentsMargins(0, 0, 0, 0)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {theme.TEXT_PRIMARY};"
        )
        name_row.addWidget(name_lbl)

        if type_badge:
            badge_lbl = QLabel(type_badge)
            badge_lbl.setStyleSheet(
                f"font-size: 10px; color: {type_color}; "
                f"border: 1px solid {type_color}; border-radius: 3px; "
                f"padding: 1px 5px;"
            )
            name_row.addWidget(badge_lbl)

        name_row.addStretch()
        info_col.addLayout(name_row)

        # Fila 2: descripción (truncada si es muy larga)
        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")
            desc_lbl.setWordWrap(False)
            # Truncar con elipsis si excede el espacio
            desc_lbl.setMaximumWidth(380)
            info_col.addWidget(desc_lbl)

        outer.addLayout(info_col, stretch=1)

        # ── Panel derecho (mutable) ───────────────────────────────────
        self._right_widget = QWidget()
        self._right_layout = QVBoxLayout(self._right_widget)
        self._right_layout.setSpacing(4)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        outer.addWidget(self._right_widget)

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
                w = item.widget()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                sub = item.layout()
                while sub.count():
                    s = sub.takeAt(0)
                    if s.widget():
                        s.widget().setParent(None)
                        s.widget().deleteLater()

        self._populate_right(status, any_running, running_proc)

    def _populate_right(self, status: str, any_running: bool, running_proc=None):
        """Construye el panel derecho según el estado actual."""
        label_text  = theme.STATUS_LABELS.get(status, "○ Desconocido")
        label_color = theme.STATUS_COLORS.get(status, theme.TEXT_META)

        # Fila: status + meta (puerto, PID) en la misma línea cuando running
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addStretch()

        status_lbl = QLabel(label_text)
        status_lbl.setStyleSheet(
            f"color: {label_color}; font-weight: bold; font-size: 12px;"
        )
        top_row.addWidget(status_lbl)

        if status == "running" and running_proc:
            port = self.app_cfg.get("default_port")
            meta_parts = []
            if port:
                meta_parts.append(f"🔌 {port}")
            if running_proc.pid:
                meta_parts.append(f"🔑 PID {running_proc.pid}")
            if meta_parts:
                meta_lbl = QLabel("  ".join(meta_parts))
                meta_lbl.setStyleSheet(
                    f"color: {theme.TEXT_META}; font-size: 11px; font-family: monospace;"
                )
                top_row.addWidget(meta_lbl)

        self._right_layout.addLayout(top_row)

        is_busy     = status in _BUSY_LABELS
        is_internal = self.app_cfg.get("is_internal", False)

        if status in ("installed", "running"):
            btn_row = QHBoxLayout()
            btn_row.setSpacing(4)
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.addStretch()

            # Botón principal: Lanzar ↔ Detener según el estado
            if status == "running":
                btn_primary = QPushButton("⏹ Detener")
                btn_primary.setObjectName("stop_btn")
                btn_primary.setToolTip("Detener la aplicación")
                btn_primary.clicked.connect(lambda: self._trigger_action("stop"))
            else:
                btn_primary = QPushButton("▶ Lanzar")
                btn_primary.setObjectName("success_btn")
                btn_primary.setEnabled(not any_running)
                if any_running:
                    btn_primary.setToolTip("Otra app está activa. Deténla primero.")
                btn_primary.clicked.connect(lambda: self._trigger_action("launch"))
            btn_primary.setFixedWidth(100)
            btn_row.addWidget(btn_primary)

            if not is_internal:
                btn_update = QPushButton("↑ Act.")
                btn_update.setObjectName("outline_btn")
                btn_update.setFixedWidth(72)
                btn_update.setToolTip("Buscar y aplicar actualizaciones")
                btn_update.setEnabled(status != "running")
                btn_update.clicked.connect(lambda: self._trigger_action("update"))

                btn_settings = QPushButton("⚙")
                btn_settings.setObjectName("outline_btn")
                btn_settings.setFixedWidth(32)
                btn_settings.setToolTip("Configurar flags y opciones")
                btn_settings.clicked.connect(lambda: self._trigger_action("settings"))

                btn_uninstall = QPushButton("✕")
                btn_uninstall.setObjectName("danger_outline_btn")
                btn_uninstall.setFixedWidth(32)
                btn_uninstall.setToolTip("Desinstalar esta aplicación")
                btn_uninstall.setEnabled(status != "running")
                btn_uninstall.clicked.connect(lambda: self._trigger_action("uninstall"))

                btn_row.addWidget(btn_update)
                btn_row.addWidget(btn_settings)
                btn_row.addWidget(btn_uninstall)

            self._right_layout.addLayout(btn_row)

        elif is_busy:
            placeholder = QPushButton(_BUSY_LABELS.get(status, "En progreso..."))
            placeholder.setObjectName("outline_btn")
            placeholder.setEnabled(False)
            self._right_layout.addWidget(placeholder, alignment=Qt.AlignRight)

            # Progress bar indeterminada para todos los estados busy
            bar = QProgressBar()
            bar.setRange(0, 0)
            bar.setFixedHeight(3)
            bar.setStyleSheet(_PROGRESS_BAR_STYLE)
            bar.setTextVisible(False)
            self._right_layout.addWidget(bar)

        else:  # not_installed
            btn_install = QPushButton("↓ Instalar")
            btn_install.setObjectName("install_btn")
            btn_install.setFixedWidth(110)
            btn_install.clicked.connect(lambda: self._trigger_action("install"))
            self._right_layout.addWidget(btn_install, alignment=Qt.AlignRight)
