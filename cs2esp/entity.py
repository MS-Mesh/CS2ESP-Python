"""
CS2 entity reader: walks Source 2 entity-list pages, resolves controllers to pawns, and builds Player snapshots for ESP, radar, triggerbot, glow and diagnostics.
"""

from dataclasses import dataclass, field
from threading import local
from struct import unpack_from
from time import perf_counter

import numpy as np

from cs2esp.memory import MemoryReader
from cs2esp.offsets import Offsets, Netvars

_CONTROLLER_SLOTS = 65


@dataclass
class Player:
	"""One player observed in the entity list."""
	controller_addr: int = 0
	pawn_addr: int = 0
	health: int = 0
	armor: int = 0
	team: int = 0
	life_state: int = 0
	name: str = ""
	position: tuple[float, float, float] = (0.0, 0.0, 0.0)
	eye_angles: tuple[float, float, float] = (0.0, 0.0, 0.0)
	is_alive: bool = False
	bones: dict = field(default_factory=dict)
	distance: float = 0.0
	bbox_mins: tuple[float, float, float] | None = None
	bbox_maxs: tuple[float, float, float] | None = None
	# Exact-model chams require the full transform slab and model path
	bone_slab: "np.ndarray | None" = None
	model_path: str = ""
	bone_base: int = 0
	scene_node: int = 0
	weapon_item_definition: int = 0


def read_game_time(mem) -> float | None:
	"""Current game time from CGlobalVars (m_flCurrentTime at +0x30); sanity 0 < t < 1e6, or None when unreadable."""
	# Accept either a global-vars pointer or an inline struct address
	if not Offsets.dwGlobalVars:
		return None
	try:
		base = mem.client(Offsets.dwGlobalVars)
	except Exception:
		return None
	candidates = []
	try:
		candidates.append(mem.read_long(base))
	except Exception:
		pass
	candidates.append(base)
	for address in candidates:
		if not address or address < 0x10000 or address > 0x7FFFFFFFFFFF:
			continue
		try:
			current_time = mem.read_float(address + 0x30)
		except Exception:
			continue
		if 0.0 < current_time < 1.0e6:
			return current_time
	return None


def _bone_is_sane(bone_x: float, bone_y: float, bone_z: float, origin_x: float, origin_y: float, origin_z: float) -> bool:
	'''Accept one bone only when it is nonzero and within 100 units of the pawn origin.'''
	return (
		abs(bone_x) < 1.0e6
		and abs(bone_y) < 1.0e6
		and abs(bone_z) < 1.0e6
		and (bone_x != 0.0 or bone_y != 0.0 or bone_z != 0.0)
		and abs(bone_x - origin_x) < 100.0
		and abs(bone_y - origin_y) < 100.0
		and abs(bone_z - origin_z) < 100.0
	)


class EntitySystem:
	"""Reads all player entities from CS2 memory."""
	def __init__(self, mem: MemoryReader):
		self.mem = mem
		self._local_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
		self._scan_local = local()
		self._empty_log_t = 0.0
		self._name_cache: dict[int, tuple[object, str]] = {}
		self._model_cache: dict[tuple[int, int], tuple[object, str]] = {}
		self._last_name_flush: float = 0.0
		self._weapon_cache: dict[int, tuple[int, int, int]] = {}

	def _active_weapon_item(self, pawn: int) -> int:
		'''Read the current full weapon handle on the state worker; only resolve economy metadata when that handle changes.'''
		try:
			services = self.mem.read_long(pawn + Netvars.m_pWeaponServices)
			if not 0x10000 <= services <= 0x7FFFFFFFFFFF:
				raise ValueError("Invalid weapon services")
			handle = self.mem.read_uint(services + Netvars.m_hActiveWeapon)
			if not 0 < handle < 0xFFFFFFFF or not handle & 0x7FFF:
				raise ValueError("Invalid active weapon handle")
			cached = self._weapon_cache.get(pawn)
			if cached is not None and cached[:2] == (services, handle):
				return cached[2]
			weapon = self.resolve_handle(handle)
			if not 0x10000 <= weapon <= 0x7FFFFFFFFFFF:
				raise ValueError("Unresolved active weapon")
			item = self.mem.read_int(weapon + Netvars.m_AttributeManager + Netvars.m_Item + Netvars.m_iItemDefinitionIndex) & 0xFFFF
			if self.mem.read_uint(services + Netvars.m_hActiveWeapon) != handle:
				raise ValueError("Weapon changed during metadata read")
			self._weapon_cache[pawn] = (services, handle, item)
			return item
		except Exception:
			self._weapon_cache.pop(pawn, None)
			return 0

	def _entity_list_entry(self, entity_list: int, handle_or_index: int) -> int:
		'''Read the entity-list chunk pointer for a handle or index.'''
		return self.mem.read_long(entity_list + 0x8 * ((handle_or_index & 0x7FFF) >> 9) + 0x10)

	def _entity_from_list_entry(self, list_entry: int, handle_or_index: int) -> int:
		'''Read the entity pointer at a chunk slot.'''
		return self.mem.read_long(list_entry + 0x70 * (handle_or_index & 0x1FF)) or 0

	def _controller_block(self, first_list_entry: int) -> bytes | None:
		'''Batch-read all controller slots in one RPM call.'''
		try:
			block = self.mem.read_bytes(first_list_entry, 0x70 * _CONTROLLER_SLOTS)
			return block if len(block) == 0x70 * _CONTROLLER_SLOTS else None
		except Exception:
			return None

	def _controller_at(self, first_list_entry: int, controllers_block: bytes | None, entity_index: int) -> int:
		'''Read one controller pointer from the batch block or the chunk fallback.'''
		if controllers_block is not None:
			return unpack_from("<Q", controllers_block, 0x70 * entity_index)[0]
		return self.mem.read_long(first_list_entry + 0x70 * (entity_index & 0x1FF))

	def _read_player_name(self, controller: int) -> str:
		'''Read the raw or sanitised controller name; the sanitised buffer still carries bot names when the raw one is empty.'''
		for attr in ("m_iszPlayerName", "m_sSanitizedPlayerName"):
			offset = getattr(Netvars, attr, 0)
			if not offset:
				continue
			name = self.mem.read_string(controller + offset).strip()
			if name and len(name) <= 64 and "\ufffd" not in name and all(character.isprintable() for character in name):
				return name
		return ""

	def _cached_player_name(self, controller: int, fallback_index: int | None = None) -> str:
		'''Keep a name tied to the exact process whose memory supplied it.'''
		process = getattr(self.mem, "pm", None)
		cached = self._name_cache.get(controller)
		if cached is not None and cached[0] is process:
			return cached[1]
		name = self._read_player_name(controller)
		if not name and fallback_index is not None:
			name = f"Player_{fallback_index}"
		if name and getattr(self.mem, "pm", None) is process:
			self._name_cache[controller] = (process, name)
		return name

	def get_local_team(self) -> int:
		controller = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerController))
		if not controller:
			return 0
		return self.mem.read_byte(controller + Netvars.m_iTeamNum)

	def get_local_pawn(self) -> int:
		'''Resolve via controller handle — dwLocalPlayerPawn can be stale.'''
		controller = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerController))
		if not controller:
			return self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerPawn))
		handle = self.mem.read_uint(controller + Netvars.m_hPlayerPawn)
		if not handle:
			return self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerPawn))
		entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		if not entity_list:
			return self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerPawn))
		pawn_list_entry = self._entity_list_entry(entity_list, handle)
		if not pawn_list_entry:
			return self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerPawn))
		pawn = self._entity_from_list_entry(pawn_list_entry, handle)
		return pawn or self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerPawn))

	def get_local_position(self) -> tuple[float, float, float]:
		return self._local_pos

	def get_scan_local_position(self) -> tuple[float, float, float]:
		'''Position belonging to this thread's most recent entity scan.'''
		return getattr(self._scan_local, "position", (0.0, 0.0, 0.0))

	def resolve_handle(self, handle: int) -> int:
		'''Resolve a full 32-bit entity handle (index | serial) to an entity pointer.'''
		if not handle or handle == 0xFFFFFFFF:
			return 0
		try:
			entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
			if not entity_list:
				return 0
			list_entry = self._entity_list_entry(entity_list, handle)
			if not list_entry:
				return 0
			return self._entity_from_list_entry(list_entry, handle)
		except Exception:
			return 0

	def entity_index_to_pawn(self, idx: int) -> int:
		'''Resolve a raw entity index (1..2048) to a pawn pointer.'''
		if idx <= 0 or idx >= 2048:
			return 0
		entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		if not entity_list:
			return 0
		list_entry = self._entity_list_entry(entity_list, idx)
		if not list_entry:
			return 0
		return self._entity_from_list_entry(list_entry, idx)

	def get_local_eye_yaw(self) -> float:
		'''Yaw of local player view (degrees). Reads client.dll + dwViewAngles because local-pawn m_angEyeAngles stays zero on current builds.'''
		try:
			view_angles = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
			return float(view_angles[1])
		except Exception:
			return 0.0

	def get_observer_target_pawn(self, local_pawn: int) -> int:
		'''Return the pawn address currently spectated by the local player, or 0 if not spectating.'''
		if not local_pawn:
			return 0
		try:
			observer_services = self.mem.read_long(local_pawn + Netvars.m_pObserverServices)
			if not observer_services:
				fallback_pawn = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerPawn))
				if fallback_pawn and fallback_pawn != local_pawn:
					observer_services = self.mem.read_long(fallback_pawn + Netvars.m_pObserverServices)
				if not observer_services:
					return 0
			mode = self.mem.read_int(observer_services + Netvars.m_iObserverMode)
			life_state = self.mem.read_byte(local_pawn + Netvars.m_lifeState)
			# Dead cameras may latch the observer mode after the pawn dies
			if mode == 0 and life_state == 0:
				return 0
			handle = self.mem.read_uint(observer_services + Netvars.m_hObserverTarget)
			if not handle or handle == 0xFFFFFFFF:
				return 0
			entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
			if not entity_list:
				return 0
			list_entry = self._entity_list_entry(entity_list, handle)
			if not list_entry:
				return 0
			target = self._entity_from_list_entry(list_entry, handle)
			if not target:
				return 0
			# Observer targets may resolve through a controller or directly to a pawn
			inner = self.mem.read_uint(target + Netvars.m_hPlayerPawn)
			if inner and inner != 0xFFFFFFFF:
				pawn_list_entry = self._entity_list_entry(entity_list, inner)
				if pawn_list_entry:
					pawn = self._entity_from_list_entry(pawn_list_entry, inner)
					if pawn:
						scene_node = self.mem.read_long(pawn + Netvars.m_pGameSceneNode)
						if scene_node:
							return pawn
			return target
		except Exception:
			return 0

	def get_spectators_of_local(self) -> list[str]:
		"""Names of players whose observer target is the local pawn."""
		spectators: list[str] = []
		try:
			local_pawn = self.get_local_pawn()
			entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		except Exception:
			return spectators
		if not local_pawn or not entity_list:
			return spectators

		first_list_entry = self.mem.read_long(entity_list + 0x10)
		if not first_list_entry:
			return spectators

		controllers_block = self._controller_block(first_list_entry)

		for entity_index in range(1, _CONTROLLER_SLOTS):
			try:
				controller = self._controller_at(first_list_entry, controllers_block, entity_index)
				if not controller:
					continue
				pawn_handle = self.mem.read_uint(controller + Netvars.m_hPlayerPawn)
				if not pawn_handle:
					continue
				list_entry = self._entity_list_entry(entity_list, pawn_handle)
				if not list_entry:
					continue
				pawn = self._entity_from_list_entry(list_entry, pawn_handle)
				if not pawn or pawn == local_pawn:
					continue
				if self.mem.read_byte(pawn + Netvars.m_lifeState) == 0:
					continue
				observer_services = self.mem.read_long(pawn + Netvars.m_pObserverServices)
				if not observer_services:
					continue
				mode = self.mem.read_int(observer_services + Netvars.m_iObserverMode)
				if mode in (0, 1):
					continue
				target_handle = self.mem.read_uint(observer_services + Netvars.m_hObserverTarget)
				if not target_handle or target_handle == 0xFFFFFFFF:
					continue
				target_entry = self._entity_list_entry(entity_list, target_handle)
				if not target_entry:
					continue
				target = self._entity_from_list_entry(target_entry, target_handle)
				if target != local_pawn:
					continue
				name = self._cached_player_name(controller)
				if name:
					spectators.append(name)
			except Exception:
				continue
		return spectators

	def get_health_snapshot(self, local_team: int) -> dict[int, tuple[str, int, int, bool]]:
		"""{pawn: (name, health, life_state, headshot)} for all non-local enemies, keeping dead pawns so the event system sees alive -> dead transitions."""
		snapshot: dict[int, tuple[str, int, int, bool]] = {}
		try:
			entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		except Exception:
			return snapshot
		if not entity_list:
			return snapshot

		local_pawn = self.get_local_pawn()
		first_list_entry = self.mem.read_long(entity_list + 0x10)
		if not first_list_entry:
			return snapshot

		controllers_block = self._controller_block(first_list_entry)

		for entity_index in range(1, _CONTROLLER_SLOTS):
			try:
				controller = self._controller_at(first_list_entry, controllers_block, entity_index)
				if not controller:
					continue
				pawn_handle = self.mem.read_uint(controller + Netvars.m_hPlayerPawn)
				if not pawn_handle:
					continue
				list_entry = self._entity_list_entry(entity_list, pawn_handle)
				if not list_entry:
					continue
				pawn = self._entity_from_list_entry(list_entry, pawn_handle)
				if not pawn or pawn == local_pawn:
					continue
				team = self.mem.read_byte(pawn + Netvars.m_iTeamNum)
				if team < 2 or team == local_team:
					continue
				health = self.mem.read_int(pawn + Netvars.m_iHealth)
				if not (0 <= health <= 200):
					continue
				life_state = self.mem.read_byte(pawn + Netvars.m_lifeState)
				name = self._cached_player_name(controller)
				# The victim stores the killing blow, independently of damage estimates
				try:
					headshot = bool(self.mem.read_byte(pawn + Netvars.m_bRagdollDamageHeadshot))
				except Exception:
					headshot = False
				snapshot[pawn] = (name or "Enemy", health, life_state, headshot)
			except Exception:
				continue
		return snapshot

	def refresh_bone_slabs(self, players, full: bool = True, update_visuals: bool = False) -> None:
		"""Re-read bones immediately before drawing so boxes, skeleton and chams share one fresh slab."""
		count = 128 if full else 23
		size = count * Netvars.BONE_STRIDE
		needed = {bone for pair in Netvars.SKELETON_PAIRS for bone in pair} if update_visuals else ()
		for player in players:
			base = getattr(player, "bone_base", 0)
			if not base:
				continue
			try:
				raw = self.mem.read_bytes(base, size)
			except Exception:
				continue
			if len(raw) != size:
				continue
			if full:
				player.bone_slab = np.frombuffer(raw, dtype=np.float32).reshape(128, 8)
			if not update_visuals:
				continue
			# Validate fresh bones against the fresh origin so a stale snapshot cannot leave old bones beside new chams
			scene_node = getattr(player, "scene_node", 0)
			if scene_node:
				try:
					origin = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
					if any(origin):
						player.position = origin
				except Exception:
					pass
			origin_x, origin_y, origin_z = player.position
			bones = {}
			for bone_index in needed:
				bone_offset = bone_index * Netvars.BONE_STRIDE
				bone_x, bone_y, bone_z = unpack_from("<3f", raw, bone_offset)
				if _bone_is_sane(bone_x, bone_y, bone_z, origin_x, origin_y, origin_z):
					bones[bone_index] = (bone_x, bone_y, bone_z)
			player.bones = bones

	def _read_model_path(self, controller: int, scene_node: int) -> str:
		'''vmdl path from CModelState::m_ModelName (pointer, then inline).'''
		key = (controller, scene_node)
		process = getattr(self.mem, "pm", None)
		cached = self._model_cache.get(key)
		if cached is not None and cached[0] is process:
			return cached[1]
		state = scene_node + Netvars.m_modelState
		path = ""
		pointer = self.mem.read_long(state + Netvars.m_ModelName)
		candidates = []
		if pointer > 0x10000:
			candidates.append(self.mem.read_string(pointer, 260))
		candidates.append(self.mem.read_string(state + Netvars.m_ModelName, 260))
		for name in candidates:
			name = name.replace("\\", "/").lower()
			if "agents/models/" in name or "characters/models/" in name:
				path = name
				break
		# Keep the process identity so an old in-flight read cannot populate a replacement session
		if getattr(self.mem, "pm", None) is process:
			self._model_cache[key] = (process, path)
		return path

	def get_players_and_health(self, *, local_team: int, read_bones: bool = True, read_bone_slab: bool = False, read_weapons: bool = False):
		'''One entity walk, including dead enemies in the event-health feed.'''
		health_rows = {}
		players = self.get_players(read_bones=read_bones, read_bone_slab=read_bone_slab, read_weapons=read_weapons,
			_health_out=health_rows, _health_team=local_team)
		# Resolve names after scanning so dead pawns can still appear in events
		health = {}
		for pawn, (controller, hit_points, life_state, headshot) in health_rows.items():
			try:
				health[pawn] = (self._cached_player_name(controller) or "Enemy", hit_points, life_state, headshot)
			except Exception:
				continue
		return players, health

	def get_players(self, read_bones: bool = True, read_bone_slab: bool = False, *, read_weapons: bool = False,
		_health_out: dict | None = None, _health_team: int = 0) -> list[Player]:
		'''Iterate the entity list and return all alive non-local players.'''
		players: list[Player] = []
		if not read_weapons:
			self._weapon_cache.clear()
		self._scan_local.position = (0.0, 0.0, 0.0)
		# Publish the completed walk once; the render thread may read position concurrently
		local_pos = (0.0, 0.0, 0.0)
		entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		if not entity_list:
			return players

		local_pawn = self.get_local_pawn()
		if local_pawn:
			try:
				life_state = self.mem.read_byte(local_pawn + Netvars.m_lifeState)
			except Exception:
				life_state = 0

			anchor = local_pawn
			if life_state != 0:
				spectator_target = self.get_observer_target_pawn(local_pawn)
				if spectator_target:
					anchor = spectator_target

			scene_node = self.mem.read_long(anchor + Netvars.m_pGameSceneNode)
			if scene_node:
				local_pos = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
		self._scan_local.position = local_pos
		self._local_pos = local_pos

		needed_bones = {bone for pair in Netvars.SKELETON_PAIRS for bone in pair} if read_bones else ()

		# Read neighbouring controller fields together
		controller_offsets = (Netvars.m_hPlayerPawn, Netvars.m_iPawnHealth, Netvars.m_iPawnArmor)
		controller_minimum = min(controller_offsets)
		controller_span = max(controller_offsets) - controller_minimum + 4

		stats = {
			"list_entries": 0,
			"controllers": 0,
			"pawn_handles": 0,
			"pawns": 0,
			"non_local": 0,
			"alive_hp": 0,
			"alive_life": 0,
			"teamed": 0,
			"scene_nodes": 0,
			"exceptions": 0,
		}

		# Player controllers occupy slots 1..64 in the first entity chunk
		first_list_entry = self.mem.read_long(entity_list + 0x10)
		if not first_list_entry:
			return players

		controllers_block = self._controller_block(first_list_entry)

		chunk_cache: dict[int, int] = {0: first_list_entry}

		pawn_offsets = (Netvars.m_pGameSceneNode, Netvars.m_pCollision, Netvars.m_iHealth, Netvars.m_lifeState, Netvars.m_iTeamNum)
		pawn_minimum = min(pawn_offsets)
		pawn_maximum = max(pawn_offsets)
		pawn_span = pawn_maximum - pawn_minimum + 8

		now = perf_counter()
		if now - self._last_name_flush > 5.0:
			self._name_cache.clear()
			self._model_cache.clear()
			self._weapon_cache.clear()
			self._last_name_flush = now

		for entity_index in range(1, _CONTROLLER_SLOTS):
			try:
				controller = self._controller_at(first_list_entry, controllers_block, entity_index)
				if not controller:
					continue
				stats["controllers"] += 1

				# Retry scalar fields when the batched controller read is short
				pawn_handle = 0
				armor = 0
				try:
					controller_data = self.mem.read_bytes(controller + controller_minimum, controller_span)
					if len(controller_data) == controller_span:
						pawn_handle = unpack_from("<I", controller_data, Netvars.m_hPlayerPawn - controller_minimum)[0]
						armor = unpack_from("<i", controller_data, Netvars.m_iPawnArmor - controller_minimum)[0]
				except Exception:
					pawn_handle = 0
				if not pawn_handle:
					pawn_handle = self.mem.read_uint(controller + Netvars.m_hPlayerPawn)
				if not pawn_handle:
					continue
				stats["pawn_handles"] += 1

				chunk_index = (pawn_handle & 0x7FFF) >> 9
				pawn_list_entry = chunk_cache.get(chunk_index)
				if pawn_list_entry is None:
					pawn_list_entry = self.mem.read_long(entity_list + 0x8 * chunk_index + 0x10)
					chunk_cache[chunk_index] = pawn_list_entry
				if not pawn_list_entry:
					continue

				pawn = self.mem.read_long(pawn_list_entry + 0x70 * (pawn_handle & 0x1FF)) or 0
				if not pawn:
					continue
				stats["pawns"] += 1
				if pawn == local_pawn:
					continue
				stats["non_local"] += 1

				pawn_data = self.mem.read_bytes(pawn + pawn_minimum, pawn_span)
				if len(pawn_data) == pawn_span:
					health = unpack_from("<i", pawn_data, Netvars.m_iHealth - pawn_minimum)[0]
					life_state = pawn_data[Netvars.m_lifeState - pawn_minimum]
					team = pawn_data[Netvars.m_iTeamNum - pawn_minimum]
					scene_node = unpack_from("<Q", pawn_data, Netvars.m_pGameSceneNode - pawn_minimum)[0]
					collision = unpack_from("<Q", pawn_data, Netvars.m_pCollision - pawn_minimum)[0]
				else:
					health = self.mem.read_int(pawn + Netvars.m_iHealth)
					if health <= 0 and _health_out is None:
						continue
					life_state = self.mem.read_byte(pawn + Netvars.m_lifeState)
					if life_state != 0 and _health_out is None:
						continue
					team = self.mem.read_byte(pawn + Netvars.m_iTeamNum)
					if team < 2:
						continue
					scene_node = collision = 0
					if health > 0 and life_state == 0:
						scene_node = self.mem.read_long(pawn + Netvars.m_pGameSceneNode)
						if scene_node:
							collision = self.mem.read_long(pawn + Netvars.m_pCollision)

				if _health_out is not None and 0 <= health <= 200 and team >= 2 and team != _health_team:
					try:
						headshot = bool(self.mem.read_byte(pawn + Netvars.m_bRagdollDamageHeadshot))
					except Exception:
						headshot = False
					_health_out[pawn] = (controller, health, life_state, headshot)
				if health <= 0 or life_state != 0 or team < 2 or not scene_node:
					continue

				stats["alive_hp"] += 1
				stats["alive_life"] += 1
				stats["teamed"] += 1
				stats["scene_nodes"] += 1

				position = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
				eye_angles = self.mem.read_vec3(pawn + Netvars.m_angEyeAngles)
				name = self._cached_player_name(controller, entity_index)

				bones = {}
				bone_slab = None
				bone_base = 0
				model_path = ""
				if read_bone_slab:
					model_path = self._read_model_path(controller, scene_node)
				if read_bones:
					try:
						bone_base = self.mem.read_long(scene_node + Netvars.m_boneArray)
					except Exception:
						bone_base = 0
					if 0x10000 <= bone_base <= 0x7FFFFFFFFFFF:
						# Reject stale bone arrays located far from the fresh pawn origin
						origin_x, origin_y, origin_z = position

						# Exact models need 128 transforms; skeleton-only rendering needs 23
						slab_count = 128 if read_bone_slab else 23
						raw_bones = self.mem.read_bytes(bone_base, slab_count * Netvars.BONE_STRIDE)
						if read_bone_slab and len(raw_bones) == 128 * Netvars.BONE_STRIDE:
							bone_slab = np.frombuffer(raw_bones, dtype=np.float32).reshape(128, 8)
						if len(raw_bones) >= 23 * Netvars.BONE_STRIDE:
							for bone_index in needed_bones:
								if bone_index < 23:
									bone_offset = bone_index * Netvars.BONE_STRIDE
									bone_x, bone_y, bone_z = unpack_from("<3f", raw_bones, bone_offset)
									if _bone_is_sane(bone_x, bone_y, bone_z, origin_x, origin_y, origin_z):
										bones[bone_index] = (bone_x, bone_y, bone_z)
						else:
							for bone_index in needed_bones:
								try:
									bone_position = self.mem.read_vec3(bone_base + bone_index * Netvars.BONE_STRIDE)
								except Exception:
									continue
								if _bone_is_sane(*bone_position, origin_x, origin_y, origin_z):
									bones[bone_index] = bone_position

				bbox_mins = bbox_maxs = None
				if collision:
					try:
						raw_bounds = self.mem.read_bytes(collision + Netvars.m_vecMins, 24)
						if len(raw_bounds) == 24:
							mins = unpack_from("<3f", raw_bounds, 0)
							maxs = unpack_from("<3f", raw_bounds, 12)
							if all(abs(value) < 512.0 for value in (*mins, *maxs)) and maxs != mins:
								bbox_mins, bbox_maxs = mins, maxs
					except Exception:
						pass

				local_x, local_y, local_z = local_pos
				delta_x = position[0] - local_x
				delta_y = position[1] - local_y
				delta_z = position[2] - local_z
				distance = (delta_x * delta_x + delta_y * delta_y + delta_z * delta_z) ** 0.5

				players.append(
					Player(
						controller_addr=controller,
						pawn_addr=pawn,
						health=health,
						armor=armor,
						team=team,
						life_state=life_state,
						name=name,
						position=position,
						eye_angles=eye_angles,
						is_alive=True,
						bones=bones,
						distance=distance,
						bbox_mins=bbox_mins,
						bbox_maxs=bbox_maxs,
						bone_slab=bone_slab,
						model_path=model_path,
						bone_base=bone_base,
						scene_node=scene_node,
						weapon_item_definition=self._active_weapon_item(pawn) if read_weapons else 0,
					)
				)
			except Exception:
				stats["exceptions"] += 1

		if not players:
			now = perf_counter()
			if now - self._empty_log_t > 5.0:
				self._empty_log_t = now
				print(f"[diag] entity walk EMPTY: el=0x{entity_list:X} local_pawn=0x{local_pawn:X} stats={stats}")

		return players
