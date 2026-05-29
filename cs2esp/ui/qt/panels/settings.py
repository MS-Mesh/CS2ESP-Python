"""Application appearance, lifecycle, persistence, and diagnostics panel."""

from time import localtime, strftime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QLabel, QPushButton, QRadioButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from cs2esp.diagnostics import run_full_health_check
from cs2esp.display_info import get_display_refresh_hz
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import FeatureCard, LabeledRow, LabeledSlider, ToggleSwitch


_THEME_LABELS = {
    "midnight": "Midnight — high-contrast near-black",
    "slate": "Slate — balanced graphite (default)",
    "daylight": "Daylight — clean light",
}


class SettingsPanel(PanelBase):
    """Settings panel; on_theme_change is forwarded to MainWindow."""

    def __init__(self, config, on_theme_change=None, parent=None) -> None:
        super().__init__(config, parent)
        self._on_theme_change = on_theme_change
        self._toggles: list[ToggleSwitch] = []
        self._sliders: list[LabeledSlider] = []

        general_config = self.config.general
        self.h1("Settings")
        self.intro(
            "Tune the menu's appearance, launch behaviour and runtime cadence. "
            "Diagnostics remain read-only and are safe to copy into reports."
        )

        display_card = FeatureCard(
            "Appearance",
            help_text=(
                "Pick the palette the menu paints itself with. The "
                "theme picker and the sidebar pill share the same "
                "config field, so changes here are visible everywhere "
                "immediately."
            ),
            risk="safe",
        )
        self.add_widget(display_card)

        theme_row = QWidget(display_card)
        theme_row.setObjectName("ThemeOptions")
        theme_layout = QVBoxLayout(theme_row)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        theme_layout.setSpacing(6)

        theme_label = QLabel("Theme")
        theme_label.setObjectName("ThemeOptionsLabel")
        theme_layout.addWidget(theme_label)

        self._theme_group = QButtonGroup(self)
        cur_theme = str(getattr(general_config, "ui_theme", "midnight"))
        for name in ("midnight", "slate", "daylight"):
            rb = QRadioButton(_THEME_LABELS[name], theme_row)
            rb.setChecked(name == cur_theme)
            rb.toggled.connect(
                lambda checked, n=name: self._on_theme_pick(n, checked)
            )
            self._theme_group.addButton(rb)
            theme_layout.addWidget(rb)
        display_card.addBodyWidget(theme_row)

        scale_value = float(getattr(general_config, "ui_scale", 1.0) or 1.0)
        if scale_value < 0.5:
            scale_value = 1.0
        scale_slider = LabeledSlider(
            "UI scale",
            lo=0.5, hi=2.5, value=scale_value,
            decimals=2, step=0.05,
            help_text=(
                "Multiplier applied to every menu font size. 1.0 = "
                "baseline (auto). Useful on 4K monitors where Qt's "
                "built-in HiDPI scaling isn't aggressive enough for "
                "comfortable reading. Use the up/down arrows or type "
                "a value directly — the mouse wheel is intentionally "
                "disabled here so you can't bump it by mistake while "
                "scrolling through the panel."
            ),
        )
        scale_slider.setWheelEnabled(False)
        scale_slider.valueChanged.connect(self._on_scale_changed)
        self._track_slider(scale_slider)
        display_card.addBodyWidget(scale_slider)

        window_card = FeatureCard(
            "Window behaviour",
            help_text=(
                "Controls how the menu window behaves at launch. The "
                "menu lives in its own OS window with always-on-top "
                "set, so it appears above CS2 even in borderless-"
                "fullscreen mode — you don't have to alt-tab to "
                "see it."
            ),
            risk="safe",
        )
        self.add_widget(window_card)

        for label, attr, tip in [
            ("Auto-focus CS2 on launch",
             "focus_cs2_on_launch",
             "When the app starts, push CS2 to the foreground. "
             "<b>Off by default.</b> The previous default "
             "(on) was punting foreground to CS2 before the menu "
             "had a chance to paint, so the menu came up minimised. "
             "Flip back on if you want the game to snap forward "
             "automatically."),
            ("Hide terminal window",
             "hide_terminal",
             "The executable is built with a console "
             "attached, which on a double-click launch from Explorer "
             "spawns an empty <code>conhost.exe</code> window next "
             "to the menu. With this on, the launcher hides that "
             "window via <code>ShowWindow(SW_HIDE)</code>. <b>Safe "
             "guard:</b> if the .exe was launched from an already-"
             "open terminal (cmd, powershell, integrated "
             "terminal) we detect the shared console and skip the "
             "hide so your shell stays visible."),
            ("Start with menu visible",
             "start_menu_visible",
             "Show the menu on launch instead of waiting for the "
             "toggle hotkey. When off, the menu subprocess only "
             "spawns on the first hotkey press — saves "
             "\u2248200 MB of resident PySide6 RAM until you need it."),
            ("Open 'Show advanced' by default",
             "menu_show_advanced_default",
             "Pre-expand every card's collapsed extras section on "
             "every launch — distance bands editor, glow tuning, "
             "anti-detection details, etc. Off keeps the menu compact "
             "by default; you can still click the chevron on any card "
             "to expand it for the session."),
        ]:
            switch = ToggleSwitch(
                checked=bool(getattr(general_config, attr, False)),
                theme_name=cur_theme,
            )
            switch._live_section = general_config  # type: ignore[attr-defined]
            switch._live_attr = attr  # type: ignore[attr-defined]
            switch.toggled.connect(
                lambda checked, a=attr: self._set_attr(general_config, a, checked)
            )
            self._track_toggle(switch)
            window_card.addBodyWidget(LabeledRow(label, switch, help_text=tip))

        persist_card = FeatureCard(
            "Runtime & persistence",
            help_text=(
                "Autosave writes config.toml on every change after a "
                "small debounce window. Disable to test settings without "
                "committing them to disk."
            ),
            risk="safe",
        )
        self.add_widget(persist_card)

        autosave = ToggleSwitch(
            checked=bool(getattr(general_config, "autosave", True)),
            theme_name=cur_theme,
        )
        autosave.toggled.connect(
            lambda checked: self._set_attr(general_config, "autosave", checked)
        )
        self._track_toggle(autosave)
        persist_card.addBodyWidget(
            LabeledRow("Autosave config on change", autosave,
                       help_text="Writes config.toml after a debounce.")
        )

        autosave_int = LabeledSlider(
            "Autosave interval (s)",
            lo=0.1, hi=10.0,
            value=float(getattr(general_config, "autosave_interval_sec", 1.0)),
            decimals=1,
            help_text="Debounce window before the file is written.",
        )
        autosave_int.valueChanged.connect(
            lambda v: self._set_attr(
                general_config, "autosave_interval_sec", float(v)
            )
        )
        self._track_slider(autosave_int)
        persist_card.addBodyWidget(autosave_int)
        autosave.toggled.connect(
            lambda c: autosave_int.setVisible(bool(c)))
        autosave_int.setVisible(bool(getattr(general_config, "autosave", True)))

        try:
            refresh_hz_raw = float(get_display_refresh_hz())
            refresh_hz = int(round(refresh_hz_raw))
            fps_max = max(60, min(refresh_hz, 480))
        except Exception:
            refresh_hz_raw = 0.0
            fps_max = 144
        current_fps = int(getattr(general_config, "target_fps", 144))
        if current_fps > fps_max:
            current_fps = fps_max
        fps = LabeledSlider(
            f"Target FPS · detected panel: {fps_max} Hz",
            lo=30, hi=fps_max,
            value=current_fps,
            help_text=(
                "Caps the main loop (overlay + memory reads). "
                f"Detected panel refresh: <b>{fps_max} Hz</b>"
                + (f" ({refresh_hz_raw:.3f} Hz precise)" if refresh_hz_raw > 0 else "")
                + ". The slider's maximum is your display's "
                "nominal rate read at app launch via DWM "
                "composition timing — anything higher than "
                "that physically can't show, so we clamp it. A 60 "
                "Hz user sees a 60-max slider, a 144 Hz user 144, "
                "a 240 Hz user 240, a 360 Hz user 360. Re-launch "
                "the app if you switch displays mid-session so the "
                "slider re-detects."
            ),
        )
        fps.valueChanged.connect(
            lambda v: self._set_attr(general_config, "target_fps", int(v))
        )
        self._track_slider(fps)
        persist_card.addBodyWidget(fps)

        self._build_health_card()

        diag_card = FeatureCard(
            "Resolved configuration",
            help_text=(
                "Read-only dump of every config field the app has "
                "loaded. Useful when reporting bugs — copy this whole "
                "block into the issue. Refreshes live as you change "
                "values; click <b>Refresh</b> if a write happened "
                "outside the menu (e.g. you edited the TOML by hand)."
            ),
            risk="safe",
        )
        self.add_widget(diag_card)

        refresh_btn = QPushButton("Refresh", diag_card)
        refresh_btn.setObjectName("DiagRefreshBtn")
        refresh_btn.setFocusPolicy(Qt.NoFocus)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setToolTip(
            "Re-read every config section and repaint the dump."
        )
        diag_card.addHeaderWidget(refresh_btn)

        diag_lbl = QLabel("", diag_card)
        diag_lbl.setProperty("muted", "true")
        diag_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        diag_lbl.setWordWrap(True)
        diag_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        diag_lbl.setObjectName("DiagReadout")
        diag_card.addBodyWidget(diag_lbl)
        self._diag_lbl = diag_lbl

        refresh_btn.clicked.connect(self._refresh_diagnostics)
        self._refresh_diagnostics()

        self.end()

    def _build_health_card(self) -> None:
        """Health & verification card."""
        health_card = FeatureCard(
            "Health & verification",
            help_text=(
                "Attaches to <code>cs2.exe</code>, re-runs the live "
                "sigscan on <code>client.dll</code>, then probes each "
                "feature's memory dependencies. Click <b>Run "
                "diagnostics</b> to refresh — every row gets a colour-"
                "coded verdict:<br>"
                "&nbsp;&nbsp;<b style='color:#4ade80;'>PASS</b> &mdash; "
                "feature's preconditions are met<br>"
                "&nbsp;&nbsp;<b style='color:#fbbf24;'>WARN</b> &mdash; "
                "works, but a non-fatal issue was found<br>"
                "&nbsp;&nbsp;<b style='color:#f87171;'>FAIL</b> &mdash; "
                "feature will NOT function as configured<br>"
                "&nbsp;&nbsp;<b style='color:#94a3b8;'>SKIP</b> &mdash; "
                "disabled in config or no live data yet<br><br>"
                "Run this <b>before</b> a match, not during &mdash; "
                "the probe takes ~200&nbsp;ms and reads the entity "
                "list."
            ),
            risk="safe",
        )
        self.add_widget(health_card)

        health_btn = QPushButton("Run diagnostics", health_card)
        health_btn.setObjectName("DiagRefreshBtn")
        health_btn.setFocusPolicy(Qt.NoFocus)
        health_btn.setCursor(Qt.PointingHandCursor)
        health_btn.setToolTip(
            "Attach to cs2.exe and run the full live-verification probe."
        )
        health_card.addHeaderWidget(health_btn)
        self._health_btn = health_btn

        self._health_summary_lbl = QLabel(
            "<i>Click <b>Run diagnostics</b> to test the cheat against "
            "the currently running CS2.</i>",
            health_card,
        )
        self._health_summary_lbl.setObjectName("HealthSummary")
        self._health_summary_lbl.setTextFormat(Qt.RichText)
        self._health_summary_lbl.setWordWrap(True)
        health_card.addBodyWidget(self._health_summary_lbl)

        self._health_rows_lbl = QLabel("", health_card)
        self._health_rows_lbl.setObjectName("HealthRows")
        self._health_rows_lbl.setTextFormat(Qt.RichText)
        self._health_rows_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._health_rows_lbl.setWordWrap(True)
        self._health_rows_lbl.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        health_card.addBodyWidget(self._health_rows_lbl)

        health_btn.clicked.connect(self._run_health_check)

        self.set_cs2_attached(False)

    def set_cs2_attached(self, attached: bool) -> None:
        """Toggle the Health-card run button when CS2 attach state flips."""
        button = getattr(self, "_health_btn", None)
        if button is None:
            return
        button.setEnabled(bool(attached))
        if attached:
            button.setText("Run diagnostics")
            button.setToolTip(
                "Attach to cs2.exe and run the full live-verification probe."
            )
        else:
            button.setText("Launch CS2 first")
            button.setToolTip(
                "Disabled because the runtime hasn't detected "
                "cs2.exe yet — launch the game first."
            )

    def _set_attr(self, section, attr: str, value) -> None:
        """Write the new value through and let Config autosave."""
        try:
            setattr(section, attr, value)
        except Exception as e:
            print(f"[settings] set {attr}={value!r} failed: {e!r}")

    def _track_toggle(self, switch: ToggleSwitch) -> None:
        """Remember every ToggleSwitch so theme-change can re-skin them."""
        self._toggles.append(switch)

    def _track_slider(self, slider: LabeledSlider) -> None:
        """Remember every LabeledSlider so theme changes re-tint arrows."""
        self._sliders.append(slider)

    def _on_theme_pick(self, name: str, checked: bool) -> None:
        if not checked:
            return
        self._set_attr(self.config.general, "ui_theme", name)
        for switch in self._toggles:
            try:
                switch.apply_theme(name)
            except Exception:
                pass
        for slider in self._sliders:
            try:
                slider.apply_theme(name)
            except Exception:
                pass
        if callable(self._on_theme_change):
            self._on_theme_change(name)

    def _on_scale_changed(self, value: float) -> None:
        """Persist UI-scale + ask the window to rebuild QSS at the new scale."""
        self._set_attr(self.config.general, "ui_scale", float(value))
        win = self.window()
        if win is not None and hasattr(win, "apply_theme"):
            try:
                win.apply_theme(
                    str(getattr(self.config.general, "ui_theme", "midnight")),
                    float(value),
                )
            except Exception:
                pass

    def _refresh_diagnostics(self) -> None:
        """Walk every Config section and repaint the readout label."""
        if not hasattr(self, "_diag_lbl"):
            return

        config = self.config
        cfg_data = getattr(config, "_data", {}) or {}

        lines: list[str] = []
        top_scalars = [k for k, v in cfg_data.items()
                       if not isinstance(v, dict)]
        if top_scalars:
            lines.append("[root]")
            for k in sorted(top_scalars):
                lines.append(self._format_diag_pair(k, cfg_data[k]))
            lines.append("")

        section_names = sorted(k for k, v in cfg_data.items()
                               if isinstance(v, dict))
        for sname in section_names:
            sdata = cfg_data[sname] or {}
            lines.append(f"[{sname}]")
            scalars = [(k, v) for k, v in sdata.items()
                       if not isinstance(v, dict)]
            for k, v in sorted(scalars, key=lambda kv: kv[0]):
                lines.append(self._format_diag_pair(k, v))
            for sub_name in sorted(k for k, v in sdata.items()
                                   if isinstance(v, dict)):
                lines.append(f" [{sname}.{sub_name}]")
                sub = sdata[sub_name] or {}
                for k, v in sorted(sub.items()):
                    if isinstance(v, dict):
                        lines.append(f" {k:<26} = <dict, len={len(v)}>")
                    else:
                        lines.append(self._format_diag_pair(k, v))
            lines.append("")

        text = "\n".join(lines).rstrip()
        self._diag_lbl.setText(text)

    @staticmethod
    def _format_diag_pair(name: str, value: object) -> str:
        """One-line key = value formatter."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return f" {name:<28} = {value!r}"
        if isinstance(value, (list, tuple)):
            return (f" {name:<28} = "
                    f"<{type(value).__name__}, len={len(value)}>")

        if isinstance(value, dict):
            return f" {name:<28} = <dict, len={len(value)}>"
        return f" {name:<28} = <{type(value).__name__}>"

    def _run_health_check(self) -> None:
        """Attach to cs2.exe, run the diagnostic, render the report."""
        button = self.sender()
        if button is not None:
            button.setEnabled(False)
            button.setText("Probing…")
            QApplication.processEvents()

        try:
            report = run_full_health_check(self.config)
        except Exception as e:
            self._health_summary_lbl.setText(
                f"<b style='color:#f87171;'>FAIL</b> &mdash; "
                f"probe raised: <code>{e!r}</code>"
            )
            self._health_rows_lbl.setText("")
            if button is not None:
                button.setEnabled(True)
                button.setText("Run diagnostics")
            return

        tally = report.summary()
        ts = strftime("%H:%M:%S", localtime(report.timestamp))
        attach_blurb = (
            f"PID {report.cs2_pid}" if report.attached
            else "<span style='color:#f87171;'>not attached</span>"
        )
        summary_html = (
            f"<b>{ts}</b> &middot; cs2.exe {attach_blurb} &middot; "
            f"<b style='color:#4ade80;'>{tally['PASS']} PASS</b> &middot; "
            f"<b style='color:#fbbf24;'>{tally['WARN']} WARN</b> &middot; "
            f"<b style='color:#f87171;'>{tally['FAIL']} FAIL</b> &middot; "
            f"<b style='color:#94a3b8;'>{tally['SKIP']} SKIP</b>"
        )
        self._health_summary_lbl.setText(summary_html)

        rank = {"FAIL": 0, "WARN": 1, "PASS": 2, "SKIP": 3}
        colour = {
            "PASS": "#4ade80",
            "WARN": "#fbbf24",
            "FAIL": "#f87171",
            "SKIP": "#94a3b8",
        }
        lines: list[str] = []
        for cat in report.categories():
            cat_rows = [r for r in report.rows if r.category == cat]
            cat_rows.sort(key=lambda r: rank.get(r.status.value, 9))
            lines.append(
                f"<b style='color:#e2e8f0;'>[ {cat} ]</b>"
            )
            for r in cat_rows:
                badge = (
                    f"<b style='color:{colour[r.status.value]};'>"
                    f"{r.status.value:<4}</b>"
                )
                name = r.name
                if len(name) > 38:
                    name = name[:35] + "..."
                name_padded = name + ("&nbsp;" * (40 - len(name)))
                reason = (r.reason or "").replace("<", "&lt;").replace(
                    ">", "&gt;"
                )
                if len(reason) > 110:
                    reason = reason[:107] + "..."
                lines.append(
                    f"&nbsp;&nbsp;{badge}&nbsp;{name_padded}{reason}"
                )
            lines.append("")
        self._health_rows_lbl.setText("<br>".join(lines).rstrip())

        if button is not None:
            button.setEnabled(True)
            button.setText("Run diagnostics")

    def sync_theme_radios(self, name: str) -> None:
        """Sync the picker after the title-bar theme pill changes theme."""
        for button in self._theme_group.buttons():
            text = button.text().lower()
            should_check = text.startswith(name.lower())
            button.blockSignals(True)
            try:
                button.setChecked(should_check)
            finally:
                button.blockSignals(False)
        for switch in self._toggles:
            try:
                switch.apply_theme(name)
            except Exception:
                pass
        for slider in self._sliders:
            try:
                slider.apply_theme(name)
            except Exception:
                pass
