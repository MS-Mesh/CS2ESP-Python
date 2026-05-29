"""Recoil-script generator for supported mouse vendors."""

from os import environ
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QWidget,
)

from cs2esp.features import norecoil_scripts as _scripts
from cs2esp.ui.qt.panels.base import PanelBase
from cs2esp.ui.qt.widgets import FeatureCard


class NoRecoilPanel(PanelBase):
    """Generate vendor-side recoil scripts without touching CS2 memory."""

    def __init__(self, config, parent: QWidget | None = None) -> None:
        super().__init__(config, parent)

        self.h1("Recoil scripts")
        self.intro(
            "Generate a G HUB Lua or AutoHotkey v2 sidecar for supported "
            "mice. Scripts run outside the game and emit ordinary HID input."
        )

        warn = QLabel(
            "<b>Pattern note</b> · Scripts cover every full-auto weapon and "
            "are calibrated for sensitivity 1.0. The patterns are fixed, not "
            "perfect: CS2's recoil changes "
            "slightly between updates, so some scatter stays — most on the "
            "last third of a long spray. If it consistently over- or "
            "under-compensates, nudge <code>MULT</code> by ±10–20%. This "
            "tuning is independent of mouse DPI."
        )
        warn.setObjectName("FeatureWarning")
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.RichText)
        self.add_widget(warn)

        for vendor in _scripts.VENDORS:
            self._build_vendor_card(vendor)

        self.end()

    def _build_vendor_card(self, vendor) -> None:
        """Build one script card with download, copy, and setup guide actions."""
        card = self.add_widget(FeatureCard(
            f"{vendor.display} · {vendor.language}",
            help_text=(
                f"A ready-to-paste <b>{vendor.language}</b> script with a "
                f"real per-bullet spray pattern for all 16 full-auto weapons, "
                f"so compensation follows each gun's actual recoil. Calibrated "
                f"for in-game sensitivity 1.0 — edit <code>MULT</code> to match "
                f"yours. Toggle <b>Caps Lock</b> in-game to enable/disable."
            ),
            risk="safe",
        ))

        row = QWidget(card)
        row.setObjectName("ScriptActions")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        download_button = QPushButton(f"Download {vendor.extension}", row)
        download_button.setObjectName("PrimaryActionBtn")
        download_button.setFocusPolicy(Qt.NoFocus)
        download_button.setCursor(Qt.PointingHandCursor)
        download_button.setToolTip(
            "Save the generated script to disk. Opens your system "
            "file dialog, defaulting to your Downloads folder."
        )
        download_button.clicked.connect(lambda *_: self._download(vendor))
        layout.addWidget(download_button)

        copy_button = QPushButton("Copy to clipboard", row)
        copy_button.setObjectName("SecondaryActionBtn")
        copy_button.setFocusPolicy(Qt.NoFocus)
        copy_button.setCursor(Qt.PointingHandCursor)
        copy_button.setToolTip(
            "Copy the whole script to the system clipboard so you "
            "can paste it straight into the vendor's script editor."
        )
        copy_button.clicked.connect(lambda *_: self._copy(vendor))
        layout.addWidget(copy_button)

        guide_btn = QPushButton("Show guide", row)
        guide_btn.setObjectName("SecondaryActionBtn")
        guide_btn.setFocusPolicy(Qt.NoFocus)
        guide_btn.setCursor(Qt.PointingHandCursor)
        guide_btn.setToolTip("Click to expand the step-by-step setup guide.")
        layout.addWidget(guide_btn)

        layout.addStretch(1)
        card.addBodyWidget(row)

        guide_lbl = QLabel(vendor.guide_html, card)
        guide_lbl.setTextFormat(Qt.RichText)
        guide_lbl.setWordWrap(True)
        guide_lbl.setOpenExternalLinks(True)
        guide_lbl.setTextInteractionFlags(
            Qt.TextBrowserInteraction
        )
        guide_lbl.setObjectName("ScriptGuide")
        guide_lbl.setVisible(False)
        card.addBodyWidget(guide_lbl)

        guide_btn.clicked.connect(
            lambda *_, guide_label=guide_lbl, guide_button=guide_btn:
            self._toggle_guide(guide_label, guide_button)
        )

    def _toggle_guide(
        self,
        guide_label: QLabel,
        guide_button: QPushButton,
    ) -> None:
        """Flip the guide visibility and swap the button label."""
        showing = not guide_label.isVisible()
        guide_label.setVisible(showing)
        guide_button.setText("Hide guide" if showing else "Show guide")

    def _download(self, vendor) -> None:
        """Save the script to disk via a standard Save dialog."""
        try:
            content = _scripts.build_script(vendor.id)
        except Exception as e:
            self._error(f"Script generation failed: {e!r}")
            return

        default_name = _scripts.suggested_filename(vendor.id)
        profile = environ.get("USERPROFILE")
        home_dir = Path(profile) if profile else Path.home()
        downloads_dir = home_dir / "Downloads"
        default_dir = downloads_dir if downloads_dir.exists() else home_dir
        default_path = str(default_dir / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {vendor.display} script",
            default_path,
            f"{vendor.language} script (*{vendor.extension})"
            ";;All files (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            self._info(
                f"Saved <b>{Path(path).name}</b> to "
                f"<code>{Path(path).parent}</code>."
            )
        except OSError as e:
            self._error(f"Could not write the file: {e.strerror or e}.")

    def _copy(self, vendor) -> None:
        """Copy the generated script to the system clipboard."""
        try:
            content = _scripts.build_script(vendor.id)
        except Exception as e:
            self._error(f"Script generation failed: {e!r}")
            return
        try:
            QGuiApplication.clipboard().setText(content)
        except Exception as e:
            self._error(f"Clipboard error: {e!r}")
            return
        self._info(
            f"Copied the {vendor.display} {vendor.language} script "
            "to your clipboard — paste it into the vendor's "
            "script editor."
        )

    def _info(self, html: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Recoil scripts")
        box.setTextFormat(Qt.RichText)
        box.setText(html)
        box.exec()

    def _error(self, html: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Recoil scripts")
        box.setTextFormat(Qt.RichText)
        box.setText(html)
        box.exec()


__all__ = ["NoRecoilPanel"]
