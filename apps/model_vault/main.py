import sys
import os
import threading
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QScrollArea, QGridLayout, QLineEdit, QPushButton, 
    QFrame, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
# Adjust path to find core and ui modules regardless of how it's launched
VAULT_DIR = os.path.dirname(os.path.abspath(__file__))
if VAULT_DIR not in sys.path:
    sys.path.insert(0, VAULT_DIR)

from core.vault_service import VaultService
from ui.components import ModelCard, ModelDetailsDialog

class UIWorkerSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(int)
    error = Signal(str)
    api_checked = Signal(bool)

class ModelVaultWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Paths
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.root_dir = os.path.dirname(os.path.dirname(self.script_dir))
        self.db_path = os.path.join(self.root_dir, "hub", ".cache", "model_vault.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Service
        # Try to get API key from Hub State if available
        
        # Try to get API key from Hub State if available
        api_key = None
        try:
            from gui.state import state
            api_key = state.get_civitai_key()
        except ImportError:
            # Fallback if running standalone outside the hub structure
            pass
            
        self.vault = VaultService(self.db_path, api_key=api_key)
        self.ModelCard = ModelCard
        self.ModelDetailsDialog = ModelDetailsDialog
        
        self.signals = UIWorkerSignals()
        self.signals.progress.connect(self._on_progress)
        self.signals.finished.connect(self._on_finished)
        self.signals.error.connect(self._on_error)
        self.signals.api_checked.connect(self._on_api_checked)

        self._apply_theme()
        self._build_ui()
        
        # Manual load from cache (don't auto-sync/index at start)
        self._load_models()

        # API Check
        QTimer.singleShot(2000, self._check_api_status)

    def _check_api_status(self):
        def _target():
            is_ok = self.vault.client.verify_api_key()
            self.signals.api_checked.emit(is_ok)
            
        threading.Thread(target=_target, daemon=True).start()

    def _on_api_checked(self, is_ok):
        if is_ok:
            self.api_status_dot.setStyleSheet("color: #00FF00; font-size: 18px; margin-left: 10px;")
            self.api_status_dot.setToolTip("Civitai API: Conectado (API Key Válida)")
        else:
            self.api_status_dot.setStyleSheet("color: #FF0000; font-size: 18px; margin-left: 10px;")
            self.api_status_dot.setToolTip("Civitai API: Error de conexión o Key inválida")

    def _apply_theme(self):
        self.setStyleSheet("""
            QWidget#vault_root { background-color: #0F0023; color: #e0e0ff; }
            QLabel { color: #e0e0ff; }
            QLineEdit {
                background: #1A0040; color: #54EFEA;
                border: 1px solid #3D1B7B; border-radius: 4px; padding: 5px 10px;
            }
            QPushButton#primary_btn { 
                background-color: #600DB5; color: white; font-weight: bold; 
                border-radius: 4px; padding: 8px 16px;
            }
            QPushButton#primary_btn:hover { background-color: #7B1FD4; }
            QProgressBar {
                background-color: #1A0040; border: 1px solid #3D1B7B; 
                border-radius: 4px; text-align: center; color: white;
            }
            QProgressBar::chunk { background-color: #600DB5; }
            QScrollArea { border: none; background: transparent; }
        """)

    def _build_ui(self):
        self.setObjectName("vault_root")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top Bar
        top_bar = QWidget()
        top_bar.setFixedHeight(70)
        top_bar.setStyleSheet("background-color: #1A0040; border-bottom: 1px solid #3D1B7B;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)
        
        title_layout = QHBoxLayout()
        title = QLabel("📦 Model Vault")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #54EFEA;")
        title_layout.addWidget(title)
        
        self.api_status_dot = QLabel("●")
        self.api_status_dot.setToolTip("Civitai API Status: Verificando...")
        self.api_status_dot.setStyleSheet("color: #666; font-size: 18px; margin-left: 10px;")
        title_layout.addWidget(self.api_status_dot)
        
        top_layout.addLayout(title_layout)
        top_layout.addStretch()
        
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Buscar por nombre, creador, tags o arquitectura...")
        self.search_box.setFixedWidth(350)
        self.search_box.textChanged.connect(self._filter_models)
        top_layout.addWidget(self.search_box)

        # Sort selector
        self.sort_box = QLineEdit() # Temporary placeholder for a better widget if needed, or just use combo
        from PySide6.QtWidgets import QComboBox
        self.sort_selector = QComboBox()
        self.sort_selector.addItems(["Más Reciente", "Nombre (A-Z)", "Arquitectura"])
        self.sort_selector.setStyleSheet("""
            QComboBox {
                background: #1A0040; color: #51CCDC;
                border: 1px solid #3D1B7B; border-radius: 4px; padding: 5px;
            }
        """)
        self.sort_selector.currentIndexChanged.connect(self._filter_models)
        top_layout.addWidget(self.sort_selector)
        
        refresh_btn = QPushButton("🔄 Actualizar / Sync")
        refresh_btn.setToolTip("Escanear archivos e indexar metadatos")
        refresh_btn.setObjectName("primary_btn")
        refresh_btn.clicked.connect(lambda: self.run_scan(deep=True))
        top_layout.addWidget(refresh_btn)
        
        root.addWidget(top_bar)

        # Main Layout
        main_layout = QHBoxLayout()
        main_layout.setSpacing(0)
        
        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(200)
        self.sidebar.setStyleSheet("background-color: #120030; border-right: 1px solid #3D1B7B;")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 20, 10, 20)
        sidebar_layout.setSpacing(5)
        
        sidebar_layout.addWidget(QLabel("CATEGORÍAS"))
        
        self.cat_buttons = {}
        categories = ["Todos", "Checkpoint", "LORA", "LoCon", "TextualInversion", "VAE", "Upscaler"]
        for cat in categories:
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            if cat == "Todos": btn.setChecked(True)
            btn.setStyleSheet("""
                QPushButton { 
                    text-align: left; padding: 10px; border: none; border-radius: 4px; color: #888aaa;
                }
                QPushButton:checked { 
                    background-color: #3D1B7B; color: #54EFEA; font-weight: bold;
                }
                QPushButton:hover:!checked { background-color: #1A0040; }
            """)
            btn.clicked.connect(self._filter_models)
            sidebar_layout.addWidget(btn)
            self.cat_buttons[cat] = btn
            
        sidebar_layout.addStretch()
        main_layout.addWidget(self.sidebar)

        # Grid Content
        right_content = QWidget()
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(20, 20, 20, 20)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(25)
        # Centered alignment for a premium gallery look
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll.setWidget(self.grid_widget)
        right_layout.addWidget(self.scroll)
        
        main_layout.addWidget(right_content)
        root.addLayout(main_layout)

    def resizeEvent(self, event):
        """Handle window resizing to adjust grid columns."""
        super().resizeEvent(event)
        # 200px card + 25px spacing = ~225px
        available_width = self.scroll.width() - 40 
        new_cols = max(2, available_width // 225)
        
        if hasattr(self, 'columns') and self.columns != new_cols:
            self.columns = new_cols
            self._rearrange_grid()

    def _rearrange_grid(self):
        """Redistribute existing widgets in the grid based on new column count."""
        if not hasattr(self, 'columns'): return
        
        widgets = []
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                widgets.append(item.widget())
        
        # Clear any manual stretches
        for c in range(50):
             self.grid.setColumnStretch(c, 0)

        for i, w in enumerate(widgets):
            self.grid.addWidget(w, i // self.columns, i % self.columns)

    def _on_progress(self, current, total, name):
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"Indexando: {name} ({current}/{total})")

    def _on_finished(self, count):
        self.progress_bar.setVisible(False)
        self._load_models()
        print(f"Indexación finalizada: {count} modelos.")

    def _on_error(self, msg):
        QMessageBox.critical(self, "Error", msg)

    def run_scan(self, deep=False):
        def _target():
            try:
                scan_dirs = [
                    "/run/media/system/Kilaya/Models/Lora/",
                    "/run/media/system/Kilaya/Models/StableDiffusion/",
                    "/run/media/system/Kilaya/Models/checkpoints/"
                ]
                
                from core.scanner import scan_models
                all_discovered = []
                for d in scan_dirs:
                    if os.path.exists(d):
                        all_discovered.extend(scan_models(d))
                
                def progress_cb(c, t, n):
                    self.signals.progress.emit(c, t, n)
                
                for i, m in enumerate(all_discovered):
                    progress_cb(i+1, len(all_discovered), m["name"])
                    if deep or not m.get("has_metadata", False):
                        if not m.get("hash"):
                            from core.hasher import calculate_sha256
                            m["hash"] = calculate_sha256(m["path"])
                        if m["hash"]:
                            self.vault.sync_model(m["path"], m["hash"])
                
                self.signals.finished.emit(len(all_discovered))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.signals.error.emit(str(e))

        threading.Thread(target=_target, daemon=True).start()

    def _load_models(self, filter_cat="Todos", search_query=""):
        if hasattr(self, 'batch_timer') and self.batch_timer.isActive():
            self.batch_timer.stop()
            
        self.scroll.setUpdatesEnabled(False)
        
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        all_models = self.vault.db.get_all_models()
        
        filtered = []
        for m in all_models:
            m_type = (m.get("model_type") or "").lower()
            f_cat = filter_cat.lower()
            
            if f_cat != "todos" and f_cat != m_type:
                if f_cat == "textualinversion" and m_type == "embedding":
                    pass
                else:
                    continue
            
            q = search_query.lower()
            if q:
                name_match = q in (m.get("display_name") or "").lower() or q in m.get("name", "").lower()
                arch_match = q in (m.get("base_model") or "").lower()
                creator_match = q in (m.get("creator_name") or "").lower()
                triggers_match = q in (m.get("triggers") or "").lower()
                tags_match = q in (m.get("custom_tags") or "").lower()
                
                if not (name_match or arch_match or creator_match or triggers_match or tags_match):
                    continue
            
            filtered.append(m)

        # Apply sorting
        sort_idx = self.sort_selector.currentIndex()
        if sort_idx == 0: # Más Reciente
            filtered.sort(key=lambda x: x.get("date_added") or "", reverse=True)
        elif sort_idx == 1: # Nombre
            filtered.sort(key=lambda x: (x.get("display_name") or x.get("name", "")).lower())
        elif sort_idx == 2: # Arquitectura
            filtered.sort(key=lambda x: (x.get("base_model") or "").lower())

        self.load_queue = filtered
        self.load_index = 0
        
        # Initial column calculation
        available_width = self.scroll.width() - 40
        self.columns = max(2, available_width // 225)
        
        if not hasattr(self, 'batch_timer'):
            self.batch_timer = QTimer()
            self.batch_timer.timeout.connect(self._add_cards_batch)
        
        self.scroll.setUpdatesEnabled(True)
        self.batch_timer.start(10)

    def _add_cards_batch(self):
        batch_size = 12
        for _ in range(batch_size):
            if self.load_index >= len(self.load_queue):
                self.batch_timer.stop()
                return
            
            m = self.load_queue[self.load_index]
            base_name = os.path.splitext(m["file_path"])[0]
            m["preview_path"] = base_name + ".preview.jpeg"
            
            card = self.ModelCard(m)
            card.doubleClicked.connect(self._show_details)
            card.tag_clicked.connect(self._open_tag_browser)
            self.grid.addWidget(card, self.load_index // self.columns, self.load_index % self.columns)
            self.load_index += 1

    def _show_details(self, model_data):
        dialog = self.ModelDetailsDialog(model_data, self)
        dialog.notes_saved.connect(self._save_notes)
        dialog.tags_saved.connect(self._save_tags)
        dialog.creator_clicked.connect(self._open_creator_browser)
        dialog.tag_clicked.connect(self._open_tag_browser)
        dialog.show()

    def _open_creator_browser(self, username):
        from ui.civitai_browser import CivitaiExplorerDialog
        browser = CivitaiExplorerDialog(self.vault.client, self.vault, self, initial_username=username)
        browser.exec()

    def _open_tag_browser(self, tag):
        from ui.civitai_browser import CivitaiExplorerDialog
        browser = CivitaiExplorerDialog(self.vault.client, self.vault, self, initial_tag=tag)
        browser.exec()

    def _save_tags(self, model_hash, tags):
        self.vault.db.update_custom_tags(model_hash, tags)
        # Re-filter to update in-memory data and UI if necessary
        # (Though tags are mostly for search, so no immediate refresh needed unless searching by tag)

    def _save_notes(self, model_hash, notes):
        self.vault.db.update_user_notes(model_hash, notes)

    def _filter_models(self):
        active_cat = "Todos"
        for cat, btn in self.cat_buttons.items():
            if btn.isChecked():
                active_cat = cat
                break
        
        search_text = self.search_box.text()
        self._load_models(filter_cat=active_cat, search_query=search_text)


class ModelVaultMainWindow(QMainWindow):
    """Standalone window wrapper for the widget."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Hub — Model Vault")
        # Adjusting default size to comfortably fit 3 columns + sidebar
        self.resize(1200, 850)
        self.widget = ModelVaultWidget(self)
        self.setCentralWidget(self.widget)

def main():
    app = QApplication(sys.argv)
    window = ModelVaultMainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
