"""
Shared compact risk labels and one menu-only refresh hub. Only mechanism/selection changes invalidate assessments; no runtime feature is invoked.
"""

from weakref import WeakSet

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy

from cs2esp.risk import RiskAssessment, RiskBinding, risk_for, risk_signature


class RiskRefreshHub(QObject):
	"""One timer and one assessment per distinct capability/selection across all menu references."""
	def __init__(self, application) -> None:
		super().__init__(application)
		self.subscribers = WeakSet()
		self._keys = {}
		self._cache = {}
		self.evaluations = 0
		self.timer = QTimer(self)
		self.timer.setInterval(250)
		self.timer.timeout.connect(self.refresh)
		self.timer.start()

	def refresh(self) -> None:
		signatures = {}
		active_keys = set()
		for subscriber in tuple(self.subscribers):
			try:
				source = subscriber._risk_source
				if not isinstance(source, RiskBinding):
					continue
				config = source.config() if callable(source.config) else source.config
				mode = source.mode() if callable(source.mode) else source.mode
				identity = id(config)
				if identity not in signatures:
					signatures[identity] = risk_signature(config)
				key = (source.capability_id, identity, mode, signatures[identity])
				active_keys.add(key)
				if key not in self._cache:
					self._cache[key] = risk_for(source.capability_id, config, mode=mode)
					self.evaluations += 1
				if self._keys.get(id(subscriber)) != key:
					subscriber.refreshRisk(self._cache[key])
					self._keys[id(subscriber)] = key
			except RuntimeError:
				self.subscribers.discard(subscriber)
			except Exception:
				# Retain the last displayed assessment and retry this subscriber on the next refresh
				self._keys.pop(id(subscriber), None)
		self._cache = {key: value for key, value in self._cache.items() if key in active_keys}
		identities = {id(subscriber) for subscriber in self.subscribers}
		self._keys = {key: value for key, value in self._keys.items() if key in identities}


def subscribe_risk(widget: QObject) -> None:
	application = QApplication.instance()
	if application is None:
		return
	hub = getattr(application, "_risk_refresh_hub", None)
	if hub is None:
		hub = RiskRefreshHub(application)
		application._risk_refresh_hub = hub
	hub.subscribers.add(widget)
	hub._keys.pop(id(widget), None)


class RiskBadge(QLabel):
	"""Existing RiskBadge QSS and typography; the visible text contains only the VAC grade."""
	def __init__(self, parent=None) -> None:
		super().__init__(parent)
		self.setObjectName("RiskBadge")
		self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
		self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
		self.setVisible(False)

	def setAssessment(self, assessment: RiskAssessment) -> None:  # noqa: N802
		self.setText(assessment.badge)
		self.setProperty("risk", assessment.bucket)
		self.setProperty("capabilityId", assessment.capability_id)
		self.setAccessibleName(assessment.heading)
		self.setAccessibleDescription(assessment.plain_text)
		self.setVisible(bool(assessment.badge))
		self.style().unpolish(self)
		self.style().polish(self)
		self.updateGeometry()
