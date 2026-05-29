"""
CS2 entity reader.

Walks Source 2 entity-list pages, resolves controllers to pawns, and builds
Player snapshots for ESP, radar, triggerbot, glow, and diagnostics.
"""

from dataclasses import dataclass, field
from time import perf_counter

from cs2esp.memory import MemoryReader
from cs2esp.offsets import Offsets, Netvars


@dataclass
class Player:
    controller_addr: int = 0
    pawn_addr: int = 0
    health: int = 0
    armor: int = 0
    team: int = 0
    life_state: int = 0
    name: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    eye_angles: tuple[float, float, float] = (0.0, 0.0, 0.0)  # Pitch, yaw, roll.
    is_alive: bool = False
    bones: dict = field(default_factory=dict)
    distance: float = 0.0
    # Collision hull relative to position; used by bounds-mode boxes.
    bbox_mins: tuple[float, float, float] | None = None
    bbox_maxs: tuple[float, float, float] | None = None


class EntitySystem:
    """Reads all player entities from CS2 memory."""

    def __init__(self, mem: MemoryReader):
        self.mem = mem
        self._local_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._empty_log_t = 0.0

    def _entity_list_entry(self, entity_list: int, handle_or_index: int) -> int:
        return self.mem.read_long(entity_list + 0x8 * ((handle_or_index & 0x7FFF) >> 9) + 0x10)

    def _entity_from_list_entry(self, list_entry: int, handle_or_index: int) -> int:
        return self.mem.read_long(list_entry + 0x70 * (handle_or_index & 0x1FF)) or 0

    def get_local_team(self) -> int:
        controller = self.mem.read_long(self.mem.client(Offsets.dwLocalPlayerController))
        if not controller:
            return 0
        return self.mem.read_byte(controller + Netvars.m_iTeamNum)

    def get_local_pawn(self) -> int:
        """Resolve via controller handle — dwLocalPlayerPawn can be stale."""
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

    def entity_index_to_pawn(self, idx: int) -> int:
        """Resolve a raw entity index (1..2048) to a pawn pointer."""
        if idx <= 0 or idx >= 2048:
            return 0
        entity_list = self.mem.read_long(
            self.mem.client(Offsets.dwEntityList))
        if not entity_list:
            return 0
        list_entry = self._entity_list_entry(entity_list, idx)
        if not list_entry:
            return 0
        return self._entity_from_list_entry(list_entry, idx)

    def get_local_eye_yaw(self) -> float:
        """Yaw of local player view (degrees).

        Reads client.dll + dwViewAngles because local-pawn m_angEyeAngles
        stays zero on current builds.
        """
        try:
            view_angles = self.mem.read_vec3(self.mem.client(Offsets.dwViewAngles))
            return float(view_angles[1])
        except Exception:
            return 0.0

    def get_observer_target_pawn(self, local_pawn: int) -> int:
        """Return the pawn address currently spectated by the local player, or 0 if not spectating."""
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
            # Mode 0 = not spectating only while alive; dead cameras latch late.
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
            # Could be a controller or pawn; try controller handle first.
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

    def get_players(self, read_bones: bool = True) -> list[Player]:
        """Iterate the entity list and return all alive non-local players."""
        players: list[Player] = []
        self._local_pos = (0.0, 0.0, 0.0)
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
                self._local_pos = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)

        needed_bones = (
            {bone for pair in Netvars.SKELETON_PAIRS for bone in pair}
            if read_bones else ()
        )

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

        for entity_index in range(1, 2048):
            try:
                list_entry = self._entity_list_entry(entity_list, entity_index)
                if not list_entry:
                    continue
                stats["list_entries"] += 1

                controller = self._entity_from_list_entry(list_entry, entity_index)
                if not controller:
                    continue
                stats["controllers"] += 1

                pawn_handle = self.mem.read_uint(controller + Netvars.m_hPlayerPawn)
                if not pawn_handle:
                    continue
                stats["pawn_handles"] += 1

                pawn_list_entry = self._entity_list_entry(entity_list, pawn_handle)
                if not pawn_list_entry:
                    continue

                pawn = self._entity_from_list_entry(pawn_list_entry, pawn_handle)
                if not pawn:
                    continue
                stats["pawns"] += 1
                if pawn == local_pawn:
                    continue
                stats["non_local"] += 1

                health = self.mem.read_int(pawn + Netvars.m_iHealth)
                if health <= 0:
                    continue
                stats["alive_hp"] += 1

                life_state = self.mem.read_byte(pawn + Netvars.m_lifeState)
                if life_state != 0:
                    continue
                stats["alive_life"] += 1

                team = self.mem.read_byte(pawn + Netvars.m_iTeamNum)
                if team < 2:
                    continue
                stats["teamed"] += 1

                scene_node = self.mem.read_long(pawn + Netvars.m_pGameSceneNode)
                if not scene_node:
                    continue
                stats["scene_nodes"] += 1

                position = self.mem.read_vec3(scene_node + Netvars.m_vecAbsOrigin)
                eye_angles = self.mem.read_vec3(pawn + Netvars.m_angEyeAngles)
                name = (
                    self.mem.read_string(controller + Netvars.m_iszPlayerName)
                    or f"Player_{entity_index}"
                )
                armor = self.mem.read_int(controller + Netvars.m_iPawnArmor)

                bones = {}
                if read_bones:
                    try:
                        bone_base = self.mem.read_long(scene_node + Netvars.m_boneArray)
                    except Exception:
                        bone_base = 0
                    if 0x10000 <= bone_base <= 0x7FFFFFFFFFFF:
                        for bone_index in needed_bones:
                            try:
                                bone_position = self.mem.read_vec3(
                                    bone_base + bone_index * Netvars.BONE_STRIDE
                                )
                            except Exception:
                                continue
                            if any(abs(value) > 1.0e6 for value in bone_position):
                                continue
                            if any(bone_position):
                                bones[bone_index] = bone_position

                bbox_mins = bbox_maxs = None
                try:
                    collision = self.mem.read_long(pawn + Netvars.m_pCollision)
                    if collision:
                        mins = self.mem.read_vec3(collision + Netvars.m_vecMins)
                        maxs = self.mem.read_vec3(collision + Netvars.m_vecMaxs)
                        if (
                            all(abs(value) < 512.0 for value in (*mins, *maxs))
                            and maxs != mins
                        ):
                            bbox_mins, bbox_maxs = mins, maxs
                except Exception:
                    pass

                local_x, local_y, local_z = self._local_pos
                dx = position[0] - local_x
                dy = position[1] - local_y
                dz = position[2] - local_z
                distance = (dx * dx + dy * dy + dz * dz) ** 0.5

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
                    )
                )
            except Exception:
                stats["exceptions"] += 1

        if not players:
            now = perf_counter()
            if now - self._empty_log_t > 5.0:
                self._empty_log_t = now
                print(
                    f"[diag] entity walk EMPTY: el=0x{entity_list:X} "
                    f"local_pawn=0x{local_pawn:X} stats={stats}"
                )

        return players
