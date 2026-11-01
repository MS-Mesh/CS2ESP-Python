"""Entry point for the standalone Qt menu process."""

import sys

from cs2esp.crash_logging import configure_crash_logging


configure_crash_logging("menu")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from cs2esp.app_identity import APP_PNG, apply_window_icon, set_app_identity
from cs2esp.config import Config
from cs2esp.ui.qt.fonts import register_space_grotesk
from cs2esp.ui.qt.main_window import MainWindow


def run() -> int:
	'''Launch the Qt menu, run its event loop until window close, and return the application exit code.'''
	# PassThrough matches the shipped geometry; with Round the screen reports fractional logical pixels and the 1460x800 menu gets clamped down
	QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

	set_app_identity()
	app = QApplication.instance() or QApplication(sys.argv)
	app.setWindowIcon(QIcon(str(APP_PNG)))
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

	try:
		anti_detection = config.anti_detection
		if bool(getattr(anti_detection, "window_class_masquerade_enabled", False)):
			from cs2esp.anti_detect import masquerade_qt_window

			target = getattr(anti_detection, "window_class_masquerade_target", "Notepad")
			actual = masquerade_qt_window(win, target_class=target)
			if actual:
				print(f"[anti-det] native menu host class={actual!r}; embedded child retains its Qt class")
			else:
				print(f"[anti-det] native menu host unavailable (target={target!r}): {getattr(win, 'window_class_status', {})}")
	except Exception as e:
		print(f"[anti-det] window-class masquerade raised: {e!r}")

	host = getattr(win, "_window_class_host", None)
	if host is not None:
		app.aboutToQuit.connect(host.close)
	win.show()
	apply_window_icon(host.handle if host is not None else int(win.winId()))
	return app.exec()


if __name__ == "__main__":
	raise SystemExit(run())
