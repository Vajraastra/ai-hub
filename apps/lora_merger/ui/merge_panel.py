"""
Panel de configuración y control del merge.
Incluye: método, pesos por LoRA, filtro de capas, rank objetivo,
sugerencias automáticas y botón de merge.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QSpinBox, QDoubleSpinBox, QGroupBox,
    QProgressBar, QTextEdit, QScrollArea, QFrame, QFileDialog,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont

from ..core.detector import LoRAInfo
from ..core.analyzer import AnalysisResult
from ..core.validator import ValidationResult
from ..core.suggester import MergeSuggestion
from ..core.merger import MergeConfig


_METHODS = [
    ("weighted_sum",  "Weighted Sum — suma ponderada"),
    ("slerp",         "SLERP — interpolación esférica (solo 2 LoRAs)"),
    ("ties",          "TIES — resolución de conflictos"),
    ("dare",          "DARE — poda + suma ponderada"),
    ("dare_ties",     "DARE + TIES — poda + resolución de conflictos"),
]

_LAYER_FILTERS = [
    ("full",      "Todas las capas"),
    ("attn-only", "Solo atención (attn-only)"),
    ("mlp-only",  "Solo MLP (mlp-only)"),
    ("attn-mlp",  "Atención + MLP"),
]

_OUTPUT_DTYPES = [
    ("bf16", "BF16 — mitad de tamaño, calidad casi idéntica (recomendado)"),
    ("f16",  "F16  — mitad de tamaño, mayor precisión en rangos pequeños"),
    ("f32",  "F32  — tamaño completo, máxima precisión"),
]

# Presets de tipo de LoRA: (id, label, descripcion, method, layer_filter)
_PRESETS = [
    (
        "style",
        "Estilo artistico",
        "Mezcla estilos visuales, paletas de color o trazo artistico. "
        "Usa SLERP para transicion suave entre dos estilos, o Weighted Sum para controlar proporciones.",
        "slerp", "attn-only",
    ),
    (
        "character",
        "Personaje / Sujeto",
        "Combina rasgos de personajes, caras o identidades. "
        "El peso mayor va al LoRA con los rasgos más distintivos.",
        "weighted_sum", "full",
    ),
    (
        "concept",
        "Concepto / Pose / Anatomia",
        "Para poses, caracteristicas fisicas, expresiones o elementos especificos. "
        "TIES resuelve conflictos cuando los LoRAs codifican poses distintas.",
        "ties", "full",
    ),
    (
        "style_char",
        "Estilo + Personaje",
        "Un LoRA de estilo y uno de personaje. "
        "Weighted Sum con pesos distintos: mas peso al personaje para que no lo tape el estilo.",
        "weighted_sum", "full",
    ),
    (
        "scene",
        "Ambiente / Escena",
        "Para fondos, entornos, iluminacion o atmosferas. "
        "Suelen vivir en capas profundas; TIES ayuda si los escenarios son muy distintos.",
        "ties", "full",
    ),
    (
        "multi",
        "Multi-LoRA (3+)",
        "Combina tres o mas LoRAs de cualquier tipo. "
        "DARE+TIES poda parámetros poco importantes y resuelve conflictos para mejor resultado.",
        "dare_ties", "full",
    ),
]


class WeightRow(QWidget):
    """Fila de control de peso para un LoRA individual."""
    changed = Signal()

    def __init__(self, lora_name: str, initial_weight: float = 0.5, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        name_lbl = QLabel(lora_name[:30])
        name_lbl.setStyleSheet("color: #AAAACC; font-size: 11px;")
        name_lbl.setFixedWidth(160)
        layout.addWidget(name_lbl)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(initial_weight * 100))
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #2A0050; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #600DB5; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #600DB5; border-radius: 2px;
            }
        """)
        layout.addWidget(self.slider, 1)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(0.0, 2.0)
        self.spin.setSingleStep(0.05)
        self.spin.setDecimals(2)
        self.spin.setValue(initial_weight)
        self.spin.setFixedWidth(60)
        self.spin.setStyleSheet(
            "background: #1A0040; color: #54EFEA; border: 1px solid #3D1B7B; "
            "border-radius: 3px; padding: 2px;"
        )
        layout.addWidget(self.spin)

        # Sincronizar slider ↔ spinbox
        self.slider.valueChanged.connect(
            lambda v: (self.spin.blockSignals(True),
                       self.spin.setValue(v / 100),
                       self.spin.blockSignals(False),
                       self.changed.emit())
        )
        self.spin.valueChanged.connect(
            lambda v: (self.slider.blockSignals(True),
                       self.slider.setValue(int(v * 100)),
                       self.slider.blockSignals(False),
                       self.changed.emit())
        )

    def get_weight(self) -> float:
        return self.spin.value()

    def set_weight(self, w: float):
        self.spin.setValue(w)


class MergePanel(QWidget):
    """
    Panel derecho con configuración de merge, sugerencias y controles.
    """
    merge_requested = Signal(MergeConfig)

    def __init__(self, output_dir: str = "", parent=None):
        super().__init__(parent)
        self._loras: list[LoRAInfo] = []
        self._analyses: list[AnalysisResult] = []
        self._validation: ValidationResult | None = None
        self._suggestion: MergeSuggestion | None = None
        self._weight_rows: list[WeightRow] = []
        self._output_dir = output_dir
        self._build()

    # ── Construcción de UI ─────────────────────────────────────────────── #

    def _build(self):
        from PySide6.QtWidgets import QLineEdit
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Presets ─────────────────────────────────────────────────────── #
        preset_group = QGroupBox("Tipo de LoRA (preset)")
        preset_group.setStyleSheet(self._group_style())
        pg_outer = QVBoxLayout(preset_group)

        self.preset_desc = QLabel("Selecciona un preset para configurar automáticamente el método y las capas.")
        self.preset_desc.setWordWrap(True)
        self.preset_desc.setStyleSheet("color: #888aaa; font-size: 11px;")
        pg_outer.addWidget(self.preset_desc)

        # Botones de preset en grid 3 columnas
        from PySide6.QtWidgets import QGridLayout
        preset_grid = QGridLayout()
        preset_grid.setSpacing(4)
        self._preset_buttons: dict[str, QPushButton] = {}
        for idx, (pid, label, _, _, _) in enumerate(_PRESETS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(False)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1A0040; border: 1px solid #3D1B7B;
                    color: #AAAACC; border-radius: 4px; padding: 5px 6px;
                    font-size: 11px; text-align: center;
                }
                QPushButton:checked {
                    background: #2A005E; border-color: #EC00F0; color: #EC00F0;
                }
                QPushButton:hover:!checked { background: #220050; }
            """)
            btn.clicked.connect(lambda checked, p=pid: self._apply_preset(p))
            preset_grid.addWidget(btn, idx // 3, idx % 3)
            self._preset_buttons[pid] = btn
        pg_outer.addLayout(preset_grid)

        root.addWidget(preset_group)

        # ── Método ─────────────────────────────────────────────────────── #
        method_group = QGroupBox("Método de merge")
        method_group.setStyleSheet(self._group_style())
        mg_layout = QVBoxLayout(method_group)

        self.method_combo = QComboBox()
        for val, label in _METHODS:
            self.method_combo.addItem(label, userData=val)
        self.method_combo.setStyleSheet(self._combo_style())
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        mg_layout.addWidget(self.method_combo)

        self.method_desc = QLabel("")
        self.method_desc.setWordWrap(True)
        self.method_desc.setStyleSheet("color: #888aaa; font-size: 11px;")
        mg_layout.addWidget(self.method_desc)

        root.addWidget(method_group)

        # ── Parámetros avanzados (SLERP t, DARE density, TIES k) ───────── #
        self.params_group = QGroupBox("Parámetros avanzados")
        self.params_group.setStyleSheet(self._group_style())
        self.params_group.setVisible(False)
        pg_layout = QVBoxLayout(self.params_group)

        # SLERP t
        self.slerp_row = self._make_param_row(
            "Interpolación (t):", 0.0, 1.0, 0.5, 0.05,
            "0.0 = 100% LoRA 1 · 1.0 = 100% LoRA 2"
        )
        pg_layout.addLayout(self.slerp_row["layout"])

        # DARE density
        self.dare_row = self._make_param_row(
            "Densidad DARE:", 0.01, 1.0, 0.5, 0.05,
            "Fracción de parámetros a conservar (0.5 = 50%)"
        )
        pg_layout.addLayout(self.dare_row["layout"])

        # TIES k
        self.ties_row = self._make_param_row(
            "Umbral TIES (k):", 0.01, 1.0, 0.2, 0.05,
            "Top-k fracción por magnitud para sparsificación (0.2 = 20%)"
        )
        pg_layout.addLayout(self.ties_row["layout"])

        root.addWidget(self.params_group)

        # ── Pesos por LoRA ─────────────────────────────────────────────── #
        self.weights_group = QGroupBox("Pesos por LoRA")
        self.weights_group.setStyleSheet(self._group_style())
        self.weights_layout = QVBoxLayout(self.weights_group)

        self.no_loras_lbl = QLabel("Carga al menos 2 LoRAs para configurar los pesos.")
        self.no_loras_lbl.setStyleSheet("color: #666699; font-size: 11px;")
        self.weights_layout.addWidget(self.no_loras_lbl)

        root.addWidget(self.weights_group)

        # ── Filtro de capas ────────────────────────────────────────────── #
        filter_group = QGroupBox("Filtro de capas")
        filter_group.setStyleSheet(self._group_style())
        fg_layout = QVBoxLayout(filter_group)

        self.filter_combo = QComboBox()
        for val, label in _LAYER_FILTERS:
            self.filter_combo.addItem(label, userData=val)
        self.filter_combo.setStyleSheet(self._combo_style())
        fg_layout.addWidget(self.filter_combo)

        root.addWidget(filter_group)

        # ── Rank objetivo ──────────────────────────────────────────────── #
        rank_group = QGroupBox("Rank objetivo")
        rank_group.setStyleSheet(self._group_style())
        rg_layout = QHBoxLayout(rank_group)

        rg_layout.addWidget(QLabel("Rank:"))
        self.rank_spin = QSpinBox()
        self.rank_spin.setRange(1, 512)
        self.rank_spin.setValue(32)
        self.rank_spin.setStyleSheet(
            "background: #1A0040; color: #54EFEA; border: 1px solid #3D1B7B; "
            "border-radius: 3px; padding: 2px; min-width: 60px;"
        )
        rg_layout.addWidget(self.rank_spin)
        self.rank_hint = QLabel("(se detecta automáticamente)")
        self.rank_hint.setStyleSheet("color: #666699; font-size: 11px;")
        rg_layout.addWidget(self.rank_hint, 1)

        root.addWidget(rank_group)

        # ── Sugerencias ────────────────────────────────────────────────── #
        self.suggestion_group = QGroupBox("Sugerencia automática")
        self.suggestion_group.setStyleSheet(self._group_style())
        sg_layout = QVBoxLayout(self.suggestion_group)

        self.suggestion_text = QLabel("Carga los LoRAs para ver la sugerencia.")
        self.suggestion_text.setWordWrap(True)
        self.suggestion_text.setStyleSheet("color: #888aaa; font-size: 11px;")
        sg_layout.addWidget(self.suggestion_text)

        self.apply_suggestion_btn = QPushButton("Aplicar sugerencia")
        self.apply_suggestion_btn.setObjectName("outline_btn")
        self.apply_suggestion_btn.setEnabled(False)
        self.apply_suggestion_btn.clicked.connect(self._apply_suggestion)
        self.apply_suggestion_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: 1px solid #600DB5;
                color: #600DB5; border-radius: 4px; padding: 5px 10px;
                font-size: 12px;
            }
            QPushButton:hover { background: rgba(96,13,181,0.2); }
            QPushButton:disabled { border-color: #333355; color: #333355; }
        """)
        sg_layout.addWidget(self.apply_suggestion_btn)

        root.addWidget(self.suggestion_group)

        root.addStretch()

        # ── Salida + Merge ─────────────────────────────────────────────── #
        out_group = QGroupBox("Archivo de salida")
        out_group.setStyleSheet(self._group_style())
        og_layout = QVBoxLayout(out_group)

        # Carpeta de destino (fija, solo informativa)
        self.out_dir_lbl = QLabel(f"Carpeta: {self._output_dir or '(sin configurar)'}")
        self.out_dir_lbl.setStyleSheet("color: #555577; font-size: 10px;")
        self.out_dir_lbl.setWordWrap(True)
        og_layout.addWidget(self.out_dir_lbl)

        # Nombre del archivo
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nombre:"))
        self.output_name_edit = QLineEdit()
        self.output_name_edit.setPlaceholderText("mi_lora_mergeado")
        self.output_name_edit.setStyleSheet(
            "background: #1A0040; color: #54EFEA; border: 1px solid #3D1B7B; "
            "border-radius: 3px; padding: 4px 8px;"
        )
        self.output_name_edit.textChanged.connect(self._update_merge_btn)
        name_row.addWidget(self.output_name_edit, 1)
        ext_lbl = QLabel(".safetensors")
        ext_lbl.setStyleSheet("color: #555577; font-size: 11px;")
        name_row.addWidget(ext_lbl)
        og_layout.addLayout(name_row)

        # Dtype de salida
        dtype_row = QHBoxLayout()
        dtype_row.addWidget(QLabel("Formato:"))
        self.dtype_combo = QComboBox()
        for val, label in _OUTPUT_DTYPES:
            self.dtype_combo.addItem(label, userData=val)
        self.dtype_combo.setStyleSheet(self._combo_style())
        dtype_row.addWidget(self.dtype_combo, 1)
        og_layout.addLayout(dtype_row)

        root.addWidget(out_group)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: #1A0040; border: 1px solid #3D1B7B;
                border-radius: 4px; text-align: center; color: white; height: 18px;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #600DB5, stop:1 #EC00F0
                );
                border-radius: 4px;
            }
        """)
        root.addWidget(self.progress_bar)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #888aaa; font-size: 11px;")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status_lbl)

        self.merge_btn = QPushButton("MERGEAR")
        self.merge_btn.setEnabled(False)
        self.merge_btn.setFixedHeight(40)
        self.merge_btn.setStyleSheet("""
            QPushButton {
                background-color: #600DB5; color: white;
                font-weight: bold; font-size: 15px;
                border-radius: 5px; border: none;
            }
            QPushButton:hover { background-color: #7B1FD4; }
            QPushButton:disabled { background-color: #2A1A4A; color: #554466; }
        """)
        self.merge_btn.clicked.connect(self._on_merge_clicked)
        root.addWidget(self.merge_btn)

        self._on_method_changed()

        # Todos los controles que se bloquean durante el merge
        self._config_groups = [
            preset_group, method_group, self.params_group,
            self.weights_group, filter_group, rank_group,
            self.suggestion_group, out_group,
        ]

    # ── Helpers de estilo ──────────────────────────────────────────────── #

    def _group_style(self) -> str:
        return """
            QGroupBox {
                color: #888aaa; border: 1px solid #3D1B7B;
                border-radius: 5px; margin-top: 8px; padding-top: 8px;
                font-size: 11px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        """

    def _combo_style(self) -> str:
        return """
            QComboBox {
                background: #1A0040; color: #DDDDFF;
                border: 1px solid #3D1B7B; border-radius: 4px;
                padding: 4px 8px; font-size: 12px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1A0040; color: #DDDDFF;
                selection-background-color: #3D1B7B;
            }
        """

    def _make_param_row(
        self, label: str, min_v: float, max_v: float,
        default: float, step: float, tooltip: str
    ) -> dict:
        layout = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #AAAACC; font-size: 11px;")
        lbl.setFixedWidth(140)
        layout.addWidget(lbl)

        spin = QDoubleSpinBox()
        spin.setRange(min_v, max_v)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        spin.setValue(default)
        spin.setToolTip(tooltip)
        spin.setFixedWidth(70)
        spin.setStyleSheet(
            "background: #1A0040; color: #54EFEA; border: 1px solid #3D1B7B; "
            "border-radius: 3px; padding: 2px;"
        )
        layout.addWidget(spin)

        hint = QLabel(tooltip)
        hint.setStyleSheet("color: #555577; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint, 1)

        return {"layout": layout, "spin": spin}

    # ── Actualizar estado desde el widget principal ────────────────────── #

    def update_loras(
        self,
        loras: list[LoRAInfo],
        analyses: list[AnalysisResult],
        validation: ValidationResult | None,
        suggestion: MergeSuggestion | None,
    ):
        self._loras = loras
        self._analyses = analyses
        self._validation = validation
        self._suggestion = suggestion

        self._rebuild_weight_rows()
        self._update_suggestion_ui()
        self._update_rank_ui()
        self._update_merge_btn()

    def _rebuild_weight_rows(self):
        # Eliminar filas anteriores
        for row in self._weight_rows:
            row.setParent(None)
            row.deleteLater()
        self._weight_rows.clear()

        if len(self._loras) < 2:
            self.no_loras_lbl.setVisible(True)
            return

        self.no_loras_lbl.setVisible(False)

        n = len(self._loras)
        default_w = round(1.0 / n, 2)

        for lora in self._loras:
            import os
            name = os.path.basename(lora.path)
            row = WeightRow(name, default_w)
            row.changed.connect(self._update_merge_btn)
            self.weights_layout.addWidget(row)
            self._weight_rows.append(row)

    def _update_suggestion_ui(self):
        if not self._suggestion:
            self.suggestion_text.setText("Carga los LoRAs para ver la sugerencia.")
            self.apply_suggestion_btn.setEnabled(False)
            return

        s = self._suggestion
        text = f"<b>Método recomendado:</b> {s.method_label}<br><br>{s.explanation}"
        if s.warnings:
            text += "<br><br><b>Avisos:</b><br>" + "<br>".join(f"⚠ {w}" for w in s.warnings)
        self.suggestion_text.setText(text)
        self.suggestion_text.setTextFormat(Qt.RichText)
        self.apply_suggestion_btn.setEnabled(True)

    def _update_rank_ui(self):
        if self._validation and self._validation.suggested_target_rank:
            r = self._validation.suggested_target_rank
            self.rank_spin.setValue(r)
            self.rank_hint.setText(f"(detectado: {r})")
        else:
            self.rank_hint.setText("(se detecta automáticamente)")

    def set_output_dir(self, path: str):
        self._output_dir = path
        self.out_dir_lbl.setText(f"Carpeta: {path}")

    def _get_output_path(self) -> str:
        import os
        name = self.output_name_edit.text().strip()
        if not name:
            return ""
        if not name.endswith(".safetensors"):
            name += ".safetensors"
        return os.path.join(self._output_dir, name) if self._output_dir else name

    def _update_merge_btn(self):
        can_merge = (
            len(self._loras) >= 2
            and bool(self.output_name_edit.text().strip())
            and bool(self._output_dir)
            and (self._validation is None or self._validation.compatible)
        )
        self.merge_btn.setEnabled(can_merge)

    def _on_method_changed(self):
        method = self.method_combo.currentData()
        descriptions = {
            "weighted_sum": "Suma directa de los deltas de cada LoRA con los pesos dados. "
                            "Simple, predecible y funciona bien para la mayoría de casos.",
            "slerp":        "Interpolación esférica en el espacio de pesos. "
                            "Produce transiciones más suaves que la suma lineal. "
                            "Solo funciona con exactamente 2 LoRAs.",
            "ties":         "Resuelve conflictos de signo: solo suma parámetros donde "
                            "la mayoría de LoRAs coinciden en dirección. "
                            "Bueno para 3+ LoRAs con áreas de influencia solapadas.",
            "dare":         "Poda aleatoriamente parámetros poco importantes antes de sumar. "
                            "Reduce interferencias entre LoRAs.",
            "dare_ties":    "Combinación de DARE y TIES: primero poda, luego resuelve conflictos. "
                            "Mejor resultado general para múltiples LoRAs.",
        }
        self.method_desc.setText(descriptions.get(method, ""))

        # Mostrar/ocultar parámetros avanzados
        show_params = method in ("slerp", "dare", "dare_ties", "ties")
        self.params_group.setVisible(show_params)
        if show_params:
            # Mostrar/ocultar filas individuales
            self.slerp_row["spin"].parent().setVisible(False)
            self.dare_row["spin"].parent().setVisible(False)
            self.ties_row["spin"].parent().setVisible(False)

            if method == "slerp":
                self._set_row_visible(self.slerp_row, True)
            elif method == "dare":
                self._set_row_visible(self.dare_row, True)
            elif method == "ties":
                self._set_row_visible(self.ties_row, True)
            elif method == "dare_ties":
                self._set_row_visible(self.dare_row, True)
                self._set_row_visible(self.ties_row, True)

    def _set_row_visible(self, row_dict: dict, visible: bool):
        """Mostrar/ocultar todos los widgets de una fila de parámetro."""
        layout = row_dict["layout"]
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.widget():
                item.widget().setVisible(visible)

    def _apply_suggestion(self):
        if not self._suggestion:
            return
        s = self._suggestion
        # Aplicar método
        for i, (val, _) in enumerate(_METHODS):
            if val == s.method:
                self.method_combo.setCurrentIndex(i)
                break
        # Aplicar pesos
        for row, w in zip(self._weight_rows, s.weights):
            row.set_weight(w)
        # Aplicar layer filter
        for i, (val, _) in enumerate(_LAYER_FILTERS):
            if val == s.layer_filter:
                self.filter_combo.setCurrentIndex(i)
                break
        # Aplicar rank
        if s.target_rank:
            self.rank_spin.setValue(s.target_rank)

    def _apply_preset(self, preset_id: str):
        """Aplica la configuración de un preset y actualiza los botones."""
        # Desmarcar todos excepto el seleccionado
        for pid, btn in self._preset_buttons.items():
            btn.setChecked(pid == preset_id)

        preset = next((p for p in _PRESETS if p[0] == preset_id), None)
        if not preset:
            return
        _, label, desc, method, layer_filter = preset

        # Actualizar descripción
        self.preset_desc.setText(desc)

        # Aplicar método
        for i, (val, _) in enumerate(_METHODS):
            if val == method:
                self.method_combo.setCurrentIndex(i)
                break

        # Aplicar filtro de capas
        for i, (val, _) in enumerate(_LAYER_FILTERS):
            if val == layer_filter:
                self.filter_combo.setCurrentIndex(i)
                break

    # ── Merge ──────────────────────────────────────────────────────────── #

    def _on_merge_clicked(self):
        import os
        output_path = self._get_output_path()
        if not output_path:
            return
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        config = MergeConfig(
            method=self.method_combo.currentData(),
            weights=[row.get_weight() for row in self._weight_rows],
            target_rank=self.rank_spin.value(),
            layer_filter=self.filter_combo.currentData(),
            dare_density=self.dare_row["spin"].value(),
            ties_k=self.ties_row["spin"].value(),
            slerp_t=self.slerp_row["spin"].value(),
            output_path=output_path,
            output_dtype=self.dtype_combo.currentData(),
        )
        self.merge_requested.emit(config)

    # ── Progress UI (llamado desde el widget principal) ────────────────── #

    def set_merging(self, merging: bool):
        for g in self._config_groups:
            g.setEnabled(not merging)
        self.merge_btn.setEnabled(not merging)
        self.progress_bar.setVisible(merging)
        if merging:
            self.progress_bar.setValue(0)
            self.status_lbl.setText("Iniciando merge...")

    def set_progress(self, current: int, total: int, layer_name: str):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))
        self.status_lbl.setText(f"Procesando: {layer_name} ({current}/{total})")

    def set_merge_done(self, output_path: str):
        self.set_merging(False)
        self.status_lbl.setText(f"Merge completado: {output_path}")
        self.status_lbl.setStyleSheet("color: #54EFEA; font-size: 11px; font-weight: bold;")
        self._update_merge_btn()

    def set_merge_error(self, error: str):
        self.set_merging(False)
        self.status_lbl.setText(f"Error: {error}")
        self.status_lbl.setStyleSheet("color: #FF4444; font-size: 11px;")
        self._update_merge_btn()
