"""
Fresh event reads independent of the bone/mesh worker. Shot-time pose supplies an explicitly estimated area when no engine impact bone exists; death bones and
validated impact models keep their own source tags. Reads never change game state or user configuration.
"""

from math import atan2, cos, degrees, isfinite, radians, sin, sqrt
from struct import unpack_from
from time import perf_counter

from cs2esp.entity import read_game_time
from cs2esp.features.event_system import EventType
from cs2esp.offsets import Netvars, Offsets
from cs2esp.visuals.weapon_icons import weapon_icon_key


_GRENADES = frozenset(("weapon_flashbang", "weapon_hegrenade", "weapon_smokegrenade", "weapon_molotov", "weapon_incgrenade", "weapon_decoy",
	"weapon_tagrenade", "weapon_firebomb", "weapon_diversion", "weapon_frag_grenade", "weapon_snowball", "weapon_bumpmine"))


def _vector_header(raw: bytes, embedded: bool = False) -> tuple[int, int]:
	'''Validate the ordinary and embedded Source 2 vector layouts before following their storage.'''
	if len(raw) < 24:
		return 0, 0
	ordinary = (unpack_from("<Q", raw, 0)[0], unpack_from("<i", raw, 16)[0])
	networked = (unpack_from("<Q", raw, 8)[0], unpack_from("<i", raw, 0)[0])
	for pointer, count in ((networked, ordinary) if embedded else (ordinary, networked)):
		if count == 0:
			continue
		if 0 < count <= 128 and 0x10000 <= pointer <= 0x7FFFFFFFFFFF:
			return pointer, count
	return 0, 0


class CombatTelemetry:
	"""Capture fresh health and a bounded set of damage records without another entity walk or a waiting thread."""
	def __init__(self, mem, entities, events) -> None:
		self.mem, self.entities, self.events = mem, entities, events
		self.reset()

	def reset(self) -> None:
		'''Do not carry injury, ownership or grenade state across local-pawn and focus transitions.'''
		self._pawn = 0
		self._health = {}
		self._fired_at = None
		self._shots = None
		self._damage_totals = {}
		self._damage_polled_at = 0.0
		self._damage_services = 0
		self._damage_baselined = False
		self._grenades = {}
		self._throw_seen = {}
		self._throws = []
		self._slot = 0
		self._slot_checked_at = 0.0

	def _local_slot(self, now: float) -> int:
		'''Only our controller bit can establish local visibility for a near-miss; team spotted is insufficient.'''
		if now - self._slot_checked_at < 2.0:
			return self._slot
		self._slot_checked_at, self._slot = now, 0
		controller = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerController))
		entity_list = self.mem.read_long(self.mem.client(Offsets.dwEntityList))
		chunk = self.mem.read_long(entity_list + 0x10) if entity_list else 0
		block = self.mem.read_bytes(chunk, 0x70 * 65) if chunk else b""
		if controller and len(block) == 0x70 * 65:
			self._slot = next((slot for slot in range(1, 65) if unpack_from("<Q", block, slot * 0x70)[0] == controller), 0)
		return self._slot

	def _impact_bone(self, pawn: int, fired_at: float, eye: tuple) -> int | None:
		'''Accept an impact only when its owner, creation time and bullet origin agree with this local shot.'''
		pointer, count = _vector_header(self.mem.read_bytes(pawn + Netvars.m_vecBulletHitModels, 24))
		if not pointer:
			return None
		pointers = self.mem.read_bytes(pointer, count * 8)
		if len(pointers) != count * 8:
			return None
		for index in range(count - 1, -1, -1):
			model = unpack_from("<Q", pointers, index * 8)[0]
			if not 0x10000 <= model <= 0x7FFFFFFFFFFF:
				continue
			raw = self.mem.read_bytes(model + Netvars.m_iBoneIndex, Netvars.m_vecStartPos + 12 - Netvars.m_iBoneIndex)
			if len(raw) < 28:
				continue
			bone, parent = unpack_from("<iI", raw, 0)
			created = unpack_from("<f", raw, Netvars.m_flTimeCreated - Netvars.m_iBoneIndex)[0]
			origin = unpack_from("<3f", raw, Netvars.m_vecStartPos - Netvars.m_iBoneIndex)
			if not raw[Netvars.m_bIsHit - Netvars.m_iBoneIndex] or not 0 <= bone < 128 or not isfinite(created):
				continue
			if abs(created - fired_at) > 0.4 or any(not isfinite(value) for value in origin):
				continue
			if sum((first - second) ** 2 for first, second in zip(origin, eye)) > 80.0**2:
				continue
			if parent and self.entities.resolve_handle(parent) != pawn:
				continue
			return bone
		return None

	def _owned_damage(self, now: float) -> list[dict]:
		'''Controller damage records prove the attacker for non-bullet damage; their first observation is only a baseline.'''
		if now - self._damage_polled_at < 0.1:
			return []
		self._damage_polled_at = now
		controller = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerController))
		services = self.mem.read_long(controller + Netvars.m_pDamageServices) if controller else 0
		if not 0x10000 <= services <= 0x7FFFFFFFFFFF:
			return []
		if services != self._damage_services:
			self._damage_services = services
			self._damage_totals.clear()
			self._damage_baselined = False
		header = self.mem.read_bytes(services + Netvars.m_DamageList, 24)
		pointer, count = _vector_header(header, embedded=True)
		if not pointer:
			if len(header) == 24 and unpack_from("<i", header, 0)[0] == 0:
				self._damage_baselined = True
			return []
		raw = self.mem.read_bytes(pointer, count * 0x78)
		if len(raw) != count * 0x78:
			return []
		changes = []
		for index in range(count):
			record = raw[index * 0x78:(index + 1) * 0x78]
			damager = unpack_from("<I", record, Netvars.m_hPlayerControllerDamager)[0]
			recipient = unpack_from("<I", record, Netvars.m_hPlayerControllerRecipient)[0]
			if not damager or not recipient or self.entities.resolve_handle(damager) != controller:
				continue
			victim_controller = self.entities.resolve_handle(recipient)
			pawn_handle = self.mem.read_uint(victim_controller + Netvars.m_hPlayerPawn) if victim_controller else 0
			pawn = self.entities.resolve_handle(pawn_handle) if pawn_handle else 0
			removed = unpack_from("<f", record, Netvars.m_flActualHealthRemoved)[0]
			hits = unpack_from("<i", record, Netvars.m_iNumHits)[0]
			if not pawn or not isfinite(removed) or not 0.0 <= removed <= 1000000.0 or not 0 <= hits <= 1000000:
				continue
			previous = self._damage_totals.get((damager, recipient), (0.0, 0) if self._damage_baselined else None)
			self._damage_totals[(damager, recipient)] = (removed, hits)
			if previous is not None and removed > previous[0] and hits >= previous[1]:
				changes.append({"pawn": pawn, "health_removed": removed - previous[0], "hit_count": hits - previous[1]})
		self._damage_baselined = True
		return changes

	def capture(self, local_pawn: int, health_hint: dict, players: list, local_pos: tuple, held_weapon, fired_at: float,
		*, shots_fired: int | None = None, target_pawn: int = 0) -> tuple[dict, dict, dict]:
		'''Read health before rendering work and freeze the input/weapon/aim context used by each observed shot.'''
		now = perf_counter()
		if local_pawn != self._pawn:
			self.reset()
			self._pawn = local_pawn
		local_pos = local_pos or (0.0, 0.0, 0.0)
		weapon_key = weapon_icon_key(getattr(held_weapon, "weapon_key", ""), int(getattr(held_weapon, "item_definition", 0) or 0))
		context = {"weapon_key": weapon_key, "item_definition": int(getattr(held_weapon, "item_definition", 0) or 0), "captured_at": now,
			"aim_bones": {}, "aim_on_target_pawns": [], "miss_targets": {}, "injury_times": {}, "bone_sources": {}, "damage_weapons": {}}
		try:
			velocity = self.mem.read_vec3(local_pawn + Netvars.m_vecAbsVelocity)
			context["speed"] = sqrt(sum(value * value for value in velocity)) if all(isfinite(value) for value in velocity) else 0.0
			context["on_ground"] = bool(self.mem.read_uint(local_pawn + Netvars.m_fFlags) & 1)
			angles = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
			if any(not isfinite(value) for value in angles):
				raise ValueError("Invalid view angles")
			pitch, yaw = radians(angles[0]), radians(angles[1])
			direction = (cos(pitch) * cos(yaw), cos(pitch) * sin(yaw), -sin(pitch))
			offset = self.mem.read_vec3(local_pawn + Netvars.m_vecViewOffset)
			eye = tuple(local_pos[index] + offset[index] for index in range(3))
			if any(not isfinite(value) for value in eye):
				raise ValueError("Invalid eye position")
		except Exception:
			direction, eye = None, tuple(local_pos)
		new_shot = (self._fired_at is not None and fired_at > self._fired_at) or (
			shots_fired is not None and self._shots is not None and shots_fired > self._shots)
		self._fired_at = fired_at
		self._shots = shots_fired
		for player in players:
			if direction is None or player.pawn_addr not in health_hint:
				continue
			bones = player.bones
			if not bones and new_shot and player.scene_node:
				base = self.mem.read_long(player.scene_node + Netvars.m_boneArray)
				data = self.mem.read_bytes(base, 23 * Netvars.BONE_STRIDE) if base else b""
				if len(data) == 23 * Netvars.BONE_STRIDE:
					bones = {index: unpack_from("<3f", data, index * Netvars.BONE_STRIDE) for index in range(1, 23)}
			best = None
			for bone, point in bones.items():
				if not 1 <= bone <= 22 or not all(isfinite(value) for value in point):
					continue
				delta = tuple(point[index] - eye[index] for index in range(3))
				along = sum(delta[index] * direction[index] for index in range(3))
				if along <= 0.0:
					continue
				side_sq = max(0.0, sum(value * value for value in delta) - along * along)
				radius = 7.0 if bone <= 5 else 4.0 if bone <= 7 else 3.0
				score = side_sq / (radius * radius)
				if best is None or score < best[0]:
					best = (score, bone, along)
			if best is not None:
				context["aim_bones"][player.pawn_addr] = best[1]
				if best[0] <= 1.0:
					context["aim_on_target_pawns"].append(player.pawn_addr)
			intent_point = player.position[0], player.position[1], player.position[2] + 40.0
			intent_delta = tuple(intent_point[index] - eye[index] for index in range(3))
			intent_along = sum(intent_delta[index] * direction[index] for index in range(3))
			intent_side = sqrt(max(0.0, sum(value * value for value in intent_delta) - intent_along * intent_along))
			angle = degrees(atan2(intent_side, intent_along))
			if player.pawn_addr == target_pawn and intent_along > 0.0 and (angle <= 8.0 or (best is not None and best[0] <= 4.0)):
				context["miss_targets"][player.pawn_addr] = {"name": player.name, "source": "crosshair", "angle": angle}
			elif best is not None and best[0] <= 4.0 and intent_along > 0.0:
				bone = bones[best[1]]
				bone_delta = tuple(bone[index] - eye[index] for index in range(3))
				ray_side = sqrt(max(0.0, sum(value * value for value in bone_delta) - best[2] * best[2]))
				near_angle = degrees(atan2(ray_side, best[2]))
				if near_angle <= 1.5:
					slot = self._local_slot(now)
					mask = self.mem.read_bytes(player.pawn_addr + Netvars.m_entitySpottedState + Netvars.m_bSpottedByMask, 8) if slot else b""
					if len(mask) == 8 and int.from_bytes(mask, "little") & (1 << (slot - 1)):
						context["miss_targets"][player.pawn_addr] = {"name": player.name, "source": "local_visibility", "angle": near_angle}
		weapon_address = int(getattr(held_weapon, "weapon_addr", 0) or 0)
		if weapon_address and bool(getattr(held_weapon, "is_firearm", False)):
			try:
				context["recoil_index"] = self.mem.read_float(weapon_address + Netvars.m_flRecoilIndex)
				context["accuracy_penalty"] = self.mem.read_float(weapon_address + Netvars.m_fAccuracyPenalty)
			except Exception:
				pass
		if weapon_address and weapon_key in _GRENADES:
			self._grenades[weapon_address] = weapon_key
		if self._grenades:
			game_time = read_game_time(self.mem)
			for address, key in tuple(self._grenades.items()):
				throw_time = self.mem.read_float(address + Netvars.m_fThrowTime)
				redraw = bool(self.mem.read_byte(address + Netvars.m_bRedraw))
				if not isfinite(throw_time) or throw_time <= 0.0 or self._throw_seen.get(address) == throw_time:
					if address != weapon_address:
						self._grenades.pop(address, None)
					continue
				if not redraw and (game_time is None or throw_time > game_time):
					continue
				self._throw_seen[address] = throw_time
				self._throws.append((now, key))
				self.events.emit(EventType.GRENADE_THROW, "THROW", "Threw " + key.removeprefix("weapon_").replace("grenade", " grenade"),
					data={"weapon_key": key, "throw_time": throw_time})
				self._grenades.pop(address, None)
		if len(self._throw_seen) > 128:
			self._throw_seen = dict(list(self._throw_seen.items())[-64:])
		self._throws = [(created, key) for created, key in self._throws if now - created <= 30.0]
		context["recent_throw_weapons"] = list(dict.fromkeys(key for _created, key in self._throws))
		health, damage_bones = {}, {}
		for pawn, entry in health_hint.items():
			try:
				start = min(Netvars.m_iHealth, Netvars.m_lifeState)
				size = max(Netvars.m_iHealth + 4, Netvars.m_lifeState + 1) - start
				raw = self.mem.read_bytes(pawn + start, size)
				if len(raw) != size:
					continue
				hp = unpack_from("<i", raw, Netvars.m_iHealth - start)[0]
				life = raw[Netvars.m_lifeState - start]
				if not 0 <= hp <= 200 or life not in (0, 1, 2):
					continue
				previous = self._health.get(pawn)
				changed = previous is not None and (hp < previous[0] or life != previous[1])
				headshot = bool(self.mem.read_byte(pawn + Netvars.m_bRagdollDamageHeadshot)) if life != 0 or hp == 0 else False
				health[pawn] = (entry[0], hp, life, headshot)
				if changed:
					injury_at = self.mem.read_float(pawn + Netvars.m_flTimeOfLastInjury)
					context["injury_times"][pawn] = injury_at
					impact = self._impact_bone(pawn, injury_at if isfinite(injury_at) and injury_at > 0.0 else fired_at, eye)
					if impact is not None:
						damage_bones[pawn], context["bone_sources"][pawn] = impact, "impact"
					elif hp <= 0 or life != 0:
						damage_bones[pawn], context["bone_sources"][pawn] = self.mem.read_int(pawn + Netvars.m_nRagdollDamageBone), "ragdoll"
					if hp <= 0 or life != 0:
						context["damage_weapons"][pawn] = weapon_icon_key(self.mem.read_string(pawn + Netvars.m_szRagdollDamageWeaponName, 64))
				self._health[pawn] = (hp, life)
			except Exception:
				continue
		context["owned_damage"] = self._owned_damage(now)
		self._health = {pawn: state for pawn, state in self._health.items() if pawn in health_hint}
		return health, damage_bones, context
