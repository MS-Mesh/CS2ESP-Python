"""Entry point for the standalone Qt menu process."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from cs2esp.config import Config
from cs2esp.ui.qt.fonts import register_space_grotesk
from cs2esp.ui.qt.main_window import MainWindow


def run() -> int:
    """Launch the Qt menu and run its event loop until window close.

    Returns the application's exit code so __main__ can pass it to sys.exit().
    """
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication.instance() or QApplication(sys.argv)
    font_family = register_space_grotesk()
    if font_family:
        app_font = app.font()
        app_font.setFamily(font_family)
        app.setFont(app_font)
    app.setApplicationName("MS Mesh · CS2 ESP")
    app.setApplicationDisplayName("MS Mesh · CS2 ESP")
    app.setOrganizationName("MS Mesh")

    config = Config.load()
    win = MainWindow(config)
    win.show()

    try:
        ad = config.anti_detection
        if bool(getattr(ad, "window_class_masquerade_enabled", False)):
            from cs2esp.anti_detect import masquerade_qt_window
            target = getattr(
                ad, "window_class_masquerade_target", "Notepad")
            actual = masquerade_qt_window(win, target_class=target)
            if actual:
                print(f"[anti-det] window class masqueraded "
                      f"as {actual!r}")
            else:
                print(f"[anti-det] window class masquerade returned "
                      f"None (target={target!r}); class atom not "
                      f"swapped - see Spy++ to confirm")
    except Exception as e:
        print(f"[anti-det] window-class masquerade raised: {e!r}")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
