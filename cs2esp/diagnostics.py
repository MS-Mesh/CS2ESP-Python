"""
Read-only health checks for the CS2 ESP runtime.
The runner attaches its own MemoryReader, verifies live offsets and returns grouped HealthReport rows for the Qt panel or CLI.
Every check is defensive: failed probes become rows, not crashes.
"""

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any

from cs2esp.memory import MemoryReader
from cs2esp.offsets import CGlowProperty, Netvars, Offsets, apply_sigscan, verify_netvars


class CheckStatus(str, Enum):
	"""Severity-ordered check outcomes rendered by the Qt panel."""
	PASS = "PASS"
	WARN = "WARN"
	FAIL = "FAIL"
	SKIP = "SKIP"


@dataclass(slots=True)
class CheckResult:
	"""Single health-check row."""
	category: str
	name: str
	status: CheckStatus
	reason: str = ""
	value: Any = None


@dataclass(slots=True)
class HealthReport:
	"""Full health-check output rendered by the Qt panel or CLI."""
	rows: list[CheckResult] = field(default_factory=list)
	timestamp: float = field(default_factory=time)
	attached: bool = False
	cs2_pid: int = 0

	def add(self, category: str, name: str, status: CheckStatus, reason: str = "", value: Any = None) -> None:
		'''Append one row in display order.'''
		self.rows.append(CheckResult(category, name, status, reason, value))

	def summary(self) -> dict:
		'''Tally every status across all rows.'''
		tally = {status.value: 0 for status in CheckStatus}
		for row in self.rows:
			tally[row.status.value] += 1
		return tally

	def categories(self) -> list[str]:
		'''Unique category names in row order.'''
		return list(dict.fromkeys(row.category for row in self.rows))


def run_full_health_check(config) -> HealthReport:
	'''Attach to CS2 and run every precondition probe.'''
	report = HealthReport()

	mem = _safe_attach(report)
	if mem is None:
		_add_skip_rows_for_unattached(report)
		_check_anti_detect_config(config, report)
		_check_rapidfire_autopistol(config, report)
		_check_wallhack_master(config, report)
		_check_box_esp(config, report)
		_check_skeleton_esp(config, report)
		_check_labels(config, report)
		_check_health_labels(config, report)
		_check_crosshair(config, report)
		_check_fps_counter(config, report)
		_check_norecoil_scripts(report)
		return report

	_check_sigscan(mem, report)
	_check_verify_netvars(mem, report)

	_check_local_pawn(mem, report)
	_check_entity_list(mem, report)

	_check_glow(mem, config, report)
	_check_triggerbot(mem, config, report)
	_check_glow_trigger(mem, config, report)
	_check_bhop(mem, config, report)
	_check_rapidfire_autopistol(config, report)
	_check_awareness(mem, config, report)
	_check_radar(mem, config, report)
	_check_visuals(mem, report)
	_check_wallhack_master(config, report)
	_check_box_esp(config, report)
	_check_skeleton_esp(config, report)
	_check_labels(config, report)
	_check_health_labels(config, report)
	_check_crosshair(config, report)
	_check_head_outline(mem, config, report)
	_check_chams(config, report)
	_check_snapline(mem, config, report)
	_check_fps_counter(config, report)
	_check_norecoil_scripts(report)

	_check_anti_detect_config(config, report)

	mem.detach()
	return report


def _check_anti_detect_config(config, report: HealthReport) -> None:
	'''Run the anti-detect, overlay and write-guard checks in display order.'''
	_check_anti_detect(config, report)
	_check_anti_detect_overlay(config, report)
	_check_anti_detect_writes(config, report)


def _safe_attach(report: HealthReport) -> MemoryReader | None:
	'''Attach to CS2 and add one Process row explaining the result.'''
	mem = MemoryReader()
	try:
		ok = mem.attach()
	except Exception as error:
		mem.detach()
		report.add("Process", "Attach to cs2.exe", CheckStatus.FAIL, f"attach raised: {error!r}")
		return None

	if not ok:
		mem.detach()
		report.add("Process", "Attach to cs2.exe", CheckStatus.FAIL, "CS2 is not running, or the menu lacks the rights "
				   "needed to open its process. Launch CS2 first; if " "that doesn't fix it, run the menu as the same " "Windows user who launched CS2.")
		return None

	pid = getattr(mem.pm, "process_id", 0) or 0
	report.attached = True
	report.cs2_pid = int(pid)
	report.add("Process", "Attach to cs2.exe", CheckStatus.PASS, f"PID {pid}, client.dll base 0x{mem.client_base:X}")
	return mem


def _add_skip_rows_for_unattached(report: HealthReport) -> None:
	'''Add SKIP rows for every check that needs a live attach.'''
	skip_reason = "CS2 not attached"
	skip_categories = [("Offsets", "Sigscan (client.dll patterns)"), ("Offsets", "Verify netvars (live drift correction)"),
		("Memory", "Local pawn readable"), ("Memory", "Entity list walkable"), ("Glow", "CGlowProperty readable"),
		("Triggerbot", "m_iIDEntIndex readable"), ("Triggerbot", "Distance bands populated"), ("Triggerbot", "Glow trigger resolver (entity_index_to_pawn)"),
		("BHop", "m_fFlags readable"), ("Awareness", "View angles readable"), ("Radar", "Local position readable"),
		("Visuals", "Scene-node pointer readable"), ("Visuals", "Bone array readable"), ("Visuals", "Head bone readable (skeleton head outline)"),
		("Visuals", "View matrix readable (snapline / chams)"), ("Visuals", "Chams pixel-probe module importable")]
	for cat, name in skip_categories:
		report.add(cat, name, CheckStatus.SKIP, skip_reason)


def _check_sigscan(mem, report: HealthReport) -> None:
	'''Re-run the sigscan against the loaded client.dll and report matched patterns.'''
	try:
		result = apply_sigscan(mem.pm)
	except Exception as error:
		report.add("Offsets", "Sigscan (client.dll patterns)", CheckStatus.FAIL, f"sigscan raised: {error!r}")
		return

	expected = ("dwLocalPlayerController", "dwEntityList", "dwViewMatrix", "dwLocalPlayerPawn", "dwViewAngles")
	missing = [offset_name for offset_name in expected if offset_name not in result and getattr(Offsets, offset_name, 0) == 0]
	matched = [offset_name for offset_name in expected if offset_name in result]

	if missing:
		report.add("Offsets", "Sigscan (client.dll patterns)", CheckStatus.FAIL, f"unresolved: {', '.join(missing)} — Valve probably "
				   f"changed the surrounding code. Wait for a " f"cs2-dumper update or extend cs2esp/sigscan.py " f"with new patterns.")
	elif len(matched) < len(expected):
		report.add("Offsets", "Sigscan (client.dll patterns)",
				   CheckStatus.WARN, f"{len(matched)}/{len(expected)} patterns matched; " f"the rest came from dumper JSON cache", value=matched)
	else:
		report.add("Offsets", "Sigscan (client.dll patterns)", CheckStatus.PASS, f"all {len(matched)} patterns matched live", value=matched)


def _check_verify_netvars(mem, report: HealthReport) -> None:
	'''Run the live netvar verifier and report how many drifted.'''
	try:
		n_fixed = verify_netvars(mem)
	except Exception as error:
		report.add("Offsets", "Verify netvars (live drift correction)", CheckStatus.FAIL, f"verify_netvars raised: {error!r}")
		return

	if n_fixed == 0:
		report.add("Offsets", "Verify netvars (live drift correction)", CheckStatus.PASS, "no drift detected — published offsets agree with live memory")
	else:
		report.add("Offsets", "Verify netvars (live drift correction)", CheckStatus.WARN,
				   f"auto-corrected {n_fixed} offset(s) that had drifted " f"from the published dumper values — CS2 has had a " f"recent struct shift")


def _check_local_pawn(mem, report: HealthReport) -> None:
	'''Verify dwLocalPlayerPawn dereferences to a sensible pawn.'''
	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
	except Exception as error:
		report.add("Memory", "Local pawn readable", CheckStatus.FAIL, f"read raised: {error!r}")
		return

	if not pawn:
		report.add("Memory", "Local pawn readable", CheckStatus.WARN, "pawn pointer is null — not currently in a match")
		return

	if pawn < 0x10000000000 or pawn > 0x7FFFFFFFFFFF:
		report.add("Memory", "Local pawn readable",
				   CheckStatus.FAIL, f"pawn pointer 0x{pawn:X} is outside the expected " f"x64 userspace range — dwLocalPlayerPawn is stale")
		return

	try:
		health = mem.read_int(pawn + Netvars.m_iHealth)
		team = mem.read_byte(pawn + Netvars.m_iTeamNum)
	except Exception as error:
		report.add("Memory", "Local pawn readable", CheckStatus.FAIL, f"pawn deref raised: {error!r}", value=pawn)
		return

	if 0 <= health <= 120 and team in (0, 1, 2, 3):
		report.add("Memory", "Local pawn readable", CheckStatus.PASS, f"HP={health}, team={team}", value=pawn)
	else:
		report.add("Memory", "Local pawn readable",
				   CheckStatus.WARN, f"pawn deref produced suspicious values " f"(HP={health}, team={team}) — possibly between matches", value=pawn)


def _check_entity_list(mem, report: HealthReport) -> None:
	'''Verify the entity list walks and produces non-null slots.'''
	try:
		entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
	except Exception as error:
		report.add("Memory", "Entity list walkable", CheckStatus.FAIL, f"read raised: {error!r}")
		return

	if not entity_list:
		report.add("Memory", "Entity list walkable", CheckStatus.FAIL, "entity-list pointer is null — sigscan didn't " "resolve dwEntityList correctly")
		return

	# Sample slots 1..64 and count resolved entity pointers
	resolved = 0
	for index in range(1, 64):
		try:
			list_entry = mem.read_long(entity_list + 0x8 * ((index & 0x7FFF) >> 9) + 0x10)
			if not list_entry:
				continue
			entity = mem.read_long(list_entry + 0x70 * (index & 0x1FF))
			if entity and 0x10000000000 < entity < 0x7FFFFFFFFFFF:
				resolved += 1
		except Exception:
			continue

	if resolved == 0:
		report.add("Memory", "Entity list walkable", CheckStatus.WARN, "list pointer is valid but no slots resolve to live " "entities — likely not in a match yet")
	else:
		report.add("Memory", "Entity list walkable", CheckStatus.PASS, f"{resolved}/63 sampled slots resolved to live entities", value=entity_list)


def _check_glow(mem, config, report: HealthReport) -> None:
	'''Verify CGlowProperty is reachable on the local pawn.'''
	if not bool(getattr(config.glow, "enabled", False)):
		report.add("Glow", "CGlowProperty readable", CheckStatus.SKIP, "glow feature disabled in config")
		return

	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
		if not pawn:
			report.add("Glow", "CGlowProperty readable", CheckStatus.SKIP, "no local pawn — not in match")
			return
		# Local pawn's glow struct is probed read-only
		glow_addr = pawn + Netvars.m_Glow
		glow_type = mem.read_int(glow_addr + CGlowProperty.m_iGlowType)
		if glow_type < -1 or glow_type > 16:
			report.add("Glow", "CGlowProperty readable", CheckStatus.WARN,
					   f"m_Glow at +0x{Netvars.m_Glow:X} reads as " f"{glow_type} — looks like garbage. m_Glow " f"offset may have drifted in a recent patch.")
			return
		report.add("Glow", "CGlowProperty readable", CheckStatus.PASS, f"m_iGlowType={glow_type} (sensible)", value=glow_addr)
	except Exception as error:
		report.add("Glow", "CGlowProperty readable", CheckStatus.FAIL, f"read raised: {error!r}")

def _check_triggerbot(mem, config, report: HealthReport) -> None:
	'''Check triggerbot offset and distance-band prerequisites.'''
	if not bool(getattr(config.triggerbot, "enabled", False)):
		report.add("Triggerbot", "m_iIDEntIndex readable", CheckStatus.SKIP, "triggerbot disabled")
		report.add("Triggerbot", "Distance bands populated", CheckStatus.SKIP, "triggerbot disabled")
		return

	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
		if not pawn:
			report.add("Triggerbot", "m_iIDEntIndex readable", CheckStatus.SKIP, "no local pawn — not in match")
		else:
			entity_index = mem.read_int(pawn + Netvars.m_iIDEntIndex)
			# Valid values: 0 (nothing under crosshair) or 1..2048 (entity index); else offset is bad
			if entity_index == 0 or (0 < entity_index < 4096):
				report.add("Triggerbot", "m_iIDEntIndex readable", CheckStatus.PASS, f"reads as {entity_index} (sensible: 0 or 1..2048)", value=Netvars.m_iIDEntIndex)
			else:
				report.add("Triggerbot", "m_iIDEntIndex readable", CheckStatus.FAIL, f"reads as 0x{entity_index:X} — offset is stale. "
						   f"verify_netvars should normally auto-fix " f"this; if it didn't, the brute-scan window " f"may need widening.")
	except Exception as error:
		report.add("Triggerbot", "m_iIDEntIndex readable", CheckStatus.FAIL, f"read raised: {error!r}")

	# Distance bands: structural check only
	bands = getattr(config.triggerbot, "distance_bands", None)
	if not bands:
		report.add("Triggerbot", "Distance bands populated",
				   CheckStatus.FAIL, "distance_bands list is empty — the triggerbot has " "nothing to match against and will never fire")
	else:
		band_count = len(bands)
		report.add("Triggerbot", "Distance bands populated", CheckStatus.PASS, f"{band_count} band(s) configured", value=band_count)


def _check_bhop(mem, config, report: HealthReport) -> None:
	'''Verify m_fFlags on the local pawn.'''
	if not bool(getattr(config.bhop, "enabled", False)):
		report.add("BHop", "m_fFlags readable", CheckStatus.SKIP, "bhop disabled")
		return
	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
		if not pawn:
			report.add("BHop", "m_fFlags readable", CheckStatus.SKIP, "no local pawn — not in match")
			return
		flags = mem.read_int(pawn + Netvars.m_fFlags)
		# m_fFlags is a bitfield of ~24 flags, normally well under 0xFFFF; huge values mean the offset drifted
		if 0 <= flags < 0x100000:
			report.add("BHop", "m_fFlags readable",
					   CheckStatus.PASS, f"flags=0x{flags:X} (FL_ONGROUND bit is " f"{'set' if flags & 1 else 'clear'})", value=Netvars.m_fFlags)
		else:
			report.add("BHop", "m_fFlags readable", CheckStatus.FAIL, f"flags=0x{flags:X} — value looks like garbage, " f"m_fFlags offset may have drifted")
	except Exception as error:
		report.add("BHop", "m_fFlags readable", CheckStatus.FAIL, f"read raised: {error!r}")


def _check_rapidfire_autopistol(config, report: HealthReport) -> None:
	'''Surface rapid-fire and auto-pistol wiring; both are SendInput-only.'''
	rapidfire_enabled = bool(getattr(config.rapidfire, "enabled", False))
	autopistol_enabled = bool(getattr(config.autopistol, "enabled", False))
	report.add("Rapid-fire", "Feature wiring",
			   CheckStatus.PASS if rapidfire_enabled else CheckStatus.SKIP, "enabled — synthetic clicks driven by hotkey hold" if rapidfire_enabled else "disabled")
	report.add("Auto-pistol", "Feature wiring",
			   CheckStatus.PASS if autopistol_enabled else CheckStatus.SKIP, "enabled — synthetic clicks driven by hotkey hold" if autopistol_enabled else "disabled")


def _check_awareness(mem, config, report: HealthReport) -> None:
	'''Verify view angles are readable and in range.'''
	if not bool(getattr(config.awareness, "enabled", False)):
		report.add("Awareness", "View angles readable", CheckStatus.SKIP, "awareness disabled")
		return
	try:
		view_angles_addr = mem.client(Offsets.dwViewAngles)
		pitch = mem.read_float(view_angles_addr)
		yaw = mem.read_float(view_angles_addr + 4)
		if -90.5 < pitch < 90.5 and -180.5 < yaw < 180.5:
			report.add("Awareness", "View angles readable", CheckStatus.PASS, f"pitch={pitch:+.2f}° yaw={yaw:+.2f}°")
		else:
			report.add("Awareness", "View angles readable", CheckStatus.FAIL, f"pitch={pitch} yaw={yaw} — out of range, " f"dwViewAngles is stale")
	except Exception as error:
		report.add("Awareness", "View angles readable", CheckStatus.FAIL, f"read raised: {error!r}")


def _check_radar(mem, config, report: HealthReport) -> None:
	'''Verify the local position via the scene node.'''
	if not bool(getattr(config.radar, "enabled", False)):
		report.add("Radar", "Local position readable", CheckStatus.SKIP, "radar disabled")
		return
	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
		if not pawn:
			report.add("Radar", "Local position readable", CheckStatus.SKIP, "no local pawn")
			return
		scene = mem.read_long(pawn + Netvars.m_pGameSceneNode)
		if not scene:
			report.add("Radar", "Local position readable", CheckStatus.FAIL, "scene-node pointer is null — pawn struct is corrupt")
			return
		position_x = mem.read_float(scene + Netvars.m_vecAbsOrigin)
		position_y = mem.read_float(scene + Netvars.m_vecAbsOrigin + 4)
		position_z = mem.read_float(scene + Netvars.m_vecAbsOrigin + 8)
		if all(abs(coordinate) < 16384 for coordinate in (position_x, position_y, position_z)):
			report.add("Radar", "Local position readable", CheckStatus.PASS, f"pos=({position_x:.0f}, {position_y:.0f}, {position_z:.0f})")
		else:
			report.add("Radar", "Local position readable", CheckStatus.FAIL,
				f"position ({position_x}, {position_y}, {position_z}) outside map bounds — " f"m_vecAbsOrigin offset is stale")
	except Exception as error:
		report.add("Radar", "Local position readable", CheckStatus.FAIL, f"read raised: {error!r}")


def _check_visuals(mem, report: HealthReport) -> None:
	'''Probe scene node and bone array readability on the local pawn.'''
	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
		if not pawn:
			report.add("Visuals", "Scene-node pointer readable", CheckStatus.SKIP, "no local pawn")
			report.add("Visuals", "Bone array readable", CheckStatus.SKIP, "no local pawn")
			return
		scene = mem.read_long(pawn + Netvars.m_pGameSceneNode)
		if not scene:
			report.add("Visuals", "Scene-node pointer readable", CheckStatus.FAIL, "scene node null — m_pGameSceneNode is stale " "or pawn struct is corrupt")
			return
		report.add("Visuals", "Scene-node pointer readable", CheckStatus.PASS, f"scene at 0x{scene:X}")

		bone_array = mem.read_long(scene + Netvars.m_boneArray)
		if not bone_array:
			report.add("Visuals", "Bone array readable", CheckStatus.WARN, f"bone array at scene_node +0x{Netvars.m_boneArray:X} is "
					   f"null — skeleton empty. Not dumper-published; " f"_verify_bone_array repairs it in-match after a patch.")
			return
		# Sanity-read bone 0 (root) as 3 floats, which must be finite
		root_x = mem.read_float(bone_array)
		if -16384 < root_x < 16384:
			report.add("Visuals", "Bone array readable", CheckStatus.PASS, f"bone 0 root x={root_x:.1f} (sensible)", value=bone_array)
		else:
			report.add("Visuals", "Bone array readable", CheckStatus.WARN, f"bone 0 x={root_x} outside map bounds — bone array " f"may be pointing at a stale struct")
	except Exception as error:
		report.add("Visuals", "Scene-node pointer readable", CheckStatus.FAIL, f"read raised: {error!r}")


def _check_anti_detect(config, report: HealthReport) -> None:
	'''Report anti-detect support flags without executing opt-in startup hooks.'''
	try:
		from cs2esp.anti_detect import (module_spoof_supported, window_class_supported)
	except Exception as error:
		report.add("Anti-detect", "Modules importable", CheckStatus.FAIL, f"import failed: {error!r}")
		return

	# Re-sync with disk so a just-flipped toggle is visible in this report
	try:
		if hasattr(config, "reload_if_changed"):
			config.reload_if_changed()
	except Exception:
		pass

	anti_detect = config.anti_detection
	for name, supported, enabled_attr in [("Module / DLL name spoof", module_spoof_supported(), "module_spoof_enabled"),
		("Window-class masquerade", window_class_supported(), "window_class_masquerade_enabled"), ("Read-pattern obfuscation", True, "read_obfuscator_enabled")]:
		enabled = bool(getattr(anti_detect, enabled_attr, False))
		if not enabled:
			# Include the attribute name + value in the SKIP reason so a persisted toggle is easy to diagnose
			report.add("Anti-detect", name, CheckStatus.SKIP, f"disabled in config " f"(anti_detection.{enabled_attr}={enabled!r})")
			continue
		if supported:
			report.add("Anti-detect", name, CheckStatus.SKIP, "enabled in config; platform supported, but startup application is not runtime-verified by this check")
		else:
			report.add("Anti-detect", name, CheckStatus.WARN, "enabled but this OS doesn't support the " "technique \u2014 module will no-op at startup")


def _check_glow_trigger(mem, config, report: HealthReport) -> None:
	'''Verify the glow trigger's entity-index-to-pawn resolver.'''
	if not bool(getattr(config.glow, "enabled", False)):
		report.add("Triggerbot", "Glow trigger resolver (entity_index_to_pawn)", CheckStatus.SKIP, "glow disabled")
		return
	try:
		from cs2esp.entity import EntitySystem
	except Exception as error:
		report.add("Triggerbot", "Glow trigger resolver (entity_index_to_pawn)", CheckStatus.FAIL, f"EntitySystem import failed: {error!r}")
		return
	try:
		entity_system = EntitySystem(mem)
	except Exception as error:
		report.add("Triggerbot", "Glow trigger resolver (entity_index_to_pawn)", CheckStatus.FAIL, f"EntitySystem construct failed: {error!r}")
		return
	resolved = 0
	for entity_index in range(1, 64):
		try:
			pawn_addr = entity_system.entity_index_to_pawn(entity_index)
		except Exception:
			continue
		if pawn_addr and 0x10000000000 < pawn_addr < 0x7FFFFFFFFFFF:
			resolved += 1
	if resolved == 0:
		report.add("Triggerbot", "Glow trigger resolver (entity_index_to_pawn)",
				   CheckStatus.WARN, "resolver returned no live pawns for slots 1..63 - " "likely not in a match yet")
	else:
		report.add("Triggerbot",
				   "Glow trigger resolver (entity_index_to_pawn)", CheckStatus.PASS, f"{resolved}/63 slots resolved to live userspace " "pawn pointers")


def _check_head_outline(mem, config, report: HealthReport) -> None:
	'''Verify the configured head bone can be read from the local pawn.'''
	skeleton_config = getattr(getattr(config, "visuals", None), "skeleton", None)
	head_on = bool(getattr(skeleton_config, "head_outline_enabled", False)) if skeleton_config else False
	if not head_on:
		report.add("Visuals", "Head bone readable (skeleton head outline)", CheckStatus.SKIP, "head outline disabled")
		return
	try:
		pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
		if not pawn:
			report.add("Visuals", "Head bone readable (skeleton head outline)", CheckStatus.SKIP, "no local pawn")
			return
		scene = mem.read_long(pawn + Netvars.m_pGameSceneNode)
		bone_array = mem.read_long(scene + Netvars.m_boneArray)
		if not bone_array:
			report.add("Visuals", "Head bone readable (skeleton head outline)", CheckStatus.WARN, "bone array null - skeleton ESP empty")
			return
		bone_off = Netvars.BONE_STRIDE * Netvars.BONE_HEAD
		head_x = mem.read_float(bone_array + bone_off + 0)
		head_y = mem.read_float(bone_array + bone_off + 4)
		head_z = mem.read_float(bone_array + bone_off + 8)
		if all(abs(coordinate) < 16384 for coordinate in (head_x, head_y, head_z)):
			report.add("Visuals", "Head bone readable (skeleton head outline)", CheckStatus.PASS, f"head bone at ({head_x:.0f}, {head_y:.0f}, {head_z:.0f})")
		else:
			report.add("Visuals", "Head bone readable (skeleton head outline)", CheckStatus.WARN,
				f"head bone ({head_x}, {head_y}, {head_z}) outside map bounds " "- struct may have drifted")
	except Exception as error:
		report.add("Visuals", "Head bone readable (skeleton head outline)", CheckStatus.FAIL, f"read raised: {error!r}")


def _check_chams(config, report: HealthReport) -> None:
	'''Confirm the chams GL pixel-probe module and symbols import.'''
	chams = getattr(getattr(config, "visuals", None), "chams", None)
	enabled = bool(getattr(chams, "enabled", False)) if chams else False
	if not enabled:
		report.add("Visuals", "Chams pixel-probe module importable", CheckStatus.SKIP, "chams disabled")
		return
	try:
		# Check the symbols the triggerbot uses so a rename can't silently break the chams fire gate
		from cs2esp.visuals.chams_pixel_probe import (probe_center_pixel, get_alpha, get_last_probe_age_s)
		_ = (probe_center_pixel, get_alpha, get_last_probe_age_s)
		report.add("Visuals", "Chams pixel-probe module importable",
				   CheckStatus.PASS, "module + symbols resolve; live alpha values " "available once the overlay has presented a frame")
	except Exception as error:
		report.add("Visuals", "Chams pixel-probe module importable", CheckStatus.FAIL, f"import / symbol lookup failed: {error!r}")


def _check_snapline(mem, config, report: HealthReport) -> None:
	'''Snapline + chams + world-to-screen all need the view matrix.'''
	snapline_config = getattr(getattr(config, "visuals", None), "snapline", None)
	chams = getattr(getattr(config, "visuals", None), "chams", None)
	snapline_enabled = bool(getattr(snapline_config, "enabled", False)) if snapline_config else False
	chams_on = bool(getattr(chams, "enabled", False)) if chams else False
	if not (snapline_enabled or chams_on):
		report.add("Visuals", "View matrix readable (snapline / chams)", CheckStatus.SKIP, "snapline + chams both disabled")
		return
	try:
		addr = mem.client(Offsets.dwViewMatrix)
		# 4x4 floats; spot-check m[3][3], should be 1.0 for a well-formed proj matrix
		m33 = mem.read_float(addr + 15 * 4)
		if 0.0 < m33 < 2.0:
			report.add("Visuals", "View matrix readable (snapline / chams)", CheckStatus.PASS, f"matrix[3][3]={m33:.3f} (sensible projection)")
		else:
			report.add("Visuals", "View matrix readable (snapline / chams)", CheckStatus.WARN, f"matrix[3][3]={m33} - reads odd; dwViewMatrix " "may have drifted")
	except Exception as error:
		report.add("Visuals", "View matrix readable (snapline / chams)", CheckStatus.FAIL, f"read raised: {error!r}")


def _check_fps_counter(config, report: HealthReport) -> None:
	'''FPS counter is overlay-only; ping uses IcmpSendEcho when enabled.'''
	fps_config = getattr(config, "fps_counter", None)
	if not fps_config or not bool(getattr(fps_config, "enabled", False)):
		report.add("FPS counter", "Module wiring", CheckStatus.SKIP, "disabled")
		return
	report.add("FPS counter", "Module wiring", CheckStatus.PASS, "overlay-only draw, no memory or syscall deps")
	if bool(getattr(fps_config, "ping_enabled", False)):
		try:
			from cs2esp.win32_utils import icmp_ping_ms, PingPoller
			_ = (icmp_ping_ms, PingPoller)
			report.add("FPS counter", "ICMP ping module importable",
					   CheckStatus.PASS, "cs2esp.win32_utils resolves (IcmpSendEcho via " "iphlpapi.dll; no external ping.exe needed)")
		except Exception as error:
			report.add("FPS counter", "ICMP ping module importable", CheckStatus.WARN, f"ping import failed: {error!r} - ping line " "will read N/A")


def _check_norecoil_scripts(report: HealthReport) -> None:
	'''Confirm the Lua and AHK script generators produce output.'''
	try:
		from cs2esp.features.norecoil_scripts import (build_script, VENDORS)
		# Smoke-test one Lua and one AHK vendor; pure string assembly makes this safe on every health check
		lua_vendor = next((vendor for vendor in VENDORS if vendor.language.startswith("Lua")), None)
		ahk_vendor = next((vendor for vendor in VENDORS if vendor.language.startswith("AHK")), None)
		if lua_vendor is None or ahk_vendor is None:
			report.add("No-Recoil scripts", "Lua + AHK generators importable", CheckStatus.WARN, "vendor registry missing Lua and/or AHK entries")
			return
		lua_src = build_script(lua_vendor.id)
		ahk_src = build_script(ahk_vendor.id)
		if len(lua_src) > 1000 and len(ahk_src) > 1000:
			report.add("No-Recoil scripts",
					   "Lua + AHK generators importable", CheckStatus.PASS, f"both builders produced output " f"(Lua={len(lua_src)}B, AHK={len(ahk_src)}B)")
		else:
			report.add("No-Recoil scripts", "Lua + AHK generators importable", CheckStatus.WARN, "builders returned suspiciously short output")
	except Exception as error:
		report.add("No-Recoil scripts", "Lua + AHK generators importable", CheckStatus.FAIL, f"import failed: {error!r}")


# Config-only probes cover wallhack, ESP primitives, legacy norecoil and anti-detection toggles without a CS2 attachment

def _check_wallhack_master(config, report: HealthReport) -> None:
	'''Report the wallhack master toggle, which flips Box, Skeleton and Glow as one key.'''
	wallhack_config = getattr(config, "wallhack", None)
	if wallhack_config is None:
		report.add("Wallhack", "Master toggle wired", CheckStatus.FAIL, "[wallhack] section missing from config")
		return
	enabled = bool(getattr(wallhack_config, "enabled", False))
	hotkey = str(getattr(wallhack_config, "hotkey_toggle", "") or "")
	report.add("Wallhack", "Master toggle wired", CheckStatus.PASS if enabled else CheckStatus.SKIP, f"enabled - hotkey={hotkey!r}" if enabled else "disabled")


def _check_box_esp(config, report: HealthReport) -> None:
	'''Surface Box ESP enabled/disabled state.'''
	box = getattr(getattr(config, "visuals", None), "box", None)
	if box is None:
		report.add("Visuals", "Box ESP wiring", CheckStatus.FAIL, "[visuals.box] section missing from config")
		return
	enabled = bool(getattr(box, "enabled", False))
	style = str(getattr(box, "style", "outline"))
	thickness = float(getattr(box, "thickness", 0.0))
	if enabled:
		report.add("Visuals", "Box ESP wiring", CheckStatus.PASS, f"enabled - style={style!r}, " f"thickness={thickness:.1f}px")
	else:
		report.add("Visuals", "Box ESP wiring", CheckStatus.SKIP, "disabled")


def _check_skeleton_esp(config, report: HealthReport) -> None:
	'''Surface Skeleton ESP and head-outline enabled state.'''
	skeleton_config = getattr(getattr(config, "visuals", None), "skeleton", None)
	if skeleton_config is None:
		report.add("Visuals", "Skeleton ESP wiring", CheckStatus.FAIL, "[visuals.skeleton] section missing from config")
		return
	enabled = bool(getattr(skeleton_config, "enabled", False))
	head_on = bool(getattr(skeleton_config, "head_outline_enabled", False))
	if enabled:
		report.add("Visuals", "Skeleton ESP wiring", CheckStatus.PASS, f"enabled - head_outline=" f"{'on' if head_on else 'off'}")
	else:
		report.add("Visuals", "Skeleton ESP wiring", CheckStatus.SKIP, "disabled")


def _check_labels(config, report: HealthReport) -> None:
	'''Report name and distance labels; both share the Labels hotkey but live in separate TOML sections.'''
	vis = getattr(config, "visuals", None)
	name = getattr(vis, "name", None) if vis else None
	dist = getattr(vis, "distance", None) if vis else None
	if name is None or dist is None:
		report.add("Visuals", "Name + distance labels", CheckStatus.FAIL, "[visuals.name] or [visuals.distance] missing")
		return
	n_on = bool(getattr(name, "enabled", False))
	d_on = bool(getattr(dist, "enabled", False))
	if n_on and d_on:
		report.add("Visuals", "Name + distance labels", CheckStatus.PASS, "both labels active")
	elif n_on or d_on:
		report.add("Visuals", "Name + distance labels", CheckStatus.WARN, f"only one label is on (name={n_on}, " f"distance={d_on}) - the 'Labels' hotkey "
				   "flips both in lock-step, so a split state " "usually means the user toggled them in the ESP " "panel separately")
	else:
		report.add("Visuals", "Name + distance labels", CheckStatus.SKIP, "both disabled")


def _check_health_labels(config, report: HealthReport) -> None:
	'''HP labels: bar / numeric / off. Pure overlay draw.'''
	health_cfg = getattr(getattr(config, "visuals", None), "health", None)
	if health_cfg is None:
		report.add("Visuals", "Health label wiring", CheckStatus.SKIP, "[visuals.health] missing from config")
		return
	mode = str(getattr(health_cfg, "mode", "off"))
	if mode == "off":
		report.add("Visuals", "Health label wiring", CheckStatus.SKIP, "mode=off")
	else:
		report.add("Visuals", "Health label wiring", CheckStatus.PASS, f"mode={mode!r}")


def _check_crosshair(config, report: HealthReport) -> None:
	'''Crosshair is a pure overlay draw - no memory deps.'''
	crosshair_config = getattr(getattr(config, "visuals", None), "crosshair", None)
	if crosshair_config is None:
		report.add("Visuals", "Crosshair wiring", CheckStatus.SKIP, "[visuals.crosshair] missing from config")
		return
	enabled = bool(getattr(crosshair_config, "enabled", False))
	size = float(getattr(crosshair_config, "size", 0.0))
	hotkey = str(getattr(crosshair_config, "hotkey_toggle", "") or "")
	if enabled:
		report.add("Visuals", "Crosshair wiring", CheckStatus.PASS, f"enabled - size={size:.0f}px, hotkey={hotkey!r}")
	else:
		report.add("Visuals", "Crosshair wiring", CheckStatus.SKIP, "disabled")


def _check_anti_detect_overlay(config, report: HealthReport) -> None:
	'''Report configured overlay options without treating flags as runtime verification.'''
	anti_detect = getattr(config, "anti_detection", None)
	if anti_detect is None:
		report.add("Anti-detect", "Overlay hide toggles", CheckStatus.FAIL, "[anti_detection] section missing from config")
		return
	rows = [("Hide overlay from screen capture", "hide_overlay_from_capture", "own-overlay capture-affinity request - live"),
		("Hide from taskbar", "hide_from_taskbar", "own-overlay WS_EX_TOOLWINDOW presentation - live"),
		("Randomise window title", "randomize_window_title", "title pool selection on next launch - restart"),
		("Reapply capture exclusion each second", "auto_capture_protect_on_focus_loss", "periodic affinity request while exclusion is enabled; no focus test - live")]
	for label, attr, description in rows:
		enabled = bool(getattr(anti_detect, attr, False))
		report.add("Anti-detect", label, CheckStatus.SKIP, f'{description}; configured {"ON" if enabled else "OFF"}; runtime application not verified')


def _check_anti_detect_writes(config, report: HealthReport) -> None:
	'''Describe the configured caller-side guard and jitter without claiming global enforcement.'''
	anti_detect = getattr(config, "anti_detection", None)
	if anti_detect is None:
		return
	read_only_mode = bool(getattr(anti_detect, "read_only_mode", False))
	glow_on = bool(getattr(getattr(config, "glow", None), "enabled", False))
	if read_only_mode and glow_on:
		report.add("Anti-detect", "Read-only scheduling guard", CheckStatus.WARN,
			"configured ON while Glow is enabled; normal Glow target scheduling is gated, but cleanup/in-flight writes can remain; runtime enforcement not verified")
	elif read_only_mode:
		report.add("Anti-detect", "Read-only scheduling guard", CheckStatus.SKIP,
			"configured ON; selected normal calls are gated, not the memory handle or every writer/input path; runtime enforcement not verified")
	else:
		report.add("Anti-detect", "Read-only scheduling guard", CheckStatus.SKIP, "configured OFF; selected write/input scheduling is not blocked by this flag")
	read_jitter_ms = int(getattr(anti_detect, "randomize_read_jitter_ms", 0))
	report.add("Anti-detect", "Read-cadence jitter", CheckStatus.SKIP,
		f"configured extra frame-tail sleep range 0-{max(0, read_jitter_ms)} ms; background-reader cadence is separate; runtime timing not verified")


__all__ = ["CheckResult", "CheckStatus", "HealthReport", "run_full_health_check"]
