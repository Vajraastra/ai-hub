import math
import os
import json
import sys
import requests
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QWidget, QScrollArea, QLabel, 
    QPushButton, QGridLayout, QHBoxLayout, QFileDialog, QProgressBar,
    QSizePolicy, QFrame, QLineEdit, QComboBox, QTextBrowser, QCheckBox,
    QLayout
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QSize
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtCore import QUrl, QPoint, QRect

# Import FlowLayout from components
from .components import FlowLayout

# --- PATH INJECTION ---
_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.abspath(os.path.join(_current_dir, "..", "..", ".."))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)
# ----------------------

from hub.gui.state import state

class FetchModelsWorker(QThread):
    finished = Signal(list, object) # items, next_cursor
    error = Signal(str)

    def __init__(self, client, username=None, tag=None, cursor=None):
        super().__init__()
        self.client = client
        self.username = username
        self.tag = tag
        self.cursor = cursor

    def run(self):
        try:
            models, next_cursor = self.client.get_models(
                username=self.username,
                tag=self.tag,
                cursor=self.cursor,
                limit=50
            )
            self.finished.emit(models, next_cursor)
        except Exception as e:
            self.error.emit(str(e))

class DownloadWorker(QThread):
    progress = Signal(int)
    status_changed = Signal(str)
    finished = Signal(str, dict, str) # Emits: final_path, version_data, hash
    error = Signal(str)

    def __init__(self, client, model_version_data, target_dir):
        super().__init__()
        self.client = client
        self.version_data = model_version_data
        self.target_dir = target_dir

    def run(self):
        try:
            files = self.version_data.get("files", [])
            primary_file = next((f for f in files if f.get("primary")), files[0] if files else None)

            if not primary_file:
                self.error.emit("No se encontró ningún archivo descargable.")
                return

            download_url = primary_file.get("downloadUrl")
            file_name_api = primary_file.get("name", "model.safetensors")
            file_hash = primary_file.get("hashes", {}).get("SHA256")
            
            save_path = os.path.join(self.target_dir, file_name_api)
            self.status_changed.emit(f"Descargando {file_name_api}...")
            
            headers = self.client.headers.copy()
            response = requests.get(download_url, headers=headers, stream=True)
            response.raise_for_status()
            
            total_size_in_bytes = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(save_path, 'wb') as file:
                for data in response.iter_content(1024 * 1024):
                    file.write(data)
                    downloaded += len(data)
                    if total_size_in_bytes:
                        self.progress.emit(int((downloaded / total_size_in_bytes) * 100))

            self.status_changed.emit("Registro automático...")
            self.finished.emit(save_path, self.version_data, file_hash)
            
        except requests.exceptions.HTTPError as he:
            if he.response.status_code == 401:
                self.error.emit("Acceso Restringido (Early Access o Pago).")
            else:
                self.error.emit(f"Error HTTP: {he.response.status_code}")
        except Exception as e:
            self.error.emit(f"Error: {str(e)}")

class OnlineModelCard(QFrame):
    details_requested = Signal(dict)
    _image_loaded = Signal(bytes)
    
    def __init__(self, model_data, client, parent=None):
        super().__init__(parent)
        self.data = model_data
        self.client = client
        self.setFixedSize(220, 320)
        self.setObjectName("modelCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            #modelCard { background-color: #1A0040; border-radius: 8px; border: 1px solid #3D1B7B; }
            #modelCard:hover { border: 2px solid #54EFEA; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.img_lbl = QLabel()
        self.img_lbl.setFixedSize(220, 220)
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setStyleSheet("background-color: #0D0020; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        layout.addWidget(self.img_lbl)

        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(10, 8, 10, 8)
        
        type_lbl = QLabel(self.data.get("type", "Unknown").upper())
        type_lbl.setStyleSheet("color: #54EFEA; font-size: 10px; font-weight: bold;")
        info_layout.addWidget(type_lbl)

        name_lbl = QLabel(self.data.get("name", "Unknown"))
        name_lbl.setStyleSheet("font-weight: bold; color: white; font-size: 13px;")
        name_lbl.setWordWrap(True)
        info_layout.addWidget(name_lbl)
        
        info_layout.addStretch()
        layout.addWidget(info)

        # Async image load
        self._image_loaded.connect(self._set_pixmap)
        
        versions = self.data.get("modelVersions", [])
        if versions and versions[0].get("images"):
            url = versions[0]["images"][0].get("url")
            def _fetch():
                try:
                    r = requests.get(url, timeout=5)
                    if r.status_code == 200: self._image_loaded.emit(r.content)
                except: pass
            import threading
            threading.Thread(target=_fetch, daemon=True).start()

    def _set_pixmap(self, data):
        pix = QPixmap()
        if not pix.loadFromData(data) or pix.isNull():
            return
        try:
            scaled = pix.scaled(self.img_lbl.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.img_lbl.setPixmap(scaled)
        except Exception as e:
            print(f"Error escalando imagen: {e}")

    def mousePressEvent(self, event):
        self.details_requested.emit(self.data)

class OnlineModelDetailsDialog(QDialog):
    download_requested = Signal(dict)
    tag_clicked = Signal(str)
    _main_img_loaded = Signal(QPixmap)
    _thumb_loaded = Signal(QLabel, QPixmap)

    def __init__(self, model_data, client, parent=None):
        super().__init__(parent)
        self.data = model_data
        self.client = client
        self.setWindowTitle(self.data.get("name", "Detalles del Modelo"))
        self.resize(1000, 800)
        self.setStyleSheet("background-color: #0D0020; color: white;")
        
        layout = QHBoxLayout(self)
        
        # Left: Images Gallery
        left_panel = QWidget()
        left_panel.setFixedWidth(600)
        left_layout = QVBoxLayout(left_panel)
        
        self.main_img = QLabel()
        self.main_img.setFixedSize(580, 580)
        self.main_img.setAlignment(Qt.AlignCenter)
        self.main_img.setStyleSheet("background-color: #0A0018; border: 1px solid #3D1B7B;")
        left_layout.addWidget(self.main_img)

        # Signals for thread-safe UI updates
        self._main_img_loaded.connect(self.main_img.setPixmap)
        self._thumb_loaded.connect(lambda lbl, pix: lbl.setPixmap(pix))
        
        thumb_scroll = QScrollArea()
        thumb_scroll.setFixedHeight(120)
        thumb_scroll.setWidgetResizable(True)
        thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        thumb_container = QWidget()
        self.thumb_layout = QHBoxLayout(thumb_container)
        self.thumb_layout.setSpacing(10)
        
        # Pull images from first version
        self.images = []
        versions = self.data.get("modelVersions", [])
        if versions:
            self.images = versions[0].get("images", [])
            for i, img_data in enumerate(self.images):
                thumb = QLabel()
                thumb.setFixedSize(100, 100)
                thumb.setCursor(Qt.PointingHandCursor)
                thumb.setStyleSheet("border: 1px solid #3D1B7B;")
                thumb.mousePressEvent = lambda e, url=img_data.get("url"): self._load_main(url)
                self.thumb_layout.addWidget(thumb)
                self._async_thumb(img_data.get("url"), thumb)
        
        thumb_scroll.setWidget(thumb_container)
        left_layout.addWidget(thumb_scroll)
        
        layout.addWidget(left_panel)
        
        # Right: Info & Download
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        title = QLabel(self.data.get("name", "Unknown"))
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #54EFEA;")
        title.setWordWrap(True)
        right_layout.addWidget(title)
        
        creator = QLabel(f"Por: {self.data.get('creator', {}).get('username', 'Unknown')}")
        creator.setStyleSheet("color: #888aaa; font-style: italic;")
        right_layout.addWidget(creator)
        
        # Triggers
        triggers = []
        if versions: triggers = versions[0].get("trainedWords", [])
        if triggers:
            t_lbl = QLabel("<b>Triggers:</b> " + ", ".join(triggers))
            t_lbl.setWordWrap(True)
            t_lbl.setStyleSheet("color: #FFD700; background: rgba(255, 215, 0, 0.1); padding: 5px; border-radius: 4px;")
            right_layout.addWidget(t_lbl)
            
        # Tags
        tags = self.data.get("tags") or []
        if not tags:
            # Try nested model data or other common formats
            tags = (self.data.get("model") or {}).get("tags") or []
            
        if tags:
            right_layout.addWidget(QLabel("<b>Etiqutas:</b>"))
            tags_widget = QWidget()
            tags_flow = FlowLayout(tags_widget)
            for t in tags:
                btn = QPushButton(t)
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(84, 239, 234, 0.1); color: #54EFEA; 
                        border: 1px solid #3D1B7B; border-radius: 4px; padding: 4px 8px; font-size: 11px;
                    }
                    QPushButton:hover { background: #54EFEA; color: #0D0020; }
                """)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda chk=False, txt=t: self.tag_clicked.emit(txt))
                tags_flow.addWidget(btn)
            right_layout.addWidget(tags_widget)
            
        desc = QTextBrowser()
        desc.setHtml(self.data.get("description") or "Sin descripción.")
        desc.setOpenExternalLinks(True)
        desc.setStyleSheet("background: transparent; border: none; font-size: 13px; color: #bbb;")
        right_layout.addWidget(desc)
        
        right_layout.addStretch()
        
        dl_btn = QPushButton("☁️ Descargar Modelo")
        dl_btn.setFixedHeight(50)
        dl_btn.setStyleSheet("background-color: #600DB5; font-size: 16px; font-weight: bold; border-radius: 6px;")
        dl_btn.clicked.connect(lambda: self.download_requested.emit(self.data))
        right_layout.addWidget(dl_btn)
        
        layout.addWidget(right_panel)
        
        if self.images: self._load_main(self.images[0].get("url"))

    def _load_main(self, url):
        def _target():
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    pix = QPixmap()
                    pix.loadFromData(r.content)
                    scaled = pix.scaled(self.main_img.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._main_img_loaded.emit(scaled)
            except: pass
        import threading
        threading.Thread(target=_target, daemon=True).start()

    def _async_thumb(self, url, label):
        def _target():
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    pix = QPixmap()
                    pix.loadFromData(r.content)
                    scaled = pix.scaled(label.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    self._thumb_loaded.emit(label, scaled)
            except: pass
        import threading
        threading.Thread(target=_target, daemon=True).start()

class CivitaiExplorerDialog(QDialog):
    def __init__(self, client, vault, parent=None, initial_username=None, initial_tag=None):
        super().__init__(parent)
        self.client = client
        self.vault = vault
        self.workers = [] # Keep references to prevent GC and "Destroyed while running"
        self.setWindowTitle("Civitai Explorer")
        self.resize(1100, 800)
        self.setStyleSheet("background-color: #0D0020; color: white;")
        
        layout = QVBoxLayout(self)
        
        # Grid Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.grid_container = QWidget()
        self.grid = QGridLayout(self.grid_container)
        self.grid.setSpacing(20)
        self.scroll.setWidget(self.grid_container)
        layout.addWidget(self.scroll)
        
        # Load More button
        self.load_more_btn = QPushButton("Cargar más resultados")
        self.load_more_btn.setFixedHeight(40)
        self.load_more_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #51CCDC;
                border: 1px solid #51CCDC; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: rgba(81,204,220,0.12); }
            QPushButton:disabled { color: #3D1B7B; border-color: #3D1B7B; }
        """)
        self.load_more_btn.clicked.connect(self._load_more)
        self.load_more_btn.hide()
        layout.addWidget(self.load_more_btn)

        # Progress Panel
        self.progress_panel = QWidget()
        self.progress_panel.setStyleSheet("background: #1A0040; border-radius: 8px; padding: 10px;")
        pp_layout = QVBoxLayout(self.progress_panel)
        self.pbar = QProgressBar()
        self.pbar.setFixedHeight(10)
        self.pbar.setStyleSheet("QProgressBar::chunk { background-color: #54EFEA; }")
        pp_layout.addWidget(self.pbar)
        self.progress_panel.hide()
        layout.addWidget(self.progress_panel)

        # Persistent Status Bar
        self.status_bar = QLabel("Listo.")
        self.status_bar.setStyleSheet("color: #54EFEA; padding: 5px; font-size: 11px;")
        layout.addWidget(self.status_bar)
        
        self.next_cursor = None
        self.initial_username = initial_username
        self.initial_tag = initial_tag
        if initial_username or initial_tag:
            self._do_search()

    def closeEvent(self, event):
        # Stop workers to prevent "QThread: Destroyed while thread is still running"
        for w in self.workers:
            if w.isRunning():
                w.terminate()
                w.wait(2000) # Wait at most 2 seconds per worker
        super().closeEvent(event)

    def _do_search(self):
        # Clear grid
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.next_cursor = None
        self.load_more_btn.hide()

        if self.initial_username:
            self.status_bar.setText(f"Explorando creador: {self.initial_username}...")
        elif self.initial_tag:
            self.status_bar.setText(f"Explorando etiqueta: {self.initial_tag}...")

        self.progress_panel.show()
        self.pbar.setRange(0, 0)

        worker = FetchModelsWorker(
            self.client,
            username=self.initial_username,
            tag=self.initial_tag
        )
        worker.finished.connect(self._show_results)
        worker.error.connect(self._on_error)
        self.workers.append(worker)
        worker.start()


    def _show_results(self, models, next_cursor):
        self.progress_panel.hide()
        self.load_more_btn.setEnabled(True)
        self.next_cursor = next_cursor

        if not models and not self.grid.count():
            self.status_bar.setText("No se encontraron resultados.")
            self.load_more_btn.hide()
            return

        current_count = self.grid.count()
        cols = 4
        for i, m in enumerate(models):
            card = OnlineModelCard(m, self.client)
            card.details_requested.connect(self._show_details)
            total = current_count + i
            self.grid.addWidget(card, total // cols, total % cols)

        total_shown = self.grid.count()
        if self.next_cursor:
            self.load_more_btn.setText(f"Cargar más  ({total_shown} mostrados)")
            self.load_more_btn.show()
            self.status_bar.setText(f"Mostrando {total_shown} modelos — hay más disponibles.")
        else:
            self.load_more_btn.hide()
            self.status_bar.setText(f"Mostrando {total_shown} modelos — fin de resultados.")

    def _load_more(self):
        if not self.next_cursor:
            return
        self.load_more_btn.setEnabled(False)
        self.load_more_btn.setText("Cargando...")
        self.progress_panel.show()
        self.pbar.setRange(0, 0)

        worker = FetchModelsWorker(
            self.client,
            username=self.initial_username,
            tag=self.initial_tag,
            cursor=self.next_cursor
        )
        worker.finished.connect(self._show_results)
        worker.error.connect(self._on_error)
        self.workers.append(worker)
        worker.start()

    def _on_error(self, err):
        self.progress_panel.hide()
        self.load_more_btn.setEnabled(True)
        self.status_bar.setText(f"Error: {err}")
        self.status_bar.setStyleSheet("color: #FF5555; padding: 5px; font-size: 11px;")
    
    def _show_details(self, model_data):
        popup = OnlineModelDetailsDialog(model_data, self.client, self)
        popup.download_requested.connect(self._start_download)
        popup.tag_clicked.connect(self._on_tag_clicked)
        popup.exec()

    def _on_tag_clicked(self, tag_name):
        self.initial_username = None # Clear username if searching by tag
        self.initial_tag = tag_name
        self._do_search()

    def _start_download(self, model_data):
        # Determine path
        m_type = model_data.get("type", "").lower()
        paths = state.get_paths()
        base = paths.get("models", "/run/media/system/Kilaya/ai-hub/Models")
        
        proposed = os.path.join(base, "Lora") if m_type in ("lora", "locon", "lycoris") else os.path.join(base, "DiffusionModels")
        if not os.path.exists(proposed): os.makedirs(proposed, exist_ok=True)
        
        target = QFileDialog.getExistingDirectory(self, "Donde guardar el modelo?", proposed)
        if not target: return
        
        self.progress_panel.show()
        self.pbar.setRange(0, 100)
        self.status_bar.setText(f"Descargando {model_data['name']}...")
        
        # Limit to one simultaneous download per dialog instance for sanity
        if any(isinstance(w, DownloadWorker) and w.isRunning() for w in self.workers):
            return
            
        version_data = model_data["modelVersions"][0]
        worker = DownloadWorker(self.client, version_data, target)
        worker.progress.connect(self.pbar.setValue)
        worker.status_changed.connect(self.status_bar.setText)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._finalize_download)
        self.workers.append(worker)
        worker.start()

    def _finalize_download(self, path, version_data, file_hash):
        from PySide6.QtCore import QTimer
        self.status_bar.setText("Guardando en la base de datos...")
        # Auto-register in DB side-thread
        def _auto_reg():
            self.vault.quick_register(path, file_hash, version_data)
            # Use QTimer to jump back to UI thread for a simple string update
            QTimer.singleShot(0, lambda: self.status_bar.setText(f"¡Completado! Registrado: {os.path.basename(path)}"))
        import threading
        threading.Thread(target=_auto_reg, daemon=True).start()
