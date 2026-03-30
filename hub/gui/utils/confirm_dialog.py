"""
confirm_dialog.py — Diálogos de confirmación reutilizables para acciones
destructivas o irreversibles en el Hub.
"""
from PySide6.QtWidgets import QMessageBox


def confirm_update(parent, app_name: str) -> bool:
    """Retorna True si el usuario confirma la actualización."""
    reply = QMessageBox.question(
        parent,
        f"Actualizar {app_name}",
        f"¿Buscar y aplicar actualizaciones para <b>{app_name}</b>?<br><br>"
        f"La app será detenida si está corriendo.<br>"
        f"Los archivos locales no serán eliminados.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return reply == QMessageBox.Yes


def confirm_uninstall(parent, app_name: str, app_dir: str) -> bool:
    """Retorna True si el usuario confirma la desinstalación."""
    reply = QMessageBox.warning(
        parent,
        f"Desinstalar {app_name}",
        f"¿Desinstalar <b>{app_name}</b>?<br><br>"
        f"<b>Se eliminará permanentemente:</b><br>{app_dir}<br><br>"
        f"Los modelos en tu carpeta configurada NO serán afectados.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return reply == QMessageBox.Yes
