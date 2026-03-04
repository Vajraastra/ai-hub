from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel

class SystemCard(QFrame):
    def __init__(self, sys_info: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(8)
        
        title = QLabel("🖥️ Información del Sistema")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #54EFEA;")
        layout.addWidget(title)

        gpu_name = sys_info.get("gpu_name", "Buscando...")
        cuda_tag = sys_info.get("cuda_tag", "N/A")
        disk_free = sys_info.get("disk_free", "N/D")

        lbl_style = "color: #e0e0ff; font-size: 13px;"
        val_style = "color: #54EFEA; font-weight: bold;"

        layout.addWidget(_info_row("GPU:", gpu_name, lbl_style, val_style))
        layout.addWidget(_info_row("CUDA Toolkit:", cuda_tag, lbl_style, val_style))
        layout.addWidget(_info_row("Espacio Libre:", disk_free, lbl_style, val_style))


def _info_row(label: str, value: str, lbl_style: str, val_style: str) -> QLabel:
    """Helper: returns a QLabel with label + value in cyberpunk colors."""
    lbl = QLabel(f"<span style='{lbl_style}'>{label}</span> "
                 f"<span style='{val_style}'>{value}</span>")
    lbl.setStyleSheet("font-size: 13px;")
    return lbl
