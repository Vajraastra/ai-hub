"""
Widget de tarjeta para mostrar la información de un LoRA cargado.
"""
from pathlib import Path
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QWidget, QToolButton, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QPixmap

from ..core.detector import LoRAInfo, get_rank_summary
from ..core.analyzer import AnalysisResult


_ARCH_COLORS = {
    "sd15":          "#54EFEA",
    "sd15_unet_only": "#54EFEA",
    "sdxl":          "#EC00F0",
    "sdxl_unet_only": "#EC00F0",
    "flux":          "#F0A800",
    "wan22":         "#60D060",
    "sd3":           "#6080FF",
    "dit_generic":   "#AAAAAA",
    "unknown":       "#666666",
}

_CONTENT_ICONS = {
    "estilo":             "🎨",
    "personaje / sujeto": "👤",
    "concepto":           "💡",
    "concepto / texto":   "💡",
    "detalles / estilo":  "✨",
    "mixto":              "🔀",
    "desconocido":        "❓",
}


class LoRACard(QFrame):
    """
    Tarjeta que muestra info de un LoRA cargado.
    Puede crearse en modo loading (loading_path != "") mientras el análisis corre en hilo.
    """
    remove_requested = Signal(str)   # emite el path del LoRA

    def __init__(self, lora_info: LoRAInfo | None = None,
                 analysis: AnalysisResult | None = None,
                 loading_path: str = "",
                 parent=None):
        super().__init__(parent)
        self.lora_info = lora_info
        self.analysis = analysis
        self._loading_path = loading_path
        self._build()

    def _build(self):
        self.setObjectName("lora_card")
        self.setStyleSheet("""
            QFrame#lora_card {
                background-color: #1A0040;
                border: 1px solid #3D1B7B;
                border-radius: 6px;
            }
            QLabel { color: #e0e0ff; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        if self.lora_info is None:
            self._populate_loading(layout)
        else:
            self._populate(layout)

    def _populate_loading(self, layout):
        """Estado de carga: muestra nombre del archivo + barra animada."""
        name = Path(self._loading_path).name if self._loading_path else "Cargando..."

        header = QHBoxLayout()
        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("monospace", 9, QFont.Bold))
        name_lbl.setStyleSheet("color: #888aaa;")
        name_lbl.setWordWrap(True)
        name_lbl.setToolTip(self._loading_path)
        header.addWidget(name_lbl, 1)

        rm_btn = QToolButton()
        rm_btn.setText("✕")
        rm_btn.setStyleSheet("""
            QToolButton { color: #EC00F0; border: none; font-size: 14px; }
            QToolButton:hover { color: #FF66FF; }
        """)
        rm_btn.setToolTip("Cancelar")
        rm_btn.clicked.connect(
            lambda: self.remove_requested.emit(self._loading_path)
        )
        header.addWidget(rm_btn)
        layout.addLayout(header)

        bar = QProgressBar()
        bar.setRange(0, 0)   # indeterminado — Qt anima automáticamente
        bar.setFixedHeight(4)
        bar.setTextVisible(False)
        bar.setStyleSheet("""
            QProgressBar {
                background: #2A0050; border-radius: 2px; border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #600DB5, stop:1 #EC00F0
                );
                border-radius: 2px;
            }
        """)
        layout.addWidget(bar)

        status_lbl = QLabel("Analizando...")
        status_lbl.setStyleSheet("color: #555577; font-size: 10px;")
        layout.addWidget(status_lbl)

    def set_loaded(self, lora_info: LoRAInfo, analysis: AnalysisResult):
        """Sustituye el estado loading por la tarjeta completa."""
        self.lora_info = lora_info
        self.analysis = analysis
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                sub_layout = item.layout()
                while sub_layout.count():
                    sub = sub_layout.takeAt(0)
                    sw = sub.widget()
                    if sw:
                        sw.setParent(None)
                        sw.deleteLater()
        self._populate(layout)

    def _find_preview(self, path: str) -> str | None:
        """Busca imagen de preview junto al .safetensors (convención Model Vault)."""
        p = Path(path)
        # Formato Civitai/Model Vault: nombre.preview.jpeg (reemplaza .safetensors)
        for ext in (".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp"):
            candidate = p.with_suffix(ext)
            if candidate.exists():
                return str(candidate)
        # Formato alternativo: nombre.jpg / nombre.png
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = p.with_suffix(ext)
            if candidate.exists():
                return str(candidate)
        return None

    def _populate(self, layout):
        # ── Fila superior: thumbnail (izq) + info básica (der) ────────── #
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)

        preview_path = self._find_preview(self.lora_info.path)
        if preview_path:
            pix = QPixmap(preview_path)
            if not pix.isNull():
                thumb_lbl = QLabel()
                scaled = pix.scaled(88, 108, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb_lbl.setPixmap(scaled)
                thumb_lbl.setFixedSize(scaled.size())
                top_row.addWidget(thumb_lbl, 0, Qt.AlignTop)

        # Columna info (derecha del thumbnail)
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_col.setContentsMargins(0, 0, 0, 0)

        # Nombre + botón quitar
        header = QHBoxLayout()
        name = Path(self.lora_info.path).name
        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("monospace", 9, QFont.Bold))
        name_lbl.setStyleSheet("color: #DDDDFF;")
        name_lbl.setWordWrap(True)
        name_lbl.setToolTip(self.lora_info.path)
        header.addWidget(name_lbl, 1)

        rm_btn = QToolButton()
        rm_btn.setText("✕")
        rm_btn.setStyleSheet("""
            QToolButton { color: #EC00F0; border: none; font-size: 14px; }
            QToolButton:hover { color: #FF66FF; }
        """)
        rm_btn.setToolTip("Eliminar este LoRA del merge")
        rm_btn.clicked.connect(lambda: self.remove_requested.emit(self.lora_info.path))
        header.addWidget(rm_btn)
        info_col.addLayout(header)

        # ── Error state ────────────────────────────────────────────────── #
        if self.lora_info.error:
            err_lbl = QLabel(f"Error: {self.lora_info.error}")
            err_lbl.setStyleSheet("color: #FF4444; font-size: 11px;")
            err_lbl.setWordWrap(True)
            info_col.addWidget(err_lbl)
            info_col.addStretch()
            top_row.addLayout(info_col, 1)
            layout.addLayout(top_row)
            return

        # Arquitectura + size
        arch_color = _ARCH_COLORS.get(self.lora_info.arch_id, "#AAAAAA")
        arch_row = QHBoxLayout()
        arch_lbl = QLabel(self.lora_info.arch)
        arch_lbl.setStyleSheet(f"color: {arch_color}; font-weight: bold; font-size: 12px;")
        arch_row.addWidget(arch_lbl)
        size_lbl = QLabel(f"{self.lora_info.size_mb:.1f} MB")
        size_lbl.setStyleSheet("color: #888aaa; font-size: 11px;")
        size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        arch_row.addWidget(size_lbl)
        info_col.addLayout(arch_row)

        # Rank
        rank_lbl = QLabel(get_rank_summary(self.lora_info))
        rank_lbl.setStyleSheet("color: #888aaa; font-size: 11px;")
        info_col.addWidget(rank_lbl)

        # Capas
        layers_lbl = QLabel(
            f"{self.lora_info.num_layers} capas"
            + (" | UNet" if self.lora_info.has_unet else "")
            + (" | TE"   if self.lora_info.has_te   else "")
        )
        layers_lbl.setStyleSheet("color: #666699; font-size: 11px;")
        info_col.addWidget(layers_lbl)
        info_col.addStretch()

        top_row.addLayout(info_col, 1)
        layout.addLayout(top_row)

        # ── Análisis (si disponible) ───────────────────────────────────── #
        if self.analysis and not self.analysis.error:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet("background-color: #3D1B7B; border: none; max-height: 1px;")
            layout.addWidget(sep)

            ct = self.analysis.content_type
            icon = _CONTENT_ICONS.get(ct, "?")
            ct_lbl = QLabel(f"{icon} {ct.capitalize()}")
            ct_lbl.setStyleSheet("color: #54EFEA; font-size: 11px; font-weight: bold;")
            layout.addWidget(ct_lbl)

            # Top 5 bloques de mayor impacto
            top_blocks = sorted(
                self.analysis.block_scores.items(), key=lambda x: -x[1]
            )[:5]
            if top_blocks:
                blocks_lbl = QLabel("Impacto por bloque:")
                blocks_lbl.setStyleSheet("color: #888aaa; font-size: 10px;")
                layout.addWidget(blocks_lbl)

                for block_name, score in top_blocks:
                    row = QHBoxLayout()
                    row.setSpacing(4)

                    b_lbl = QLabel(block_name[:28])
                    b_lbl.setStyleSheet("color: #AAAACC; font-size: 10px;")
                    b_lbl.setFixedWidth(140)
                    row.addWidget(b_lbl)

                    bar = QProgressBar()
                    bar.setRange(0, 100)
                    bar.setValue(int(score))
                    bar.setTextVisible(False)
                    bar.setFixedHeight(6)
                    bar.setStyleSheet("""
                        QProgressBar {
                            background: #2A0050; border-radius: 3px; border: none;
                        }
                        QProgressBar::chunk {
                            background: qlineargradient(
                                x1:0, y1:0, x2:1, y2:0,
                                stop:0 #600DB5, stop:1 #EC00F0
                            );
                            border-radius: 3px;
                        }
                    """)
                    row.addWidget(bar, 1)

                    pct_lbl = QLabel(f"{score:.0f}%")
                    pct_lbl.setStyleSheet("color: #888aaa; font-size: 10px;")
                    pct_lbl.setFixedWidth(30)
                    row.addWidget(pct_lbl)

                    layout.addLayout(row)

    def update_analysis(self, analysis: AnalysisResult):
        """Actualiza la tarjeta con el resultado del análisis (reconstruye UI)."""
        self.analysis = analysis
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                sub_layout = item.layout()
                while sub_layout.count():
                    sub = sub_layout.takeAt(0)
                    sw = sub.widget()
                    if sw:
                        sw.setParent(None)
                        sw.deleteLater()
        self._populate(layout)
