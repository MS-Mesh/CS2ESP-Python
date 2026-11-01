"""
Correlate observed local shots, the server-hit counter and timestamped victim health changes. Click requests are not shots; missing or ambiguous telemetry never
becomes a named hit or miss; automatic-fire misses are reported once per burst, not once per render-loop observation.
"""

from dataclasses import dataclass, field, replace
from math import isfinite
from time import perf_counter

from cs2esp.features.event_system import EventSystem, EventType


# Local-shot correlation windows (seconds); counters may arrive slightly before their local observation
_CLOCK_SKEW_S = 0.1
_SNAPSHOT_HOLD_S = 0.1
_CONTEXT_SYNC_S = 0.035
_IMPACT_CORRELATION_S = 0.4
_PING_WAIT_FACTOR = 1.5


# Stable core agent-rig bones; unmapped attachment and cloth bones stay generic
_KILL_BODY_PARTS = {
	1: "pelvis", 2: "stomach", 3: "chest", 4: "chest", 5: "chest", 6: "neck", 7: "head",
	**dict.fromkeys(range(8, 12), "left arm"), **dict.fromkeys(range(12, 17), "right arm"),
	**dict.fromkeys(range(17, 20), "left leg"), **dict.fromkeys(range(20, 23), "right leg"),
}


@dataclass(slots=True)
class _ShotBurst:
	identifier: int
	started_at: float
	last_shot_at: float
	pawn: int
	name: str
	count: int
	miss_ids: set[int] = field(default_factory=set)
	miss_reasons: dict[int, tuple[int, str, str]] = field(default_factory=dict)
	rendered_reasons: set[tuple[int, str, str]] = field(default_factory=set)
	reason_contexts: dict[tuple[int, str, str], dict] = field(default_factory=dict)
	dirty: bool = False


@dataclass(slots=True)
class _ObservedShot:
	identifier: int
	observed_at: float
	burst: _ShotBurst
	confirmed: bool = False
	uncertain: bool = False
	missed: bool = False
	acknowledged: bool = False
	outcome_pawn: int = 0
	outcome_dead: bool = False
	outcome_damage: int = 0
	miss_pawn: int = 0
	miss_name: str = ""
	context: dict = field(default_factory=dict)


@dataclass(slots=True)
class _HealthChange:
	observed_at: float
	pawn: int
	name: str
	damage: int
	health: int
	dead: bool
	headshot: bool
	body_part: str
	damage_bone: int | None


class CombatEventTracker:
	"""One producer for local combat outcomes; all counters are read-only observations from the same local pawn."""
	BURST_GAP_S = 0.25
	MISS_WAIT_S = 0.65
	CORRELATION_S = 2.0
	HEALTH_WAIT_S = 0.35

	def __init__(self, events: EventSystem) -> None:
		self.events = events
		self._serial = 0
		self._shot_serial = 0
		self.reset()

	def reset(self) -> None:
		'''Forget unfinished outcomes on pawn changes, death, focus loss or disabled tracking without clearing visible cards.'''
		self._pawn = 0
		self._shots = None
		self._hits = None
		self._fired_at = None
		self._target = (0, "shot", 0.0)
		self._health = {}
		self._health_at = float('-inf')
		self._bursts: list[_ShotBurst] = []
		self._pending: list[_ObservedShot] = []
		self._changes: list[_HealthChange] = []
		self._receipts: list[tuple[float, int]] = []
		self._previous_context = {}
		self._counter_shots_at: list[float] = []
		self._timestamp_shots_at: list[float] = []
		self._reported_damage: list[list] = []

	def _track_health(self, valid_health: dict, snapshot_at: float, damage_bones: dict | None) -> None:
		'''Record health edges since the previous snapshot; a respawn drops that pawn's pending outcomes.'''
		if snapshot_at <= self._health_at:
			return
		for pawn, entry in valid_health.items():
			previous = self._health.get(pawn)
			if previous is not None and entry[2] == 0 and (entry[1] > previous[1] or previous[2] != 0):
				self._changes = [change for change in self._changes if change.pawn != pawn]
				for burst in self._bursts:
					if burst.pawn == pawn:
						for shot in self._pending:
							if shot.burst is burst:
								shot.uncertain = True
			if previous is None or previous[1] <= 0 or previous[2] != 0:
				continue
			dead = entry[1] <= 0 or entry[2] != 0
			if entry[1] < previous[1] or dead:
				headshot = dead and len(entry) > 3 and entry[3] is True
				damage_bone = damage_bones.get(pawn) if damage_bones is not None else None
				body_part = "head" if headshot else _KILL_BODY_PARTS.get(damage_bone, "body")
				self._changes.append(_HealthChange(snapshot_at, pawn, entry[0], max(0, previous[1] - entry[1]), entry[1], dead, headshot, body_part, damage_bone))
		self._health, self._health_at = valid_health, snapshot_at

	def _confirm_shots(self, received_at: float, count: int, burst: _ShotBurst | None = None, *, acknowledged: bool = True,
		shot_id: int | None = None, pawn: int = 0) -> list[int]:
		'''Associate a server acknowledgement with observed bullets; correct provisional misses using their identities.'''
		candidates = [shot for shot in self._pending if not shot.confirmed and -_CLOCK_SKEW_S <= received_at - shot.observed_at <= self.CORRELATION_S
			and (burst is None or shot.burst is burst) and (shot_id is None or shot.identifier == shot_id)]
		candidates.sort(key=lambda shot: (shot.miss_pawn != pawn if pawn else not shot.miss_pawn, abs(received_at - shot.observed_at)))
		confirmed = candidates[:count]
		for shot in confirmed:
			shot.confirmed = True
			shot.acknowledged = acknowledged
			if shot.missed:
				shot.burst.miss_ids.discard(shot.identifier)
				shot.burst.miss_reasons.pop(shot.identifier, None)
				shot.burst.dirty = True
		return [shot.identifier for shot in confirmed]

	def _emit_damage(self, change: _HealthChange, burst: _ShotBurst | None, shot_ids: list[int], source: str, context: dict) -> None:
		'''One formatting path covers fast health edges and later server reconciliation without inventing hitgroups.'''
		shots = [shot for shot in self._pending if shot.identifier in shot_ids]
		shot_context = shots[0].context if shots else context
		weapon = context.get("damage_weapons", {}).get(change.pawn) or (shot_context.get("weapon_key", "") if shots else "")
		if weapon == "weapon_inferno" and len(context.get("recent_throw_weapons", [])) == 1:
			throw_weapon = context["recent_throw_weapons"][0]
			if throw_weapon in ("weapon_molotov", "weapon_incgrenade"):
				weapon = throw_weapon
		body_part, estimated = change.body_part, False
		bone_source = context.get("bone_sources", {}).get(change.pawn, "")
		if not change.dead and bone_source != "impact":
			body_part = _KILL_BODY_PARTS.get(shot_context.get("aim_bones", {}).get(change.pawn), "body") if shots else "body"
			estimated = body_part != "body"
		kind = EventType.HEADSHOT if change.headshot else EventType.KILL if change.dead else EventType.HIT
		if "knife" in weapon or weapon == "weapon_bayonet":
			kind = EventType.KNIFE
		elif weapon == "weapon_taser":
			kind = EventType.ZEUS
		verb = "Headshot killed" if change.headshot else "Killed" if change.dead else "Hit"
		location = "" if change.headshot else f" {'near' if estimated else 'in'} the {body_part}"
		data = {"pawn": change.pawn, "name": change.name, "damage": change.damage, "hp": max(0, change.health),
			"headshot": change.headshot or (not estimated and bone_source == "impact" and change.damage_bone == 7),
			"one_tap": change.dead and burst is not None and burst.count == 1, "shot_count": burst.count if burst is not None else 0,
			"burst_id": burst.identifier if burst is not None else None, "shot_ids": shot_ids, "hit_location": body_part,
			"hit_location_source": "aim_geometry" if estimated else bone_source or "unknown", "hit_location_estimated": estimated,
			"damage_bone": change.damage_bone, "confirmed_by": source, "weapon_key": weapon,
			"item_definition": shot_context.get("item_definition", 0) if weapon == shot_context.get("weapon_key") else 0,
			"outcome": "kill" if change.dead else "hit"}
		if shot_ids:
			data["slot"] = ("combat_outcome", tuple(shot_ids), change.pawn)
		previous_damage = max((shot.outcome_damage for shot in shots if shot.outcome_pawn == change.pawn), default=0)
		if source != "controller_damage_record" and change.damage > previous_damage:
			self._reported_damage.append([perf_counter(), change.pawn, change.damage - previous_damage])
		for shot in shots:
			shot.outcome_pawn, shot.outcome_dead, shot.outcome_damage = change.pawn, change.dead, change.damage
		remaining = "" if change.dead else f" ({max(0, change.health)} remaining)"
		self.events.emit(kind, kind.value.upper(), f"{verb} {change.name}{location} for {change.damage}{remaining}", data=data)

	def _miss_reason(self, shot: _ObservedShot) -> str:
		'''These are shot-context estimates, not a server-provided miss explanation.'''
		context = shot.context
		weapon = context.get("weapon_key", "")
		if "knife" in weapon or weapon == "weapon_bayonet":
			return "melee range / aim deviation"
		if weapon == "weapon_taser":
			return "Zeus range / aim deviation"
		if context.get("on_ground") is False:
			return "airborne inaccuracy"
		if float(context.get("speed", 0.0) or 0.0) > 130.0:
			return "moving inaccuracy"
		if "aim_on_target_pawns" in context and not context["aim_on_target_pawns"]:
			return "aim deviation"
		if float(context.get("recoil_index", 0.0) or 0.0) > 1.0:
			return "recoil / weapon spread"
		return "weapon spread"

	def update(
		self, local_pawn: int, shots_fired: int | None, server_hits: int | None, health: dict, *, target_pawn: int = 0,
		snapshot_at: float | None = None, ping_ms: float | None = None, fired_at: float | None = None, damage_bones: dict | None = None,
		shot_context: dict | None = None,
	) -> None:
		'''Publish fresh local-shot health edges immediately; reconcile server counters later. Estimated areas and miss causes keep explicit source tags.'''
		now = perf_counter()
		context = shot_context or {}
		snapshot_at = now if snapshot_at is None else min(now, snapshot_at)
		valid_health = {pawn: entry for pawn, entry in health.items() if len(entry) >= 3 and 0 <= entry[1] <= 200 and entry[2] in (0, 1, 2)}
		shots_fired = shots_fired if isinstance(shots_fired, int) and 0 <= shots_fired <= 1000 else None
		server_hits = server_hits if isinstance(server_hits, int) and 0 <= server_hits < 2**30 else None
		fired_at = fired_at if fired_at is not None and isfinite(fired_at) and fired_at > 0.0 else None
		if not local_pawn:
			self.reset()
			return
		if self._pawn != local_pawn:
			self.reset()
			self._pawn = local_pawn
			self._shots, self._hits = shots_fired, server_hits
			self._fired_at = fired_at
			self._health, self._health_at = valid_health, snapshot_at
			self._target = (target_pawn, valid_health.get(target_pawn, ("shot",))[0], now)
			self._previous_context = dict(context)
			return

		self._track_health(valid_health, snapshot_at, damage_bones)
		counter_delta = shots_fired - self._shots if shots_fired is not None and self._shots is not None else 0
		time_advanced = fired_at > self._fired_at if fired_at is not None and self._fired_at is not None else False
		self._counter_shots_at = [created for created in self._counter_shots_at if now - created <= self.CORRELATION_S]
		self._timestamp_shots_at = [created for created in self._timestamp_shots_at if now - created <= self.CORRELATION_S]
		if counter_delta < 0:
			self._timestamp_shots_at.clear()
		shot_count = 0
		if 0 < counter_delta <= 32:
			paired = min(counter_delta, len(self._timestamp_shots_at))
			del self._timestamp_shots_at[:paired]
			shot_count = counter_delta - paired
			self._counter_shots_at.extend([now] * shot_count)
		if time_advanced:
			if self._counter_shots_at:
				self._counter_shots_at.clear()
			else:
				shot_count += 1
				self._timestamp_shots_at.append(now)
		if context.get("weapon_key") in ("weapon_flashbang", "weapon_hegrenade", "weapon_smokegrenade", "weapon_molotov", "weapon_incgrenade",
			"weapon_decoy", "weapon_tagrenade", "weapon_c4", "weapon_healthshot", "weapon_tablet"):
			shot_count = 0
			self._counter_shots_at.clear()
			self._timestamp_shots_at.clear()
		if shot_count:
			previous_target, previous_name, target_at = self._target
			shot_target = previous_target if previous_target and now - target_at <= _SNAPSHOT_HOLD_S else target_pawn
			shot_name = previous_name if shot_target == previous_target else valid_health.get(shot_target, ("shot",))[0]
			last = self._bursts[-1] if self._bursts else None
			if last is not None and now - last.last_shot_at <= self.BURST_GAP_S:
				last.count += shot_count
				last.last_shot_at = now
				if last.pawn != shot_target:
					last.pawn, last.name = 0, "shot"
			else:
				self._serial += 1
				self._bursts.append(_ShotBurst(self._serial, now, now, shot_target, shot_name or "shot", shot_count))
			burst = self._bursts[-1]
			previous_context = self._previous_context
			miss_targets = context.get("miss_targets")
			if miss_targets is None:
				miss_targets = {target_pawn: {"name": valid_health[target_pawn][0], "source": "crosshair"}} if target_pawn in valid_health else {}
			if not miss_targets and now - previous_context.get("captured_at", 0.0) <= _CONTEXT_SYNC_S:
				miss_targets = {pawn: evidence for pawn, evidence in previous_context.get("miss_targets", {}).items()
					if pawn in context.get("aim_on_target_pawns", []) and evidence.get("source") == "crosshair"}
			eligible = [(pawn, evidence) for pawn, evidence in miss_targets.items() if pawn in valid_health
				and valid_health[pawn][1] > 0 and valid_health[pawn][2] == 0]
			eligible.sort(key=lambda item: (item[1].get("source") != "crosshair", item[1].get("angle", 0.0), item[0]))
			miss_pawn, miss_evidence = eligible[0] if eligible else (0, {})
			shot_state = previous_context if now - previous_context.get("captured_at", 0.0) <= _SNAPSHOT_HOLD_S and (
				previous_context.get("weapon_key") == context.get("weapon_key")) and (
				previous_context.get("aim_bones") or not context.get("aim_bones")) else context
			shot_state = dict(shot_state, target_pawn=shot_target)
			for _index in range(shot_count):
				self._shot_serial += 1
				self._pending.append(_ObservedShot(self._shot_serial, now, burst, miss_pawn=miss_pawn,
					miss_name=valid_health[miss_pawn][0] if miss_pawn else "", context=dict(shot_state, miss_target_source=miss_evidence.get("source"))))
		self._shots = shots_fired
		self._fired_at = fired_at
		self._target = (target_pawn, valid_health.get(target_pawn, ("shot",))[0], now)
		self._previous_context = dict(context)

		if server_hits is None or self._hits is None or server_hits < self._hits:
			for shot in self._pending:
				shot.uncertain = True
		elif server_hits > self._hits:
			count = server_hits - self._hits
			early = [shot for shot in self._pending if shot.confirmed and not shot.acknowledged and now - shot.observed_at <= self.CORRELATION_S]
			for shot in early[:count]:
				shot.acknowledged = True
				deaths = [change for change in self._changes if change.pawn == shot.outcome_pawn and change.dead and not shot.outcome_dead
					and 0.0 <= change.observed_at - shot.observed_at <= self.HEALTH_WAIT_S]
				newer = any(not pending.confirmed and pending.observed_at > shot.observed_at and (
					pending.context.get("target_pawn") == shot.outcome_pawn or shot.outcome_pawn in pending.context.get("aim_on_target_pawns", []))
					for pending in self._pending)
				if deaths and not newer:
					change = deaths[-1]
					self._emit_damage(replace(change, damage=change.damage + shot.outcome_damage), shot.burst, [shot.identifier], "server_hit_counter", context)
					self._changes.remove(change)
			count -= min(count, len(early))
			if count:
				self._receipts.append((now, count))
		self._hits = server_hits

		waiting = []
		for received_at, count in self._receipts:
			bursts = [burst for burst in self._bursts if -_CLOCK_SKEW_S <= received_at - burst.last_shot_at <= self.CORRELATION_S]
			changes = [change for change in self._changes if abs(change.observed_at - received_at) <= self.HEALTH_WAIT_S
				and any(burst.started_at - _CLOCK_SKEW_S <= change.observed_at <= burst.last_shot_at + self.CORRELATION_S for burst in bursts)]
			if count == 1 and len({change.pawn for change in changes}) == 1:
				changes = changes[-1:]
			matches = [(burst, change) for change in changes for burst in reversed(bursts) if burst.pawn == change.pawn]
			if not matches and len(changes) == 1 and bursts:
				matches = [(bursts[-1], changes[0])]
			if len({change.pawn for change in changes}) > 1:
				matches = []
			if matches:
				seen = set()
				remaining_hits = count
				for burst, change in matches:
					if id(change) in seen or len(seen) >= count:
						continue
					seen.add(id(change))
					shot_ids = self._confirm_shots(received_at, max(1, remaining_hits - (len(changes) - len(seen))), burst, pawn=change.pawn)
					remaining_hits -= len(shot_ids)
					self._emit_damage(change, burst, shot_ids, "server_hit_counter", context)
				self._changes = [change for change in self._changes if not any(
					id(matched) in seen and matched.pawn == change.pawn and change.observed_at <= matched.observed_at for matched in changes)]
				continue
			if now - received_at < self.HEALTH_WAIT_S:
				waiting.append((received_at, count))
			elif bursts:
				shot_ids = self._confirm_shots(received_at, count)
				if shot_ids:
					self.events.emit(EventType.HIT, "HIT", "Hit confirmed", data={"confirmed_by": "server_hit_counter", "target_unknown": True, "shot_ids": shot_ids})
		self._receipts = waiting

		for change in tuple(self._changes):
			injury_at = context.get("injury_times", {}).get(change.pawn)
			time_matches = bool(self._counter_shots_at) or injury_at is None or fired_at is None or abs(injury_at - fired_at) <= _IMPACT_CORRELATION_S
			candidates = [shot for shot in self._pending if context and time_matches and not shot.confirmed
				and -_CLOCK_SKEW_S <= change.observed_at - shot.observed_at <= _IMPACT_CORRELATION_S
				and (shot.context.get("target_pawn") == change.pawn or change.pawn in shot.context.get("aim_on_target_pawns", [])
					or context.get("bone_sources", {}).get(change.pawn) == "impact")]
			if candidates:
				shot = min(candidates, key=lambda value: abs(change.observed_at - value.observed_at))
				shot_ids = self._confirm_shots(now, 1, shot.burst, acknowledged=False, shot_id=shot.identifier)
				self._emit_damage(change, shot.burst, shot_ids, "local_impact" if context.get("bone_sources", {}).get(change.pawn) == "impact"
					else "local_shot_health", context)
				self._changes.remove(change)
		for record in context.get("owned_damage", []):
			remaining = record["health_removed"]
			for reported in self._reported_damage:
				if reported[1] != record["pawn"] or now - reported[0] > self.CORRELATION_S:
					continue
				consumed = min(remaining, reported[2])
				reported[2] -= consumed
				remaining -= consumed
				if remaining <= 0.0:
					break
			for change in tuple(self._changes):
				if change.pawn == record["pawn"] and remaining > 0.0 and abs(change.damage - remaining) <= 1.0:
					self._emit_damage(change, None, [], "controller_damage_record", context)
					self._changes.remove(change)
					break
		self._reported_damage = [reported for reported in self._reported_damage if reported[2] > 0.0 and now - reported[0] <= self.CORRELATION_S]

		miss_wait = self.MISS_WAIT_S + min(1.0, max(0.0, float(ping_ms or 0.0)) / 1000.0 * _PING_WAIT_FACTOR)
		for shot in self._pending:
			burst = shot.burst
			if shot.confirmed or shot.uncertain or shot.missed or server_hits is None or not shot.miss_pawn:
				continue
			if shot.miss_pawn not in valid_health:
				shot.uncertain = True
				continue
			if now - shot.observed_at < miss_wait or self._health_at < shot.observed_at + miss_wait or not valid_health:
				continue
			if any(-_CLOCK_SKEW_S <= received_at - shot.observed_at <= miss_wait for received_at, _count in waiting):
				continue
			shot.missed = True
			burst.miss_ids.add(shot.identifier)
			reason = self._miss_reason(shot)
			group = (shot.miss_pawn, shot.miss_name, reason)
			burst.miss_reasons[shot.identifier] = group
			burst.reason_contexts[group] = shot.context
			burst.dirty = True
		for burst in self._bursts:
			if not burst.dirty:
				continue
			burst.dirty = False
			reasons = set(burst.miss_reasons.values())
			for group in reasons | burst.rendered_reasons:
				pawn, name, reason = group
				slot = ("combat_miss", burst.identifier, pawn, reason)
				identifiers = [identifier for identifier, value in burst.miss_reasons.items() if value == group]
				if not identifiers:
					self.events.drop_slot(slot)
					continue
				miss_count = len(identifiers)
				count_text = f": {miss_count} shots" if miss_count > 1 else ""
				detail = f"Missed {name}{count_text} due to {reason}"
				shot_state = burst.reason_contexts.get(group, {})
				data = {"pawn": pawn, "name": name, "shot_count": miss_count, "burst_id": burst.identifier,
					"shot_ids": identifiers, "reason": reason, "reason_estimated": True, "slot": slot,
					"target_source": shot_state.get("miss_target_source"),
					"weapon_key": shot_state.get("weapon_key", ""), "item_definition": shot_state.get("item_definition", 0)}
				if not self.events.refresh_slot(slot, detail, data):
					self.events.emit(EventType.MISS, "MISS", detail, data=data)
			burst.rendered_reasons = reasons
		self._bursts = [burst for burst in self._bursts if now - burst.last_shot_at <= self.CORRELATION_S]
		self._pending = [shot for shot in self._pending if now - shot.observed_at <= self.CORRELATION_S]
		self._changes = [change for change in self._changes if now - change.observed_at <= self.CORRELATION_S]
