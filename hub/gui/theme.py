"""
AI Hub — Paleta y stylesheet global de la interfaz PySide6.

Colores corporativos:
    #0F0023  background principal
    #1A0040  surface (topbar, cards)
    #2A0A5E  surface elevada (selected)
    #3D1B7B  borde sutil
    #600DB5  violet — acción primaria
    #7B1FD4  violet hover
    #51CCDC  cyan — acción secundaria / outline
    #54EFEA  cyan brillante — texto de énfasis / running
    #EC00F0  phlox — stop / warning
    #e0e0ff  texto principal
    #888aaa  texto secundario
"""

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
    QTabBar::tab {
        background: #1A0040;
        color: #888aaa;
        padding: 8px 20px;
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
