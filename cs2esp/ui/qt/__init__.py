"""PySide6 settings menu that runs in a separate process from main.py."""


def run() -> int:
    """Lazily launch the menu without loading every panel on package import."""
    from cs2esp.ui.qt.menu_app import run as launch_menu

    return launch_menu()

__all__ = ["run"]
