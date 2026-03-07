"""
LoRA Merger — widget principal y ventana standalone.

Integración como tab en AI Hub:
    from apps.lora_merger.main import LoraMergerWidget
    tabs.addTab(LoraMergerWidget(), "LoRA Merger")
"""
import sys
import os
import json
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QMessageBox,
    QFileDialog, QSizePolicy, QSplitter
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent

MERGER_DIR = os.path.dirname(os.path.abspath(__file__))
if MERGER_DIR not in sys.path:
    sys.path.insert(0, MERGER_DIR)
PROJECT_ROOT = os.path.dirname(os.path.dirname(MERGER_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _resolve_output_dir() -> str:
    """
    Lee la carpeta de outputs del hub desde hub_config.json.
    Retorna <outputs>/lora_merges; si no hay config, usa PROJECT_ROOT/outputs/lora_merges.
    """
    hub_config_path = os.path.join(PROJECT_ROOT, "hub", "hub_config.json")
    try:
        with open(hub_config_path, "r") as f:
            cfg = json.load(f)
        base = cfg.get("paths", {}).get("outputs", "")
        if base:
            return os.path.join(base, "lora_merges")
    except Exception:
        pass
    return os.path.join(PROJECT_ROOT, "outputs", "lora_merges")

from apps.lora_merger.core.detector import LoRAInfo, detect
from apps.lora_merger.core.analyzer import AnalysisResult, analyze
from apps.lora_merger.core.validator import ValidationResult, validate
from apps.lora_merger.core.suggester import MergeSuggestion, suggest
from apps.lora_merger.core.merger import MergeConfig, MergeProgress, merge
from apps.lora_merger.ui.lora_card import LoRACard
from apps.lora_merger.ui.merge_panel import MergePanel


# ── Señales de worker ─────────────────────────────────────────────────── #

class WorkerSignals(QObject):
    lora_analyzed   = Signal(str, object, object)   # path, LoRAInfo, AnalysisResult
    validation_done = Signal(object, object)        # ValidationResult, MergeSuggestion
    merge_progress  = Signal(int, int, str)         # current, total, layer
    merge_done      = Signal(str)                   # output_path
    merge_error     = Signal(str)


# ── Widget principal ──────────────────────────────────────────────────── #

class LoraMergerWidget(QWidget):
    """
    Widget embebible como tab en el AI Hub.
    Gestiona la carga de LoRAs, análisis, validación y ejecución del merge.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loras: dict[str, LoRAInfo] = {}        # path → LoRAInfo
        self._analyses: dict[str, AnalysisResult] = {}
        self._cards: dict[str, LoRACard] = {}
        self._validation: ValidationResult | None = None
        self._suggestion: MergeSuggestion | None = None
        self._merging = False

        self.signals = WorkerSignals()
        self.signals.lora_analyzed.connect(self._on_lora_analyzed)
        self.signals.validation_done.connect(self._on_validation_done)
        self.signals.merge_progress.connect(self._on_merge_progress)
        self.signals.merge_done.connect(self._on_merge_done)
        self.signals.merge_error.connect(self._on_merge_error)

        self._apply_theme()
        self._build_ui()
        self.setAcceptDrops(True)
        self._refresh_output_dir()

    def showEvent(self, event):
        """Re-lee la config del hub cada vez que el tab se hace visible."""
        super().showEvent(event)
        self._refresh_output_dir()

    def _refresh_output_dir(self):
        output_dir = _resolve_output_dir()
        os.makedirs(output_dir, exist_ok=True)
        if hasattr(self, "merge_panel"):
            self.merge_panel.set_output_dir(output_dir)

    # ── Tema ──────────────────────────────────────────────────────────── #

    def _apply_theme(self):
        self.setStyleSheet("""
            QWidget { background-color: #0F0023; color: #e0e0ff; }
            QScrollArea { border: none; background: transparent; }
            QSplitter::handle { background-color: #3D1B7B; }
            QLabel { color: #e0e0ff; }
        """)

    # ── Construcción de UI ─────────────────────────────────────────────── #

    def _build_ui(self):
        self.setObjectName("merger_root")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ─────────────────────────────────────────────────────── #
        top_bar = QWidget()
        top_bar.setFixedHeight(62)
        top_bar.setStyleSheet("background-color: #1A0040; border-bottom: 1px solid #3D1B7B;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)

        title = QLabel("LoRA Merger")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #EC00F0;")
        top_layout.addWidget(title)

        self.status_lbl = QLabel("Carga 2 o más LoRAs para comenzar")
        self.status_lbl.setStyleSheet("color: #888aaa; font-size: 12px;")
        top_layout.addWidget(self.status_lbl)
        top_layout.addStretch()

        add_btn = QPushButton("+ Agregar LoRA")
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: #600DB5; color: white; font-weight: bold;
                border-radius: 4px; padding: 8px 18px; font-size: 13px;
            }
            QPushButton:hover { background-color: #7B1FD4; }
        """)
        add_btn.clicked.connect(self._add_lora_dialog)
        top_layout.addWidget(add_btn)

        root.addWidget(top_bar)

        # ── Body: splitter izquierda (LoRAs) / derecha (config) ────────── #
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background: #3D1B7B; width: 2px; }")

        # Panel izquierdo — lista de LoRAs
        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        # Panel derecho — configuración
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setStyleSheet("background: #0F0023;")

        output_dir = _resolve_output_dir()
        os.makedirs(output_dir, exist_ok=True)
        self.merge_panel = MergePanel(output_dir=output_dir)
        self.merge_panel.merge_requested.connect(self._start_merge)
        right_scroll.setWidget(self.merge_panel)
        splitter.addWidget(right_scroll)

        splitter.setSizes([420, 520])
        root.addWidget(splitter, 1)

    def _build_left_panel(self) -> QWidget:
        container = QWidget()
        container.setMinimumWidth(360)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 8)
        layout.setSpacing(10)

        # Zona de drop
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._load_loras)
        self.drop_zone.clicked.connect(self._add_lora_dialog)
        layout.addWidget(self.drop_zone)

        # Scroll de tarjetas
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.NoFrame)
        self.cards_scroll.setStyleSheet("background: transparent;")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()

        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll, 1)

        # Advertencias de compatibilidad
        self.compat_lbl = QLabel("")
        self.compat_lbl.setWordWrap(True)
        self.compat_lbl.setStyleSheet("color: #FF6644; font-size: 11px;")
        self.compat_lbl.setVisible(False)
        layout.addWidget(self.compat_lbl)

        return container

    # ── Drag & Drop ─────────────────────────────────────────────────────── #

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(p.endswith(".safetensors") for p in paths):
                event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [
            u.toLocalFile() for u in event.mimeData().urls()
            if u.toLocalFile().endswith(".safetensors")
        ]
        self._load_loras(paths)

    # ── Carga de LoRAs ──────────────────────────────────────────────────── #

    def _add_lora_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar LoRAs",
            os.path.expanduser("~"),
            "SafeTensors (*.safetensors)"
        )
        if paths:
            self._load_loras(paths)

    def _load_loras(self, paths: list[str]):
        new_paths = [p for p in paths if p not in self._loras]
        if not new_paths:
            return

        self.status_lbl.setText(f"Analizando {len(new_paths)} LoRA(s)...")
        self.drop_zone.setVisible(False)

        for path in new_paths:
            # Placeholder visible de inmediato mientras el análisis corre en hilo
            card = LoRACard(loading_path=path)
            card.remove_requested.connect(self._remove_lora)
            self._cards[path] = card
            count = self.cards_layout.count()
            self.cards_layout.insertWidget(count - 1, card)
            self._start_analysis(path)

    def _start_analysis(self, path: str):
        def _target():
            lora_info = detect(path)
            analysis = analyze(lora_info) if not lora_info.error else AnalysisResult(lora_path=path, error=lora_info.error)
            self.signals.lora_analyzed.emit(path, lora_info, analysis)
        threading.Thread(target=_target, daemon=True).start()

    def _on_lora_analyzed(self, path: str, lora_info: LoRAInfo, analysis: AnalysisResult):
        if path not in self._cards:
            # El usuario eliminó el LoRA mientras se analizaba — ignorar
            return

        self._loras[path] = lora_info
        self._analyses[path] = analysis
        self._cards[path].set_loaded(lora_info, analysis)
        self._run_validation()

    def _remove_lora(self, path: str):
        if path in self._cards:
            card = self._cards.pop(path)
            card.setParent(None)
            card.deleteLater()
        self._loras.pop(path, None)
        self._analyses.pop(path, None)

        if not self._loras:
            self.drop_zone.setVisible(True)

        self._run_validation()

    # ── Validación y sugerencias ─────────────────────────────────────────── #

    def _run_validation(self):
        loras = list(self._loras.values())
        analyses = [self._analyses[l.path] for l in loras if l.path in self._analyses]

        if len(loras) < 2:
            self._validation = None
            self._suggestion = None
            self.compat_lbl.setVisible(False)
            self.status_lbl.setText("Carga 2 o más LoRAs para comenzar")
            self.merge_panel.update_loras(loras, analyses, None, None)
            return

        def _target():
            v = validate(loras)
            s = suggest(loras, analyses, v) if v.compatible else None
            self.signals.validation_done.emit(v, s)
        threading.Thread(target=_target, daemon=True).start()

    def _on_validation_done(self, validation: ValidationResult, suggestion: MergeSuggestion | None):
        self._validation = validation
        self._suggestion = suggestion

        loras = list(self._loras.values())
        analyses = [self._analyses.get(l.path) for l in loras]
        analyses = [a for a in analyses if a is not None]

        # Mostrar errores/advertencias
        all_msgs = validation.errors + validation.warnings
        if all_msgs:
            self.compat_lbl.setText("\n".join(all_msgs))
            self.compat_lbl.setStyleSheet(
                "color: #FF4444;" if validation.errors else "color: #FFAA44;"
            )
            self.compat_lbl.setStyleSheet(
                "color: #FF6644; font-size: 11px;" if validation.errors
                else "color: #FFAA44; font-size: 11px;"
            )
            self.compat_lbl.setVisible(True)
        else:
            self.compat_lbl.setVisible(False)

        n = len(loras)
        if validation.compatible:
            self.status_lbl.setText(f"{n} LoRAs listos para merge")
            self.status_lbl.setStyleSheet("color: #54EFEA; font-size: 12px;")
        else:
            self.status_lbl.setText(f"Incompatibilidad detectada — revisa los errores")
            self.status_lbl.setStyleSheet("color: #FF4444; font-size: 12px;")

        self.merge_panel.update_loras(loras, analyses, validation, suggestion)

    # ── Merge ──────────────────────────────────────────────────────────── #

    def _start_merge(self, config: MergeConfig):
        if self._merging:
            return
        self._merging = True
        self.merge_panel.set_merging(True)

        loras = list(self._loras.values())

        def _target():
            try:
                def _progress(prog: MergeProgress):
                    self.signals.merge_progress.emit(
                        prog.current, prog.total, prog.layer_name
                    )
                merge(loras, config, progress_callback=_progress)
                self.signals.merge_done.emit(config.output_path)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.signals.merge_error.emit(str(e))

        threading.Thread(target=_target, daemon=True).start()

    def _on_merge_progress(self, current: int, total: int, layer: str):
        self.merge_panel.set_progress(current, total, layer)

    def _on_merge_done(self, output_path: str):
        self._merging = False
        self.merge_panel.set_merge_done(output_path)
        QMessageBox.information(
            self, "Merge completado",
            f"LoRA mergeado correctamente.\n\nArchivo guardado en:\n{output_path}"
        )

    def _on_merge_error(self, error: str):
        self._merging = False
        self.merge_panel.set_merge_error(error)
        QMessageBox.critical(self, "Error en el merge", error)


# ── Zona de drop ─────────────────────────────────────────────────────── #

class DropZone(QFrame):
    """Área clickeable y de drop para archivos .safetensors."""
    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(100)
        self.setStyleSheet("""
            QFrame {
                background-color: #120030;
                border: 2px dashed #3D1B7B;
                border-radius: 8px;
            }
            QFrame:hover { border-color: #600DB5; }
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        lbl = QLabel("Arrastra archivos .safetensors aquí\no haz clic para seleccionar")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #555577; font-size: 12px; border: none;")
        layout.addWidget(lbl)

    def mousePressEvent(self, event):
        self.clicked.emit()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(p.endswith(".safetensors") for p in paths):
                self.setStyleSheet("""
                    QFrame {
                        background-color: #1E005E;
                        border: 2px dashed #600DB5;
                        border-radius: 8px;
                    }
                """)
                event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QFrame {
                background-color: #120030;
                border: 2px dashed #3D1B7B;
                border-radius: 8px;
            }
            QFrame:hover { border-color: #600DB5; }
        """)

    def dropEvent(self, event: QDropEvent):
        self.dragLeaveEvent(event)
        paths = [
            u.toLocalFile() for u in event.mimeData().urls()
            if u.toLocalFile().endswith(".safetensors")
        ]
        if paths:
            self.files_dropped.emit(paths)


# ── Ventana standalone ────────────────────────────────────────────────── #

class LoraMergerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Hub — LoRA Merger")
        self.resize(980, 720)
        self.widget = LoraMergerWidget(self)
        self.setCentralWidget(self.widget)


def main():
    app = QApplication(sys.argv)
    window = LoraMergerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
