import sys
import os
import threading
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QScrollArea, QGridLayout, QLineEdit, QPushButton, 
    QFrame, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from core.vault_service import VaultService
from ui.components import ModelCard, ModelDetailsDialog

class UIWorkerSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(int)
    error = Signal(str)

class ModelVaultMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Hub — Model Vault")
        self.resize(1100, 800)
        
        # Paths
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.root_dir = os.path.dirname(os.path.dirname(self.script_dir))
        self.db_path = os.path.join(self.root_dir, "hub", ".cache", "model_vault.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Service
        self.vault = VaultService(self.db_path)
        self.signals = UIWorkerSignals()
        self.signals.progress.connect(self._on_progress)
        self.signals.finished.connect(self._on_finished)
        self.signals.error.connect(self._on_error)

        self._apply_theme()
        self._build_ui()
        
        # Initial scan (shallow)
        self.run_scan(deep=False)

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget#main_bg { background-color: #0F0023; color: #e0e0ff; }
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
        container = QWidget()
        container.setObjectName("main_bg")
        self.setCentralWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0) # Removed margins for full-height sidebar
        root.setSpacing(0)

        # Top Bar
        top_bar = QWidget()
        top_bar.setFixedHeight(70)
        top_bar.setStyleSheet("background-color: #1A0040; border-bottom: 1px solid #3D1B7B;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)
        
        title = QLabel("📦 Model Vault")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #54EFEA;")
        top_layout.addWidget(title)
        top_layout.addStretch()
        
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Buscar modelos o LoRAs...")
        self.search_box.setFixedWidth(300)
        self.search_box.textChanged.connect(self._filter_models)
        top_layout.addWidget(self.search_box)
        
        refresh_btn = QPushButton("🔄 Sincronizar")
        refresh_btn.setObjectName("primary_btn")
        refresh_btn.clicked.connect(lambda: self.run_scan(deep=True))
        top_layout.addWidget(refresh_btn)
        
        root.addWidget(top_bar)

        # Main Layout (Sidebar + Grid)
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

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        # Grid of models
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(25) # Increased spacing
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.grid_widget)
        right_layout.addWidget(self.scroll)
        
        main_layout.addWidget(right_content)
        root.addLayout(main_layout)

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
                # ComfyUI and standard paths
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
        # Stop any active batch loading
        if hasattr(self, 'batch_timer') and self.batch_timer.isActive():
            self.batch_timer.stop()
            
        # Performance optimization
        self.scroll.setUpdatesEnabled(False)
        
        # Clear grid safely
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        all_models = self.vault.db.get_all_models()
        
        # Filtering logic
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
                name_match = q in m.get("name", "").lower()
                arch_match = q in (m.get("base_model") or "").lower()
                type_match = q in (m.get("model_type") or "").lower()
                if not (name_match or arch_match or type_match):
                    continue
            
            filtered.append(m)

        # Batch loading setup
        self.load_queue = filtered
        self.load_index = 0
        self.columns = 3
        
        if not hasattr(self, 'batch_timer'):
            self.batch_timer = QTimer()
            self.batch_timer.timeout.connect(self._add_cards_batch)
        
        self.scroll.setUpdatesEnabled(True)
        self.batch_timer.start(10) # process batch every 10ms

    def _add_cards_batch(self):
        batch_size = 12 # Process 12 models at a time
        for _ in range(batch_size):
            if self.load_index >= len(self.load_queue):
                self.batch_timer.stop()
                return
            
            m = self.load_queue[self.load_index]
            base_name = os.path.splitext(m["file_path"])[0]
            m["preview_path"] = base_name + ".preview.jpeg"
            
            card = ModelCard(m)
            card.doubleClicked.connect(self._show_details)
            self.grid.addWidget(card, self.load_index // self.columns, self.load_index % self.columns)
            self.load_index += 1

    def _show_details(self, model_data):
        dialog = ModelDetailsDialog(model_data, self)
        dialog.notes_saved.connect(self._save_notes)
        dialog.show()

    def _save_notes(self, model_hash, notes):
        self.vault.db.update_user_notes(model_hash, notes)
        # We don't need to reload everything, just update the local cache if we want
        # But since details dialog is already updated, we are good.

    def _filter_models(self):
        # Determine active category
        active_cat = "Todos"
        for cat, btn in self.cat_buttons.items():
            if btn.isChecked():
                active_cat = cat
                break
        
        search_text = self.search_box.text()
        self._load_models(filter_cat=active_cat, search_query=search_text)

def main():
    app = QApplication(sys.argv)
    window = ModelVaultMainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
