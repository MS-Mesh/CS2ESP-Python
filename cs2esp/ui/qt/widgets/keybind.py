"""
Click-to-capture keybind row with combo support.

Used by both the Keybinds panel and inline feature cards. The row polls held
keys during a short capture window, shows the provisional combo, and commits only
on OK. Stored strings match the keyboard package format, while a peer registry
keeps duplicate rows for the same config field synchronised.
"""

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)


_PEER_REGISTRY: dict = {}

try:
    import keyboard as _kb  # type: ignore
except Exception:  # pragma: no cover
    _kb = None


_CAPTURE_CANDIDATES: list[str] = (
    list("abcdefghijklmnopqrstuvwxyz0123456789")
    + ["space", "tab", "enter", "shift", "ctrl", "alt",
       "left", "right", "up", "down",
       "home", "end", "page up", "page down", "insert", "delete",
       "[", "]", ";", "'", ",", ".", "/", "\\", "`", "-", "="]
    + [f"f{i}" for i in range(1, 13)]
)


class _ComboCaptureSession(QObject):
    """Poll held keys and maintain the ten-second capture countdown."""

    provisional_changed = Signal(str)
    timed_out = Signal()
    cancelled = Signal()
    tick = Signal(int)

    POLL_MS = 30
    TICK_MS = 100
    COUNTDOWN_S = 10

    _MOD_ORDER: tuple[str, ...] = ("ctrl", "shift", "alt", "windows")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._prev_down: set[str] = set()
        self._provisional: str = ""
        self._started_at: float = 0.0
        self._last_tick_sec: int = -1

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._poll_tick)

        self._ticker = QTimer(self)
        self._ticker.setInterval(self.TICK_MS)
        self._ticker.timeout.connect(self._countdown_tick)

    def start(self) -> None:
        if _kb is None:
            self.cancelled.emit()
            return
        self._prev_down = self._snapshot()
        self._provisional = ""
        self._started_at = time.monotonic()
        self._last_tick_sec = -1
        self._poll.start()
        self._ticker.start()
        self._countdown_tick()

    def stop(self) -> None:
        self._poll.stop()
        self._ticker.stop()

    def provisional(self) -> str:
        """Current candidate binding string (empty = no key pressed yet)."""
        return self._provisional

    @staticmethod
    def _snapshot() -> set[str]:
        """Return the set of candidate keys currently held down."""
        if _kb is None:
            return set()
        out: set[str] = set()
        for k in _CAPTURE_CANDIDATES:
            try:
                if _kb.is_pressed(k):
                    out.add(k)
            except Exception:
                continue
        return out

    def _poll_tick(self) -> None:
        """30 ms loop that watches for new key presses and ESC bail-out."""
        if _kb is None:
            self.stop()
            self.cancelled.emit()
            return
        try:
            if _kb.is_pressed("esc"):
                self.stop()
                self.cancelled.emit()
                return
        except Exception:
            pass
        now = self._snapshot()
        new = now - self._prev_down
        self._prev_down = now
        if not new:
            return
        modifiers = [key for key in self._MOD_ORDER if key in now]
        other_keys = sorted(key for key in now if key not in self._MOD_ORDER)
        combo = "+".join(modifiers + other_keys)
        if combo and combo != self._provisional:
            self._provisional = combo
            self.provisional_changed.emit(combo)

    def _countdown_tick(self) -> None:
        """100 ms loop for the visible 10 s timer."""
        elapsed = time.monotonic() - self._started_at
        remaining = max(0, int(self.COUNTDOWN_S - elapsed))
        if remaining != self._last_tick_sec:
            self._last_tick_sec = remaining
            self.tick.emit(remaining)
        if elapsed >= self.COUNTDOWN_S:
            self.stop()
            self.timed_out.emit()


class KeybindRow(QWidget):
    """Four-cell row for displaying, capturing, clearing, and committing a keybind."""

    changed = Signal(str)

    def __init__(
        self,
        label: str,
        section: Any,
        attr: str,
        *,
        on_change: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._section = section
        self._attr = attr
        self._on_change = on_change
        self.setObjectName("KeybindRow")

        self._capturing = False
        self._session: _ComboCaptureSession | None = None
        self._last_remaining: int = 0

        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(6)

        self._label = QLabel(label, self)
        self._label.setObjectName("KeybindLabel")
        self._label.setWordWrap(True)
        self._label.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Minimum,
        )

        self._key_lbl = QLabel(self)
        self._key_lbl.setObjectName("KeybindCurrent")
        self._key_lbl.setAlignment(Qt.AlignCenter)
        self._key_lbl.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed,
        )

        self._rebind_btn = QPushButton("Rebind", self)
        self._rebind_btn.setObjectName("KeybindRebind")
        self._rebind_btn.setFocusPolicy(Qt.StrongFocus)
        self._rebind_btn.clicked.connect(self._on_primary_clicked)

        self._clear_btn = QPushButton("Clear", self)
        self._clear_btn.setObjectName("KeybindClear")
        self._clear_btn.setFocusPolicy(Qt.StrongFocus)
        self._clear_btn.clicked.connect(self._on_secondary_clicked)

        self._compact: bool | None = None
        self._layout_mode: str | None = None
        self._update_metrics()
        self._set_layout_mode("wide")

        self._refresh_idle_display()

        self._registry_key = (id(section), str(attr))
        bucket = _PEER_REGISTRY.setdefault(self._registry_key, [])
        bucket.append(self)
        self.destroyed.connect(self._unregister_peer)

    def _unregister_peer(self, *_args) -> None:
        """Remove this row from the peer registry. Idempotent."""
        bucket = _PEER_REGISTRY.get(getattr(self, "_registry_key", None))
        if not bucket:
            return
        try:
            bucket.remove(self)
        except ValueError:
            pass
        if not bucket:
            _PEER_REGISTRY.pop(self._registry_key, None)

    def apply_theme(self, _theme_name: str) -> None:
        """Refresh font-dependent widths after a live theme change."""
        self._update_metrics()
        self._update_layout_mode()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_layout_mode(event.size().width())

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._update_metrics()
            self._update_layout_mode()

    def _update_metrics(self) -> None:
        em = max(7, self.fontMetrics().horizontalAdvance("M"))
        self._key_lbl.setMinimumWidth(max(132, 14 * em))
        self._rebind_btn.setMinimumWidth(max(76, 8 * em))
        self._clear_btn.setMinimumWidth(max(68, 7 * em))

    def _update_layout_mode(self, width: int | None = None) -> None:
        width = self.width() if width is None else width
        em = max(7, self.fontMetrics().horizontalAdvance("M"))
        controls = (
            self._key_lbl.minimumWidth()
            + self._rebind_btn.minimumWidth()
            + self._clear_btn.minimumWidth()
            + 24
        )
        breakpoint = max(720, controls + 28 * em)
        if width > 0 and width < controls + 16:
            mode = "stacked"
        elif width > 0 and width < breakpoint:
            mode = "compact"
        else:
            mode = "wide"
        self._set_layout_mode(mode)

    def _set_layout_mode(self, mode: str) -> None:
        if mode == self._layout_mode:
            return
        for widget in (
            self._label, self._key_lbl, self._rebind_btn, self._clear_btn,
        ):
            self._layout.removeWidget(widget)

        if mode == "stacked":
            self._layout.addWidget(self._label, 0, 0, 1, 4)
            self._layout.addWidget(self._key_lbl, 1, 0, 1, 4)
            self._layout.addWidget(self._rebind_btn, 2, 0, 1, 2)
            self._layout.addWidget(self._clear_btn, 2, 2, 1, 2)
        elif mode == "compact":
            self._layout.addWidget(self._label, 0, 0, 1, 4)
            self._layout.addWidget(self._key_lbl, 1, 0, 1, 2)
            self._layout.addWidget(self._rebind_btn, 1, 2)
            self._layout.addWidget(self._clear_btn, 1, 3)
        else:
            self._layout.addWidget(self._label, 0, 0)
            self._layout.addWidget(self._key_lbl, 0, 1)
            self._layout.addWidget(self._rebind_btn, 0, 2)
            self._layout.addWidget(self._clear_btn, 0, 3)
        for column in range(4):
            self._layout.setColumnStretch(
                column, 1 if mode == "stacked" or column == 0 else 0
            )
        self._layout_mode = mode
        self._compact = mode != "wide"
        self.setProperty("layoutMode", mode)
        self.updateGeometry()

    def refresh(self) -> None:
        """External hook for callers that mutated section.attr
        directly and want the display to re-read it."""
        if not self._capturing:
            self._refresh_idle_display()

    def _write(self, key: str) -> None:
        try:
            setattr(self._section, self._attr, key)
        except Exception as e:
            print(f"[keybind] set {self._attr}={key!r} failed: {e!r}")
        self.changed.emit(key)
        if callable(self._on_change):
            try:
                self._on_change(key)
            except Exception:
                pass
        bucket = _PEER_REGISTRY.get(self._registry_key, [])
        for peer in bucket:
            if peer is self:
                continue
            try:
                peer.refresh()
            except Exception:
                pass

    def _refresh_idle_display(self) -> None:
        """Render the idle state with the current committed binding."""
        try:
            current_key = str(getattr(self._section, self._attr, "") or "")
        except Exception:
            current_key = ""
        if current_key:
            self._key_lbl.setText(current_key)
            self._set_key_state("bound")
            self._clear_btn.setEnabled(True)
        else:
            self._key_lbl.setText("<unset>")
            self._set_key_state("unset")
            self._clear_btn.setEnabled(False)

        self._rebind_btn.setText("Rebind")
        self._rebind_btn.setProperty("role", "default")
        self._clear_btn.setText("Clear")
        self._clear_btn.setProperty("role", "default")
        self._repolish(self._rebind_btn)
        self._repolish(self._clear_btn)

    def _render_capture(self) -> None:
        """Render the capturing state with provisional key and countdown."""
        provisional = self._session.provisional() if self._session else ""
        seconds_left = self._last_remaining
        if provisional:
            self._key_lbl.setText(f"{provisional} {seconds_left}s")
        else:
            self._key_lbl.setText(f"Press key… {seconds_left}s")
        self._set_key_state("capturing")

        self._rebind_btn.setText("OK")
        self._rebind_btn.setProperty("role", "commit")
        self._rebind_btn.setEnabled(bool(provisional))
        self._repolish(self._rebind_btn)

        self._clear_btn.setText("Cancel")
        self._clear_btn.setProperty("role", "cancel")
        self._clear_btn.setEnabled(True)
        self._repolish(self._clear_btn)

    def _set_key_state(self, state: str) -> None:
        self._key_lbl.setProperty("state", state)
        self._repolish(self._key_lbl)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()

    def _on_primary_clicked(self) -> None:
        """Start capture in idle state, or commit while capturing."""
        if self._capturing:
            if self._session is None:
                self._end_capture(revert=True)
                return
            provisional = self._session.provisional()
            if not provisional:
                self._end_capture(revert=True)
                return
            self._end_capture(revert=False, commit_value=provisional)
            return
        if _kb is None:
            self._key_lbl.setText("(keyboard lib missing)")
            return
        self._capturing = True
        self._session = _ComboCaptureSession(self)
        self._session.provisional_changed.connect(self._on_provisional_changed)
        self._session.tick.connect(self._on_tick)
        self._session.timed_out.connect(self._on_timed_out)
        self._session.cancelled.connect(self._on_cancelled)
        self._last_remaining = _ComboCaptureSession.COUNTDOWN_S
        self._session.start()
        self._render_capture()

    def _on_secondary_clicked(self) -> None:
        """Clear in idle state, or cancel while capturing."""
        if self._capturing:
            self._end_capture(revert=True)
            return
        self._write("")
        self._refresh_idle_display()

    def _on_provisional_changed(self, _combo: str) -> None:
        self._render_capture()

    def _on_tick(self, remaining: int) -> None:
        self._last_remaining = remaining
        self._render_capture()

    def _on_timed_out(self) -> None:
        self._end_capture(revert=True)

    def _on_cancelled(self) -> None:
        self._end_capture(revert=True)

    def _end_capture(self, *, revert: bool,
                     commit_value: str | None = None) -> None:
        if self._session is not None:
            self._session.stop()
            self._session.deleteLater()
            self._session = None
        self._capturing = False
        if not revert and commit_value:
            self._write(commit_value)
        self._refresh_idle_display()


__all__ = ["KeybindRow"]
