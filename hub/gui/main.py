#!/usr/bin/env python3
"""
AI Hub — PySide6 GUI Main Entry Point
"""
import os
import sys
import traceback
import logging

# Ensure hub/ dir is in path to import gui modules
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(GUI_DIR)
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLabel, QScrollArea, QFrame,
                               QMessageBox, QTabWidget, QPushButton, QSplitter)
from PySide6.QtCore import Qt, QTimer

from gui.state import state
from gui.theme import STYLESHEET
from gui.components.app_card import AppCard
from gui.components.hub_settings import HubSettings
from gui.components.event_log_viewer import EventLogViewer
from gui.components.app_settings_dialog import AppSettingsDialog
from gui.components.app_terminal import AppTerminal
from gui.workers import HubWorkers
from gui.utils.confirm_dialog import confirm_update, confirm_uninstall



class AIHubMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Hub — Panel de Control")
        self.resize(920, 680)
        self.setStyleSheet(STYLESHEET)

        self.workers = HubWorkers(shared_state=state)
        # All state updates come through signals — no polling timer needed.
        self.workers.signals.app_state_changed.connect(self.refresh_apps_list)
        self.workers.signals.app_state_changed.connect(self._on_app_state_changed)
        self.workers.signals.error_message.connect(self.show_error)
        self.workers.signals.cleanup_result.connect(self._show_cleanup_result)
        # log_message is connected to terminal in _build_apps_tab (after terminal is created)

        # Dict de cards persistentes: app_id → AppCard
        self._app_cards: dict = {}

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

        # Cleanup button — mata procesos stale en puertos conocidos
        cleanup_btn = QPushButton("🧹 Limpiar")
        cleanup_btn.setObjectName("outline_btn")
        cleanup_btn.setToolTip(
            "Terminar procesos de IA que ocupen puertos conocidos\n"
            "pero no estén gestionados por el hub"
        )
        cleanup_btn.clicked.connect(self.workers.cleanup_stale_processes)
        topbar_layout.addWidget(cleanup_btn)

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

        # Integrate LoRA Merger
        try:
            from apps.lora_merger.main import LoraMergerWidget
            self.merger_tab = LoraMergerWidget()
            self.tabs.addTab(self.merger_tab, "🔀  LoRA Merger")
        except Exception as e:
            print(f"Nota: LoRA Merger no pudo integrarse como tab: {e}")
            self.merger_tab = None

        self.tabs.addTab(HubSettings(),          "⚙️  Hub")
        self.tabs.addTab(EventLogViewer(),        "📋  Log")

    def _build_apps_tab(self) -> QWidget:
        # ── Scroll area con tarjetas de apps ──────────────────────────
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

        # ── Panel de terminal ─────────────────────────────────────────
        self.terminal = AppTerminal()
        self.workers.signals.log_message.connect(self.terminal.append_line)

        # ── Splitter vertical: cards arriba, terminal abajo ───────────
        splitter = QSplitter(Qt.Vertical)
        splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                background: #2A0A5E;
                height: 3px;
            }
        """)
        splitter.addWidget(scroll_area)
        splitter.addWidget(self.terminal)
        splitter.setSizes([300, 340])   # proporciones iniciales: más espacio al terminal
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, True)

        return splitter

    # ------------------------------------------------------------------ #
    # Apps list refresh                                                    #
    # ------------------------------------------------------------------ #

    def refresh_apps_list(self, emitting_app_id=None):
        # Una app está "ocupando recursos" si corre o se está deteniendo
        any_running = bool(state.running_apps) or any(
            s == "stopping" for s in state.busy_apps.values()
        )

        # Limpiar entradas stale de running_apps (proceso ya terminó)
        state.cleanup_stale()

        all_apps = {**state.registry_apps, **state.registry_utilities}

        for app_id, app_cfg in all_apps.items():
            # Skip utilities (model-vault, etc.) from the apps list
            if app_cfg.get("is_utility") or app_cfg.get("is_internal"):
                continue

            app_status   = state.get_app_status(app_id)
            running_proc = state.running_apps.get(app_id)

            if app_id in self._app_cards:
                self._app_cards[app_id].update_status(app_status, any_running, running_proc)
            else:
                card = AppCard(app_id, app_cfg, app_status, self.on_app_action,
                               any_running=any_running, running_proc=running_proc)
                self._app_cards[app_id] = card
                self.apps_layout.addWidget(card)

    # ------------------------------------------------------------------ #
    # Action dispatcher                                                    #
    # ------------------------------------------------------------------ #

    def on_app_action(self, action: str, app_id: str):
        # Guard global: ignorar si ya hay una operación en curso para esta app
        if action not in ("stop", "settings") and app_id in state.busy_apps:
            return

        if action == "launch":
            if app_id in state.registry_utilities:
                self._launch_model_vault() if app_id == "model-vault" else None
            else:
                # Feedback visual inmediato antes de que el thread arranque
                state.set_busy(app_id, "launching")
                self.refresh_apps_list()
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
            if confirm_update(self, app_name):
                self.workers.update_app(app_id)
        elif action == "uninstall":
            app_name = state.registry_apps.get(app_id, {}).get("name", app_id)
            app_dir  = state.installed_apps.get(app_id, {}).get("dir", "")
            if confirm_uninstall(self, app_name, app_dir):
                self.workers.uninstall_app(app_id)

    def _on_app_state_changed(self, app_id: str):
        """Sincroniza el panel de terminal con el estado de la app."""
        status = state.get_app_status(app_id)
        if status == "running":
            app_name = state.registry_apps.get(app_id, {}).get("name", app_id)
            self.terminal.set_active_app(app_id, app_name)
        elif status in ("installed", "not_installed"):
            # App se detuvo
            self.terminal.on_app_stopped(app_id)

    def _show_cleanup_result(self, summary: str):
        QMessageBox.information(self, "Limpieza de procesos", summary)

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


def _setup_logging():
    """
    Configura logging estructurado hacia stderr (terminal) y captura
    cualquier excepción no manejada antes de que la GUI muera silenciosamente.
    run.sh redirige stderr→tee, así todo queda en el log de sesión.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    def _excepthook(exc_type, exc_value, exc_tb):
        """Captura crashes no atrapados: los imprime completos antes de morir."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.critical(
            "CRASH NO MANEJADO — traceback completo:",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        # También al stderr directo para que tee lo capture aunque logging falle
        traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)

    sys.excepthook = _excepthook


def main():
    _setup_logging()
    app = QApplication(sys.argv)
    window = AIHubMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
