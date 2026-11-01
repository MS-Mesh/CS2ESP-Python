"""
Read-only held-weapon telemetry for recoil profile selection. Snapshots stay in memory; the controller forwards the small pointer-free payload through its
existing status publisher.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError, load
from math import isfinite
from pathlib import Path
from time import time

from cs2esp.config import CONFIG_DIR
from cs2esp.features.recoil_profiles import weapon_info
from cs2esp.offsets import Netvars


@dataclass(frozen=True, slots=True)
class HeldWeapon:
	"""One validated pawn/weapon observation; addresses never enter the UI status payload."""
	pawn_addr: int = 0
	weapon_handle: int = 0
	weapon_addr: int = 0
	item_definition: int = 0
	weapon_key: str | None = None
	weapon_name: str = ""
	is_firearm: bool = False
	scope_capable: bool = False
	is_scoped: bool | None = None
	valid: bool = False
	reason: str = "unavailable"

	@property
	def identity(self) -> tuple[int, int, int, int]:
		return self.pawn_addr, self.weapon_handle, self.weapon_addr, self.item_definition

	def status_payload(self) -> dict:
		'''Own just the UI metadata, without memory addresses, game writes, or configuration mutations.'''
		return {
			"item_definition": self.item_definition,
			"weapon_key": self.weapon_key,
			"weapon_name": self.weapon_name,
			"is_firearm": self.is_firearm,
			"scope_capable": self.scope_capable,
			"is_scoped": self.is_scoped,
			"valid": self.valid,
			"reason": self.reason,
		}


def _pointer_valid(address) -> bool:
	return isinstance(address, int) and not isinstance(address, bool) and 0x10000 <= address <= 0x7FFFFFFFFFFF


class RecoilRuntime:
	"""Sample held metadata through the existing resolver; no input, registry access, threads, or disk writes."""
	def __init__(self, mem, entity_system) -> None:
		self.mem = mem
		self.entities = entity_system
		self.latest = HeldWeapon()

	def sample(self, local_pawn: int) -> HeldWeapon:
		'''Read a stable weapon observation, invalidating partial or changed handles rather than retaining the previous profile.'''
		if not _pointer_valid(local_pawn):
			return self._invalid(0, "no_pawn")
		try:
			services = self.mem.read_long(local_pawn + Netvars.m_pWeaponServices)
			if not _pointer_valid(services):
				return self._invalid(local_pawn, "invalid_services")
			handle = self.mem.read_uint(services + Netvars.m_hActiveWeapon)
			if not isinstance(handle, int) or isinstance(handle, bool) or not 0 < handle < 0xFFFFFFFF or not (handle & 0x7FFF):
				return self._invalid(local_pawn, "invalid_handle")
			# Keep the full handle for transitions; the entity-list API resolves its index
			weapon = self.entities.resolve_handle(handle)
			if not _pointer_valid(weapon):
				return self._invalid(local_pawn, "unresolved_handle")
			item_definition = self.mem.read_int(weapon + Netvars.m_AttributeManager + Netvars.m_Item + Netvars.m_iItemDefinitionIndex) & 0xFFFF
			info = weapon_info(item_definition)
			if info is None:
				return self._invalid(local_pawn, "unknown_weapon")
			scoped = None
			if info.scope_capable:
				try:
					scoped = bool(self.mem.read_byte(local_pawn + Netvars.m_bIsScoped))
				except Exception:
					pass
			unchanged = (
				self.mem.read_long(local_pawn + Netvars.m_pWeaponServices) == services
				and self.mem.read_uint(services + Netvars.m_hActiveWeapon) == handle
				and self.entities.resolve_handle(handle) == weapon
			)
			if not unchanged:
				return self._invalid(local_pawn, "weapon_changed")
			snapshot = HeldWeapon(
				pawn_addr=local_pawn,
				weapon_handle=handle,
				weapon_addr=weapon,
				item_definition=item_definition,
				weapon_key=info.key,
				weapon_name=info.name,
				is_firearm=info.is_firearm,
				scope_capable=info.scope_capable,
				is_scoped=scoped,
				valid=True,
				reason="ready" if info.is_firearm else "non_firearm",
			)
		except Exception:
			snapshot = HeldWeapon(pawn_addr=local_pawn, reason="unreadable")
		self.latest = snapshot
		return snapshot

	def _invalid(self, pawn_addr: int, reason: str) -> HeldWeapon:
		'''Publish and return an invalid observation.'''
		snapshot = HeldWeapon(pawn_addr=pawn_addr, reason=reason)
		self.latest = snapshot
		return snapshot

	def reset(self) -> HeldWeapon:
		'''Invalidate telemetry on detach/lobby/shutdown without changing configuration.'''
		self.latest = HeldWeapon()
		return self.latest

	def status_payload(self) -> dict:
		return self.latest.status_payload()


def held_weapon_from_status(status: Mapping | None, *, max_age_sec: float = 2.0, now: float | None = None) -> HeldWeapon:
	'''Validate controller telemetry; stale/malformed status never selects or edits a remembered firearm.'''
	if not isinstance(status, Mapping) or status.get("cs2_attached") is not True:
		return HeldWeapon()
	try:
		stamp = float(status.get("ts", 0.0))
		age = (time() if now is None else now) - stamp
		if not isfinite(stamp) or not isfinite(age) or age < -max_age_sec or age > max_age_sec:
			return HeldWeapon(reason="stale")
	except (TypeError, ValueError, OverflowError):
		return HeldWeapon(reason="stale")
	payload = status.get("recoil_runtime")
	if not isinstance(payload, Mapping) or payload.get("valid") is not True:
		return HeldWeapon()
	item_definition = payload.get("item_definition")
	info = weapon_info(item_definition)
	if info is None or payload.get("weapon_key") != info.key or payload.get("is_firearm") is not info.is_firearm:
		return HeldWeapon(reason="invalid_status")
	scoped = payload.get("is_scoped")
	if scoped is not None and not isinstance(scoped, bool):
		return HeldWeapon(reason="invalid_status")
	return HeldWeapon(
		item_definition=item_definition,
		weapon_key=info.key,
		weapon_name=info.name,
		is_firearm=info.is_firearm,
		scope_capable=info.scope_capable,
		is_scoped=scoped if info.scope_capable else None,
		valid=True,
		reason="ready" if info.is_firearm else "non_firearm",
	)


def read_held_weapon_status(path: Path | None = None, *, max_age_sec: float = 2.0) -> HeldWeapon:
	'''Read the existing controller status file only; never create a second status channel or save settings.'''
	try:
		with (Path(path) if path is not None else CONFIG_DIR / "status.json").open("r", encoding="utf-8") as stream:
			status = load(stream)
	except (OSError, JSONDecodeError, UnicodeError):
		return HeldWeapon()
	return held_weapon_from_status(status, max_age_sec=max_age_sec)


__all__ = ["HeldWeapon", "RecoilRuntime", "held_weapon_from_status", "read_held_weapon_status"]
