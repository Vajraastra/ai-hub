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
                               QLabel, QScrollArea, QFrame, QMessageBox, QTabWidget)
from PySide6.QtCore import Qt

from gui.state import state
from gui.components.system_card import SystemCard
from gui.components.app_card import AppCard
from gui.components.hub_settings import HubSettings
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
        border: 1px solid #3D1B7B;
        background-color: #0F0023;
    }
    QTabBar::tab {
        background: #1F004B;
        color: #aaaacc;
        padding: 8px 22px;
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
        background-color: #2A0A5E;
        border-radius: 8px;
        border: 1px solid #3D1B7B;
    }

    /* ── Labels ──────────────────────────────── */
    QLabel { color: #e0e0ff; }

    /* ── Buttons — base ──────────────────────── */
    QPushButton {
        padding: 6px 14px;
        border-radius: 4px;
        font-weight: bold;
        border: none;
        font-size: 13px;
    }
    QPushButton:disabled {
        background-color: #3D1B7B;
        color: #555588;
        border: 1px solid #2A0A5E;
    }

    /* Launch / Install — Grape accent */
    QPushButton#success_btn  { background-color: #600DB5; color: #54EFEA; }
    QPushButton#success_btn:hover  { background-color: #7B1FD4; }
    QPushButton#install_btn  { background-color: #600DB5; color: #54EFEA; }
    QPushButton#install_btn:hover  { background-color: #7B1FD4; }

    /* Update / outline — Robin egg blue */
    QPushButton#outline_btn {
        background-color: transparent;
        border: 1px solid #51CCDC;
        color: #51CCDC;
    }
    QPushButton#outline_btn:hover { background-color: rgba(81,204,220,0.12); }

    /* Stop — Phlox solid */
    QPushButton#stop_btn { background-color: #EC00F0; color: #0F0023; }
    QPushButton#stop_btn:hover { background-color: #FF33FF; }

    /* Uninstall — Phlox outline */
    QPushButton#danger_outline_btn {
        background-color: transparent;
        border: 1px solid #EC00F0;
        color: #EC00F0;
    }
    QPushButton#danger_outline_btn:hover { background-color: rgba(236,0,240,0.10); }

    /* ── Scrollbar ────────────────────────────── */
    QScrollBar:vertical {
        border: none; background: #0F0023; width: 8px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #600DB5; min-height: 20px; border-radius: 4px;
    }
    QScrollBar::handle:vertical:hover { background: #7B1FD4; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

    /* ── Message / Dialog boxes ───────────────── */
    QMessageBox, QDialog { background-color: #1F004B; color: #e0e0ff; }
"""


class AIHubMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Hub — Panel de Control")
        self.resize(960, 720)
        self.setStyleSheet(_STYLESHEET)

        # Background workers
        self.workers = HubWorkers()
        self.workers.signals.app_state_changed.connect(self.refresh_apps_list)
        self.workers.signals.error_message.connect(self.show_error)

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

        # ── Header ──────────────────────────────────────────────────────
        header_widget = QWidget()
        header_widget.setStyleSheet("background-color: #1F004B; padding: 14px 30px 10px;")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(30, 14, 30, 10)

        header_lbl = QLabel("🤖 AI Hub")
        header_lbl.setStyleSheet("font-size: 28px; font-weight: bold; color: #54EFEA;")
        header_layout.addWidget(header_lbl)

        self.sys_card = SystemCard(state.sys_info)
        header_layout.addWidget(self.sys_card)

        root.addWidget(header_widget)

        # ── Tab widget ───────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs)

        # Tab 1: Applications
        self.tabs.addTab(self._build_apps_tab(), "📦  Aplicaciones")

        # Tab 2: Hub Settings
        self.tabs.addTab(HubSettings(), "⚙️  Hub")

    def _build_apps_tab(self) -> QWidget:
        """Builds the scrollable app cards list."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setStyleSheet("background: #0F0023;")

        scroll_content = QWidget()
        scroll_content.setObjectName("main_bg")
        self.scroll_layout = QVBoxLayout(scroll_content)
        self.scroll_layout.setContentsMargins(30, 24, 30, 24)
        self.scroll_layout.setSpacing(12)

        # Apps container
        self.apps_container = QWidget()
        self.apps_layout = QVBoxLayout(self.apps_container)
        self.apps_layout.setContentsMargins(0, 0, 0, 0)
        self.apps_layout.setSpacing(12)
        self.scroll_layout.addWidget(self.apps_container)
        self.scroll_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        self.refresh_apps_list()
        return scroll_area

    # ------------------------------------------------------------------ #
    # Apps list refresh                                                    #
    # ------------------------------------------------------------------ #

    def refresh_apps_list(self, emitting_app_id=None):
        # Clear existing cards
        while self.apps_layout.count():
            child = self.apps_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        any_running = bool(state.running_apps) or any(
            s == "stopping" for s in state.busy_apps.values()
        )

        for app_id, app_cfg in state.registry_apps.items():
            app_status = state.get_app_status(app_id)
            card = AppCard(app_id, app_cfg, app_status, self.on_app_action,
                           any_running=any_running)
            self.apps_layout.addWidget(card)

    # ------------------------------------------------------------------ #
    # Action dispatcher                                                    #
    # ------------------------------------------------------------------ #

    def on_app_action(self, action: str, app_id: str):
        if action == "launch":
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
                self,
                f"Actualizar {app_name}",
                f"¿Buscar y aplicar actualizaciones para <b>{app_name}</b>?<br><br>"
                f"La app será detenida si está corriendo.<br>"
                f"Los archivos locales no serán eliminados.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.workers.update_app(app_id)
        elif action == "uninstall":
            app_name = state.registry_apps.get(app_id, {}).get("name", app_id)
            app_dir  = state.installed_apps.get(app_id, {}).get("dir", "")
            reply = QMessageBox.warning(
                self,
                f"Desinstalar {app_name}",
                f"¿Desinstalar <b>{app_name}</b>?<br><br>"
                f"<b>Se eliminará permanentemente:</b><br>{app_dir}<br><br>"
                f"Los modelos y outputs en tu carpeta de modelos NO serán afectados.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.workers.uninstall_app(app_id)

    def show_error(self, app_id: str, message: str):
        QMessageBox.critical(self, f"Error en {app_id}", message)


def main():
    app = QApplication(sys.argv)
    window = AIHubMainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
