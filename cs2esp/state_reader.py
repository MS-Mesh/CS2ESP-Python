"""
Read game state on a worker and publish snapshots independently of the render loop.
"""

from __future__ import annotations

import threading
from copy import copy
from dataclasses import dataclass, field
from time import perf_counter, sleep


@dataclass(frozen=True, slots=True)
class GameSnapshot:
	"""One read of the game, produced off the render thread."""
	taken_at: float
	local_team: int
	local_pawn: int
	local_yaw: float
	local_pos: tuple = (0.0, 0.0, 0.0)
	players: list = field(default_factory=list)
	health: dict = field(default_factory=dict)
	read_completed_at: float = 0.0
	# Sampled before publication work, not at the instant the snapshot becomes visible
	published_at: float = 0.0
	sequence: int = 0

	def render_players(self) -> list:
		'''Renderer owns fresh poses; published objects keep their read-time state.'''
		players = []
		for source in self.players:
			player = copy(source)
			if hasattr(source, "bones"):
				player.bones = dict(source.bones)
			slab = getattr(source, "bone_slab", None)
			if slab is not None and slab.flags.writeable:
				player.bone_slab = slab.copy()
			players.append(player)
		return players


class GameStateReader:
	"""Continuously publishes player/health snapshots from a worker thread."""
	def __init__(self, entities, *, target_hz: float = 90.0) -> None:
		self._entities = entities
		self._lock = threading.Lock()
		self._snapshot: GameSnapshot | None = None
		self._generation = 0
		self._sequence = 0
		self._want_bones = True
		self._want_slab = False
		self._want_health = True
		self._want_weapons = False
		self._enabled = False
		self._period = 1.0 / max(30.0, float(target_hz))
		self._stop = threading.Event()
		self._wake = threading.Event()
		self._thread = threading.Thread(target=self._run, name="game-state", daemon=True)
		self._thread.start()

	def request(self, *, enabled: bool, bones: bool, slab: bool, health: bool, weapons: bool = False) -> None:
		'''Declare what the next snapshots must contain.'''
		with self._lock:
			previous = (self._enabled, self._want_bones, self._want_slab, self._want_health, self._want_weapons)
			requested = (bool(enabled), bool(bones), bool(slab), bool(health), bool(weapons))
			if previous != requested:
				self._generation += 1
				self._snapshot = None
			self._enabled, self._want_bones, self._want_slab, self._want_health, self._want_weapons = requested
		if requested[0] and previous != requested:
			self._wake.set()

	def set_target_hz(self, target_hz: float) -> None:
		'''Adjust the worker cadence without restarting its thread.'''
		with self._lock:
			self._period = 1.0 / max(30.0, min(240.0, float(target_hz)))

	def latest(self, max_age_s: float = 0.25) -> GameSnapshot | None:
		'''Newest snapshot, or None when missing or older than max_age_s.'''
		with self._lock:
			snapshot = self._snapshot
		if snapshot is not None and perf_counter() - snapshot.taken_at > max_age_s:
			return None
		return snapshot

	def clear(self) -> None:
		'''Drop the current snapshot and invalidate in-flight reads.'''
		with self._lock:
			self._snapshot = None
			self._generation += 1

	def stop(self) -> None:
		'''Stop the worker and wait up to one second for it to exit.'''
		with self._lock:
			self._generation += 1
			self._snapshot = None
		self._stop.set()
		self._wake.set()
		self._thread.join(timeout=1.0)

	def _run(self) -> None:
		'''Read and publish until stopped, honoring the latest request flags.'''
		entities = self._entities
		while not self._stop.is_set():
			with self._lock:
				enabled = self._enabled
				generation = self._generation
				want_bones = self._want_bones
				want_slab = self._want_slab
				want_health = self._want_health
				weapon_options = {"read_weapons": True} if self._want_weapons else {}
			if not enabled:
				self._wake.wait(0.25)
				self._wake.clear()
				continue
			started = perf_counter()
			try:
				local_team = entities.get_local_team()
				local_pawn = entities.get_local_pawn()
				local_yaw = entities.get_local_eye_yaw()
				combined_read = getattr(entities, "get_players_and_health", None)
				if want_health and combined_read is not None:
					players, health = combined_read(local_team=local_team, read_bones=want_bones, read_bone_slab=want_slab, **weapon_options)
				else:
					players = entities.get_players(read_bones=want_bones, read_bone_slab=want_slab, **weapon_options)
					health = (entities.get_health_snapshot(local_team) if want_health else {})
				# Keep the scan and its local position together across synchronous fallback reads
				get_scan_pos = getattr(entities, "get_scan_local_position", None)
				local_pos = get_scan_pos() if get_scan_pos is not None else entities.get_local_position()
				read_completed_at = perf_counter()
				next_sequence = self._sequence + 1
				snapshot = GameSnapshot(taken_at=started, local_team=local_team, local_pawn=local_pawn, local_yaw=local_yaw,
					local_pos=local_pos, players=players, health=health, read_completed_at=read_completed_at, published_at=perf_counter(), sequence=next_sequence)
				with self._lock:
					if self._enabled and self._generation == generation and not self._stop.is_set():
						self._sequence = next_sequence
						self._snapshot = snapshot
			except Exception as exc:
				print(f"[state] read failed: {exc!r}")
				sleep(0.05)
			with self._lock:
				period = self._period
			remaining = period - (perf_counter() - started)
			if remaining > 0:
				sleep(remaining)


__all__ = ["GameSnapshot", "GameStateReader"]
