"""
Thread-safe event bus for MS MESH / CS2 notifications, with a bounded recent-history ring for the HUD event log.
"""

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from time import perf_counter
from typing import Any, Callable


class EventType(str, Enum):
	# Combat
	HIT = "hit"
	MISS = "miss"
	KILL = "kill"
	HEADSHOT = "headshot"
	KNIFE = "knife"
	ZEUS = "zeus"
	GRENADE_THROW = "grenade_throw"

	# Modules
	TRIGGERBOT = "triggerbot"
	BHOP = "bhop"

	# Objective / C4
	BOMB_PLANT = "bomb_plant"
	BOMB_DEFUSING = "bomb_defusing"
	BOMB_DEFUSED = "bomb_defused"
	BOMB_EXPLODED = "bomb_exploded"

	# Tactical / Awareness
	SPECTATOR_NEW = "spectator_new"
	REAR_THREAT = "rear_threat"

	# System
	OFFSETS_UPDATED = "offsets_updated"
	HOTKEY_TOGGLED = "hotkey_toggled"
	CONFIG_LOADED = "config_loaded"


@dataclass(slots=True)
class EventNotification:
	"""A single event notification entry rendered on the HUD."""
	# Fully opaque while shown, then a smoothstep fade over FADE_OUT_S; never translucent mid-life
	FADE_OUT_S = 0.35
	event_type: EventType
	title: str
	detail: str
	created_at: float = field(default_factory=perf_counter)
	duration: float = 3.5
	data: dict[str, Any] = field(default_factory=dict)
	alpha_override: float | None = None

	@property
	def age(self) -> float:
		return perf_counter() - self.created_at

	@property
	def is_expired(self) -> bool:
		if self.alpha_override is not None:
			return False
		return self.age >= self.duration

	@property
	def alpha(self) -> float:
		'''1.0 while shown; smoothstep to 0 over the final FADE_OUT_S.'''
		if self.alpha_override is not None:
			return max(0.0, min(1.0, float(self.alpha_override)))
		elapsed = self.age
		if elapsed < 0.0 or elapsed >= self.duration:
			return 0.0
		remaining = self.duration - elapsed
		fade = min(self.FADE_OUT_S, self.duration * 0.5)
		if fade <= 0.0 or remaining >= fade:
			return 1.0
		fraction = max(0.0, min(1.0, remaining / fade))
		return fraction * fraction * (3.0 - 2.0 * fraction)

	@property
	def slide_offset(self) -> float:
		'''Horizontal slide offset in pixels for entry animation.'''
		elapsed = self.age
		if elapsed < 0.08:
			fraction = elapsed / 0.08
			return -6.0 * (1.0 - fraction)
		return 0.0


class EventSystem:
	"""Singleton-friendly event bus and notification manager."""
	_instance: "EventSystem | None" = None

	def __init__(self, max_history: int = 16, default_duration: float = 4.5) -> None:
		self._lock = Lock()
		self._history: deque[EventNotification] = deque(maxlen=max_history)
		self._listeners: dict[EventType, list[Callable[[EventNotification], None]]] = {}
		self.default_duration = default_duration

	@classmethod
	def get(cls) -> "EventSystem":
		'''Return the shared event bus, creating it on first use.'''
		if cls._instance is None:
			cls._instance = EventSystem()
		return cls._instance

	def subscribe(self, event_type: EventType, listener: Callable[[EventNotification], None]) -> None:
		with self._lock:
			self._listeners.setdefault(event_type, []).append(listener)

	def unsubscribe(self, event_type: EventType, listener: Callable[[EventNotification], None]) -> None:
		with self._lock:
			if event_type in self._listeners:
				try:
					self._listeners[event_type].remove(listener)
				except ValueError:
					pass

	def emit(self, event_type: EventType, title: str, detail: str, duration: float | None = None, data: dict[str, Any] | None = None) -> EventNotification:
		'''Emit a new event notification and notify all registered listeners.'''
		notification = EventNotification(
			event_type=event_type, title=title, detail=detail, duration=duration if duration is not None else self.default_duration, data=data or {}
		)
		slot = notification.data.get("slot")
		with self._lock:
			if slot is not None:
				self._drop_slot_locked(slot)
			self._history.appendleft(notification)
			listeners = list(self._listeners.get(event_type, []))

		for listener in listeners:
			try:
				listener(notification)
			except Exception as e:
				print(f"[event_system] listener error for {event_type}: {e}")

		return notification

	def _drop_slot_locked(self, slot: Any) -> None:
		kept = [item for item in self._history if item.data.get("slot") != slot]
		if len(kept) != len(self._history):
			self._history.clear()
			self._history.extend(kept)

	def drop_slot(self, slot: Any) -> None:
		'''Remove every card that occupies ``slot`` (one card per feature).'''
		with self._lock:
			self._drop_slot_locked(slot)

	def refresh_slot(self, slot: Any, detail: str, data: dict) -> bool:
		'''Refresh an existing aggregate card without replaying notification listeners or its sound.'''
		with self._lock:
			for item in self._history:
				if item.data.get("slot") == slot and not item.is_expired:
					item.detail, item.data, item.created_at = detail, data, perf_counter()
					return True
		return False

	def get_active_notifications(self, max_visible: int | None = 6) -> list[EventNotification]:
		'''Return non-expired notifications up to max_visible for HUD rendering.'''
		with self._lock:
			active = [item for item in self._history if not item.is_expired]
		return active if max_visible is None else active[:max_visible]

	def clear(self) -> None:
		with self._lock:
			self._history.clear()

	def emit_combat_hit(self, name: str, hitgroup: str, damage: int, remaining: int, duration: float | None = None) -> None:
		'''Manually or externally emit a verified combat hit event.'''
		self.emit(
			EventType.HIT,
			title="HIT",
			detail=f"Hit {name} in {hitgroup} for {damage} ({remaining} remaining)",
			duration=duration,
			data={"name": name, "damage": damage, "hp": remaining, "headshot": hitgroup.strip().lower().endswith("head")},
		)

	def emit_combat_miss(self, name: str, reason: str = "due to spread", duration: float | None = None) -> None:
		'''Emit a missed shot combat notification.'''
		self.emit(EventType.MISS, title="MISS", detail=f"Missed {name} {reason}", duration=duration, data={"name": name, "reason": reason})


events = EventSystem.get()
