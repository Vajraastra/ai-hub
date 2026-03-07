import os
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QWidget, QHBoxLayout, 
    QPushButton, QDialog, QScrollArea, QTextBrowser, 
    QTextEdit, QLineEdit, QSizePolicy, QApplication, QLayout
)
from PySide6.QtCore import Qt, Signal, QSize, QPoint, QRect, QUrl, QTimer
from PySide6.QtGui import QPixmap, QDesktopServices

class FlowLayout(QLayout):
    """
    A layout that arranges widgets in a flow (like text).
    """
    def __init__(self, parent=None, margin=0, hspacing=5, vspacing=5):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._hspacing = hspacing
        self._vspacing = vspacing
        self._items = []

    def __del__(self):
        while self.count():
            item = self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def horizontalSpacing(self):
        return self._hspacing

    def verticalSpacing(self):
        return self._vspacing

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        size += QSize(2 * self.contentsMargins().top(), 2 * self.contentsMargins().top())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            wid = item.widget()
            space_x = self.horizontalSpacing()
            space_y = self.verticalSpacing()
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()

class ModelDetailsDialog(QDialog):
    """
    Detailed view for a model using a single scrollable page layout.
    """
    notes_saved = Signal(str, str) # Emits hash, notes
    tags_saved = Signal(str, str)  # Emits hash, tags
    creator_clicked = Signal(str)  # Emits username
    tag_clicked = Signal(str)      # Emits tag name
    _tags_fetched = Signal(list)   # Internal: async civitai fetch result

    def __init__(self, model_data, parent=None):
        super().__init__(parent)
        self.data = model_data
        self.setWindowTitle(f"Ficha de Modelo - {self.data.get('name', 'Modelo')}")
        self.setMinimumSize(850, 850)
        self.setStyleSheet("""
            QDialog { background-color: #0B001A; color: #E0E0FF; }
            QLabel { color: #E0E0FF; }
            QScrollArea { border: none; background-color: transparent; }
            QWidget#scrollContent { background-color: transparent; }
            QTextBrowser {
                background-color: #1F004B; border: 1px solid #600DB5;
                border-radius: 8px; color: #FFFFFF; font-size: 15px;
                padding: 15px; line-height: 1.6;
            }
            QTextEdit {
                background-color: #1F004B; border: 1px solid #600DB5;
                border-radius: 8px; color: #54EFEA; font-size: 15px;
                padding: 10px;
            }
            QPushButton#actionBtn {
                background-color: #600DB5; border: 1px solid #EC00F0;
                padding: 10px 25px; border-radius: 6px; font-weight: bold;
                color: white;
            }
            QPushButton#actionBtn:hover { background-color: #EC00F0; color: white; }
            QPushButton#chip {
                background-color: #1F004B; border: 2px solid #600DB5;
                border-radius: 14px; padding: 6px 14px; color: #51CCDC;
                font-size: 12px; font-weight: bold; margin: 3px;
            }
            QPushButton#chip:hover { border: 2px solid #54EFEA; color: white; background-color: #600DB5; }
            QLabel#sectionHeader { color: #51CCDC; font-weight: bold; font-size: 14px; margin-top: 15px; }
            QLineEdit#promptEdit {
                background-color: #1F004B; border: 1px solid #54EFEA;
                border-radius: 6px; color: #FFFFFF; padding: 8px; font-size: 14px;
            }
        """)
        self._setup_ui()
        self._tags_fetched.connect(self._populate_civitai_tag_chips)
        # If no tags cached in DB but model is known on Civitai, fetch live
        if not (self.data.get("custom_tags") or "").strip() and self.data.get("model_id"):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(400, self._fetch_tags_async)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll Area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        content = QWidget()
        content.setObjectName("scrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 30)
        layout.setSpacing(25)

        # 1. Image Header
        preview_path = self.data.get("preview_path")
        if preview_path and os.path.exists(preview_path):
            img_lbl = QLabel()
            img_lbl.setMinimumHeight(450)
            img_lbl.setMaximumHeight(650)
            img_lbl.setAlignment(Qt.AlignCenter)
            pix = QPixmap(preview_path)
            img_lbl.setPixmap(pix.scaled(850, 650, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            img_lbl.setStyleSheet("background-color: #000; border-bottom: 3px solid #600DB5;")
            layout.addWidget(img_lbl)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(40, 0, 40, 0)
        inner_layout.setSpacing(30)

        # 2. Main Title
        id_section = QVBoxLayout()
        name_lbl = QLabel(self.data.get("name", "Unknown"))
        name_lbl.setStyleSheet("font-size: 32px; font-weight: bold; color: #54EFEA;")
        name_lbl.setWordWrap(True)
        id_section.addWidget(name_lbl)
        
        info_strip = QHBoxLayout()
        info_strip.setSpacing(30)
        def add_info_mini(label, value):
            lbl = QLabel(f"<font color='#51CCDC'>{label}:</font> <b style='color: white;'>{value}</b>")
            lbl.setStyleSheet("font-size: 15px;")
            info_strip.addWidget(lbl)
        
        add_info_mini("Arquitectura", self.data.get("base_model", "Unknown"))
        add_info_mini("Tipo", self.data.get("model_type", "Unknown"))
        add_info_mini("Versión", self.data.get("version_name", "Unknown"))
        
        creator_name = self.data.get("creator_name")
        if creator_name and creator_name != "Unknown":
            creator_btn = QPushButton(f"👤 Creador: {creator_name}")
            creator_btn.setToolTip("Haz clic para explorar los modelos de este creador")
            creator_btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #54EFEA; 
                    border: none; font-size: 15px; font-weight: bold;
                    text-align: left;
                }
                QPushButton:hover { text-decoration: underline; color: white; }
            """)
            creator_btn.setCursor(Qt.PointingHandCursor)
            creator_btn.clicked.connect(lambda: self.creator_clicked.emit(creator_name))
            info_strip.addWidget(creator_btn)
            
        info_strip.addStretch()
        id_section.addLayout(info_strip)
        inner_layout.addLayout(id_section)

        # 2.5 Tags de Civitai (prominente, clickable → CivitaiBrowser)
        civitai_tags_section = QVBoxLayout()
        civitai_tags_h = QLabel("🏷️ Tags de Civitai")
        civitai_tags_h.setObjectName("sectionHeader")
        civitai_tags_section.addWidget(civitai_tags_h)

        self._tags_loading_lbl = QLabel("Buscando tags en Civitai...")
        self._tags_loading_lbl.setStyleSheet("color: #555577; font-style: italic; font-size: 12px;")

        self.civitai_tags_widget = QWidget()
        self.civitai_tags_flow = FlowLayout(self.civitai_tags_widget, margin=0, hspacing=6, vspacing=6)
        self.civitai_tags_widget.hide()

        raw_tags = (self.data.get("custom_tags") or "").strip()
        if raw_tags:
            # Tags already in DB — show immediately, no fetch needed
            self._tags_loading_lbl.hide()
            for t in [x.strip() for x in raw_tags.split(",") if x.strip()]:
                btn = QPushButton(t)
                btn.setObjectName("chip")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, text=t: self.tag_clicked.emit(text))
                self.civitai_tags_flow.addWidget(btn)
            self.civitai_tags_widget.show()
        else:
            # Will fetch async if model_id is available (wired up in __init__)
            if not self.data.get("model_id"):
                self._tags_loading_lbl.setText("Sin tags disponibles")

        civitai_tags_section.addWidget(self._tags_loading_lbl)
        civitai_tags_section.addWidget(self.civitai_tags_widget)
        inner_layout.addLayout(civitai_tags_section)

        # 3. Prompt Builder & Trigger Chips
        triggers = self.data.get("triggers", "")
        if triggers:
            prompt_container = QWidget()
            prompt_vbox = QVBoxLayout(prompt_container)
            prompt_vbox.setContentsMargins(0, 0, 0, 0)
            prompt_vbox.setSpacing(15)
            
            h_lbl = QLabel("🏗️ Prompt Builder - <i>Toca los tags para armar tu prompt</i>")
            h_lbl.setObjectName("sectionHeader")
            prompt_vbox.addWidget(h_lbl)
            
            chips_widget = QWidget()
            chips_layout = FlowLayout(chips_widget)
            for t in triggers.split(","):
                tag = t.strip()
                if not tag: continue
                btn = QPushButton(tag)
                btn.setObjectName("chip")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, text=tag, b=btn: self._add_to_prompt(text, b))
                chips_layout.addWidget(btn)
            prompt_vbox.addWidget(chips_widget)
            
            # The actual prompt text area
            builder_layout = QHBoxLayout()
            self.prompt_display = QTextEdit()
            self.prompt_display.setPlaceholderText("Tu prompt se construirá aquí...")
            self.prompt_display.setFixedHeight(80)
            self.prompt_display.setStyleSheet("""
                background-color: #1F004B; border: 2px solid #51CCDC; 
                border-radius: 8px; color: #FFFFFF; font-weight: bold;
            """)
            builder_layout.addWidget(self.prompt_display)
            
            copy_prompt_btn = QPushButton("📋 Copiar All")
            copy_prompt_btn.setFixedSize(120, 80)
            copy_prompt_btn.setObjectName("actionBtn")
            copy_prompt_btn.clicked.connect(self._copy_full_prompt)
            builder_layout.addWidget(copy_prompt_btn)
            
            prompt_vbox.addLayout(builder_layout)
            
            clear_btn = QPushButton("Limpiar Prompt")
            clear_btn.setStyleSheet("color: #EC00F0; background: transparent; border: none; text-align: left; font-weight: bold;")
            clear_btn.clicked.connect(lambda: self.prompt_display.clear())
            prompt_vbox.addWidget(clear_btn)
            
            inner_layout.addWidget(prompt_container)

        # 4. Description
        desc_section = QVBoxLayout()
        desc_h = QLabel("📖 Descripción de Civitai")
        desc_h.setObjectName("sectionHeader")
        desc_section.addWidget(desc_h)
        
        self.desc_browser = QTextBrowser()
        self.desc_browser.setOpenExternalLinks(True)
        self.desc_browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        html_desc = self.data.get("description") or "<i>No hay descripción disponible.</i>"
        self.desc_browser.setHtml(html_desc)
        self.desc_browser.document().contentsChanged.connect(
            lambda: self.desc_browser.setFixedHeight(int(self.desc_browser.document().size().height() + 60))
        )
        desc_section.addWidget(self.desc_browser)
        inner_layout.addLayout(desc_section)

        # 5. User Notes Section
        notes_section = QVBoxLayout()
        notes_h = QLabel("✍️ Mis Apuntes y Consejos")
        notes_h.setStyleSheet("color: #888aaa; font-weight: bold; font-size: 13px; margin-top: 10px;")
        notes_section.addWidget(notes_h)
        
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Ej: 'Usar peso 0.8', 'Sampler: DPM++ 2M SDE', 'CFG: 5.5'...")
        self.notes_edit.setText(self.data.get("user_notes", ""))
        self.notes_edit.setFixedHeight(180)
        notes_section.addWidget(self.notes_edit)
        
        save_btn = QPushButton("💾 Guardar Mis Notas")
        save_btn.setObjectName("actionBtn")
        save_btn.clicked.connect(self._save_notes)
        notes_section.addWidget(save_btn, 0, Qt.AlignRight)
        inner_layout.addLayout(notes_section)

        # 5.5 Mis Etiquetas (user-editable, personal organization)
        tags_section = QVBoxLayout()
        tags_h = QLabel("✏️ Mis Etiquetas")
        tags_h.setStyleSheet("color: #888aaa; font-weight: bold; font-size: 13px; margin-top: 10px;")
        tags_section.addWidget(tags_h)

        self.tags_edit = QLineEdit()
        self.tags_edit.setObjectName("promptEdit")
        self.tags_edit.setPlaceholderText("Ej: fav, anime, landscape (separadas por coma)")
        self.tags_edit.setText(self.data.get("custom_tags", ""))
        tags_section.addWidget(self.tags_edit)

        save_tags_btn = QPushButton("💾 Guardar Etiquetas")
        save_tags_btn.setObjectName("actionBtn")
        save_tags_btn.clicked.connect(self._save_tags)
        tags_section.addWidget(save_tags_btn, 0, Qt.AlignRight)
        inner_layout.addLayout(tags_section)

        # 6. Tech Metadata Footer
        footer_info = QLabel(f"SHA256: {self.data.get('hash', 'Unknown')}")
        footer_info.setStyleSheet("color: #333; font-size: 11px; margin-top: 30px;")
        inner_layout.addWidget(footer_info)

        layout.addWidget(inner)
        scroll.setWidget(content)
        main_layout.addWidget(scroll)

        # Action Bar (Fixed at bottom)
        action_bar = QWidget()
        action_bar.setStyleSheet("background-color: #0B001A; border-top: 1px solid #3D1B7B;")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(25, 20, 25, 20)
        
        self.update_btn = QPushButton("🔄 Buscar Actualización")
        self.update_btn.setObjectName("actionBtn")
        self.update_btn.clicked.connect(self._check_update)

        open_folder_btn = QPushButton("📁 Abrir Carpeta")
        open_folder_btn.setObjectName("actionBtn")
        open_folder_btn.clicked.connect(self._open_folder)
        
        web_btn = QPushButton("🌐 Ver en Civitai")
        web_btn.setObjectName("actionBtn")
        web_btn.clicked.connect(self._open_web)
        
        action_layout.addWidget(self.update_btn)
        action_layout.addWidget(open_folder_btn)
        action_layout.addWidget(web_btn)
        action_layout.addStretch()
        
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("actionBtn")
        close_btn.clicked.connect(self.close)
        action_layout.addWidget(close_btn)
        
        main_layout.addWidget(action_bar)

    def _check_update(self):
        """Initial feedback when clicking the update button."""
        self.update_btn.setText("⏳ Buscando...")
        self.update_btn.setEnabled(False)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._perform_update_check)

    def _perform_update_check(self):
        """Logic to call the vault service and show results."""
        try:
            # The parent of this dialog is ModelVaultWidget which has the vault service
            vault = self.parent().vault
            result = vault.check_for_updates(self.data["hash"])
            
            if not result:
                self.update_btn.setText("❓ No encontrado")
                self.update_btn.setEnabled(True)
            elif result.get("status") == "up_to_date":
                self.update_btn.setText("✅ Al día")
                self.update_btn.setStyleSheet("background-color: #2A5E2A; color: white;")
                self.update_btn.setEnabled(True)
            else:
                new_name = result["new_version_name"]
                self.update_btn.setText(f"✨ Nueva: {new_name}")
                self.update_btn.setStyleSheet("background-color: #EC00F0; color: white;")
                self.update_btn.setToolTip(f"Hay una nueva versión disponible en Civitai: {new_name}")
                self.update_btn.setEnabled(True)
                # If there's a new version, clicking it again could open the web
                try:
                    self.update_btn.clicked.disconnect()
                except: pass
                self.update_btn.clicked.connect(self._open_web)
                
        except Exception as e:
            self.update_btn.setText("❌ Error")
            self.update_btn.setEnabled(True)
            print(f"Update check error: {e}")

    def _fetch_tags_async(self):
        """Background fetch of Civitai tags when not cached in DB."""
        model_id = self.data.get("model_id")
        if not model_id:
            return

        def _target():
            try:
                vault = self.parent().vault
                info = vault.client.get_model_info(model_id)
                tags = info.get("tags", []) if info else []
                self._tags_fetched.emit(tags)
            except Exception as e:
                print(f"[tags fetch] {e}")
                self._tags_fetched.emit([])

        import threading
        threading.Thread(target=_target, daemon=True).start()

    def _populate_civitai_tag_chips(self, tags):
        """Populate the Civitai tags section with clickable chips (thread-safe via signal)."""
        self._tags_loading_lbl.hide()

        # Clear any existing chips
        while self.civitai_tags_flow.count():
            item = self.civitai_tags_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not tags:
            self._tags_loading_lbl.setText("Sin tags en Civitai")
            self._tags_loading_lbl.show()
            return

        for t in tags:
            btn = QPushButton(t)
            btn.setObjectName("chip")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, text=t: self.tag_clicked.emit(text))
            self.civitai_tags_flow.addWidget(btn)

        self.civitai_tags_widget.show()
        self.civitai_tags_widget.updateGeometry()

    def _add_to_prompt(self, text, btn=None):
        """Append a tag to the prompt display area with comma separation."""
        current = self.prompt_display.toPlainText().strip()
        if not current:
            self.prompt_display.setPlainText(text)
        else:
            # Check if tag already exists to avoid duplicates
            tags = [t.strip() for t in current.split(",")]
            if text not in tags:
                self.prompt_display.setPlainText(f"{current}, {text}")
        
        # Visual feedback on the chip
        if btn is None:
            btn = self.sender()
            
        if btn:
            btn.setStyleSheet("background-color: #54EFEA; color: #1F004B; border: 2px solid white;")
            QTimer.singleShot(400, lambda: btn.setStyleSheet(""))

    def _copy_full_prompt(self):
        """Copy the entire built prompt to clipboard."""
        prompt = self.prompt_display.toPlainText().strip()
        if not prompt: return
        
        QApplication.clipboard().setText(prompt)
        
        # Feedback on copy all button
        btn = self.sender()
        old_text = btn.text()
        btn.setText("✅ Copiado!")
        btn.setStyleSheet("background-color: #54EFEA; color: #1F004B;")
        QTimer.singleShot(1000, lambda: (btn.setText(old_text), btn.setStyleSheet("")))

    def _save_tags(self):
        tags = self.tags_edit.text().strip()
        self.tags_saved.emit(self.data.get("hash"), tags)
        btn = self.sender()
        old_text = btn.text()
        btn.setText("✅ Etiquetas Guardadas")
        btn.setEnabled(False)
        QTimer.singleShot(1500, lambda: (btn.setText(old_text), btn.setEnabled(True)))

    def _save_notes(self):
        notes = self.notes_edit.toPlainText()
        self.notes_saved.emit(self.data.get("hash"), notes)
        btn = self.sender()
        old_text = btn.text()
        btn.setText("✅ Notas Guardadas")
        btn.setEnabled(False)
        QTimer.singleShot(1500, lambda: (btn.setText(old_text), btn.setEnabled(True)))

    def _open_folder(self):
        path = self.data.get("file_path")
        if path and os.path.exists(path):
            folder = os.path.dirname(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _open_web(self):
        m_id = self.data.get("model_id")
        v_id = self.data.get("version_id")
        if m_id:
            url = f"https://civitai.com/models/{m_id}"
            if v_id: url += f"?modelVersionId={v_id}"
            QDesktopServices.openUrl(QUrl(url))

class ModelCard(QFrame):
    """
    A premium-looking card for a single model/LoRA.
    """
    clicked = Signal(str)
    doubleClicked = Signal(dict)
    tag_clicked = Signal(str)

    ARCH_COLORS = {
        "SDXL 1.0": "#6A5ACD",   
        "SDXL Turbo": "#6A5ACD",
        "Flux.1 S": "#008080",   
        "Flux.1 D": "#008080",
        "Pony": "#C71585",       
        "SD 1.5": "#4682B4",      
        "Illustrious": "#4B0082", 
    }

    def __init__(self, model_data, parent=None):
        super().__init__(parent)
        self.data = model_data
        self.setObjectName("card")
        self.setFixedSize(200, 320) 
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        container = QWidget()
        container.setFixedSize(200, 220)
        img_layout = QVBoxLayout(container)
        img_layout.setContentsMargins(0, 0, 0, 0)

        self.img_lbl = QLabel()
        self.img_lbl.setFixedSize(200, 220)
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setStyleSheet("background-color: #1A0040; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        
        preview_path = self.data.get("preview_path")
        if preview_path and os.path.exists(preview_path):
            pix = QPixmap(preview_path)
            self.img_lbl.setPixmap(pix.scaled(200, 220, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            self.img_lbl.setText("🖼️ Sin Preview")
            self.img_lbl.setStyleSheet("color: #444466; background-color: #1A0040;")

        img_layout.addWidget(self.img_lbl)

        arch = self.data.get("base_model") or "Unknown"
        color = "#3D1B7B"
        for key, val in self.ARCH_COLORS.items():
            if key in arch:
                color = val
                break

        arch_badge = QLabel(arch, self.img_lbl)
        arch_badge.setStyleSheet(f"""
            background-color: {color}; color: white; border-radius: 4px; 
            padding: 2px 6px; font-size: 10px; font-weight: bold;
        """)
        arch_badge.move(10, 10)

        layout.addWidget(container)

        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(12, 10, 2, 10)
        info_layout.setSpacing(4)

        mtype = (self.data.get("model_type") or "DESCONOCIDO").upper()
        type_lbl = QLabel(mtype)
        type_lbl.setStyleSheet("""
            background-color: #1A0040; color: #888aaa; 
            border: 1px solid #3D1B7B; border-radius: 4px;
            padding: 2px 6px; font-size: 9px; font-weight: bold; letter-spacing: 1px;
        """)
        info_layout.addWidget(type_lbl)
        info_layout.setAlignment(type_lbl, Qt.AlignLeft)

        name_lbl = QLabel(self.data.get("display_name") or self.data.get("name", "Unknown"))
        name_lbl.setStyleSheet("font-weight: bold; color: #54EFEA; font-size: 14px;")
        name_lbl.setWordWrap(True)
        name_lbl.setMaximumHeight(40)
        info_layout.addWidget(name_lbl)

        # Tag chips (max 3, clickable → CivitaiBrowser)
        tags_str = self.data.get("custom_tags") or ""
        if tags_str.strip():
            tags = [t.strip() for t in tags_str.split(",") if t.strip()][:3]
            tags_widget = QWidget()
            tags_layout = FlowLayout(tags_widget, margin=0, hspacing=3, vspacing=2)
            for tag in tags:
                btn = QPushButton(tag)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(84,239,234,0.08); color: #54EFEA;
                        border: 1px solid #3D1B7B; border-radius: 7px;
                        padding: 1px 5px; font-size: 9px; font-weight: bold;
                    }
                    QPushButton:hover { background: #54EFEA; color: #1F004B; }
                """)
                btn.clicked.connect(lambda checked=False, t=tag: self.tag_clicked.emit(t))
                tags_layout.addWidget(btn)
            info_layout.addWidget(tags_widget)

        info_layout.addStretch()

        layout.addWidget(info_widget)

        self.setStyleSheet("""
            QFrame#card {
                background-color: #1E004E;
                border-radius: 8px;
                border: 1px solid #3D1B7B;
            }
            QFrame#card:hover {
                border: 1px solid #54EFEA;
                background-color: #2A0A5E;
            }
        """)

    def mousePressEvent(self, event):
        self.clicked.emit(self.data.get("file_path"))
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit(self.data)
        super().mouseDoubleClickEvent(event)
