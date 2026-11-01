"""
Offset registry with remote refresh, a local cache and runtime validation of fields that can drift between builds.
"""

import json
from hashlib import sha256
from math import isfinite
from os import fdopen, replace, unlink
from pathlib import Path
from tempfile import mkstemp
from time import time
from urllib.request import urlopen
from cs2esp.config import CONFIG_DIR

DUMPER_OFFSETS_URL = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/offsets.json"
DUMPER_CLIENT_URL = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/client_dll.json"
OFFSETS_CACHE_PATH: Path = CONFIG_DIR / "offsets.json"
_last_offsets_cache_mtime: float = 0.0
_last_offsets_cache_receipt: dict | None = None


class Offsets:
	"""Global client.dll offsets updated on startup."""
	dwEntityList: int = 0
	dwLocalPlayerController: int = 0
	dwLocalPlayerPawn: int = 0
	dwViewMatrix: int = 0
	dwViewAngles: int = 0
	dwPlantedC4: int = 0
	dwGlobalVars: int = 0


class Netvars:
	"""Netvar offsets from client_dll.json class dumps."""

	m_iszPlayerName: int = 0x6FC
	# Sanitised names remain available when bot controller name buffers are empty
	m_sSanitizedPlayerName: int = 0x878
	m_iPing: int = 0x838

	# Controller fields
	m_hPlayerPawn: int = 0x92C
	m_iPawnHealth: int = 0x938
	m_iPawnArmor: int = 0x93C

	# Base entity fields
	m_iHealth: int = 0x34C
	m_iTeamNum: int = 0x3E7
	m_lifeState: int = 0x354
	m_pGameSceneNode: int = 0x330
	m_fFlags: int = 0x3F4
	m_nActualMoveType: int = 0x526
	m_flGravityScale: int = 0x540
	m_pCollision: int = 0x340

	# Embedded collision bounds
	m_vecMins: int = 0x40
	m_vecMaxs: int = 0x4C

	# Pawn fields
	m_angEyeAngles: int = 0x3340
	m_bIsScoped: int = 0x1C70
	m_iIDEntIndex: int = 0x36DC
	# Ragdoll fields record the killing blow on the victim
	m_bRagdollDamageHeadshot: int = 0x1D74
	m_nRagdollDamageBone: int = 0x1D24
	m_szRagdollDamageWeaponName: int = 0x1D34
	m_entitySpottedState: int = 0x1C58
	# The spotted bool is team-wide; the adjacent mask identifies individual observer slots
	m_bSpotted: int = 0x8
	m_bSpottedByMask: int = 0xC
	m_pObserverServices: int = 0x1220
	m_iObserverMode: int = 0x48
	m_hObserverTarget: int = 0x4C

	m_vecAbsVelocity: int = 0x3F8
	m_nNextPrimaryAttackTick: int = 0x1918

	# Active weapon: pawn -> services -> handle -> weapon -> item ID
	m_pWeaponServices: int = 0x1208
	m_hActiveWeapon: int = 0x60
	m_AttributeManager: int = 0x11A8
	m_Item: int = 0x50
	m_iItemDefinitionIndex: int = 0x1BA

	# Scene node and model state
	m_vecAbsOrigin: int = 0xC8
	m_modelState: int = 0x140
	m_ModelName: int = 0xA8
	m_Glow: int = 0xDE0
	m_vecViewOffset: int = 0xE78

	# Planted bomb fields
	m_bBombTicking: int = 0x1288
	m_nBombSite: int = 0x128C
	m_flC4Blow: int = 0x12B8
	m_bHasExploded: int = 0x12BD
	m_bBeingDefused: int = 0x12C4
	m_flDefuseCountDown: int = 0x12D8
	m_bBombDefused: int = 0x12DC
	m_hBombDefuser: int = 0x12E0

	# Predicted aim punch is half the view kick
	m_pAimPunchServices: int = 0x1598
	m_predictableBaseAngle: int = 0x50
	m_iShotsFired: int = 0x1EB8
	m_pBulletServices: int = 0x1570
	m_totalHitsOnServer: int = 0x48
	m_flLastFiredWeaponTime: int = 0x15AC
	m_flTimeOfLastInjury: int = 0x15EC
	m_vecBulletHitModels: int = 0x1E68
	m_iBoneIndex: int = 0x1298
	m_hPlayerParent: int = 0x129C
	m_bIsHit: int = 0x12A0
	m_flTimeCreated: int = 0x12A4
	m_vecStartPos: int = 0x12A8
	m_fAccuracyPenalty: int = 0x1A18
	m_flRecoilIndex: int = 0x1A28
	m_fThrowTime: int = 0x1F28
	m_bRedraw: int = 0x1F21
	m_pDamageServices: int = 0x830
	m_DamageList: int = 0x48
	m_hPlayerControllerDamager: int = 0x38
	m_hPlayerControllerRecipient: int = 0x3C
	m_flActualHealthRemoved: int = 0x68
	m_iNumHits: int = 0x6C

	# The bone-array pointer is not networked or supplied by the remote schema; runtime validation checks it separately
	m_boneArray: int = 0x1C0
	BONE_STRIDE: int = 32

	BONE_HEAD = 7
	BONE_NECK = 6
	BONE_CHEST = 5
	BONE_SPINE = 3
	BONE_PELVIS = 1
	BONE_L_CLAV = 8
	BONE_L_SHOULDER = 9
	BONE_L_ELBOW = 10
	BONE_L_HAND = 11
	BONE_R_CLAV = 12
	BONE_R_SHOULDER = 13
	BONE_R_ELBOW = 14
	BONE_R_HAND = 15
	BONE_L_THIGH = 17
	BONE_L_KNEE = 18
	BONE_L_FOOT = 19
	BONE_R_THIGH = 20
	BONE_R_KNEE = 21
	BONE_R_FOOT = 22

	SKELETON_PAIRS: tuple[tuple[int, int], ...] = (
		(BONE_HEAD, BONE_NECK), (BONE_NECK, BONE_CHEST), (BONE_CHEST, BONE_SPINE), (BONE_SPINE, BONE_PELVIS), (BONE_CHEST, BONE_L_CLAV),
		(BONE_L_CLAV, BONE_L_SHOULDER), (BONE_L_SHOULDER, BONE_L_ELBOW), (BONE_L_ELBOW, BONE_L_HAND), (BONE_CHEST, BONE_R_CLAV),
		(BONE_R_CLAV, BONE_R_SHOULDER), (BONE_R_SHOULDER, BONE_R_ELBOW), (BONE_R_ELBOW, BONE_R_HAND), (BONE_PELVIS, BONE_L_THIGH),
		(BONE_L_THIGH, BONE_L_KNEE), (BONE_L_KNEE, BONE_L_FOOT), (BONE_PELVIS, BONE_R_THIGH), (BONE_R_THIGH, BONE_R_KNEE), (BONE_R_KNEE, BONE_R_FOOT)
	)

	# Continuous chains reduce per-player draw calls
	SKELETON_CHAINS: tuple[tuple[int, ...], ...] = (
		(BONE_HEAD, BONE_NECK, BONE_CHEST, BONE_SPINE, BONE_PELVIS),
		(BONE_CHEST, BONE_L_CLAV, BONE_L_SHOULDER, BONE_L_ELBOW, BONE_L_HAND),
		(BONE_CHEST, BONE_R_CLAV, BONE_R_SHOULDER, BONE_R_ELBOW, BONE_R_HAND),
		(BONE_PELVIS, BONE_L_THIGH, BONE_L_KNEE, BONE_L_FOOT),
		(BONE_PELVIS, BONE_R_THIGH, BONE_R_KNEE, BONE_R_FOOT)
	)


class CGlowProperty:
	"""CGlowProperty fields used by the glow writer."""
	m_fGlowColor: int = 0x08
	m_iGlowType: int = 0x30
	m_glowColorOverride: int = 0x40
	m_bEligibleForScreenHighlight: int = 0x50
	m_bGlowing: int = 0x51

	NONE = 0
	VISIBLE_ONLY = 1
	WORLD_ALWAYS = 2


def _fetch_json(url: str) -> dict | None:
	'''Fetch and decode a JSON object, or None on any failure.'''
	try:
		with urlopen(url, timeout=5) as response:
			data = json.load(response)
		return data if isinstance(data, dict) else None
	except Exception:
		return None


def export_offsets_dict() -> dict:
	'''Return every registry value as a plain dict for cache serialization.'''
	offsets_map = {key: getattr(Offsets, key) for key in dir(Offsets) if not key.startswith("_") and isinstance(getattr(Offsets, key), int)}
	netvars_map = {key: getattr(Netvars, key) for key in dir(Netvars) if not key.startswith("_") and not key.isupper() and isinstance(getattr(Netvars, key), int)}
	glow_map = {
		key: getattr(CGlowProperty, key)
		for key in dir(CGlowProperty)
		if not key.startswith("_") and not key.isupper() and isinstance(getattr(CGlowProperty, key), int)
	}
	return {"offsets": offsets_map, "netvars": netvars_map, "glow": glow_map}


def import_offsets_dict(data: dict) -> bool:
	'''Validate the whole payload before changing any shared registry fields.'''
	if not isinstance(data, dict):
		return False
	changes = []
	for section, cls in (("offsets", Offsets), ("netvars", Netvars), ("glow", CGlowProperty)):
		values = data.get(section, {})
		if not isinstance(values, dict):
			return False
		for key, value in values.items():
			if key.startswith("_") or not hasattr(cls, key):
				continue
			if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**64:
				return False
			changes.append((cls, key, value))
	for cls, key, value in changes:
		setattr(cls, key, value)
	return bool(changes)


def save_offsets_cache() -> bool:
	'''Save current in-memory offsets to local disk cache (atomic write).'''
	global _last_offsets_cache_mtime, _last_offsets_cache_receipt
	_last_offsets_cache_receipt = None
	try:
		data = export_offsets_dict()
		globals_ = data.get("offsets", {})
		if globals_ and not any(int(offset or 0) for offset in globals_.values()):
			# Do not overwrite the last usable cache with unresolved global offsets
			print("[offsets] cache save skipped: no resolved globals")
			return False
		registry_digest = sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
		data["_saved_at"] = time()
		raw = json.dumps(data, indent=2).encode("utf-8")

		OFFSETS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
		temp_fd, temp_path = mkstemp(prefix=".offsets-", suffix=".json.tmp", dir=str(OFFSETS_CACHE_PATH.parent))
		try:
			with fdopen(temp_fd, "wb") as cache_file:
				cache_file.write(raw)
			replace(temp_path, OFFSETS_CACHE_PATH)
			_last_offsets_cache_receipt = {"sha256": sha256(raw).hexdigest(), "registry_sha256": registry_digest}
			try:
				_last_offsets_cache_mtime = OFFSETS_CACHE_PATH.stat().st_mtime
			except Exception:
				pass
		except Exception:
			try:
				unlink(temp_path)
			except OSError:
				pass
			raise
		return True
	except Exception as e:
		print(f"[offsets] save_offsets_cache failed: {e!r}")
		return False


def verified_offsets_cache_receipt() -> dict | None:
	'''Confirm that the exact bytes this process saved still identify its current registry.'''
	receipt = _last_offsets_cache_receipt
	if receipt is None:
		return None
	try:
		raw = OFFSETS_CACHE_PATH.read_bytes()
		digest = sha256(json.dumps(export_offsets_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
		if sha256(raw).hexdigest() == receipt["sha256"] and digest == receipt["registry_sha256"]:
			return dict(receipt)
	except (OSError, TypeError, ValueError):
		pass
	return None


def load_offsets_cache(expected_sha256: str | None = None) -> bool:
	'''Load cached offsets from local disk into memory if available.'''
	global _last_offsets_cache_mtime
	if not OFFSETS_CACHE_PATH.is_file():
		return False
	try:
		raw = OFFSETS_CACHE_PATH.read_bytes()
		if expected_sha256 is not None and sha256(raw).hexdigest() != expected_sha256:
			print("[offsets] cache digest changed before runtime apply")
			return False
		data = json.loads(raw.decode("utf-8"))
		ok = import_offsets_dict(data)
		if ok:
			try:
				_last_offsets_cache_mtime = OFFSETS_CACHE_PATH.stat().st_mtime
			except Exception:
				pass
			print("[offsets] Loaded and applied offsets from disk cache")
		return ok
	except Exception as e:
		print(f"[offsets] load_offsets_cache failed: {e!r}")
		return False


def reload_if_changed() -> bool:
	'''Reload offsets from disk cache if the file was modified since last read.'''
	global _last_offsets_cache_mtime
	if not OFFSETS_CACHE_PATH.is_file():
		return False
	try:
		mtime = OFFSETS_CACHE_PATH.stat().st_mtime
		if mtime > _last_offsets_cache_mtime:
			ok = load_offsets_cache()
			_last_offsets_cache_mtime = mtime
			return ok
	except Exception:
		pass
	return False


_last_update_report: dict = {}


def update_offsets(*, persist: bool = True) -> bool:
	'''Fetch a complete remote pair; retain startup cache fallback without calling it remote success.'''
	global _last_update_report
	_last_update_report = {"source": "none", "remote_globals": False, "remote_netvars": False, "remote_complete": False, "cache_saved": False, "error": None}
	report = _last_update_report
	data = _fetch_json(DUMPER_OFFSETS_URL)
	if data is None:
		print("[offsets] Remote fetch failed — checking local cache")
		if load_offsets_cache():
			report["source"] = "cache"
			report["error"] = "Remote fetch failed; retained local cached offsets"
			return True
		report["error"] = "Remote fetch failed and local cache is unavailable"
		print("[offsets] Local cache missing or invalid — using hardcoded fallback")
		return False

	client = data.get("client.dll", {})
	required = ("dwEntityList", "dwLocalPlayerController", "dwLocalPlayerPawn", "dwViewMatrix", "dwViewAngles")
	if not isinstance(client, dict) or any(
		isinstance(client.get(key), bool) or not isinstance(client.get(key), int) or not 0 < client[key] < 2**32 for key in required
	):
		report["error"] = "Remote globals payload is missing required nonzero offsets"
		return False
	staged = export_offsets_dict()
	for key, val in client.items():
		if hasattr(Offsets, key):
			if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val < 2**32:
				report["error"] = f"Invalid remote global: {key}"
				return False
			staged["offsets"][key] = val
	report["remote_globals"] = True
	data = _fetch_json(DUMPER_CLIENT_URL)
	if data is None:
		print("[offsets] Failed to fetch netvars; current offsets retained")
		report["error"] = "Remote netvar fetch failed; current offsets retained"
		return False
	classes = data.get("client.dll", {}).get("classes", {})
	if not isinstance(classes, dict) or not classes:
		report["error"] = "Remote netvar payload has no client.dll classes"
		return False

	required_fields = {
		"C_BaseEntity": ("m_iHealth", "m_iTeamNum", "m_lifeState", "m_pGameSceneNode"),
		"CCSPlayerController": ("m_hPlayerPawn",),
		"CGameSceneNode": ("m_vecAbsOrigin",),
	}
	for class_name, names in required_fields.items():
		entry = classes.get(class_name, {})
		fields = entry.get("fields", {}) if isinstance(entry, dict) else {}
		if not isinstance(fields, dict) or any(name not in fields for name in names):
			report["error"] = f"Remote netvar payload is missing required fields in {class_name}"
			return False
	if any(not isinstance(entry, dict) or not isinstance(entry.get("fields", {}), dict) for entry in classes.values()):
		report["error"] = "Remote netvar payload contains malformed class fields"
		return False

	netvar_map = {
		"CBasePlayerController": [("m_iszPlayerName", "m_iszPlayerName"), ("m_iPing", "m_iPing")],
		"CCSPlayerController": [
			("m_pDamageServices", "m_pDamageServices"), ("m_hPlayerPawn", "m_hPlayerPawn"), ("m_iPawnHealth", "m_iPawnHealth"), ("m_iPawnArmor", "m_iPawnArmor"),
			("m_sSanitizedPlayerName", "m_sSanitizedPlayerName"),
		],
		"C_BaseEntity": [
			("m_iHealth", "m_iHealth"), ("m_iTeamNum", "m_iTeamNum"), ("m_lifeState", "m_lifeState"), ("m_pGameSceneNode", "m_pGameSceneNode"),
			("m_fFlags", "m_fFlags"), ("m_pCollision", "m_pCollision"), ("m_vecAbsVelocity", "m_vecAbsVelocity"), ("m_nActualMoveType", "m_nActualMoveType"),
			("m_flGravityScale", "m_flGravityScale"),
		],
		"C_BasePlayerWeapon": [("m_nNextPrimaryAttackTick", "m_nNextPrimaryAttackTick")],
		"CCollisionProperty": [("m_vecMins", "m_vecMins"), ("m_vecMaxs", "m_vecMaxs")],
		"CGameSceneNode": [("m_vecAbsOrigin", "m_vecAbsOrigin")],
		"CSkeletonInstance": [("m_modelState", "m_modelState")],
		"CModelState": [("m_ModelName", "m_ModelName")],
		"C_BaseModelEntity": [("m_Glow", "m_Glow"), ("m_vecViewOffset", "m_vecViewOffset")],
		"C_EconEntity": [("m_AttributeManager", "m_AttributeManager")],
		"C_AttributeContainer": [("m_Item", "m_Item")],
		"C_EconItemView": [("m_iItemDefinitionIndex", "m_iItemDefinitionIndex")],
		"C_BasePlayerPawn": [("m_pObserverServices", "m_pObserverServices"), ("m_pWeaponServices", "m_pWeaponServices")],
		"CPlayer_WeaponServices": [("m_hActiveWeapon", "m_hActiveWeapon")],
		"CPlayer_ObserverServices": [("m_iObserverMode", "m_iObserverMode"), ("m_hObserverTarget", "m_hObserverTarget")],
		"C_CSPlayerPawn": [
			("m_iIDEntIndex", "m_iIDEntIndex"), ("m_angEyeAngles", "m_angEyeAngles"), ("m_bIsScoped", "m_bIsScoped"),
			("m_entitySpottedState", "m_entitySpottedState"), ("m_bRagdollDamageHeadshot", "m_bRagdollDamageHeadshot"),
			("m_nRagdollDamageBone", "m_nRagdollDamageBone"), ("m_szRagdollDamageWeaponName", "m_szRagdollDamageWeaponName"),
			("m_pAimPunchServices", "m_pAimPunchServices"), ("m_iShotsFired", "m_iShotsFired"), ("m_pBulletServices", "m_pBulletServices"),
			("m_flLastFiredWeaponTime", "m_flLastFiredWeaponTime"), ("m_flTimeOfLastInjury", "m_flTimeOfLastInjury"),
			("m_vecBulletHitModels", "m_vecBulletHitModels"),
		],
		"C_BulletHitModel": [(name, name) for name in ("m_iBoneIndex", "m_hPlayerParent", "m_bIsHit", "m_flTimeCreated", "m_vecStartPos")],
		"C_CSWeaponBase": [("m_fAccuracyPenalty", "m_fAccuracyPenalty"), ("m_flRecoilIndex", "m_flRecoilIndex")],
		"C_BaseCSGrenade": [("m_fThrowTime", "m_fThrowTime"), ("m_bRedraw", "m_bRedraw")],
		"CCSPlayerController_DamageServices": [("m_DamageList", "m_DamageList")],
		"CDamageRecord": [(name, name) for name in ("m_hPlayerControllerDamager", "m_hPlayerControllerRecipient", "m_flActualHealthRemoved", "m_iNumHits")],
		"CCSPlayer_AimPunchServices": [("m_predictableBaseAngle", "m_predictableBaseAngle")],
		"CCSPlayer_BulletServices": [("m_totalHitsOnServer", "m_totalHitsOnServer")],
		"EntitySpottedState_t": [("m_bSpotted", "m_bSpotted"), ("m_bSpottedByMask", "m_bSpottedByMask")],
		"C_PlantedC4": [
			("m_bBombTicking", "m_bBombTicking"), ("m_nBombSite", "m_nBombSite"), ("m_flC4Blow", "m_flC4Blow"),
			("m_bHasExploded", "m_bHasExploded"), ("m_bBeingDefused", "m_bBeingDefused"), ("m_flDefuseCountDown", "m_flDefuseCountDown"),
			("m_bBombDefused", "m_bBombDefused"),("m_hBombDefuser", "m_hBombDefuser"),
		],
	}

	updated = 0
	for cls_name, fields in netvar_map.items():
		cls_fields = classes.get(cls_name, {}).get("fields", {})
		for field_name, attr_name in fields:
			if field_name in cls_fields:
				val = cls_fields[field_name]
				if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val < 0x100000:
					report["error"] = f"Invalid remote netvar: {cls_name}.{field_name}"
					return False
				if attr_name == "m_iIDEntIndex" and val < 0x36DC:
					continue
				staged["netvars"][attr_name] = val
				updated += 1


	glow_fields = classes.get("CGlowProperty", {}).get("fields", {})
	for fname in ("m_fGlowColor", "m_iGlowType", "m_glowColorOverride", "m_bEligibleForScreenHighlight", "m_bGlowing"):
		if fname in glow_fields:
			val = glow_fields[fname]
			if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val < 0x100000:
				report["error"] = f"Invalid remote glow field: {fname}"
				return False
			staged["glow"][fname] = val
			updated += 1

	if not updated or not import_offsets_dict(staged):
		report["error"] = "Remote netvar payload contains no usable fields"
		return False
	report.update(source="remote", remote_netvars=True, remote_complete=True)
	print(f"[offsets] Updated global offsets and {updated} netvars from remote")
	if persist:
		report["cache_saved"] = save_offsets_cache()
		if not report["cache_saved"]:
			report["error"] = "Remote offsets applied in memory, but cache write failed"
	return True


def apply_sigscan(pm) -> dict:
	'''Derive RVAs from the loaded client.dll and apply them as a self-healing layer over the remote dumper.'''
	try:
		from .sigscan import scan_client
	except Exception as e:
		print(f"[offsets] sigscan unavailable: {e}")
		return {}
	try:
		result = scan_client(pm)
	except Exception as e:
		print(f"[offsets] sigscan failed: {e}")
		return {}
	applied: dict = {}
	for name, value in result.items():
		if hasattr(Offsets, name):
			setattr(Offsets, name, value)
			applied[name] = value
		elif hasattr(Netvars, name):
			setattr(Netvars, name, value)
			applied[name] = value
	if applied:
		print(f"[offsets] sigscan applied {len(applied)} offsets from local client.dll")
	return applied


def _verify_base_entity_offsets(mem) -> int:
	'''Verify core C_BaseEntity offsets from the live local pawn.'''
	try:
		local_pawn = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
	except Exception:
		return 0
	if not local_pawn or local_pawn < 0x10000000000:
		return 0

	try:
		local_ctrl = mem.read_long(mem.client(Offsets.dwLocalPlayerController))
	except Exception:
		local_ctrl = 0

	corrected = 0

	# Scan entity slots directly so a stale pawn-handle field cannot invalidate the sample
	fleet: list[int] = [local_pawn]
	try:
		entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
	except Exception:
		entity_list = 0
	if entity_list:
		for entity_index in range(1, 256):
			if len(fleet) >= 64:
				break
			try:
				list_entry = mem.read_long(entity_list + 0x8 * ((entity_index & 0x7FFF) >> 9) + 0x10)
				if not list_entry:
					continue
				ent = mem.read_long(list_entry + 0x70 * (entity_index & 0x1FF))
				if not ent or ent == local_pawn or ent < 0x10000000000:
					continue
				fleet.append(ent)
			except Exception:
				continue

	controller_health = -1
	if local_ctrl:
		try:
			value = mem.read_int(local_ctrl + Netvars.m_iPawnHealth)
			if 0 <= value <= 120:
				controller_health = value
		except Exception:
			pass

	# A scene node must resolve to an origin inside map bounds
	def _scene_is_valid(ptr: int) -> bool:
		if not ptr or ptr < 0x10000000000 or ptr > 0x7FFFFFFFFFFF:
			return False
		try:
			origin_x = mem.read_float(ptr + Netvars.m_vecAbsOrigin)
			origin_y = mem.read_float(ptr + Netvars.m_vecAbsOrigin + 4)
			origin_z = mem.read_float(ptr + Netvars.m_vecAbsOrigin + 8)
		except Exception:
			return False
		return all(abs(value) < 16384.0 for value in (origin_x, origin_y, origin_z))

	def _score_scene(off: int) -> int:
		score = 0
		for entity in fleet:
			try:
				if _scene_is_valid(mem.read_long(entity + off)):
					score += 1
			except Exception:
				pass
		return score

	cur_scene_off = Netvars.m_pGameSceneNode
	cur_scene_score = _score_scene(cur_scene_off)
	# Require several matching entities before replacing the scene-node offset
	if cur_scene_score < 3:
		best_off = cur_scene_off
		best_score = cur_scene_score
		for off in range(0x300, 0x360, 8):
			score = _score_scene(off)
			if score > best_score:
				best_score = score
				best_off = off
		if best_off != cur_scene_off and best_score >= 3:
			print(
				f"[offsets] AUTO-CORRECT m_pGameSceneNode: 0x{cur_scene_off:X} (score {cur_scene_score}) -> 0x{best_off:X} (score {best_score}, fleet={len(fleet)})"
			)
			Netvars.m_pGameSceneNode = best_off
			corrected += 1

	pawn_fleet = [entity for entity in fleet if _scene_is_valid(mem.read_long(entity + Netvars.m_pGameSceneNode))]

	def _score_hp(off: int) -> int:
		score = 0
		for pawn in pawn_fleet:
			try:
				value = mem.read_int(pawn + off)
				if 0 <= value <= 120:
					score += 1
			except Exception:
				pass
		return score

	cur_hp_off = Netvars.m_iHealth
	cur_hp_score = _score_hp(cur_hp_off)
	# Matching controller health strengthens the candidate without being mandatory
	if cur_hp_score < max(3, len(pawn_fleet) // 2):
		best_off = cur_hp_off
		best_score = cur_hp_score
		for off in range(0x320, 0x380, 4):
			score = _score_hp(off)
			if score > best_score:
				best_score = score
				best_off = off
		if best_off != cur_hp_off and best_score >= 3:
			print(
				f"[offsets] AUTO-CORRECT m_iHealth: 0x{cur_hp_off:X} (score {cur_hp_score}) -> 0x{best_off:X} (score {best_score}, ctrl_mirror={controller_health})"
			)
			Netvars.m_iHealth = best_off
			corrected += 1

	def _score_team(off: int) -> int:
		score = 0
		for pawn in pawn_fleet:
			try:
				if mem.read_byte(pawn + off) in (2, 3):
					score += 1
			except Exception:
				pass
		if local_ctrl and mem.read_byte(local_ctrl + off) not in (2, 3):
			return 0
		return score

	cur_team_off = Netvars.m_iTeamNum
	cur_team_score = _score_team(cur_team_off)
	if cur_team_score < max(3, len(pawn_fleet) // 2):
		best_off = cur_team_off
		best_score = cur_team_score
		for off in range(0x380, 0x420):
			score = _score_team(off)
			if score > best_score:
				best_score = score
				best_off = off
		if best_off != cur_team_off and best_score >= 3:
			print(f"[offsets] AUTO-CORRECT m_iTeamNum: 0x{cur_team_off:X} (score {cur_team_score}) -> 0x{best_off:X} (score {best_score}, fleet={len(pawn_fleet)})")
			Netvars.m_iTeamNum = best_off
			corrected += 1

	# Validate life state near health, including the zero padding that follows it
	def _score_life(off: int) -> int:
		score = 0
		for pawn in pawn_fleet:
			try:
				if mem.read_byte(pawn + off) != 0:
					continue
				tail = mem.pm.read_bytes(pawn + off + 1, 3)
				if tail != b"\x00\x00\x00":
					continue
				score += 1
			except Exception:
				pass
		return score

	cur_life_off = Netvars.m_lifeState
	cur_life_score = _score_life(cur_life_off)
	if cur_life_score < max(3, len(pawn_fleet) // 2):
		anchor = Netvars.m_iHealth
		best_off = cur_life_off
		best_score = cur_life_score
		for off in range(anchor + 4, anchor + 0x20):
			score = _score_life(off)
			if score > best_score:
				best_score = score
				best_off = off
		if best_off != cur_life_off and best_score >= 3:
			print(f"[offsets] AUTO-CORRECT m_lifeState: 0x{cur_life_off:X} (score {cur_life_score}) -> 0x{best_off:X} (score {best_score}, fleet={len(pawn_fleet)})")
			Netvars.m_lifeState = best_off
			corrected += 1

	try:
		from .entity import EntitySystem

		entities = EntitySystem(mem)
		local_pawn = entities.get_local_pawn()
		if local_pawn:
			cur_idx_off = Netvars.m_iIDEntIndex
			cur_idx = mem.read_int(local_pawn + cur_idx_off)
			cur_pawn = entities.entity_index_to_pawn(cur_idx) if cur_idx > 0 else 0
			if cur_pawn not in pawn_fleet:
				for off in range(0x3680, 0x3780, 4):
					cand_idx = mem.read_int(local_pawn + off)
					if 1 <= cand_idx <= 2048:
						cand_pawn = entities.entity_index_to_pawn(cand_idx)
						if cand_pawn in pawn_fleet:
							print(f"[offsets] AUTO-CORRECT m_iIDEntIndex: 0x{cur_idx_off:X} -> 0x{off:X} (matched target pawn 0x{cand_pawn:X})")
							Netvars.m_iIDEntIndex = off
							corrected += 1
							break
	except Exception:
		pass

	return corrected


def _verify_bone_array(mem) -> int:
	'''Repair the bone-array pointer, which no dumper publishes, from live memory; returns 1 when corrected.'''
	# Bone candidates must stay near the pawn and span a plausible body height
	try:
		entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
		if not entity_list:
			return 0
	except Exception:
		return 0

	fleet: list[tuple[int, tuple[float, float, float]]] = []
	for entity_index in range(1, 256):
		if len(fleet) >= 24:
			break
		try:
			list_entry = mem.read_long(entity_list + 0x8 * ((entity_index & 0x7FFF) >> 9) + 0x10)
			if not list_entry:
				continue
			ent = mem.read_long(list_entry + 0x70 * (entity_index & 0x1FF))
			if not ent or ent < 0x10000000000 or ent > 0x7FFFFFFFFFFF:
				continue
			health = mem.read_int(ent + Netvars.m_iHealth)
			team = mem.read_byte(ent + Netvars.m_iTeamNum)
			if not (0 < health <= 100) or team not in (1, 2, 3):
				continue
			scene = mem.read_long(ent + Netvars.m_pGameSceneNode)
			if not scene or scene < 0x10000000000 or scene > 0x7FFFFFFFFFFF:
				continue
			origin_x = mem.read_float(scene + Netvars.m_vecAbsOrigin)
			origin_y = mem.read_float(scene + Netvars.m_vecAbsOrigin + 4)
			origin_z = mem.read_float(scene + Netvars.m_vecAbsOrigin + 8)
			if not all(abs(value) < 16384.0 for value in (origin_x, origin_y, origin_z)):
				continue
			# World-origin placeholders would make zero-filled bone arrays look valid
			if abs(origin_x) < 1.0 and abs(origin_y) < 1.0 and abs(origin_z) < 1.0:
				continue
			fleet.append((scene, (origin_x, origin_y, origin_z)))
		except Exception:
			continue

	if len(fleet) < 1:
		return 0

	def _bone_array_valid(scene: int, off: int, origin: tuple) -> bool:
		'''True iff scene+off derefs to a real bone array: bones near the origin AND with genuine vertical spread (a skeleton is ~70u tall).'''
		try:
			base = mem.read_long(scene + off)
		except Exception:
			return False
		if not base or base < 0x10000 or base > 0x7FFFFFFFFFFF:
			return False
		origin_x, origin_y, origin_z = origin
		good = 0
		heights: list[float] = []
		for bone_idx in (0, 1, 3, 5, 7):
			try:
				bone_x = mem.read_float(base + bone_idx * Netvars.BONE_STRIDE)
				bone_y = mem.read_float(base + bone_idx * Netvars.BONE_STRIDE + 4)
				bone_z = mem.read_float(base + bone_idx * Netvars.BONE_STRIDE + 8)
			except Exception:
				return False
			if abs(bone_x - origin_x) < 80.0 and abs(bone_y - origin_y) < 80.0 and -32.0 < (bone_z - origin_z) < 96.0:
				good += 1
				heights.append(bone_z)
		return good >= 4 and len(heights) >= 4 and (max(heights) - min(heights)) > 20.0

	def _score(off: int) -> int:
		return sum(1 for scene, origin in fleet if _bone_array_valid(scene, off, origin))

	cur_off = Netvars.m_boneArray
	cur_score = _score(cur_off)
	accept = max(1, (len(fleet) + 1) // 2)
	if cur_score >= accept:
		return 0

	best_off = cur_off
	best_score = cur_score
	for off in range(0x150, 0x260, 8):
		if off == cur_off:
			continue
		score = _score(off)
		if score > best_score:
			best_score = score
			best_off = off

	if best_off != cur_off and best_score >= accept:
		print(f"[offsets] AUTO-CORRECT m_boneArray: 0x{cur_off:X} (score {cur_score}) -> 0x{best_off:X} (score {best_score}, fleet={len(fleet)})")
		Netvars.m_boneArray = best_off
		return 1
	return 0


def _live_offsets_readable(mem) -> bool:
	'''Check required globals against this process before replacing its last-good cache.'''
	try:
		entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
		matrix = mem.read_matrix(mem.client(Offsets.dwViewMatrix))
		values = [float(value) for row in matrix for value in row]
		return (
			entity_list > 0x10000
			and len(values) == 16
			and all(isfinite(value) and abs(value) < 1e9 for value in values)
			and any(abs(value) > 1e-8 for value in values)
			and any(abs(value) > 1e-8 for value in values[12:16])
		)
	except Exception:
		return False


def refresh_offsets_live(mem=None) -> dict:
	'''Fetch, heal and persist a report whose success reflects a real cache update.'''
	report = dict(
		success=False,
		dumper_ok=False,
		source="none",
		remote_globals=False,
		remote_netvars=False,
		remote_complete=False,
		cache_saved=False,
		cache_sha256=None,
		attached=False,
		live_verified=None,
		sigscan_count=0,
		netvars_corrected=0,
		changed=0,
		changed_fields=[],
		error=None,
		warnings=[],
	)
	module_before = export_offsets_dict()
	before = module_before
	try:
		cached = json.loads(OFFSETS_CACHE_PATH.read_bytes().decode("utf-8"))
		if (
			isinstance(cached, dict)
			and isinstance(cached.get("offsets"), dict)
			and any(
				key in module_before["offsets"] and isinstance(value, int) and not isinstance(value, bool) and value > 0 for key, value in cached["offsets"].items()
			)
			and import_offsets_dict(cached)
		):
			before = export_offsets_dict()
	except (OSError, UnicodeError, TypeError, ValueError):
		pass
	created_mem = False
	target_mem = mem
	try:
		update_offsets(persist=False)
		report.update(_last_update_report)
		report["dumper_ok"] = bool(report["remote_complete"])
		if report["error"]:
			report["warnings"].append(report["error"])
		if target_mem is None:
			from .memory import MemoryReader

			target_mem = MemoryReader()
			created_mem = True
			report["attached"] = bool(target_mem.attach(quiet=True))
		else:
			report["attached"] = bool(getattr(target_mem, "pm", None) and getattr(target_mem, "client_base", 0))
		if report["attached"]:
			report["sigscan_count"] = len(apply_sigscan(target_mem.pm))
			report["netvars_corrected"] = verify_netvars(target_mem)
			report["live_verified"] = _live_offsets_readable(target_mem)
			if not report["live_verified"]:
				report["error"] = "Live global validation failed; previous offsets and cache retained"
				raise RuntimeError(report["error"])
		fresh = report["remote_complete"] or report["sigscan_count"] or report["netvars_corrected"]
		if fresh:
			report["cache_saved"] = bool(save_offsets_cache())
			if report["cache_saved"]:
				receipt = verified_offsets_cache_receipt()
				if receipt is not None:
					report["cache_sha256"] = receipt["sha256"]
					report["success"] = True
				else:
					report["cache_saved"] = False
					report["error"] = "Offset cache changed before its saved bytes could be confirmed; retry update"
			else:
				report["error"] = "Offset refresh completed in memory, but saving the cache failed"
		elif not report["error"]:
			report["error"] = "No fresh remote or live offsets were obtained; previous cache retained"
	except Exception as exc:
		report["error"] = f"Offset refresh failed: {exc!r}"
	finally:
		if created_mem and target_mem is not None:
			try:
				target_mem.detach()
			except Exception:
				pass
	if not report["success"]:
		import_offsets_dict(module_before)
	after = export_offsets_dict()
	report["changed_fields"] = (
		[
			f"{section}.{key}"
			for section in ("offsets", "netvars", "glow")
			for key in set(before[section]) | set(after[section])
			if before[section].get(key) != after[section].get(key)
		]
		if report["success"]
		else []
	)
	report["changed"] = len(report["changed_fields"])
	return report


def verify_netvars(mem) -> int:
	'''Validate drift-prone pawn, bone, view-angle and crosshair fields against the live build; returns the number of corrected registry fields.'''
	# Validate base entity fields before checks that depend on pawn identity
	corrected_total = _verify_base_entity_offsets(mem)

	corrected_total += _verify_bone_array(mem)

	try:
		entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
		if not entity_list:
			return corrected_total
	except Exception:
		return corrected_total

	# Sample several controllers so a coincidental field match cannot dominate the score
	controllers: list[int] = []
	for entity_index in range(1, 256):
		try:
			list_entry = mem.read_long(entity_list + 0x8 * ((entity_index & 0x7FFF) >> 9) + 0x10)
			if not list_entry:
				continue
			ctrl = mem.read_long(list_entry + 0x70 * (entity_index & 0x1FF))
			if not ctrl:
				continue
			team = mem.read_byte(ctrl + Netvars.m_iTeamNum)
			if team in (2, 3):
				controllers.append(ctrl)
				if len(controllers) >= 32:
					break
		except Exception:
			continue

	if not controllers:
		return corrected_total

	corrected = corrected_total

	def _resolve_pawn(handle: int) -> int:
		if not handle or handle == 0xFFFFFFFF:
			return 0
		ple = mem.read_long(entity_list + 0x8 * ((handle & 0x7FFF) >> 9) + 0x10)
		if not ple or ple < 0x10000 or ple > 0x7FFFFFFFFFFF:
			return 0
		pawn = mem.read_long(ple + 0x70 * (handle & 0x1FF))
		if not pawn or pawn < 0x10000000000 or pawn > 0x7FFFFFFFFFFF:
			return 0
		return pawn

	def _looks_like_pawn(pawn: int) -> bool:
		try:
			health = mem.read_int(pawn + Netvars.m_iHealth)
			team = mem.read_byte(pawn + Netvars.m_iTeamNum)
			life_state = mem.read_byte(pawn + Netvars.m_lifeState)
			return 0 < health <= 100 and team in (2, 3) and life_state in (0, 1, 2)
		except Exception:
			return False

	current = Netvars.m_hPlayerPawn

	def _score_handle_offset(off: int) -> int:
		'''Count how many controllers resolve to a healthy pawn at `off`.'''
		score = 0
		for ctrl in controllers:
			try:
				handle = mem.read_uint(ctrl + off)
			except Exception:
				continue
			pawn = _resolve_pawn(handle)
			if pawn and _looks_like_pawn(pawn):
				score += 1
		return score

	cur_score = _score_handle_offset(current)
	# Rescan the window even when the current offset works for a stale controller
	scan_lo = max(0x800, current - 0x40)
	scan_hi = current + 0x40
	best_off = current
	best_score = cur_score
	for off in range(scan_lo, scan_hi + 1, 4):
		if off == current:
			continue
		score = _score_handle_offset(off)
		if score > best_score:
			best_score = score
			best_off = off
	# Require a clear score improvement before replacing the current field
	if best_off != current and best_score >= max(2, cur_score + 2):
		print(f"[offsets] AUTO-CORRECT m_hPlayerPawn: 0x{current:X} (score {cur_score}) -> 0x{best_off:X} (score {best_score})")
		Netvars.m_hPlayerPawn = best_off
		corrected += 1

	# Match local eye angles against dwViewAngles instead of relying on yaw diversity among bots
	try:
		local_pawn_ptr = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
	except Exception:
		local_pawn_ptr = 0

	if local_pawn_ptr and Offsets.dwViewAngles:
		try:
			view_address = mem.client(Offsets.dwViewAngles)
			view_pitch = mem.read_float(view_address)
			view_yaw = mem.read_float(view_address + 4)
		except Exception:
			view_pitch = view_yaw = None

		if view_yaw is not None and (-90.5 < view_pitch < 90.5) and (-180.5 < view_yaw < 180.5):
			current_offset = Netvars.m_angEyeAngles

			def _matches_view(off: int) -> bool:
				try:
					pitch = mem.read_float(local_pawn_ptr + off)
					yaw = mem.read_float(local_pawn_ptr + off + 4)
				except Exception:
					return False
				# Allow one degree for the netvar update lag during fast turns
				return abs(pitch - view_pitch) < 1.0 and abs(yaw - view_yaw) < 1.0

			if not _matches_view(current_offset):
				best_offset = None
				for off in range(current_offset - 0x80, current_offset + 0x100 + 1, 4):
					if off == current_offset or off < 0:
						continue
					if _matches_view(off):
						best_offset = off
						break
				if best_offset is not None:
					print(
						f"[offsets] AUTO-CORRECT m_angEyeAngles: 0x{current_offset:X} -> 0x{best_offset:X} "
						f"(matched dwViewAngles pitch={view_pitch:+.2f} yaw={view_yaw:+.2f})"
					)
					Netvars.m_angEyeAngles = best_offset
					corrected += 1

	try:
		from .sigscan import scan_client

		if getattr(mem, "pm", None):
			scan_result = scan_client(mem.pm)
			target_offset = scan_result.get("m_iIDEntIndex")
			if target_offset and target_offset != Netvars.m_iIDEntIndex:
				print(f"[offsets] AUTO-CORRECT m_iIDEntIndex: 0x{Netvars.m_iIDEntIndex:X} -> 0x{target_offset:X}")
				Netvars.m_iIDEntIndex = target_offset
				corrected += 1
	except Exception:
		pass

	return corrected
