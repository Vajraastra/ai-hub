#!/usr/bin/env python3
"""
AI Hub — PySide6 GUI Main Entry Point
"""
import os
import sys

# Ensure hub/ dir is in path to import gui modules
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(GUI_DIR)
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLabel, QScrollArea, QFrame,
                               QMessageBox, QTabWidget, QPushButton)
from PySide6.QtCore import Qt, QTimer

from gui.state import state
from gui.components.app_card import AppCard
from gui.components.hub_settings import HubSettings
from gui.components.event_log_viewer import EventLogViewer
from gui.components.app_settings_dialog import AppSettingsDialog
from gui.workers import HubWorkers

_STYLESHEET = """
    /* ── Backgrounds ─────────────────────────── */
    QMainWindow, QWidget#main_bg {
        background-color: #0F0023;
        color: #e0e0ff;
    }

    /* ── Tab widget ───────────────────────────── */
    QTabWidget::pane {
        border: none;
        background-color: #0F0023;
    }
    QTabBar::tab {
        background: #1A0040;
        color: #888aaa;
        padding: 8px 20px;
        border: 1px solid #3D1B7B;
        border-bottom: none;
        border-radius: 4px 4px 0 0;
        font-size: 13px;
        font-weight: bold;
        margin-right: 2px;
    }
    QTabBar::tab:selected {
        background: #2A0A5E;
        color: #54EFEA;
        border-bottom: 2px solid #600DB5;
    }
    QTabBar::tab:hover:!selected { background: #2A0A5E; color: #e0e0ff; }

    /* ── Cards ───────────────────────────────── */
    QFrame#card {
        background-color: #1E004E;
        border-radius: 8px;
        border: 1px solid #3D1B7B;
    }

    /* ── Labels ──────────────────────────────── */
    QLabel { color: #e0e0ff; }

    /* ── Buttons ─────────────────────────────── */
    QPushButton {
        padding: 5px 14px;
        border-radius: 4px;
        font-weight: bold;
        border: none;
        font-size: 13px;
        min-width: 80px;
    }

    /* Disabled: grey, but keeps button shape */
    QPushButton:disabled {
        background-color: #2A1A4A;
        color: #554466;
        border: 1px solid #3D1B7B;
    }

    /* Launch / primary — violet */
    QPushButton#success_btn  { background-color: #600DB5; color: #FFFFFF; }
    QPushButton#success_btn:hover  { background-color: #7B1FD4; }
    QPushButton#install_btn  { background-color: #600DB5; color: #FFFFFF; }
    QPushButton#install_btn:hover  { background-color: #7B1FD4; }

    /* Update / outline — cyan */
    QPushButton#outline_btn {
        background-color: transparent;
        border: 1px solid #51CCDC;
        color: #51CCDC;
    }
    QPushButton#outline_btn:hover { background-color: rgba(81,204,220,0.12); }
    QPushButton#outline_btn:disabled {
        border: 1px solid #3D1B7B;
        color: #444466;
        background: #1A0040;
    }

    /* Stop — phlox solid */
    QPushButton#stop_btn { background-color: #EC00F0; color: #0F0023; }
    QPushButton#stop_btn:hover { background-color: #FF33FF; }

    /* Uninstall — phlox outline */
    QPushButton#danger_outline_btn {
        background-color: transparent;
        border: 1px solid #EC00F0;
        color: #EC00F0;
    }
    QPushButton#danger_outline_btn:hover { background-color: rgba(236,0,240,0.10); }

    /* ── Scrollbar ────────────────────────────── */
    QScrollBar:vertical {
        border: none; background: #0F0023; width: 6px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #3D1B7B; min-height: 20px; border-radius: 3px;
    }
    QScrollBar::handle:vertical:hover { background: #600DB5; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

    /* ── Dialogs ─────────────────────────────── */
    QMessageBox, QDialog { background-color: #1F004B; color: #e0e0ff; }
"""


class AIHubMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Hub — Panel de Control")
        self.resize(920, 680)
        self.setStyleSheet(_STYLESHEET)

        self.workers = HubWorkers(shared_state=state)
        # Signal for immediate updates (best-effort from worker thread)
        self.workers.signals.app_state_changed.connect(self.refresh_apps_list)
        self.workers.signals.error_message.connect(self.show_error)

        # Polling timer — runs in the main thread every 1s, guarantees UI
        # reflects the real state regardless of cross-thread signal delivery.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self.refresh_apps_list)
        self._poll_timer.start()

        self.build_ui()

    # ------------------------------------------------------------------ #
    # UI Construction                                                      #
    # ------------------------------------------------------------------ #

    def build_ui(self):
        container = QWidget()
        container.setObjectName("main_bg")
        self.setCentralWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Compact top bar ───────────────────────────────────────────────
        topbar = QWidget()
        topbar.setStyleSheet("background-color: #1A0040; border-bottom: 1px solid #3D1B7B;")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(20, 10, 20, 10)
        topbar_layout.setSpacing(16)

        # App name — compact
        name_lbl = QLabel("🤖 <b>AI Hub</b>")
        name_lbl.setStyleSheet("font-size: 18px; color: #54EFEA;")
        topbar_layout.addWidget(name_lbl)

        # System info inline (compact)
        info = state.sys_info
        gpu_text = info.get("gpu_name", "GPU desconocida")
        cuda_text = info.get("cuda_tag", "")
        disk_text = info.get("disk_free", "")

        sep = QLabel("|")
        sep.setStyleSheet("color: #3D1B7B;")

        info_lbl = QLabel(
            f"<span style='color:#888aaa'>GPU:</span> <span style='color:#54EFEA'>{gpu_text}</span>"
            f"  <span style='color:#3D1B7B'>|</span>"
            f"  <span style='color:#888aaa'>CUDA:</span> <span style='color:#54EFEA'>{cuda_text}</span>"
            f"  <span style='color:#3D1B7B'>|</span>"
            f"  <span style='color:#888aaa'>Libre:</span> <span style='color:#51CCDC'>{disk_text}</span>"
        )
        info_lbl.setStyleSheet("font-size: 12px;")

        topbar_layout.addWidget(sep)
        topbar_layout.addWidget(info_lbl)
        topbar_layout.addStretch()

        # Model Vault Button
        vault_btn = QPushButton("📦 Gestionar Modelos")
        vault_btn.setObjectName("outline_btn")
        vault_btn.setToolTip("Abrir Model Vault")
        vault_btn.clicked.connect(self._launch_model_vault)
        topbar_layout.addWidget(vault_btn)

        root.addWidget(topbar)

        # ── Tab widget fills the rest ─────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs)

        self.tabs.addTab(self._build_apps_tab(), "📦  Aplicaciones")
        
        # Integrate Model Vault
        try:
            # project_root is the parent of HUB_DIR
            project_root = os.path.dirname(HUB_DIR)
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from apps.model_vault.main import ModelVaultWidget
            self.vault_tab = ModelVaultWidget()
            self.tabs.addTab(self.vault_tab, "🔍  Model Vault")
        except Exception as e:
            # We already have a button as fallback, so only print the error
            print(f"Nota: Model Vault no pudo integrarse como tab (usando modo ventana): {e}")
            self.vault_tab = None

        self.tabs.addTab(HubSettings(),          "⚙️  Hub")
        self.tabs.addTab(EventLogViewer(),        "📋  Log")

    def _build_apps_tab(self) -> QWidget:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("background: #0F0023;")

        scroll_content = QWidget()
        scroll_content.setObjectName("main_bg")
        self.scroll_layout = QVBoxLayout(scroll_content)
        self.scroll_layout.setContentsMargins(20, 16, 20, 16)
        self.scroll_layout.setSpacing(10)

        self.apps_container = QWidget()
        self.apps_layout = QVBoxLayout(self.apps_container)
        self.apps_layout.setContentsMargins(0, 0, 0, 0)
        self.apps_layout.setSpacing(10)
        self.scroll_layout.addWidget(self.apps_container)
        self.scroll_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        self.refresh_apps_list()
        return scroll_area

    # ------------------------------------------------------------------ #
    # Apps list refresh                                                    #
    # ------------------------------------------------------------------ #

    def refresh_apps_list(self, emitting_app_id=None):
        while self.apps_layout.count():
            child = self.apps_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        any_running = bool(state.running_apps) or any(
            s == "stopping" for s in state.busy_apps.values()
        )

        # Combine regular apps and utilities
        all_apps = {**state.registry_apps, **state.registry_utilities}
        for app_id, app_cfg in all_apps.items():
            # Skip Model Vault as it now has a primary dedicated button/tab
            if app_id == "model-vault":
                continue
                
            app_status = state.get_app_status(app_id)
            card = AppCard(app_id, app_cfg, app_status, self.on_app_action,
                           any_running=any_running)
            self.apps_layout.addWidget(card)

    # ------------------------------------------------------------------ #
    # Action dispatcher                                                    #
    # ------------------------------------------------------------------ #

    def on_app_action(self, action: str, app_id: str):
        if action == "launch":
            if app_id in state.registry_utilities:
                # Special handling for utilities (standalone launch)
                self._launch_model_vault() if app_id == "model-vault" else None
            else:
                self.workers.start_app(app_id)
        elif action == "stop":
            self.workers.stop_app(app_id)
        elif action == "install":
            self.workers.install_app(app_id)
        elif action == "settings":
            dlg = AppSettingsDialog(app_id, parent=self)
            dlg.exec()
        elif action == "update":
            app_name = state.registry_apps.get(app_id, {}).get("name", app_id)
            reply = QMessageBox.question(
                self, f"Actualizar {app_name}",
                f"¿Buscar y aplicar actualizaciones para <b>{app_name}</b>?<br><br>"
                f"La app será detenida si está corriendo.<br>"
                f"Los archivos locales no serán eliminados.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.workers.update_app(app_id)
        elif action == "uninstall":
            app_name = state.registry_apps.get(app_id, {}).get("name", app_id)
            app_dir  = state.installed_apps.get(app_id, {}).get("dir", "")
            reply = QMessageBox.warning(
                self, f"Desinstalar {app_name}",
                f"¿Desinstalar <b>{app_name}</b>?<br><br>"
                f"<b>Se eliminará permanentemente:</b><br>{app_dir}<br><br>"
                f"Los modelos en tu carpeta configurada NO serán afectados.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.workers.uninstall_app(app_id)

    def show_error(self, app_id: str, message: str):
        QMessageBox.critical(self, f"Error en {app_id}", message)

    def _launch_model_vault(self):
        """Switches to the integrated Model Vault tab."""
        if hasattr(self, "tabs"):
            for i in range(self.tabs.count()):
                if "Model Vault" in self.tabs.tabText(i):
                    self.tabs.setCurrentIndex(i)
                    return
        
        # Fallback to standalone if tab not found for some reason
        import subprocess
        script_path = os.path.join(HUB_DIR, "..", "apps", "model_vault", "run_vault.sh")
        if sys.platform == "win32":
            script_path = os.path.join(HUB_DIR, "..", "apps", "model_vault", "run_vault.bat")
        
        try:
            subprocess.Popen([script_path], start_new_session=True)
        except Exception as e:
            self.show_error("Model Vault", f"No se pudo iniciar: {e}")


def main():
    app = QApplication(sys.argv)
    window = AIHubMainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
