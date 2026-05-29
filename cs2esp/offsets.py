"""
CS2 offset and netvar registry.

Startup fetches current values from a2x/cs2-dumper when available, then
falls back to hardcoded values and local signature scans. Runtime verify
helpers repair the drift-prone offsets from live memory.
"""

from json import load as load_json
from urllib.request import urlopen

DUMPER_OFFSETS_URL = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/offsets.json"
DUMPER_CLIENT_URL = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/client_dll.json"


class Offsets:
    """Global client.dll offsets updated on startup."""
    dwEntityList: int = 0
    dwLocalPlayerController: int = 0
    dwLocalPlayerPawn: int = 0
    dwViewMatrix: int = 0
    dwViewAngles: int = 0


class Netvars:
    """Netvar offsets from client_dll.json class dumps."""

    # Fallback values track the a2x/cs2-dumper build after the 2026-07-09 patch. Used only when remote fetch fails; overwritten live on success.

    m_iszPlayerName: int = 0x6F4

    # CCSPlayerController pawn fields.
    m_hPlayerPawn: int = 0x914
    m_iPawnHealth: int = 0x920
    m_iPawnArmor: int = 0x924

    # C_BaseEntity core fields used to recognise live pawns.
    m_iHealth: int = 0x34C
    m_iTeamNum: int = 0x3E7
    m_lifeState: int = 0x354
    m_pGameSceneNode: int = 0x330
    m_fFlags: int = 0x3F4
    # CCollisionProperty hull used by bounds-mode ESP boxes.
    m_pCollision: int = 0x340

    # CCollisionProperty — embedded bounds vectors.
    m_vecMins: int = 0x40
    m_vecMaxs: int = 0x4C

    # C_CSPlayerPawnBase / C_CSPlayerPawn; eye angles live on the derived class.
    m_angEyeAngles: int = 0x3340  # Vector3 pitch, yaw, roll.
    # Scope state for scoped-weapon triggerbot gates. Moved from C_CSPlayerPawnBase to C_CSPlayerPawn in 2026-07-09 patch.
    m_bIsScoped: int = 0x1C70
    m_iIDEntIndex: int = 0x341C  # Entity index under crosshair.
    # Engine-tracked spotted state used for VISIBLE_ONLY glow.
    m_entitySpottedState: int = 0x1C58  # EntitySpottedState_t embedded
    m_bSpottedByMask: int = 0xC          # u32 inside EntitySpottedState_t
    m_pObserverServices: int = 0x1220  # CPlayer_ObserverServices pointer.
    m_iObserverMode: int = 0x48
    m_hObserverTarget: int = 0x4C

    # Active weapon lookup: pawn -> services -> handle -> weapon -> item ID.
    m_pWeaponServices: int = 0x1208  # CCSPlayer_WeaponServices pointer.
    m_hActiveWeapon: int = 0x60
    m_AttributeManager: int = 0x11A8
    m_Item: int = 0x50
    m_iItemDefinitionIndex: int = 0x1BA

    # CGameSceneNode
    m_vecAbsOrigin: int = 0xC8
    # C_BaseModelEntity — embedded glow struct (live-fetched)
    m_Glow: int = 0xDE0
    m_vecViewOffset: int = 0xE78

    # Bone-array pointer at scene_node + this. Lives in the non-networked CModelState blob, so no dumper publishes it — verify_netvars() brute-scans it live when the skeleton is empty; temp/probe_bone_offset.py re-derives it after a patch. Verified live 2026-07-10: 0x1C0 (was 0x1D0 pre-2026-07-09).
    m_boneArray: int = 0x1C0
    BONE_STRIDE: int = 32

    # Verified bone indices: root 0, pelvis 1, spine 3, chest 5, neck 6, head 7, arms L8-11/R12-15, legs L17-19/R20-22.
    BONE_HEAD = 7  # Head top, above the shoulders.
    BONE_NECK = 6
    BONE_CHEST = 5  # Arms branch here.
    BONE_SPINE = 3
    BONE_PELVIS = 1  # Bone 0 is the feet anchor.
    # Left arm (player's left)
    BONE_L_CLAV = 8
    BONE_L_SHOULDER = 9
    BONE_L_ELBOW = 10
    BONE_L_HAND = 11
    # Right arm
    BONE_R_CLAV = 12
    BONE_R_SHOULDER = 13
    BONE_R_ELBOW = 14
    BONE_R_HAND = 15
    # Legs (3 bones each)
    BONE_L_THIGH = 17
    BONE_L_KNEE = 18
    BONE_L_FOOT = 19
    BONE_R_THIGH = 20
    BONE_R_KNEE = 21
    BONE_R_FOOT = 22

    SKELETON_PAIRS: tuple[tuple[int, int], ...] = (
        # spine
        (BONE_HEAD, BONE_NECK),
        (BONE_NECK, BONE_CHEST),
        (BONE_CHEST, BONE_SPINE),
        (BONE_SPINE, BONE_PELVIS),
        # left arm (4 segments: chest -> clav -> shoulder -> elbow -> hand)
        (BONE_CHEST, BONE_L_CLAV),
        (BONE_L_CLAV, BONE_L_SHOULDER),
        (BONE_L_SHOULDER, BONE_L_ELBOW),
        (BONE_L_ELBOW, BONE_L_HAND),
        # right arm
        (BONE_CHEST, BONE_R_CLAV),
        (BONE_R_CLAV, BONE_R_SHOULDER),
        (BONE_R_SHOULDER, BONE_R_ELBOW),
        (BONE_R_ELBOW, BONE_R_HAND),
        # left leg
        (BONE_PELVIS, BONE_L_THIGH),
        (BONE_L_THIGH, BONE_L_KNEE),
        (BONE_L_KNEE, BONE_L_FOOT),
        # right leg
        (BONE_PELVIS, BONE_R_THIGH),
        (BONE_R_THIGH, BONE_R_KNEE),
        (BONE_R_KNEE, BONE_R_FOOT),
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
    try:
        with urlopen(url, timeout=5) as response:
            data = load_json(response)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def update_offsets() -> bool:
    """Pull latest offsets from a2x/cs2-dumper. Returns True on success."""
    data = _fetch_json(DUMPER_OFFSETS_URL)
    if data is None:
        print("[offsets] Remote fetch failed — using hardcoded fallback")
        return False

    client = data.get("client.dll", {})

    for key, val in client.items():
        if hasattr(Offsets, key):
            setattr(Offsets, key, val)

    print("[offsets] Updated global offsets from remote")
    data = _fetch_json(DUMPER_CLIENT_URL)
    if data is None:
        print("[offsets] Failed to fetch netvars — using hardcoded fallback")
        return True
    classes = data.get("client.dll", {}).get("classes", {})

    netvar_map = {
        "CBasePlayerController": [
            ("m_iszPlayerName", "m_iszPlayerName"),
        ],
        "CCSPlayerController": [
            ("m_hPlayerPawn", "m_hPlayerPawn"),
            ("m_iPawnHealth", "m_iPawnHealth"),
            ("m_iPawnArmor", "m_iPawnArmor"),
        ],
        "C_BaseEntity": [
            ("m_iHealth", "m_iHealth"),
            ("m_iTeamNum", "m_iTeamNum"),
            ("m_lifeState", "m_lifeState"),
            ("m_pGameSceneNode", "m_pGameSceneNode"),
            ("m_fFlags", "m_fFlags"),
            ("m_pCollision", "m_pCollision"),
        ],
        "CCollisionProperty": [
            ("m_vecMins", "m_vecMins"),
            ("m_vecMaxs", "m_vecMaxs"),
        ],
        "CGameSceneNode": [
            ("m_vecAbsOrigin", "m_vecAbsOrigin"),
        ],
        "C_BaseModelEntity": [
            ("m_Glow", "m_Glow"),
            ("m_vecViewOffset", "m_vecViewOffset"),
        ],
        "C_EconEntity": [
            # Kept fresh so the triggerbot's scoped-sniper check walks to the right struct field.
            ("m_AttributeManager", "m_AttributeManager"),
        ],
        "C_AttributeContainer": [
            ("m_Item", "m_Item"),
        ],
        "C_EconItemView": [
            ("m_iItemDefinitionIndex", "m_iItemDefinitionIndex"),
        ],
        "C_BasePlayerPawn": [
            ("m_pObserverServices", "m_pObserverServices"),
            # Active weapon resolution via CCSPlayer_WeaponServices.
            ("m_pWeaponServices", "m_pWeaponServices"),
        ],
        "CPlayer_WeaponServices": [
            ("m_hActiveWeapon", "m_hActiveWeapon"),
        ],
        "CPlayer_ObserverServices": [
            ("m_iObserverMode", "m_iObserverMode"),
            ("m_hObserverTarget", "m_hObserverTarget"),
        ],
        "C_CSPlayerPawn": [
            # Both live on the derived class, not Base. Looking up on the wrong class broke the triggerbot (m_iIDEntIndex always 0) and rear-aim awareness (yaw always 0°).
            ("m_iIDEntIndex", "m_iIDEntIndex"),
            ("m_angEyeAngles", "m_angEyeAngles"),
            # Scoped flag (AWP/SSG zoom), read by triggerbot to damp FOV inflation while scoped. Moved here from C_CSPlayerPawnBase in the 2026-07-09 patch.
            ("m_bIsScoped", "m_bIsScoped"),
            # Embedded EntitySpottedState_t. Engine maintains bSpottedByMask/bSpotted per tick from team LoS; gates m_bGlowing for the visible-only glow path.
            ("m_entitySpottedState", "m_entitySpottedState"),
        ],
        "EntitySpottedState_t": [
            ("m_bSpottedByMask", "m_bSpottedByMask"),
        ],
    }

    updated = 0
    for cls_name, fields in netvar_map.items():
        cls_fields = classes.get(cls_name, {}).get("fields", {})
        for field_name, attr_name in fields:
            if field_name in cls_fields:
                setattr(Netvars, attr_name, cls_fields[field_name])
                updated += 1

    # m_boneArray is not dumped or tied to a dumped field, so only the live brute-scan can recover it.

    # CGlowProperty sub-offsets
    glow_fields = classes.get("CGlowProperty", {}).get("fields", {})
    for fname in (
        "m_fGlowColor",
        "m_iGlowType", "m_glowColorOverride",
        "m_bEligibleForScreenHighlight", "m_bGlowing",
    ):
        if fname in glow_fields:
            setattr(CGlowProperty, fname, glow_fields[fname])
            updated += 1

    print(f"[offsets] Updated {updated} netvars from remote")
    return True


def apply_sigscan(pm) -> dict:
    """Run a local pattern scan on the loaded ``client.dll`` and apply any
    RVAs we resolve to :class:`Offsets`.

    Used as a *self-healing* layer over the remote dumper: whenever Valve
    ships a CS2 patch, the dumper trails by hours-to-days. Without this,
    every pointer dereferences to zero during that window. With it, we
    simply derive the offsets from the running game's bytes ourselves.

    Returns the dict of applied ``{name: rva}`` (empty on failure).
    """
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
    for k, v in result.items():
        if hasattr(Offsets, k):
            setattr(Offsets, k, v)
            applied[k] = v
    if applied:
        print(f"[offsets] sigscan applied {len(applied)} offsets from local client.dll")
    return applied


def _verify_base_entity_offsets(mem) -> int:
    """Verify core C_BaseEntity offsets from the live local pawn."""
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

    # Walk entity-list slots directly (avoids depending on possibly-stale m_hPlayerPawn). Correct offset wins by pass-count.
    fleet: list[int] = [local_pawn]
    try:
        el = mem.read_long(mem.client(Offsets.dwEntityList))
    except Exception:
        el = 0
    if el:
        for i in range(1, 256):
            if len(fleet) >= 64:
                break
            try:
                le = mem.read_long(el + 0x8 * ((i & 0x7FFF) >> 9) + 0x10)
                if not le:
                    continue
                ent = mem.read_long(le + 0x70 * (i & 0x1FF))
                if not ent or ent == local_pawn or ent < 0x10000000000:
                    continue
                fleet.append(ent)
            except Exception:
                continue

    # m_iPawnHealth on the controller (stable mirror, ground truth)
    ctrl_hp = -1
    if local_ctrl:
        try:
            v = mem.read_int(local_ctrl + Netvars.m_iPawnHealth)
            if 0 <= v <= 120:
                ctrl_hp = v
        except Exception:
            pass

    # m_pGameSceneNode first — strongest signal. Valid deref means m_vecAbsOrigin reads a finite vec3 inside CS2 map bounds (±16384), near-impossible by accident.
    def _scene_is_valid(ptr: int) -> bool:
        if not ptr or ptr < 0x10000000000 or ptr > 0x7FFFFFFFFFFF:
            return False
        try:
            x = mem.read_float(ptr + Netvars.m_vecAbsOrigin)
            y = mem.read_float(ptr + Netvars.m_vecAbsOrigin + 4)
            z = mem.read_float(ptr + Netvars.m_vecAbsOrigin + 8)
        except Exception:
            return False
        return all(abs(v) < 16384.0 for v in (x, y, z))

    def _score_scene(off: int) -> int:
        s = 0
        for e in fleet:
            try:
                if _scene_is_valid(mem.read_long(e + off)):
                    s += 1
            except Exception:
                pass
        return s

    cur_scene_off = Netvars.m_pGameSceneNode
    cur_scene_score = _score_scene(cur_scene_off)
    # Pawns are ~10/64 of the entity list, plus local pawn. Need a few valid derefs to lock in; ≥3 is high-confidence.
    if cur_scene_score < 3:
        best_off = cur_scene_off
        best_score = cur_scene_score
        for off in range(0x300, 0x360, 8):
            s = _score_scene(off)
            if s > best_score:
                best_score = s
                best_off = off
        if best_off != cur_scene_off and best_score >= 3:
            print(f"[offsets] AUTO-CORRECT m_pGameSceneNode: 0x{cur_scene_off:X} "
                  f"(score {cur_scene_score}) -> 0x{best_off:X} "
                  f"(score {best_score}, fleet={len(fleet)})")
            Netvars.m_pGameSceneNode = best_off
            corrected += 1

    # m_iHealth: with m_pGameSceneNode locked in, fleet members passing the scene-node check are a high-purity pawn sub-fleet.
    pawn_fleet = [
        e for e in fleet
        if _scene_is_valid(mem.read_long(e + Netvars.m_pGameSceneNode))
    ]

    def _score_hp(off: int) -> int:
        s = 0
        for p in pawn_fleet:
            try:
                v = mem.read_int(p + off)
                # Alive pawn: 1..100 HP; corpse: 0; some modes go up to 120.
                if 0 <= v <= 120:
                    s += 1
            except Exception:
                pass
        return s

    cur_hp_off = Netvars.m_iHealth
    cur_hp_score = _score_hp(cur_hp_off)
    # Controller mirror at 0x910 matching local is extra-strong evidence, but a bonus, not required.
    if cur_hp_score < max(3, len(pawn_fleet) // 2):
        best_off = cur_hp_off
        best_score = cur_hp_score
        for off in range(0x320, 0x380, 4):
            s = _score_hp(off)
            if s > best_score:
                best_score = s
                best_off = off
        if best_off != cur_hp_off and best_score >= 3:
            print(f"[offsets] AUTO-CORRECT m_iHealth: 0x{cur_hp_off:X} "
                  f"(score {cur_hp_score}) -> 0x{best_off:X} "
                  f"(score {best_score}, ctrl_mirror={ctrl_hp})")
            Netvars.m_iHealth = best_off
            corrected += 1

    # m_iTeamNum: on a pawn the byte must be 2 or 3 (T/CT). CCSPlayerController inherits C_BaseEntity, so the same offset works on both.
    def _score_team(off: int) -> int:
        s = 0
        for p in pawn_fleet:
            try:
                if mem.read_byte(p + off) in (2, 3):
                    s += 1
            except Exception:
                pass
        return s

    cur_team_off = Netvars.m_iTeamNum
    cur_team_score = _score_team(cur_team_off)
    if cur_team_score < max(3, len(pawn_fleet) // 2):
        best_off = cur_team_off
        best_score = cur_team_score
        for off in range(0x380, 0x420):
            s = _score_team(off)
            if s > best_score:
                best_score = s
                best_off = off
        if best_off != cur_team_off and best_score >= 3:
            print(f"[offsets] AUTO-CORRECT m_iTeamNum: 0x{cur_team_off:X} "
                  f"(score {cur_team_score}) -> 0x{best_off:X} "
                  f"(score {best_score}, fleet={len(pawn_fleet)})")
            Netvars.m_iTeamNum = best_off
            corrected += 1

    # m_lifeState: u8, 0 = alive, padding bytes after should also be 0. Typically lives at m_iHealth + 8, so anchor the search there.
    def _score_life(off: int) -> int:
        s = 0
        for p in pawn_fleet:
            try:
                if mem.read_byte(p + off) != 0:
                    continue
                tail = mem.pm.read_bytes(p + off + 1, 3)
                if tail != b"\x00\x00\x00":
                    continue
                s += 1
            except Exception:
                pass
        return s

    cur_life_off = Netvars.m_lifeState
    cur_life_score = _score_life(cur_life_off)
    if cur_life_score < max(3, len(pawn_fleet) // 2):
        anchor = Netvars.m_iHealth
        best_off = cur_life_off
        best_score = cur_life_score
        for off in range(anchor + 4, anchor + 0x20):
            s = _score_life(off)
            if s > best_score:
                best_score = s
                best_off = off
        if best_off != cur_life_off and best_score >= 3:
            print(f"[offsets] AUTO-CORRECT m_lifeState: 0x{cur_life_off:X} "
                  f"(score {cur_life_score}) -> 0x{best_off:X} "
                  f"(score {best_score}, fleet={len(pawn_fleet)})")
            Netvars.m_lifeState = best_off
            corrected += 1

    return corrected


def _verify_bone_array(mem) -> int:
    """Repair the bone-array pointer from live memory (no dumper supplies it).

    Brute-scans scene-node offsets against a fleet of alive pawns; a
    candidate wins only if its bones cluster near the pawn origin AND show
    real head-to-foot vertical spread. The spread check is essential —
    without it, all-zero blobs at world origin false-positive (seen at 0x118).
    Must run after _verify_base_entity_offsets. Returns 1 if corrected.
    """
    try:
        entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
        if not entity_list:
            return 0
    except Exception:
        return 0

    # Alive pawns only — health/team/non-zero-origin filters out controllers, weapons, props, and (0,0,0) placeholders.
    fleet: list[tuple[int, tuple[float, float, float]]] = []
    for i in range(1, 256):
        if len(fleet) >= 24:
            break
        try:
            le = mem.read_long(entity_list + 0x8 * ((i & 0x7FFF) >> 9) + 0x10)
            if not le:
                continue
            ent = mem.read_long(le + 0x70 * (i & 0x1FF))
            if not ent or ent < 0x10000000000 or ent > 0x7FFFFFFFFFFF:
                continue
            hp = mem.read_int(ent + Netvars.m_iHealth)
            team = mem.read_byte(ent + Netvars.m_iTeamNum)
            if not (0 < hp <= 100) or team not in (1, 2, 3):
                continue
            scene = mem.read_long(ent + Netvars.m_pGameSceneNode)
            if not scene or scene < 0x10000000000 or scene > 0x7FFFFFFFFFFF:
                continue
            ox = mem.read_float(scene + Netvars.m_vecAbsOrigin)
            oy = mem.read_float(scene + Netvars.m_vecAbsOrigin + 4)
            oz = mem.read_float(scene + Netvars.m_vecAbsOrigin + 8)
            if not all(abs(v) < 16384.0 for v in (ox, oy, oz)):
                continue
            # Reject world-origin placeholders — all-zero bones false-positive against a (0,0,0) origin.
            if abs(ox) < 1.0 and abs(oy) < 1.0 and abs(oz) < 1.0:
                continue
            fleet.append((scene, (ox, oy, oz)))
        except Exception:
            continue

    if len(fleet) < 1:
        return 0

    def _bone_array_valid(scene: int, off: int, origin: tuple) -> bool:
        """True iff scene+off derefs to a real bone array: bones near the
        origin AND with genuine vertical spread (a skeleton is ~70u tall)."""
        try:
            base = mem.read_long(scene + off)
        except Exception:
            return False
        if not base or base < 0x10000 or base > 0x7FFFFFFFFFFF:
            return False
        ox, oy, oz = origin
        good = 0
        zs: list[float] = []
        # Sample root/pelvis/spine/chest/head — spans the whole body.
        for bone_idx in (0, 1, 3, 5, 7):
            try:
                bx = mem.read_float(base + bone_idx * Netvars.BONE_STRIDE)
                by = mem.read_float(base + bone_idx * Netvars.BONE_STRIDE + 4)
                bz = mem.read_float(base + bone_idx * Netvars.BONE_STRIDE + 8)
            except Exception:
                return False
            if (abs(bx - ox) < 80.0 and abs(by - oy) < 80.0
                    and -32.0 < (bz - oz) < 96.0):
                good += 1
                zs.append(bz)
        # Require most bones near the body AND real head-to-pelvis spread — kills the all-zero false positive.
        return good >= 4 and len(zs) >= 4 and (max(zs) - min(zs)) > 20.0

    def _score(off: int) -> int:
        return sum(1 for scene, origin in fleet
                   if _bone_array_valid(scene, off, origin))

    cur_off = Netvars.m_boneArray
    cur_score = _score(cur_off)
    accept = max(1, (len(fleet) + 1) // 2)  # majority of the fleet
    # Current offset already resolves on a majority — leave it alone.
    if cur_score >= accept:
        return 0

    best_off = cur_off
    best_score = cur_score
    # Scan the plausible window at pointer alignment (8-byte).
    for off in range(0x150, 0x260, 8):
        if off == cur_off:
            continue
        s = _score(off)
        if s > best_score:
            best_score = s
            best_off = off

    if best_off != cur_off and best_score >= accept:
        print(f"[offsets] AUTO-CORRECT m_boneArray: 0x{cur_off:X} "
              f"(score {cur_score}) -> 0x{best_off:X} "
              f"(score {best_score}, fleet={len(fleet)})")
        Netvars.m_boneArray = best_off
        return 1
    return 0


def verify_netvars(mem) -> int:
    """Validate the most-drift-prone netvars against live memory and
    auto-correct them if Valve has shifted the struct layout.

    The netvars in the dumper's ``client_dll.json`` are derived from the
    schema-system dump and lag the same hours-to-days as the global
    offsets. We can't easily re-implement the schema parser, but we
    *can* spot-check the two netvars whose drift visibly breaks ESP:

      * ``m_hPlayerPawn`` — pawn handle on CCSPlayerController. If the
        published offset reads 0 on every alive controller, scan a small
        window around it for a u32 that resolves to a valid pawn.
      * ``m_angEyeAngles`` — Vector3 on C_CSPlayerPawn. If pitch/yaw
        read as near-zero on every alive enemy, scan ±0x40 for a float
        triple that looks like real angles.

    Returns the number of netvars corrected.
    """
    # Step 0: heal C_BaseEntity offsets first — later auto-corrects depend on _looks_like_pawn() validating via m_iHealth + m_iTeamNum + m_lifeState.
    corrected_total = _verify_base_entity_offsets(mem)

    # Step 0.5: heal the bone-array pointer, the one offset the dumper never supplies — live brute-scan is the only backstop.
    corrected_total += _verify_bone_array(mem)

    try:
        entity_list = mem.read_long(mem.client(Offsets.dwEntityList))
        if not entity_list:
            return corrected_total
    except Exception:
        return corrected_total

    # Collect candidate alive controllers (team>=2, valid pawn ptr in entry). With too few pawns a nonzero structural field can accidentally outscore the real m_angEyeAngles field; 32+ gives enough yaw diversity to distinguish them.
    controllers: list[int] = []
    for i in range(1, 256):
        try:
            le = mem.read_long(entity_list + 0x8 * ((i & 0x7FFF) >> 9) + 0x10)
            if not le:
                continue
            ctrl = mem.read_long(le + 0x70 * (i & 0x1FF))
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

    # m_hPlayerPawn: verify at least one controller's pawn handle resolves to a healthy pawn; else brute-scan a window.
    def _resolve_pawn(handle: int) -> int:
        if not handle or handle == 0xFFFFFFFF:
            return 0
        ple = mem.read_long(entity_list + 0x8 * ((handle & 0x7FFF) >> 9) + 0x10)
        if not ple or ple < 0x10000 or ple > 0x7FFFFFFFFFFF:
            return 0
        p = mem.read_long(ple + 0x70 * (handle & 0x1FF))
        if not p or p < 0x10000000000 or p > 0x7FFFFFFFFFFF:
            return 0
        return p

    def _looks_like_pawn(p: int) -> bool:
        try:
            hp = mem.read_int(p + Netvars.m_iHealth)
            team = mem.read_byte(p + Netvars.m_iTeamNum)
            ls = mem.read_byte(p + Netvars.m_lifeState)
            return 0 < hp <= 100 and team in (2, 3) and ls in (0, 1, 2)
        except Exception:
            return False

    current = Netvars.m_hPlayerPawn

    def _score_handle_offset(off: int) -> int:
        """Count how many controllers resolve to a healthy pawn at `off`."""
        s = 0
        for ctrl in controllers:
            try:
                h = mem.read_uint(ctrl + off)
            except Exception:
                continue
            p = _resolve_pawn(h)
            if p and _looks_like_pawn(p):
                s += 1
        return s

    cur_score = _score_handle_offset(current)
    # Always brute-scan a window — one stale controller can make the published offset look "valid enough" while the real offset 8 bytes away resolves ALL controllers. Pick the highest-scoring offset, swap only on a clear win.
    scan_lo = max(0x800, current - 0x40)
    scan_hi = current + 0x40
    best_off = current
    best_score = cur_score
    for off in range(scan_lo, scan_hi + 1, 4):
        if off == current:
            continue
        s = _score_handle_offset(off)
        if s > best_score:
            best_score = s
            best_off = off
    # Swap if strictly better — most walked "controllers" are stale slots reading garbage at every offset, so any candidate resolving more live pawns wins.
    if best_off != current and best_score >= max(2, cur_score + 2):
        print(f"[offsets] AUTO-CORRECT m_hPlayerPawn: 0x{current:X} (score {cur_score}) "
              f"-> 0x{best_off:X} (score {best_score})")
        Netvars.m_hPlayerPawn = best_off
        corrected += 1

    # m_angEyeAngles: ground-truth via dwViewAngles, which holds the local player's actual look direction. Scan a window on the local pawn for an offset whose (pitch, yaw) match dwViewAngles within ~1°. Works even when test bots all face the same direction, which broke the previous "spread across pawns" heuristic.
    try:
        local_pawn_ptr = mem.read_long(mem.client(Offsets.dwLocalPlayerPawn))
    except Exception:
        local_pawn_ptr = 0

    if local_pawn_ptr and Offsets.dwViewAngles:
        try:
            va_addr = mem.client(Offsets.dwViewAngles)
            view_pitch = mem.read_float(va_addr)
            view_yaw = mem.read_float(va_addr + 4)
        except Exception:
            view_pitch = view_yaw = None

        if view_yaw is not None and (-90.5 < view_pitch < 90.5) and (-180.5 < view_yaw < 180.5):
            cur = Netvars.m_angEyeAngles

            def _matches_view(off: int) -> bool:
                try:
                    p = mem.read_float(local_pawn_ptr + off)
                    y = mem.read_float(local_pawn_ptr + off + 4)
                except Exception:
                    return False
                # 1° tolerance — the netvar lags a tick behind the view angles cmd on fast turns.
                return abs(p - view_pitch) < 1.0 and abs(y - view_yaw) < 1.0

            if not _matches_view(cur):
                best = None
                # A CS2 patch can shift the struct by up to ~0x80 in either direction.
                for off in range(cur - 0x80, cur + 0x100 + 1, 4):
                    if off == cur or off < 0:
                        continue
                    if _matches_view(off):
                        best = off
                        break
                if best is not None:
                    print(f"[offsets] AUTO-CORRECT m_angEyeAngles: 0x{cur:X} -> 0x{best:X} "
                          f"(matched dwViewAngles pitch={view_pitch:+.2f} yaw={view_yaw:+.2f})")
                    Netvars.m_angEyeAngles = best
                    corrected += 1

    # m_iIDEntIndex and m_Glow stay dumper-led because false-positive reads and writes are riskier than a no-op.

    return corrected
