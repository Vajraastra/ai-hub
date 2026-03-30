"""
AI Hub — Paleta y stylesheet global de la interfaz PySide6.

Todas las constantes de color están definidas aquí como variables Python.
Los componentes deben importar estas constantes en lugar de hardcodear colores.
"""

# ── Paleta de colores ─────────────────────────────────────────────────────── #
BG_MAIN          = "#0F0023"   # background principal
BG_SURFACE       = "#1A0040"   # surface (topbar, cards)
BG_ELEVATED      = "#2A0A5E"   # surface elevada (selected/hover)
BG_CARD          = "#1E004E"   # fondo de tarjetas de app
BORDER_NEUTRAL   = "#3D1B7B"   # borde sutil

ACCENT_VIOLET        = "#600DB5"   # acción primaria (launch/install)
ACCENT_VIOLET_HOVER  = "#7B1FD4"   # hover de acción primaria
ACCENT_CYAN          = "#54EFEA"   # énfasis / running
ACCENT_CYAN_2        = "#51CCDC"   # acción secundaria / outline
ACCENT_PINK          = "#EC00F0"   # stop / warning / danger
ACCENT_PINK_HOVER    = "#FF33FF"

TEXT_PRIMARY   = "#e0e0ff"   # texto principal
TEXT_SECONDARY = "#888aaa"   # texto secundario
TEXT_META      = "#555588"   # texto terciario / meta

# ── Estado de apps — colores y etiquetas ─────────────────────────────────── #
STATUS_COLORS = {
    "running":      ACCENT_CYAN,
    "installed":    ACCENT_CYAN_2,
    "not_installed": TEXT_META,
    "launching":    ACCENT_PINK,
    "installing":   ACCENT_PINK,
    "updating":     ACCENT_PINK,
    "stopping":     ACCENT_PINK,
    "uninstalling": ACCENT_PINK,
}

STATUS_LABELS = {
    "running":      "● Corriendo",
    "installed":    "● Instalada",
    "not_installed": "○ No instalada",
    "launching":    "⏳ Iniciando...",
    "installing":   "⏳ Instalando...",
    "updating":     "⏳ Actualizando...",
    "stopping":     "⏳ Deteniendo...",
    "uninstalling": "⏳ Desinstalando...",
}

STYLESHEET = """
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
    QTabBar {
        background: #1A0040;
        border-bottom: 1px solid #3D1B7B;
    }
    QTabBar::tab {
        background: #1A0040;
        color: #888aaa;
        padding: 10px 22px;
        border: none;
        border-bottom: 3px solid transparent;
        font-size: 13px;
        font-weight: bold;
        margin-right: 1px;
        min-width: 100px;
    }
    QTabBar::tab:selected {
        background: #2A0A5E;
        color: #54EFEA;
        border-bottom: 3px solid #54EFEA;
    }
    QTabBar::tab:hover:!selected {
        background: #200850;
        color: #e0e0ff;
        border-bottom: 3px solid #3D1B7B;
    }

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

# Estilo reutilizable para QGroupBox en settings
GROUPBOX_STYLE = """
    QGroupBox {
        color: #54EFEA;
        font-size: 14px;
        font-weight: bold;
        border: 1px solid #3D1B7B;
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 12px;
    }
    QGroupBox::title { subcontrol-origin: margin; padding: 0 6px; }
"""
