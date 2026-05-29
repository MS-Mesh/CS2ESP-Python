"""Anti-detection settings panel for overlay, read, and write controls."""

from PySide6.QtWidgets import QComboBox, QWidget

from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import (
    FeatureCard, LabeledRow, LabeledSlider, ToggleSwitch,
)


class AntiDetectionPanel(PanelBase):
    """Concealment / detection-shaping panel."""

    def __init__(self, config, parent: QWidget = None) -> None:
        super().__init__(config, parent)
        ad = self.config.anti_detection

        self.h1("Anti-detection")
        self.intro(
            "Control overlay visibility, read cadence and the global write "
            "guard from one place. Advanced identity controls are grouped "
            "below with their operational trade-offs."
        )

        self.h2("Runtime protection")
        ov_card = self.add_widget(FeatureCard(
            "Overlay visibility",
            help_text=(
                "Surface-level concealment: stop the overlay from "
                "appearing in screen-capture / OBS / Discord, drop "
                "the taskbar entry, and randomise the OS window "
                "title so window-name signature scans don't latch on."
            ),
        ))
        capture_switch = None
        auto_capture_row = None
        for label, attr, tip in [
            ("Hide overlay from screen capture", "hide_overlay_from_capture",
             "<b>Live.</b> Toggling this updates the overlay "
             "instantly. Calls <code>SetWindowDisplayAffinity"
             "(WDA_EXCLUDEFROMCAPTURE)</code> so the DWM blanks the "
             "overlay out of any GDI/DXGI/PrintWindow capture buffer "
             "(OBS, Discord, Faceit anti-cheat snapshot, Windows "
             "Snipping Tool). Turn OFF when you want to screen-record "
             "the overlay for your own use."),
            ("Hide from taskbar", "hide_from_taskbar",
             "<b>Live.</b> Flips <code>WS_EX_TOOLWINDOW</code> on the overlay "
             "window. This drops its taskbar tile and removes it from the "
             "Alt-Tab list without stopping overlay rendering."),
            ("Randomise window title each launch", "randomize_window_title",
             "<b>Restart required.</b> When ON, picks a fresh title "
             "from a benign decoy pool (Display Settings, "
             "Personalisation, etc.) every time the app starts. When "
             "OFF, the overlay uses the literal title "
             "<code>\"CS2 Master Overlay\"</code>. Defeats simple "
             "FindWindow-by-title signature scans."),
            ("Auto-protect capture on focus loss",
             "auto_capture_protect_on_focus_loss",
             "<b>Live.</b> While capture-protect is ON, "
             "re-applies the no-capture flag once per second so "
             "screenshot tools that nudge the affinity bit between "
             "snaps can't slip through. No effect when capture-"
             "protect itself is OFF."),
        ]:
            switch = ToggleSwitch()
            switch.setChecked(bool(getattr(ad, attr)))
            switch.toggled.connect(
                lambda c, a=attr: setattr(ad, a, bool(c)))
            row = LabeledRow(label, switch, help_text=tip)
            ov_card.addBodyWidget(row)
            if attr == "hide_overlay_from_capture":
                capture_switch = switch
            elif attr == "auto_capture_protect_on_focus_loss":
                auto_capture_row = row
        if capture_switch is not None and auto_capture_row is not None:
            capture_switch.toggled.connect(
                lambda c: auto_capture_row.setVisible(bool(c)))
            auto_capture_row.setVisible(
                bool(getattr(ad, "hide_overlay_from_capture", False)))

        read_card = self.add_widget(FeatureCard(
            "Read cadence",
            help_text=(
                "<b>Live.</b> Slider value is read on every frame, "
                "so dragging it changes the cadence instantly with "
                "no restart needed.<br><br>"
                "Adds 0-N ms of jitter to the per-frame memory-read "
                "cadence so the access pattern doesn't look like a "
                "perfect periodic burst. Useful against heuristic "
                "pattern-detectors that fingerprint constant-rate IO."
            ),
        ))
        read_jitter_slider = LabeledSlider(
            "Read jitter (ms)", lo=0, hi=10,
            value=int(getattr(ad, "randomize_read_jitter_ms", 2)),
            decimals=0,
        )
        read_jitter_slider.valueChanged.connect(
            lambda v: setattr(ad, "randomize_read_jitter_ms", int(v)))
        read_card.addBodyWidget(read_jitter_slider)

        wr_card = self.add_widget(FeatureCard(
            "Write guard",
            help_text=(
                "<b>Live.</b> This control takes effect on the next frame. "
                "The only feature that performs cross-process writes is Glow; "
                "read-only mode is the global kill switch for all writes."
            ),
        ))
        read_only_switch = ToggleSwitch()
        read_only_switch.setChecked(bool(getattr(ad, "read_only_mode", False)))
        read_only_switch.toggled.connect(
            lambda checked: setattr(ad, "read_only_mode", bool(checked)))
        wr_card.addBodyWidget(LabeledRow(
            "Read-only mode (block ALL writes globally)",
            read_only_switch,
            help_text=(
                "<b>Live.</b> Master kill switch on the only writing feature. "
                "Leave ON for the safest profile; this disables Glow entirely."
            ),
        ))

        self.h2("Process identity")
        self.section_intro(
            "These startup-only techniques alter process-visible identity. "
            "Review each control's help before enabling it."
        )

        mod_card = self.add_widget(FeatureCard(
            "Module / DLL name spoof",
            help_text=(
                "<b>Restart required.</b> One-shot at app start - the "
                "PEB module list is only walked once during the "
                "spawn handshake; flipping this toggle mid-session "
                "writes to disk but doesn't touch the live process. "
                "<br><br>"
                "<b>What it does:</b> Copies <code>python3xx.dll</code> "
                "to a benign filename (default "
                "<code>Microsoft.UI.Xaml.dll</code>), <code>LoadLibrary</code>s "
                "the copy, and rewrites the PEB's "
                "<code>InLoadOrderModuleList</code> so scanners that "
                "<code>EnumProcessModules</code> + grep for "
                "<code>python</code> see no match.<br><br>"
                "<b>How to verify:</b> Open <i>Process Explorer</i> "
                "(SysInternals), pick the <code>cs2esp.exe</code> "
                "process, switch to the <i>DLLs</i> view "
                "(<kbd>Ctrl+D</kbd>). With the toggle ON + restart, "
                "the Python interpreter DLL appears under its "
                "masquerade name; <code>python3*.dll</code> entry is "
                "gone from the InLoadOrder list.<br><br>"
                "<b>Limits:</b> EDR / Defender keep their own image-"
                "load callbacks alongside the PEB; this only blinds "
                "user-mode scanners that trust the PEB. Useless "
                "against any sufficiently motivated detector.<br><br>"
                "<b>CPU:</b> one-shot at startup, zero runtime cost. "
                "<b>Stability:</b> high - touches bookkeeping fields "
                "only, never moves real code."
            ),
            risk="medium",
            toggle_target=(ad, "module_spoof_enabled"),
        ))
        unlink_sw = ToggleSwitch()
        unlink_sw.setChecked(bool(getattr(ad, "module_spoof_unlink_from_peb",
                                          True)))
        unlink_sw.toggled.connect(
            lambda c: setattr(ad, "module_spoof_unlink_from_peb", bool(c)))
        mod_card.addBodyWidget(LabeledRow(
            "Unlink from PEB module lists", unlink_sw,
            help_text=("Also remove the entry from the three PEB "
                       "lists so kernel-mode walkers don't see it "
                       "either. Off = name-only rewrite."),
        ))

        ro_card = self.add_widget(FeatureCard(
            "Read-pattern obfuscation",
            help_text=(
                "<b>Restart required.</b> The wrapper is installed "
                "globally over <code>ReadProcessMemory</code> at app "
                "start. Toggling mid-session updates the config but "
                "the runtime keeps the original unwrapped reader until next "
                "launch. The bogus-read count is also captured during "
                "startup, so changing either control requires a restart."
                "<br><br>"
                "<b>What it does:</b> Wraps every "
                "<code>ReadProcessMemory</code> call so each real "
                "read is preceded by N bogus reads at random "
                "userspace addresses. Smears the cheat's "
                "fingerprint-perfect periodic burst pattern into "
                "what looks like a noisy debugger session.<br><br>"
                "<b>How to verify:</b> Attach <i>API Monitor</i> or "
                "<i>RTSS</i> to <code>cs2esp.exe</code> and watch "
                "<code>ReadProcessMemory</code> calls. With the "
                "toggle OFF you see one read per entity-list slot "
                "per frame; with it ON you see N+1 reads per real "
                "fetch and the addresses are scattered across "
                "userspace instead of clustered.<br><br>"
                "<b>Limits:</b> Doesn't change WHAT we read, only "
                "WHEN and at WHICH addresses around it. A detector "
                "that correlates reads against CS2's known entity "
                "structure offsets still has signal.<br><br>"
                "<b>CPU:</b> ~3 us per bogus read x N per real read. "
                "At default 4 / read and 144 fps the overhead is "
                "well under 1 % of a core."
            ),
            risk="medium",
            toggle_target=(ad, "read_obfuscator_enabled"),
        ))
        bpr = LabeledSlider(
            "Bogus reads per real read",
            lo=1, hi=16,
            value=int(getattr(ad, "read_obfuscator_bogus_per_real", 4)),
            decimals=0,
            help_text=("Higher = more noise = more CPU. The "
                       "sweet spot bench tests is 4–6."),
        )
        bpr.valueChanged.connect(
            lambda v: setattr(ad, "read_obfuscator_bogus_per_real", int(v)))
        ro_card.addBodyWidget(bpr)

        wc_card = self.add_widget(FeatureCard(
            "Window-class masquerade",
            help_text=(
                "<b>Restart required.</b> Qt registers the window "
                "class once during construction; the masquerade "
                "shim runs immediately after the menu's "
                "<code>show()</code>. Flipping this on a live "
                "session writes to disk but the running window "
                "keeps its original class atom until you relaunch."
                "<br><br>"
                "<b>What it does:</b> Re-registers the menu window's "
                "class atom to match a benign Windows app "
                "(default <code>Notepad</code>). Scanners that "
                "<code>EnumWindows</code> + <code>GetClassNameW</code> "
                "to identify cheats see a Notepad window where ours "
                "is.<br><br>"
                "<b>How to verify:</b> Run <i>Spy++</i> "
                "(comes with Visual Studio) or <i>WinSpy</i>, click "
                "the menu window. With the toggle ON + restart, "
                "the <i>Class</i> field reads <code>Notepad</code> "
                "(or whichever target you picked). With it OFF, the "
                "class shows Qt's autogen name "
                "(<code>Qt664QWindowIcon</code> or similar).<br><br>"
                "<b>Limits:</b> The window procedure is still Qt's. "
                "Anything that sends Notepad-specific window "
                "messages will get weirdly handled - but no real "
                "scanner does that. The masquerade does NOT affect "
                "the process name shown in Task Manager.<br><br>"
                "<b>CPU:</b> one-shot at window creation, zero "
                "runtime cost."
            ),
            risk="safe",
            toggle_target=(ad, "window_class_masquerade_enabled"),
        ))
        wc_combo = QComboBox()
        wc_combo.addItems(["Notepad", "CalcFrame",
                           "ApplicationFrameWindow", "Explorer"])
        cur = str(getattr(ad, "window_class_masquerade_target", "Notepad"))
        idx = wc_combo.findText(cur)
        if idx >= 0:
            wc_combo.setCurrentIndex(idx)
        wc_combo.currentTextChanged.connect(
            lambda v: setattr(ad, "window_class_masquerade_target", str(v)))
        wc_card.addBodyWidget(LabeledRow(
            "Masquerade as", wc_combo,
            help_text=("Pick the class atom we'll borrow. Notepad is "
                       "the safest because it always exists on every "
                       "Windows install."),
        ))

        self.end()


__all__ = ["AntiDetectionPanel"]
