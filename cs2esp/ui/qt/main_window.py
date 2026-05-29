"""
Top-level Qt menu window with custom chrome, sidebar, panels, and footer.

MainWindow wraps the native settings panels, restores geometry, paints the
frameless shell, and keeps footer hotkey pills in sync. It also polls status.json
and config changes so runtime flips update the Qt controls without pretending to
be user edits.
"""

from PySide6.QtCore import (
    QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer,
)
from PySide6.QtGui import (
    QColor, QCursor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from cs2esp.ui.qt import theme as qtheme
from cs2esp.ui.qt.icons import HAS_ICONS, icon
from cs2esp.ui.qt.panels import (
    AntiDetectionPanel, AwarenessPanel, CombatPanel, ESPPanel,
    KeybindsPanel, NoRecoilPanel, RadarPanel, SettingsPanel,
)
from cs2esp.ipc import is_stale, read_status
from cs2esp.ui.qt.widgets.keybind import KeybindRow
from cs2esp.ui.qt.widgets.toggle import ToggleSwitch


_NAV: tuple[tuple[str, str, str], ...] = (
    ("Keybinds", "keybinds", "mdi.keyboard-outline"),
    ("ESP", "esp", "mdi.eye-outline"),
    ("Radar", "radar", "mdi.radar"),
    ("Awareness", "awareness", "mdi.alert-circle-outline"),
    ("Combat", "combat", "mdi.crosshairs"),
    ("Recoil scripts", "norecoil", "mdi.script-text-outline"),
    ("Anti-Detection", "anti_detection", "mdi.shield-outline"),
    ("Settings", "settings", "mdi.cog-outline"),
)


def _paint_nav_icon(key: str, color: str) -> QPixmap:
    """Draw a dependency-free approximation when qtawesome is unavailable."""
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if key == "keybinds":
        painter.drawRoundedRect(QRectF(1.5, 4, 15, 10), 1.5, 1.5)
        for x in (4, 7, 10, 13):
            painter.drawPoint(QPointF(x, 7))
        painter.drawLine(QPointF(4, 11), QPointF(14, 11))
    elif key == "esp":
        path = QPainterPath(QPointF(1.5, 9))
        path.cubicTo(5, 3.5, 13, 3.5, 16.5, 9)
        path.cubicTo(13, 14.5, 5, 14.5, 1.5, 9)
        painter.drawPath(path)
        painter.drawEllipse(QRectF(6.5, 6.5, 5, 5))
    elif key == "radar":
        painter.drawEllipse(QRectF(2, 2, 14, 14))
        painter.drawEllipse(QRectF(6, 6, 6, 6))
        painter.drawLine(QPointF(9, 9), QPointF(14, 4))
        painter.drawPoint(QPointF(13.5, 11.5))
    elif key == "awareness":
        painter.drawEllipse(QRectF(2, 2, 14, 14))
        painter.drawLine(QPointF(9, 5), QPointF(9, 10))
        painter.drawPoint(QPointF(9, 13))
    elif key == "combat":
        painter.drawEllipse(QRectF(5, 5, 8, 8))
        painter.drawLine(QPointF(9, 1), QPointF(9, 5))
        painter.drawLine(QPointF(9, 13), QPointF(9, 17))
        painter.drawLine(QPointF(1, 9), QPointF(5, 9))
        painter.drawLine(QPointF(13, 9), QPointF(17, 9))
    elif key == "norecoil":
        painter.drawLine(QPointF(9, 2), QPointF(9, 12))
        painter.drawLine(QPointF(5, 8), QPointF(9, 12))
        painter.drawLine(QPointF(13, 8), QPointF(9, 12))
        painter.drawLine(QPointF(3, 15), QPointF(15, 15))
    elif key == "anti_detection":
        path = QPainterPath(QPointF(9, 1.5))
        path.lineTo(15, 4)
        path.lineTo(14, 11)
        path.cubicTo(13.5, 14, 11, 16, 9, 16.5)
        path.cubicTo(7, 16, 4.5, 14, 4, 11)
        path.lineTo(3, 4)
        path.closeSubpath()
        painter.drawPath(path)
    else:
        painter.drawEllipse(QRectF(5, 5, 8, 8))
        for angle in range(0, 360, 45):
            painter.save()
            painter.translate(9, 9)
            painter.rotate(angle)
            painter.drawLine(QPointF(0, -6), QPointF(0, -8))
            painter.restore()
        painter.drawEllipse(QRectF(7.5, 7.5, 3, 3))

    painter.end()
    return pixmap


def _fallback_nav_icon(key: str, normal: str, selected: str) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_paint_nav_icon(key, normal), QIcon.Normal, QIcon.Off)
    icon.addPixmap(_paint_nav_icon(key, selected), QIcon.Selected, QIcon.Off)
    icon.addPixmap(_paint_nav_icon(key, selected), QIcon.Active, QIcon.Off)
    return icon


class MainWindow(QMainWindow):
    """Frameless menu window with sidebar + content stack."""

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.setObjectName("RootWindow")
        self.setWindowTitle("MS Mesh · CS2 ESP")

        g = config.general
        self._theme_name = str(getattr(g, "ui_theme", "slate"))
        scale_raw = float(getattr(g, "ui_scale", 1.0) or 1.0)
        self._ui_scale = min(2.5, max(0.5, scale_raw))
        self._metrics = qtheme.metrics(self._ui_scale)

        target_screen = self._target_screen()
        if target_screen is None:
            minimum_w, minimum_h = 960, 600
            default_w, default_h = 1100, 720
            maximum_w, maximum_h = 2560, 1440
        else:
            available = target_screen.availableGeometry()
            maximum_w = max(760, available.width() - 24)
            maximum_h = max(480, available.height() - 24)
            minimum_w = min(960, maximum_w)
            minimum_h = min(600, maximum_h)
            default_w = max(
                minimum_w, min(int(round(available.width() * 0.76)), 1680)
            )
            default_h = max(
                minimum_h, min(int(round(available.height() * 0.72)), 1000)
            )
        self.setMinimumSize(minimum_w, minimum_h)
        w = int(getattr(g, "menu_window_width", default_w) or default_w)
        h = int(getattr(g, "menu_window_height", default_h) or default_h)
        self.resize(
            min(maximum_w, max(minimum_w, w)),
            min(maximum_h, max(minimum_h, h)),
        )
        self._center_on_screen()

        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMouseTracking(True)

        self._resize_margin = self._metrics.resize_margin
        self._drag_pos: QPoint | None = None
        self._master_on = bool(getattr(g, "master_enabled", True))
        self._cs2_attached = False
        self._fps: float | None = None
        self._content_scrolls: list[QScrollArea] = []
        self._window_buttons: dict[str, QPushButton] = {}

        self._initial_panel_key = str(
            getattr(g, "last_panel_key", "settings") or "settings"
        )

        root = QWidget(self)
        root.setObjectName("RootContainer")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_titlebar())

        body = QWidget(root)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar(), 0)
        body_layout.addWidget(self._build_content(), 1)
        root_layout.addWidget(body, 1)
        root_layout.addWidget(self._build_footer())
        self.setCentralWidget(root)

        self.apply_theme(self._theme_name, self._ui_scale)
        for keybind in self.findChildren(KeybindRow):
            try:
                keybind.changed.connect(self.refresh_footer_hotkeys)
            except Exception:
                pass
        self.refresh_footer_hotkeys()

        self._geom_timer = QTimer(self)
        self._geom_timer.setSingleShot(True)
        self._geom_timer.setInterval(250)
        self._geom_timer.timeout.connect(self._save_geometry)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(500)
        self._autosave_timer.timeout.connect(self._autosave_pump)
        self._autosave_timer.start()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(250)
        self._status_timer.timeout.connect(self._status_pump)
        self._status_timer.start()

    def _autosave_pump(self) -> None:
        """Tick: ask the config to flush itself if anything mutated.

        Wrapped in a try/except so a transient OSError on the TOML
        write (e.g. file briefly locked by an editor) doesn't poison
        subsequent ticks.
        """
        try:
            self.config.autosave_if_due()
        except Exception as e:
            print(f"[menu] autosave pump failed: {e!r}")

    def _status_pump(self) -> None:
        """Refresh status labels, config reloads, and live-bound widgets."""
        reloaded = False
        try:
            reloaded = self.config.reload_if_changed()
        except Exception as e:
            print(f"[menu] status reload failed: {e!r}")
        new_master = bool(getattr(
            self.config.general, "master_enabled", True
        ))
        if new_master != self._master_on:
            self._master_on = new_master
            self._refresh_master_pill()
        if reloaded:
            try:
                self._refresh_live_widgets()
            except Exception as e:
                print(f"[menu] live-widget refresh failed: {e!r}")

        try:
            status = read_status()
            if status is None:
                self.set_fps(None)
                self.set_cs2_attached(False)
                return
            if is_stale(status):
                self.set_fps(None)
                self.set_cs2_attached(False)
                return
            self.set_fps(status.get("fps"))
            self.set_cs2_attached(bool(status.get("cs2_attached", False)))
        except Exception as e:
            print(f"[menu] status pump failed: {e!r}")

    def _build_titlebar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("TitleBar")
        self._titlebar = bar
        self._titlebar_layout = QHBoxLayout(bar)

        self._brand_mark = QLabel("M", bar)
        self._brand_mark.setObjectName("BrandMark")
        self._titlebar_layout.addWidget(
            self._brand_mark, 0, Qt.AlignVCenter
        )

        brand_copy = QWidget(bar)
        self._brand_copy = brand_copy
        brand_layout = QVBoxLayout(brand_copy)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)

        logo = QLabel("MS Mesh", brand_copy)
        logo.setObjectName("TitleBarLogo")
        brand_layout.addWidget(logo)

        subtitle = QLabel("External control center", brand_copy)
        subtitle.setObjectName("TitleBarSubtitle")
        brand_layout.addWidget(subtitle)
        self._titlebar_layout.addWidget(brand_copy, 0, Qt.AlignVCenter)

        self._titlebar_layout.addStretch(1)

        self._theme_pill = QPushButton(self)
        self._theme_pill.setObjectName("ThemePillBtn")
        self._theme_pill.setAccessibleName("Change colour theme")
        self._theme_pill.setFocusPolicy(Qt.StrongFocus)
        self._theme_pill.setCursor(Qt.PointingHandCursor)
        self._theme_pill.clicked.connect(self._cycle_theme)
        self._titlebar_layout.addWidget(
            self._theme_pill, 0, Qt.AlignVCenter
        )

        self._master_pill = QPushButton(self)
        self._master_pill.setObjectName("MasterPillBtn")
        self._master_pill.setAccessibleName("Toggle all features")
        self._master_pill.setFocusPolicy(Qt.StrongFocus)
        self._master_pill.setCursor(Qt.PointingHandCursor)
        self._master_pill.clicked.connect(self._toggle_master)
        self._titlebar_layout.addWidget(
            self._master_pill, 0, Qt.AlignVCenter
        )

        self._save_btn = QPushButton("Save", self)
        self._save_btn.setObjectName("SaveBtn")
        self._save_btn.setAccessibleName("Save settings now")
        self._save_btn.setFocusPolicy(Qt.StrongFocus)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setToolTip(
            "Save every pending setting immediately. Autosave normally "
            "does this in the background."
        )
        self._save_btn.clicked.connect(self._manual_save)
        self._titlebar_layout.addWidget(
            self._save_btn, 0, Qt.AlignVCenter
        )

        labels = {"min": "−", "max": "□", "close": "×"}
        tooltips = {
            "min": "Minimise",
            "max": "Maximise or restore",
            "close": "Close menu",
        }
        for role in ("min", "max", "close"):
            button = QPushButton(labels[role], bar)
            button.setObjectName("WindowControlBtn")
            button.setProperty("role", role)
            button.setAccessibleName(tooltips[role])
            button.setToolTip(tooltips[role])
            button.setFocusPolicy(Qt.StrongFocus)
            button.clicked.connect(lambda _=False, r=role: self._on_window_btn(r))
            self._window_buttons[role] = button
            self._titlebar_layout.addWidget(button, 0)

        self._refresh_theme_pill()
        self._refresh_master_pill()
        self._refresh_action_icons()
        return bar

    def _build_sidebar(self) -> QWidget:
        side = QWidget(self)
        side.setObjectName("Sidebar")
        self._sidebar = side
        self._sidebar_layout = QVBoxLayout(side)

        self._status_card = QFrame(side)
        self._status_card.setObjectName("SidebarStatusCard")
        status_layout = QHBoxLayout(self._status_card)
        self._status_layout = status_layout

        self._status_dot = QFrame(self._status_card)
        self._status_dot.setObjectName("StatusDot")
        self._status_dot.setProperty("active", "false")
        status_layout.addWidget(self._status_dot, 0, Qt.AlignTop)

        status_copy = QWidget(self._status_card)
        self._status_copy = status_copy
        status_copy_layout = QVBoxLayout(status_copy)
        status_copy_layout.setContentsMargins(0, 0, 0, 0)
        status_copy_layout.setSpacing(2)

        status_title = QLabel("CS2 connection", status_copy)
        status_title.setObjectName("SidebarStatusTitle")
        status_copy_layout.addWidget(status_title)

        self._attach_lbl = QLabel("Waiting for CS2", status_copy)
        self._attach_lbl.setObjectName("StatusDetached")
        status_copy_layout.addWidget(self._attach_lbl)
        status_layout.addWidget(status_copy, 1)

        self._fps_lbl = QLabel("— fps", self._status_card)
        self._fps_lbl.setObjectName("StatusFps")
        status_layout.addWidget(self._fps_lbl, 0, Qt.AlignTop)
        self._sidebar_layout.addWidget(self._status_card)

        nav_label = QLabel("Controls", side)
        nav_label.setObjectName("SidebarLabel")
        self._sidebar_label = nav_label
        self._sidebar_layout.addWidget(nav_label)

        self._nav_list = QListWidget(side)
        self._nav_list.setObjectName("SidebarList")
        self._nav_list.setFocusPolicy(Qt.NoFocus)
        self._nav_list.setFrameShape(QListWidget.NoFrame)
        self._nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._nav_list.setTextElideMode(Qt.ElideRight)
        self._nav_list.setIconSize(QSize(
            self._metrics.nav_icon_size, self._metrics.nav_icon_size
        ))
        for label, key, icon_spec in _NAV:
            item = QListWidgetItem(f"  {label}")
            item.setData(Qt.UserRole, key)
            item.setData(Qt.UserRole + 1, icon_spec)
            self._nav_list.addItem(item)
        self._nav_list.currentRowChanged.connect(self._on_nav_change)
        self._sidebar_layout.addWidget(self._nav_list, 1)

        return side

    def _build_content(self) -> QWidget:
        self._stack = QStackedWidget(self)
        self._stack.setObjectName("ContentStack")
        self._panels_by_key: dict[str, QWidget] = {
            "keybinds": KeybindsPanel(self.config),
            "esp": ESPPanel(self.config),
            "radar": RadarPanel(self.config),
            "awareness": AwarenessPanel(self.config),
            "combat": CombatPanel(self.config),
            "norecoil": NoRecoilPanel(self.config),
            "anti_detection": AntiDetectionPanel(self.config),
            "settings": SettingsPanel(
                self.config, on_theme_change=self.apply_theme
            ),
        }
        for _label, key, _icon_spec in _NAV:
            panel = self._panels_by_key[key]
            panel.setProperty("nav_key", key)
            scroll = QScrollArea(self._stack)
            scroll.setObjectName("ContentScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setProperty("nav_key", key)
            panel.setMaximumWidth(self._metrics.content_max_width)
            scroll.setWidget(panel)
            scroll.setProperty("panel_ref", id(panel))
            self._content_scrolls.append(scroll)
            self._stack.addWidget(scroll)

        target = self._initial_panel_key
        for i in range(self._stack.count()):
            if self._stack.widget(i).property("nav_key") == target:
                self._nav_list.setCurrentRow(i)
                break
        else:
            for i in range(self._stack.count()):
                if self._stack.widget(i).property("nav_key") == "settings":
                    self._nav_list.setCurrentRow(i)
                    break

        return self._stack

    def _build_footer(self) -> QWidget:
        """Bottom status bar with live hotkey pills."""
        foot = QWidget(self)
        foot.setObjectName("Footer")
        self._footer = foot
        self._footer_layout = QHBoxLayout(foot)

        self._footer_pills: list[tuple[QWidget, QLabel, QLabel, str, str]] = []
        pill_specs = [
            ("toggle_menu_key", "menu"),
            ("master_toggle_key", "master"),
            ("exit_key", "exit"),
        ]
        for attr, text in pill_specs:
            pill = QWidget(foot)
            pill.setObjectName("FooterPill")
            pill_layout = QHBoxLayout(pill)
            pill_layout.setContentsMargins(0, 0, 0, 0)
            pill_layout.setSpacing(6)

            key_label = QLabel(pill)
            key_label.setObjectName("FooterKey")
            pill_layout.addWidget(key_label, 0, Qt.AlignVCenter)

            text_label = QLabel(text, pill)
            text_label.setObjectName("FooterText")
            pill_layout.addWidget(text_label, 0, Qt.AlignVCenter)

            self._footer_layout.addWidget(pill, 0, Qt.AlignVCenter)
            self._footer_pills.append((pill, key_label, text_label, attr, text))

        self._footer_layout.addStretch(1)

        version = QLabel("MS Mesh · research build")
        version.setObjectName("FooterText")
        self._version_label = version
        self._footer_layout.addWidget(version, 0, Qt.AlignVCenter)

        self.refresh_footer_hotkeys()
        return foot

    def refresh_footer_hotkeys(self) -> None:
        """Re-read app-control keys from config and update each pill.

        Called every time any :class:`KeybindRow` commits a new value
        (regardless of which key changed — repainting all three is a
        few microseconds and saves an attr-to-pill mapping).
        """
        if not hasattr(self, "_footer_pills"):
            return
        g = getattr(self.config, "general", None)
        if g is None:
            return
        for pill, key_lbl, _txt_lbl, attr, descriptor in self._footer_pills:
            raw = str(getattr(g, attr, "") or "")
            display = raw if raw else "—"
            key_lbl.setText(display)
            pill.setToolTip(f"{display} — {descriptor}")

    THEME_ORDER = ("midnight", "slate", "daylight")

    def apply_theme(self, name: str,
                    scale: float | None = None) -> None:
        """Re-skin the window for name, optionally changing UI scale."""
        self._theme_name = name if name in qtheme.PALETTES else "slate"
        if scale is not None:
            self._ui_scale = min(2.5, max(0.5, float(scale)))
        self._metrics = qtheme.metrics(self._ui_scale)
        self.setStyleSheet(qtheme.stylesheet(
            self._theme_name, self._ui_scale
        ))
        self._apply_metrics()
        self._refresh_theme_pill()
        self._refresh_master_pill()
        self._refresh_status_labels()
        self._refresh_nav_icons()
        self._refresh_action_icons()
        for widget in self.findChildren(QWidget):
            hook = getattr(widget, "apply_theme", None)
            if not callable(hook):
                continue
            try:
                hook(self._theme_name)
            except Exception:
                continue
        self._repolish(self)

    def _apply_metrics(self) -> None:
        """Apply scale-aware geometry to the shell without rebuilding it."""
        m = self._metrics
        density = m.density

        if hasattr(self, "_titlebar"):
            self._titlebar.setFixedHeight(m.titlebar_height)
            self._titlebar_layout.setContentsMargins(
                round(18 * density), 0, round(6 * density), 0
            )
            self._titlebar_layout.setSpacing(round(8 * density))
            brand_size = round(34 * density)
            self._brand_mark.setFixedSize(brand_size, brand_size)
            self._theme_pill.setFixedSize(
                round(118 * density), m.header_button_height
            )
            self._master_pill.setFixedSize(
                round(126 * density), m.header_button_height
            )
            self._save_btn.setFixedSize(
                round(82 * density), m.header_button_height
            )
            for button in self._window_buttons.values():
                button.setFixedSize(
                    m.window_button_width, m.titlebar_height
                )

        if hasattr(self, "_sidebar"):
            self._sidebar.setFixedWidth(m.sidebar_width)
            inset = round(10 * density)
            self._sidebar_layout.setContentsMargins(
                inset, round(12 * density), inset, round(10 * density)
            )
            self._sidebar_layout.setSpacing(round(8 * density))
            if hasattr(self, "_status_layout"):
                self._status_layout.setContentsMargins(
                    round(12 * density), round(11 * density),
                    round(12 * density), round(11 * density),
                )
                self._status_layout.setSpacing(round(9 * density))
            dot_size = max(7, round(8 * density))
            self._status_dot.setFixedSize(dot_size, dot_size)
            self._fps_lbl.setVisible(self._ui_scale < 1.8)
            self._nav_list.setIconSize(QSize(
                m.nav_icon_size, m.nav_icon_size
            ))

        if hasattr(self, "_footer"):
            self._footer.setFixedHeight(m.footer_height)
            self._footer_layout.setContentsMargins(
                round(16 * density), 0, round(16 * density), 0
            )
            self._footer_layout.setSpacing(round(18 * density))

        for panel in getattr(self, "_panels_by_key", {}).values():
            panel.setMaximumWidth(m.content_max_width)
            panel_layout = getattr(panel, "layout", None)
            if isinstance(panel_layout, QVBoxLayout):
                panel_layout.setContentsMargins(
                    round(28 * density), round(24 * density),
                    round(28 * density), round(24 * density),
                )
                panel_layout.setSpacing(round(8 * density))
            for label in panel.findChildren(QLabel):
                if label.objectName() in ("PanelIntro", "SectionIntro"):
                    label.setMaximumWidth(round(760 * density))
        self._resize_margin = m.resize_margin
        self._update_responsive_shell()

    def _update_responsive_shell(self) -> None:
        """Keep the shell usable when high UI scale meets a narrow window."""
        if not hasattr(self, "_sidebar"):
            return
        compact_limit = round(760 + 120 * self._ui_scale)
        compact = self.width() < compact_limit
        density = self._metrics.density

        self._brand_copy.setVisible(not compact)
        self._status_copy.setVisible(not compact)
        self._sidebar_label.setVisible(not compact)
        self._version_label.setVisible(not compact)
        self._fps_lbl.setVisible(not compact and self._ui_scale < 1.8)

        if compact:
            compact_sidebar_width = max(
                round(64 * density),
                self._metrics.nav_icon_size + round(44 * density),
            )
            self._sidebar.setFixedWidth(compact_sidebar_width)
        else:
            self._sidebar.setFixedWidth(self._metrics.sidebar_width)

        for row, (label, _key, _icon_spec) in enumerate(_NAV):
            item = self._nav_list.item(row)
            item.setText("" if compact else f"  {label}")
            item.setToolTip(label)
            item.setData(Qt.AccessibleTextRole, label)

        for pill, key_label, text_label, _attr, descriptor in (
                self._footer_pills):
            text_label.setVisible(not compact)
            pill.setToolTip(f"{key_label.text()} — {descriptor}")

        horizontal_policy = (
            Qt.ScrollBarAsNeeded
            if compact or self._ui_scale >= 1.8
            else Qt.ScrollBarAlwaysOff
        )
        for scroll in self._content_scrolls:
            scroll.setHorizontalScrollBarPolicy(horizontal_policy)

    def _cycle_theme(self) -> None:
        """Click handler on the title-bar theme pill."""
        try:
            idx = self.THEME_ORDER.index(self._theme_name)
        except ValueError:
            idx = 0
        new = self.THEME_ORDER[(idx + 1) % len(self.THEME_ORDER)]
        try:
            self.config.general.ui_theme = new
        except Exception:
            pass
        try:
            panel = self._find_panel("settings")
            if panel is not None and hasattr(panel, "sync_theme_radios"):
                panel.sync_theme_radios(new)
        except Exception:
            pass
        self.apply_theme(new)

    def _toggle_master(self) -> None:
        """Click handler on the title-bar MASTER pill.

        ``Section.__setattr__`` now immediate-saves any
        ``bool`` write, so assigning ``master_enabled`` flushes to
        disk for free \u2014 the runtime's per-frame mtime poll picks
        it up within one display frame. No explicit ``save()`` call
        needed.
        """
        self._master_on = not self._master_on
        try:
            self.config.general.master_enabled = bool(self._master_on)
        except Exception as e:
            print(f"[menu] master toggle failed: {e!r}")
        self._refresh_master_pill()

    def _refresh_live_widgets(self) -> None:
        """Re-sync every live-bound switch + keybind row from config.

        Walks the entire widget tree once per reload tick (~250 ms).
        Only widgets tagged with ``_live_section`` / ``_live_attr``
        get touched, so untagged widgets in third-party panels are
        invisible to this pass and keep whatever local state they had.

        ``blockSignals`` is essential: setting ``isChecked`` on a
        ToggleSwitch fires its ``toggled`` signal, which the bound
        ``_on_toggled`` lambda would interpret as a USER edit and
        write back to the same field. With block enabled the refresh
        is a one-way reflection, not a loop.
        """
        for sw in self.findChildren(ToggleSwitch):
            sec = getattr(sw, "_live_section", None)
            attr = getattr(sw, "_live_attr", None)
            if sec is None or not attr:
                continue
            try:
                new_val = bool(getattr(sec, attr, False))
            except Exception:
                continue
            if sw.isChecked() == new_val:
                continue
            blocked = sw.blockSignals(True)
            try:
                sw.setChecked(new_val)
                sw._set_position(1.0 if new_val else 0.0)
            finally:
                sw.blockSignals(blocked)

        for kb in self.findChildren(KeybindRow):
            try:
                kb.refresh()
            except Exception:
                pass

    def _manual_save(self) -> None:
        """Flush pending settings and acknowledge the checkpoint."""
        try:
            ok = bool(self.config.save())
        except Exception as e:
            print(f"[menu] manual save failed: {e!r}")
            ok = False
        prev = self._save_btn.text()
        self._save_btn.setText("Saved" if ok else "Retry")
        self._save_btn.setProperty("saved", "true" if ok else "false")
        self._repolish(self._save_btn)
        QTimer.singleShot(800, lambda: self._clear_save_feedback(prev))

    def _clear_save_feedback(self, text: str) -> None:
        if not hasattr(self, "_save_btn"):
            return
        self._save_btn.setText(text)
        self._save_btn.setProperty("saved", "false")
        self._repolish(self._save_btn)

    def _repolish(self, w: QWidget) -> None:
        try:
            w.style().unpolish(w)
            w.style().polish(w)
        except Exception:
            pass
        for child in w.findChildren(QWidget):
            try:
                child.style().unpolish(child)
                child.style().polish(child)
            except Exception:
                pass

    def _refresh_action_icons(self) -> None:
        """Retint vector actions for the active palette."""
        if not HAS_ICONS:
            return
        palette = qtheme.PALETTES.get(
            self._theme_name, qtheme.PALETTES["slate"]
        )
        icon_size = max(14, round(16 * self._metrics.density))

        def set_icon(button: QPushButton, spec: str, color: str) -> bool:
            try:
                button.setIcon(icon(
                    spec,
                    color=color,
                    color_active=palette["text"],
                    color_selected=palette["text"],
                ))
                button.setIconSize(QSize(icon_size, icon_size))
                return True
            except Exception:
                return False

        if hasattr(self, "_save_btn"):
            set_icon(
                self._save_btn, "mdi.content-save-outline",
                palette["text_dim"],
            )
        if hasattr(self, "_master_pill"):
            master_color = (
                palette["safe"] if self._master_on else palette["danger"]
            )
            set_icon(self._master_pill, "mdi.power", master_color)

        specs = {
            "min": "mdi.window-minimize",
            "max": "mdi.window-maximize",
            "close": "mdi.close",
        }
        for role, button in self._window_buttons.items():
            if set_icon(button, specs[role], palette["text_mute"]):
                button.setText("")

        try:
            self.setWindowIcon(icon(
                "mdi.vector-square", color=palette["accent"]
            ))
        except Exception:
            pass

    def _refresh_theme_pill(self) -> None:
        """Repaint the swatch-dot icon + label on the theme pill."""
        if not hasattr(self, "_theme_pill"):
            return
        palette = qtheme.PALETTES.get(
            self._theme_name, qtheme.PALETTES["slate"]
        )
        dot_color = QColor(palette["theme_dot"])
        ring_color = QColor(palette["border"])
        icon_size = max(12, round(14 * self._metrics.density))
        pm = QPixmap(icon_size, icon_size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(ring_color)
        painter.setBrush(dot_color)
        painter.drawEllipse(1, 1, icon_size - 3, icon_size - 3)
        painter.end()
        self._theme_pill.setIcon(QIcon(pm))
        self._theme_pill.setIconSize(QSize(icon_size, icon_size))
        self._theme_pill.setText(self._theme_name.title())
        self._theme_pill.setToolTip(
            f"Theme: {self._theme_name.title()}. Click to cycle."
        )

    def _refresh_master_pill(self) -> None:
        if not hasattr(self, "_master_pill"):
            return
        on = bool(self._master_on)
        self._master_pill.setText("Master on" if on else "Master off")
        self._master_pill.setProperty("active", "true" if on else "false")
        self._master_pill.style().unpolish(self._master_pill)
        self._master_pill.style().polish(self._master_pill)
        self._refresh_action_icons()
        self._master_pill.setToolTip(
            "Master switch — all features. "
            f"Currently {'enabled' if on else 'disabled'}. Click to toggle."
        )

    def _refresh_status_labels(self) -> None:
        """Update the sidebar's connection and FPS readout."""
        if hasattr(self, "_attach_lbl"):
            if self._cs2_attached:
                self._attach_lbl.setText("Attached")
                self._attach_lbl.setObjectName("StatusAttached")
            else:
                self._attach_lbl.setText("Waiting for CS2")
                self._attach_lbl.setObjectName("StatusDetached")
            self._attach_lbl.style().unpolish(self._attach_lbl)
            self._attach_lbl.style().polish(self._attach_lbl)
        if hasattr(self, "_status_dot"):
            self._status_dot.setProperty(
                "active", "true" if self._cs2_attached else "false"
            )
            self._status_dot.style().unpolish(self._status_dot)
            self._status_dot.style().polish(self._status_dot)
        if hasattr(self, "_fps_lbl"):
            if self._fps is None:
                self._fps_lbl.setText("— fps")
            else:
                self._fps_lbl.setText(f"{int(round(self._fps))} fps")
        if hasattr(self, "_status_card"):
            attach_text = "Attached" if self._cs2_attached else "Waiting for CS2"
            fps_text = self._fps_lbl.text() if hasattr(self, "_fps_lbl") else ""
            self._status_card.setToolTip(
                f"CS2 connection: {attach_text}. {fps_text}".strip()
            )

    def _refresh_nav_icons(self) -> None:
        """Re-tint every sidebar icon, using local vectors as a fallback."""
        if not hasattr(self, "_nav_list"):
            return
        palette = qtheme.PALETTES.get(
            self._theme_name, qtheme.PALETTES["midnight"]
        )
        normal = palette["text_dim"]
        active = palette["accent"]
        for i in range(self._nav_list.count()):
            item = self._nav_list.item(i)
            spec = str(item.data(Qt.UserRole + 1) or "")
            key = str(item.data(Qt.UserRole) or "settings")
            if spec.startswith("emoji:"):
                item.setIcon(QIcon())
                continue
            icon = None
            if HAS_ICONS and spec:
                try:
                    nav_icon = icon(
                        spec,
                        color=normal,
                        color_active=active,
                        color_selected=active,
                    )
                except Exception:
                    nav_icon = None
            else:
                nav_icon = None
            item.setIcon(
                nav_icon or _fallback_nav_icon(key, normal, active)
            )

    def _find_panel(self, key: str) -> QWidget | None:
        """Return the *inner panel* (SettingsPanel etc.) for ``key``.

        The stack now holds QScrollArea wrappers, not the panels
        themselves — use the lookup table populated in
        ``_build_content`` so callers (e.g. ``_cycle_theme`` which
        needs ``sync_theme_radios``) get the actual panel widget.
        """
        return getattr(self, "_panels_by_key", {}).get(key)

    def set_cs2_attached(self, attached: bool) -> None:
        """Update the sidebar attach indicator and Settings diagnostics button."""
        if self._cs2_attached == bool(attached):
            return
        self._cs2_attached = bool(attached)
        self._refresh_status_labels()
        try:
            settings_panel = self._find_panel("settings")
            if settings_panel is not None and hasattr(
                settings_panel, "set_cs2_attached"
            ):
                settings_panel.set_cs2_attached(bool(attached))
        except Exception:
            pass

    def set_fps(self, fps: float | None) -> None:
        """Update the sidebar FPS gauge. Pass ``None`` to reset."""
        self._fps = None if fps is None else float(fps)
        self._refresh_status_labels()

    def _on_nav_change(self, row: int) -> None:
        if 0 <= row < self._stack.count():
            self._stack.setCurrentIndex(row)
            key = self._stack.widget(row).property("nav_key")
            if key:
                try:
                    self.config.general.last_panel_key = str(key)
                except Exception:
                    pass

    def _on_window_btn(self, role: str) -> None:
        if role == "min":
            self.showMinimized()
        elif role == "max":
            if self.isMaximized():
                self.showNormal()
                w = int(getattr(self.config.general,
                                "menu_pre_max_width", 0) or 0)
                h = int(getattr(self.config.general,
                                "menu_pre_max_height", 0) or 0)
                if w >= 960 and h >= 600:
                    self.resize(w, h)
            else:
                try:
                    self.config.general.menu_pre_max_width = int(self.width())
                    self.config.general.menu_pre_max_height = int(self.height())
                except Exception:
                    pass
                self.showMaximized()
        elif role == "close":
            self.close()

    def _hit_test_edge(self, pos: QPoint) -> Qt.Edges:
        """Which window edge (if any) is ``pos`` near?

        Returns a bitmask of :class:`~PySide6.QtCore.Qt.Edges`. The
        empty value means "not on any edge" — used as the trigger to
        fall back to title-bar drag-to-move.
        """
        if self.isMaximized():
            return Qt.Edges()
        m = self._resize_margin
        edges = Qt.Edges()
        if pos.x() <= m:
            edges |= Qt.LeftEdge
        elif pos.x() >= self.width() - m:
            edges |= Qt.RightEdge
        if pos.y() <= m:
            edges |= Qt.TopEdge
        elif pos.y() >= self.height() - m:
            edges |= Qt.BottomEdge
        return edges

    def mousePressEvent(self, event) -> None: # noqa: N802
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            edges = self._hit_test_edge(pos)
            if edges:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edges)
                    event.accept()
                    return
            titlebar_height = (
                self._titlebar.height() if hasattr(self, "_titlebar") else 56
            )
            if self._resize_margin < pos.y() < titlebar_height:
                self._drag_pos = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None: # noqa: N802
        if self._drag_pos is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        if not (event.buttons() & Qt.LeftButton):
            edges = self._hit_test_edge(event.position().toPoint())
            if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
                cursor = Qt.SizeFDiagCursor
            elif edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
                cursor = Qt.SizeBDiagCursor
            elif edges & (Qt.LeftEdge | Qt.RightEdge):
                cursor = Qt.SizeHorCursor
            elif edges & (Qt.TopEdge | Qt.BottomEdge):
                cursor = Qt.SizeVerCursor
            else:
                cursor = Qt.ArrowCursor
            self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None: # noqa: N802
        self._drag_pos = None
        self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None: # noqa: N802
        try:
            self.setCursor(Qt.ArrowCursor)
        except Exception:
            pass
        super().leaveEvent(event)

    def changeEvent(self, event) -> None: # noqa: N802
        try:
            if event.type() == QEvent.ActivationChange:
                if not self.isActiveWindow():
                    self.setCursor(Qt.ArrowCursor)
        except Exception:
            pass
        super().changeEvent(event)

    def resizeEvent(self, event) -> None: # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()
        self._update_responsive_shell()

    def showEvent(self, event) -> None: # noqa: N802
        """Re-center over CS2's monitor each time the menu is shown.

        The launcher shows/hides this window from the overlay process, so
        we re-home it on every show — otherwise it would stay wherever it
        was last dragged, which may be off the game's display.
        """
        super().showEvent(event)
        if not (self.isMaximized() or self.isFullScreen()):
            self._center_on_screen()

    def _save_geometry(self) -> None:
        if self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return
        g = self.config.general
        try:
            g.menu_window_width = int(self.width())
            g.menu_window_height = int(self.height())
        except Exception:
            pass

    def _target_screen(self):
        """Pick the screen to center/size the menu on.

        Prefers the screen holding CS2 (its monitor rect is published in
        status.json by the overlay process), so the menu always opens on
        the game's display. Falls back to the screen under the cursor,
        then the primary screen.
        """
        try:
            status = read_status()
            if status and not is_stale(status):
                rect = status.get("cs2_monitor")
                if rect and len(rect) == 4:
                    cx = int(rect[0]) + int(rect[2]) // 2
                    cy = int(rect[1]) + int(rect[3]) // 2
                    screen = QGuiApplication.screenAt(QPoint(cx, cy))
                    if screen is not None:
                        return screen
        except Exception:
            pass
        try:
            screen = QGuiApplication.screenAt(QCursor.pos())
            if screen is not None:
                return screen
        except Exception:
            pass
        return QGuiApplication.primaryScreen()

    def _center_on_screen(self) -> None:
        screen = self._target_screen()
        if screen is None:
            return
        cr = screen.availableGeometry()
        fg = self.frameGeometry()
        fg.moveCenter(cr.center())
        self.move(fg.topLeft())
