"""
Feedback triggerbot driven by the engine crosshair raycast. Targeting uses m_iIDEntIndex, so wall respect comes from the engine; click delivery reuses the vanilla helper.
Fire control watches target health: confirmed damage keeps firing, while no drop inside the feedback window pauses briefly before retrying.
"""

import ctypes
import random
import time

from cs2esp.entity import EntitySystem, Player, read_game_time
from cs2esp.memory import MemoryReader
from cs2esp.offsets import Offsets, Netvars
from cs2esp.ui.hotkeys import is_feature_active
from cs2esp.features.triggerbot_vanilla import _SCOPED_WEAPON_IDS, _click, _ray_hits_sphere, view_direction, weapon_class_allowed
from cs2esp.visuals.skeleton import compute_head_sphere


class FeedbackTriggerbot:
	'''Health-feedback triggerbot firing through the engine crosshair raycast.'''
	def __init__(self, mem: MemoryReader, entity_system: EntitySystem, config) -> None:
		self.mem = mem
		self.entities = entity_system
		self.config = config
		self._watched_pawn: int = 0
		self._baseline: dict[int, int] = {}
		self._click_ts: list[float] = []
		self._pause_until: float = 0.0
		# Humanizer state mirrors vanilla _dispatch_fire pacing
		self._sighted_at: float | None = None
		self._sighted_idx: int = 0
		self._fired_at: float = 0.0
		self._last_fired_idx: int = 0

	def _reset_sight_state(self) -> None:
		'''Drop the reaction-timer lock so the next sighting starts fresh.'''
		self._sighted_at = None
		self._sighted_idx = 0

	def update(self, players: list[Player], local_pawn: int, local_team: int) -> None:
		'''Resolve the engine target, apply the feedback gate, then deliver at most one click.'''
		triggerbot_config = self.config.triggerbot
		if not is_feature_active(triggerbot_config) or local_pawn == 0:
			self._reset_sight_state()
			return

		# User mouse priority: yield while the user manually holds LMB
		if bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):
			self._reset_sight_state()
			return

		# Scoped weapons fire only while actually scoped in (default on)
		weapon = 0
		holding_scoped = False
		try:
			weapon_services = self.mem.read_long(local_pawn + Netvars.m_pWeaponServices)
			active_weapon = self.mem.read_uint(weapon_services + Netvars.m_hActiveWeapon) if weapon_services else 0
			if active_weapon:
				weapon = self.entities.resolve_handle(active_weapon)
			if weapon:
				item_definition = self.mem.read_int(weapon + Netvars.m_AttributeManager + Netvars.m_Item + Netvars.m_iItemDefinitionIndex) & 0xFFFF
				holding_scoped = item_definition in _SCOPED_WEAPON_IDS
				if not weapon_class_allowed(triggerbot_config, item_definition):
					self._reset_sight_state()
					return
		except Exception:
			pass

		if holding_scoped and bool(getattr(triggerbot_config, "scoped_require_scope", True)):
			try:
				scoped_now = bool(self.mem.read_byte(local_pawn + Netvars.m_bIsScoped))
				self._last_scoped = scoped_now
			except Exception:
				scoped_now = getattr(self, "_last_scoped", False)
			if not scoped_now:
				self._reset_sight_state()
				return

		# Fire condition gate: always (default) / shift held / ctrl held / no movement
		fire_condition = str(getattr(triggerbot_config, "fire_condition", "always") or "always").lower()
		if fire_condition == "shift":
			if not bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000):
				self._reset_sight_state()
				return
		elif fire_condition == "ctrl":
			if not bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000):
				self._reset_sight_state()
				return
		elif fire_condition == "no_move":
			try:
				velocity_x, velocity_y, velocity_z = self.mem.read_vec3(local_pawn + Netvars.m_vecAbsVelocity)
				if (velocity_x * velocity_x + velocity_y * velocity_y + velocity_z * velocity_z) ** 0.5 > 30.0:
					self._reset_sight_state()
					return
			except Exception:
				pass

		# Can-fire gate: skip clicks during bolt/reload so no phantom shots register
		if weapon:
			try:
				next_tick = self.mem.read_int(weapon + Netvars.m_nNextPrimaryAttackTick)
				game_time = read_game_time(self.mem)
				if game_time is not None and next_tick > 0 and (next_tick / 64.0) > game_time + 0.02:
					self._reset_sight_state()
					return
			except Exception:
				pass

		now = time.perf_counter()

		# m_iIDEntIndex is the engine's crosshair raycast result
		entity_index = self.mem.read_int(local_pawn + Netvars.m_iIDEntIndex)
		if entity_index <= 0 or entity_index >= 2048:
			self._reset_sight_state()
			return

		target_pawn = self.entities.entity_index_to_pawn(entity_index)
		if not target_pawn:
			self._reset_sight_state()
			return

		target_player = next((player for player in players if player.pawn_addr == target_pawn), None)
		if target_player is not None:
			target_team = target_player.team
			target_health = target_player.health
			target_life_state = target_player.life_state
		else:
			try:
				target_team = self.mem.read_byte(target_pawn + Netvars.m_iTeamNum)
				target_health = self.mem.read_int(target_pawn + Netvars.m_iHealth)
				target_life_state = self.mem.read_byte(target_pawn + Netvars.m_lifeState)
			except Exception:
				self._reset_sight_state()
				return

		if target_team == local_team or target_health <= 0 or target_life_state != 0:
			self._reset_sight_state()
			return

		if bool(getattr(triggerbot_config, "head_only", False)):
			if target_player is None or not target_player.bones:
				self._reset_sight_state()
				return
			try:
				skeleton_config = self.config.visuals.skeleton
				view = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
				scene_node = self.mem.read_long(local_pawn + Netvars.m_pGameSceneNode)
				if not scene_node:
					self._reset_sight_state()
					return
				origin = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
				view_offset = self.mem.read_vec3(local_pawn + Netvars.m_vecViewOffset)
			except Exception:
				self._reset_sight_state()
				return
			eye = tuple(origin[index] + view_offset[index] for index in range(3))
			if not any(eye):
				self._reset_sight_state()
				return
			sphere = compute_head_sphere(target_player, skeleton_config, camera_pos=eye)
			if sphere is None:
				self._reset_sight_state()
				return
			center, radius = sphere
			direction = view_direction(float(view[1]), float(view[0]))
			if not _ray_hits_sphere(eye, direction, center, radius):
				self._reset_sight_state()
				return

		if not self._feedback_allows_fire(now, target_pawn, target_health):
			return

		if target_pawn != self._sighted_idx:
			self._sighted_idx = target_pawn
			self._sighted_at = now
			return
		if self._sighted_at is None:
			self._sighted_at = now
			return

		delay_ms = float(getattr(triggerbot_config, "delay_ms", 25))
		jitter_ms = float(getattr(triggerbot_config, "delay_jitter_ms", 15))
		min_delay_ms = float(getattr(triggerbot_config, "min_delay_ms", 40))
		reaction_wait = max(min_delay_ms, delay_ms + random.uniform(-jitter_ms, jitter_ms)) / 1000.0
		if now - self._sighted_at < reaction_wait:
			return

		cooldown_ms = float(getattr(triggerbot_config, "refire_cooldown_ms", 350))
		since_last_click_ms = (now - self._fired_at) * 1000.0
		if target_pawn == self._last_fired_idx and since_last_click_ms < cooldown_ms:
			return
		_click()
		self._fired_at = now
		self._last_fired_idx = target_pawn
		self._click_ts.append(now)

	def _feedback_allows_fire(self, now: float, target_pawn: int, health: int) -> bool:
		'''Health-drop fire gate. Returns True to allow, False to defer.'''
		triggerbot_config = self.config.triggerbot
		confirm_seconds = getattr(triggerbot_config, "feedback_confirm_ms", 100) / 1000
		resume_seconds = getattr(triggerbot_config, "feedback_resume_ms", 100) / 1000

		# New target: baseline health, clear timers, allow
		if target_pawn != self._watched_pawn:
			self._watched_pawn = target_pawn
			self._baseline = {target_pawn: health}
			self._click_ts = []
			self._pause_until = 0.0
			return True

		if self._baseline.get(target_pawn) is None:
			self._baseline[target_pawn] = health

		if resume_seconds <= 0:
			self._baseline[target_pawn] = health
			self._click_ts = []
			self._pause_until = 0.0
			return True

		# Confirmed damage: re-baseline and keep firing
		if health < self._baseline[target_pawn]:
			self._baseline[target_pawn] = health
			self._click_ts = []
			self._pause_until = 0.0
			return True

		if self._pause_until > 0:
			if now < self._pause_until:
				return False
			self._pause_until = 0.0
			self._baseline[target_pawn] = health
			self._click_ts = []
			return True

		# Clicks past the confirm window with no drop = miss. The pending-shot pipeline in
		# EventSystem emits the classified miss event, so do not double-emit here
		if any(now - click_time >= confirm_seconds for click_time in self._click_ts):
			self._pause_until = now + resume_seconds
			self._click_ts = []
			return False
		return True
