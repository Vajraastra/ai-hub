"""
AppSettingsDialog — Diálogo de configuración por aplicación.
Permite configurar:
  - Flags CLI (checkboxes para flags conocidas + campo libre)
  - Puerto override (opcional)
  - Info de instalación (read-only)
"""
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QSpinBox, QGroupBox, QScrollArea,
    QWidget, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from gui.state import state

# Badge color per risk level
_RISK_COLORS = {
    "low":    ("#66bb6a", "🟢"),
    "medium": ("#ffa726", "🟡"),
    "high":   ("#ef5350", "🔴"),
}

# Badge per GPU profile category
_PROFILE_BADGES = {
    "recommended":      ("⭐", "#54EFEA", "Recomendado para tu GPU"),
    "optional":         ("🔵", "#51CCDC", "Opcional"),
    "experimental":     ("⚗️",  "#ffa726", "Experimental"),
    "not_recommended":  ("🚫", "#ef5350", "No recomendado para tu GPU"),
    "warn_before_enable": ("⚠️", "#ffa726", "Usar con precaución"),
}


class AppSettingsDialog(QDialog):
    def __init__(self, app_id: str, parent=None):
        super().__init__(parent)
        self.app_id      = app_id
        self.app_cfg     = state.registry_apps.get(app_id, {})
        self.app_flags   = state.load_app_flags().get(app_id, {})
        self.gpu_profile = state.load_gpu_profile()
        self._flag_checks: dict = {}   # flag_string -> (QCheckBox, flag_def, requires_arg)
        self._flag_arg_edits: dict = {}  # flag_string -> QLineEdit (for flags with args)

        self.setWindowTitle(f"⚙️  Config: {self.app_cfg.get('name', app_id)}")
        self.setMinimumSize(620, 520)
        self.setModal(True)

        self._apply_style()
        self._build_ui()

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog { background-color: #0F0023; color: #e0e0ff; }
            QGroupBox {
                color: #54EFEA; font-size: 13px; font-weight: bold;
                border: 1px solid #3D1B7B; border-radius: 6px;
                margin-top: 10px; padding-top: 10px;
            }
            QGroupBox::title { subcontrol-origin: margin; padding: 0 6px; }
            QLabel { color: #e0e0ff; }
            QCheckBox { color: #e0e0ff; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QLineEdit, QSpinBox {
                background: #1A0040; color: #54EFEA;
                border: 1px solid #3D1B7B; border-radius: 4px;
                padding: 3px 8px;
            }
        """)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)

        # Title
        name = self.app_cfg.get("name", self.app_id)
        title = QLabel(f"⚙️  {name}")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #54EFEA;")
        outer.addWidget(title)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setSpacing(14)
        layout.setContentsMargins(0, 0, 10, 0)

        layout.addWidget(self._build_info_group())
        layout.addWidget(self._build_port_group())

        flags_list = self.app_flags.get("flags", [])
        if flags_list:
            layout.addWidget(self._build_flags_group(flags_list))
        else:
            no_flags = QLabel("ℹ️  Esta aplicación no tiene flags configurables disponibles.")
            no_flags.setStyleSheet("color: #777799; font-style: italic;")
            layout.addWidget(no_flags)

        layout.addWidget(self._build_custom_flags_group())
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)
        outer.addWidget(self._build_buttons())

    # ------------------------------------------------------------------ #
    # Info group (read-only)                                               #
    # ------------------------------------------------------------------ #

    def _build_info_group(self) -> QGroupBox:
        box = QGroupBox("📂 Información de Instalación")
        layout = QVBoxLayout(box)
        layout.setSpacing(4)

        info = state.installed_apps.get(self.app_id, {})
        app_dir = info.get("dir", "No instalada")
        branch  = self.app_cfg.get("branch", "main")
        port    = self.app_cfg.get("default_port", "N/D")

        for label, value in [
            ("Directorio:", app_dir),
            ("Branch:", branch),
            ("Puerto base:", str(port)),
        ]:
            row = QLabel(
                f"<span style='color:#aaaacc'>{label}</span> "
                f"<span style='color:#54EFEA; font-family:monospace'>{value}</span>"
            )
            layout.addWidget(row)
        return box

    # ------------------------------------------------------------------ #
    # Port override group                                                  #
    # ------------------------------------------------------------------ #

    def _build_port_group(self) -> QGroupBox:
        box = QGroupBox("🔌 Puerto")
        layout = QHBoxLayout(box)

        default_port = self.app_cfg.get("default_port", 8080)
        current_override = state.get_port_override(self.app_id)

        self._port_check = QCheckBox("Usar puerto personalizado")
        self._port_check.setChecked(current_override is not None)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(current_override or default_port)
        self._port_spin.setFixedWidth(100)
        self._port_spin.setEnabled(current_override is not None)
        self._port_spin.setToolTip("Puerto en el que se iniciará la app")

        self._port_check.toggled.connect(self._port_spin.setEnabled)

        hint = QLabel(f"(base: {default_port})")
        hint.setStyleSheet("color: #777799; font-size: 11px;")

        layout.addWidget(self._port_check)
        layout.addWidget(self._port_spin)
        layout.addWidget(hint)
        layout.addStretch()
        return box

    # ------------------------------------------------------------------ #
    # Flags group                                                          #
    # ------------------------------------------------------------------ #

    def _build_flags_group(self, flags_list: list) -> QGroupBox:
        gpu_flags = self.gpu_profile.get("app_flags", {}).get(self.app_id, {})
        current_args = state.get_user_extra_args(self.app_id)

        box = QGroupBox("🚩 Flags de Rendimiento")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)

        # Note about GPU
        gpu_name = state.sys_info.get("gpu_name", "")
        profile_id = self.gpu_profile.get("gpu_id", "default")
        note = QLabel(f"GPU detectada: <b>{gpu_name}</b> (perfil: {profile_id})")
        note.setStyleSheet("color: #51CCDC; font-size: 12px;")
        layout.addWidget(note)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #3D1B7B;")
        layout.addWidget(sep)

        for flag_def in flags_list:
            flag   = flag_def["flag"]
            label  = flag_def["label"]
            desc   = flag_def["description"]
            risk   = flag_def.get("risk", "low")
            risk_note = flag_def.get("risk_note", "")
            req_arg = flag_def.get("requires_arg", False)
            arg_ph  = flag_def.get("arg_placeholder", "")

            # Determine GPU profile badge
            profile_badge = ""
            for category, (icon, color, tooltip) in _PROFILE_BADGES.items():
                if flag in gpu_flags.get(category, []):
                    profile_badge = f"<span style='color:{color}'> {icon} {tooltip}</span>"
                    break

            # Risk badge
            risk_color, risk_icon = _RISK_COLORS.get(risk, ("#e0e0ff", "⚪"))

            # Is this flag currently active?
            is_active = any(
                a == flag or (req_arg and str(a).startswith(flag))
                for a in current_args
            )

            # --- Row layout ---
            row = QVBoxLayout()

            # Top line: checkbox + badge
            top = QHBoxLayout()
            cb = QCheckBox(f"<b>{flag}</b>  {label}")
            cb.setChecked(is_active)
            cb.setStyleSheet("color: #e0e0ff; font-size: 13px;")

            risk_lbl = QLabel(
                f"<span style='color:{risk_color}'>{risk_icon} Riesgo {risk}</span>"
                f"{profile_badge}"
            )
            risk_lbl.setStyleSheet("font-size: 11px;")

            top.addWidget(cb)
            top.addStretch()
            top.addWidget(risk_lbl)
            row.addLayout(top)

            # Description
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #aaaacc; font-size: 12px; margin-left: 24px;")
            row.addWidget(desc_lbl)

            # Risk note (if present)
            if risk_note:
                risk_note_lbl = QLabel(f"<i>{risk_note}</i>")
                risk_note_lbl.setWordWrap(True)
                risk_note_lbl.setStyleSheet(f"color: {risk_color}; font-size: 11px; margin-left: 24px;")
                row.addWidget(risk_note_lbl)

            # Arg input if requires_arg
            arg_edit = None
            if req_arg:
                arg_row = QHBoxLayout()
                arg_lbl = QLabel("Valor:")
                arg_lbl.setStyleSheet("color: #aaaacc; margin-left: 24px; font-size: 12px;")
                arg_lbl.setFixedWidth(50)
                arg_edit = QLineEdit(arg_ph)
                arg_edit.setPlaceholderText(arg_ph)
                arg_edit.setFixedWidth(150)
                arg_edit.setEnabled(is_active)
                cb.toggled.connect(arg_edit.setEnabled)
                arg_row.addWidget(arg_lbl)
                arg_row.addWidget(arg_edit)
                arg_row.addStretch()
                row.addLayout(arg_row)

            layout.addLayout(row)

            # Divider between flags
            div = QFrame()
            div.setFrameShape(QFrame.HLine)
            div.setStyleSheet("color: #2A0A5E;")
            layout.addWidget(div)

            self._flag_checks[flag] = (cb, flag_def, req_arg)
            if arg_edit:
                self._flag_arg_edits[flag] = arg_edit

        return box

    # ------------------------------------------------------------------ #
    # Custom flags group                                                   #
    # ------------------------------------------------------------------ #

    def _build_custom_flags_group(self) -> QGroupBox:
        box = QGroupBox("✏️  Flags Adicionales (avanzado)")
        layout = QVBoxLayout(box)

        hint = QLabel(
            "Flags extra separadas por espacios. Se añaden al final del comando.\n"
            "Ejemplo: --no-half --skip-version-check"
        )
        hint.setStyleSheet("color: #777799; font-size: 11px;")
        layout.addWidget(hint)

        # Build initial value from known flags not shown above
        current_args = state.get_user_extra_args(self.app_id)
        known_flags  = {fd["flag"] for fd in self.app_flags.get("flags", [])}
        custom_args  = [a for a in current_args if not any(a.startswith(k) for k in known_flags)]

        self._custom_edit = QLineEdit(" ".join(custom_args))
        self._custom_edit.setPlaceholderText("--flag-extra --otra-flag valor")
        layout.addWidget(self._custom_edit)
        return box

    # ------------------------------------------------------------------ #
    # Buttons                                                              #
    # ------------------------------------------------------------------ #

    def _build_buttons(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("outline_btn")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("💾 Guardar")
        save_btn.setObjectName("success_btn")
        save_btn.clicked.connect(self._save)

        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(save_btn)
        return container

    # ------------------------------------------------------------------ #
    # Save logic                                                           #
    # ------------------------------------------------------------------ #

    def _save(self):
        """Builds the user_extra_args list from UI state and persists."""
        args = []

        # Collect checked flags
        for flag, (cb, flag_def, req_arg) in self._flag_checks.items():
            if cb.isChecked():
                if req_arg and flag in self._flag_arg_edits:
                    val = self._flag_arg_edits[flag].text().strip()
                    if val:
                        args.append(f"{flag} {val}")
                    else:
                        args.append(flag)
                else:
                    args.append(flag)

        # Collect custom flags
        custom = self._custom_edit.text().strip()
        if custom:
            args.extend(custom.split())

        state.set_user_extra_args(self.app_id, args)

        # Port override
        if self._port_check.isChecked():
            state.set_port_override(self.app_id, self._port_spin.value())
        else:
            state.set_port_override(self.app_id, None)

        self.accept()
