"""
Native CS2 glow — writes to each pawn's CGlowProperty.

The engine renders the highlight itself. A daemon thread repeats the writes at a fixed cadence while the render loop only swaps the target pawn list under a lock.
"""

from pathlib import Path
from struct import pack
from threading import Lock, Thread
from time import perf_counter, sleep, strftime

from cs2esp.memory import MemoryReader
from cs2esp.offsets import Offsets, Netvars, CGlowProperty
from cs2esp.visuals.math_utils import TEAM_CT


# The writer runs at least every two milliseconds to beat engine wipes.
_WRITER_PERIOD = 1.0 / 500.0

# Fixed glow-start time so the shader sees the fade-in as already done.
_GLOW_TIME_STEADY = 0.0

GlowTarget = tuple[int, int, tuple, float, int, int]


class GlowSystem:
    def __init__(self, mem: MemoryReader):
        self.mem = mem
        self._lock = Lock()
        self._target: list[GlowTarget] = []
        self._known: set[int] = set()
        self._last_seen: dict[int, float] = {}
        # main.py refreshes live-tunable [glow.advanced] knobs via configure().
        self._writer_burst: int = 1
        self._writer_sleep_ms: float = 10.0
        self._write_color: bool = True
        self._write_type_block: bool = True
        self._write_eligible: bool = True
        self._write_glowing: bool = True
        self._clear_on_drop: bool = True
        self._debug_log: bool = False
        # Local-player slot for VISIBLE_ONLY m_bSpottedByMask tests.
        self._local_slot: int = 0
        self._local_slot_t: float = 0.0
        self._running = True
        self._thread = Thread(
            target=self._writer_loop, name="GlowWriter", daemon=True,
        )
        self._thread.start()

    def configure(
        self,
        *,
        writer_burst: int = 1,
        writer_sleep_ms: float = 10.0,
        write_color: bool = True,
        write_type_block: bool = True,
        write_eligible: bool = True,
        write_glowing: bool = True,
        clear_on_drop: bool = True,
        debug_log: bool = False,
    ) -> None:
        """Push [glow.advanced] knobs into the writer thread live."""
        self._writer_burst = max(1, min(30, int(writer_burst)))
        self._writer_sleep_ms = max(0.0, min(50.0, float(writer_sleep_ms)))
        self._write_color = bool(write_color)
        self._write_type_block = bool(write_type_block)
        self._write_eligible = bool(write_eligible)
        self._write_glowing = bool(write_glowing)
        self._clear_on_drop = bool(clear_on_drop)
        self._debug_log = bool(debug_log)

    def _write_pawn(self, pawn: int, team: int, col: tuple,
                    opacity: float, glow_type: int, range_units: int) -> None:
        """Write CGlowProperty for one pawn.

        The hot fields (type..glowing) are contiguous at 0x30-0x51, so when all
        of them are enabled we push them in one write_bytes. Fewer cross-process
        writes = higher writer cadence, which is what keeps the engine from
        winning the reset race and causing flicker / dark pawns.
        """
        r, g, b, _ = col
        red = max(0, min(255, int(r * 255)))
        green = max(0, min(255, int(g * 255)))
        blue = max(0, min(255, int(b * 255)))
        alpha = max(0, min(255, int(max(0.0, min(1.0, opacity)) * 255)))
        color32 = red | (green << 8) | (blue << 16) | (alpha << 24)
        base = pawn + Netvars.m_Glow
        if int(glow_type) != CGlowProperty.VISIBLE_ONLY:
            glowing_byte = 1
        elif self._local_slot <= 0:
            glowing_byte = 0
        else:
            try:
                spotted_mask = self.mem.read_uint(
                    pawn
                    + Netvars.m_entitySpottedState
                    + Netvars.m_bSpottedByMask
                )
                glowing_byte = int(bool(
                    (spotted_mask >> (self._local_slot - 1)) & 1
                ))
            except Exception:
                glowing_byte = 0

        if self._write_color:
            self.mem.pm.write_bytes(
                base + CGlowProperty.m_fGlowColor,
                pack("<3f", float(r), float(g), float(b)), 12)

        if self._write_type_block and self._write_eligible and self._write_glowing:
            # Fast path: one write over 0x30..0x51 (type block, fade timers, eligible+glowing). "3x" skips padding after m_bFlashing.
            self.mem.pm.write_bytes(
                base + CGlowProperty.m_iGlowType,
                pack("<iiIIIB3xffBB",
                     int(glow_type),                # 0x30 m_iGlowType
                     int(team),                     # 0x34 m_iGlowTeam
                     int(range_units) & 0xFFFFFFFF, # 0x38 m_nGlowRange
                     0,                             # 0x3C m_nGlowRangeMin
                     color32 & 0xFFFFFFFF,          # 0x40 m_glowColorOverride
                     0,                             # 0x44 m_bFlashing
                     _GLOW_TIME_STEADY,             # 0x48 m_flGlowTime
                     0.0,                           # 0x4C m_flGlowStartTime
                     1,                             # 0x50 m_bEligible
                     glowing_byte),                 # 0x51 m_bGlowing
                34)
            return

        # Fallback: sub-flags disabled, write only the requested pieces.
        if self._write_type_block:
            # 0x30..0x50: type block + fade timers (32 bytes).
            self.mem.pm.write_bytes(
                base + CGlowProperty.m_iGlowType,
                pack("<iiIIIB3xff",
                     int(glow_type), int(team),
                     int(range_units) & 0xFFFFFFFF, 0,
                     color32 & 0xFFFFFFFF, 0,
                     _GLOW_TIME_STEADY, 0.0),
                32)
        if self._write_eligible:
            self.mem.write_byte(
                base + CGlowProperty.m_bEligibleForScreenHighlight, 1)
        if self._write_glowing:
            self.mem.write_byte(base + CGlowProperty.m_bGlowing, glowing_byte)

    def update_pawns(
        self,
        pawns: list[tuple[int, int, tuple]],
        local_team: int,
        glow_type: int = CGlowProperty.WORLD_ALWAYS,
        opacity: float = 0.85,
        team_esp: bool = False,
        ct_color=(0.302, 0.647, 1.000, 1.000),
        t_color=(1.000, 0.314, 0.314, 1.000),
        miss_grace_frames: int = 30,
        range_units: float = 0.0,
    ) -> None:
        """Swap the writer-thread target list, honouring miss grace."""
        glow_range = max(0, int(range_units))
        # Floors at 50ms so even tiny grace still smooths single hiccups.
        miss_grace_s = max(0.05, float(miss_grace_frames) / 60.0)
        now = perf_counter()

        target: list[GlowTarget] = []
        seen: set[int] = set()
        for pawn, team, _pos in pawns:
            if not pawn:
                continue
            if team == local_team and not team_esp:
                continue
            color = ct_color if team == TEAM_CT else t_color
            target.append((int(pawn), int(team), tuple(color),
                           float(opacity), int(glow_type), glow_range))
            seen.add(int(pawn))

        for pawn in seen:
            self._last_seen[pawn] = now

        with self._lock:
            prev_target = list(self._target)
        for entry in prev_target:
            pawn_address = entry[0]
            if pawn_address in seen:
                continue
            last_seen = self._last_seen.get(pawn_address)
            if last_seen is None:
                continue
            if now - last_seen < miss_grace_s:
                target.append(entry)
                seen.add(pawn_address)

        with self._lock:
            self._target = target
            stale = self._known - seen
            self._known = seen
        for pawn in stale:
            self._last_seen.pop(pawn, None)
            if not self._clear_on_drop:
                continue
            try:
                self._clear_pawn(pawn)
            except Exception:
                pass

    def _writer_loop(self) -> None:
        """Per-pawn burst writes at the configured cadence."""
        cycles = 0
        last_log = perf_counter()
        while self._running:
            started_at = perf_counter()
            if started_at - self._local_slot_t >= 2.0:
                local_slot = 0
                try:
                    local_controller = self.mem.read_long(
                        self.mem.client(Offsets.dwLocalPlayerController)
                    )
                    entity_list = self.mem.read_long(
                        self.mem.client(Offsets.dwEntityList)
                    )
                except Exception:
                    local_controller = entity_list = 0
                if local_controller and entity_list:
                    for slot in range(1, 65):
                        try:
                            list_entry = self.mem.read_long(
                                entity_list
                                + 0x8 * ((slot & 0x7FFF) >> 9)
                                + 0x10
                            )
                            if not list_entry:
                                continue
                            controller = self.mem.read_long(
                                list_entry + 0x70 * (slot & 0x1FF)
                            )
                            if controller == local_controller:
                                local_slot = slot
                                break
                        except Exception:
                            continue
                self._local_slot = local_slot
                self._local_slot_t = started_at
            burst = max(1, int(self._writer_burst))
            with self._lock:
                target = list(self._target)
            for pawn, team, color, opacity, glow_type, glow_range in target:
                for _ in range(burst):
                    try:
                        self._write_pawn(
                            pawn, team, color, opacity, glow_type, glow_range,
                        )
                    except Exception:
                        break
            elapsed = perf_counter() - started_at
            # The knob may speed up, but never slow down, the base cadence.
            effective_sleep = min(self._writer_sleep_ms / 1000.0, _WRITER_PERIOD)
            tail = max(0.0, effective_sleep - elapsed)
            if tail > 0.0:
                sleep(tail)
            cycles += 1
            if self._debug_log and (perf_counter() - last_log) >= 1.0:
                try:
                    log_dir = Path(__file__).resolve().parents[2] / "temp"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    timestamp = strftime("%H:%M:%S")
                    lines = [
                        f"{timestamp} pawns={len(target)} burst={burst} "
                        f"cycles_s={cycles} writes_pawn_s={cycles * burst}\n"
                    ]
                    for pawn, team, _color, _opacity, glow_type, _range in target:
                        base = pawn + Netvars.m_Glow
                        try:
                            actual_type = self.mem.read_int(
                                base + CGlowProperty.m_iGlowType
                            )
                            glowing = self.mem.read_byte(
                                base + CGlowProperty.m_bGlowing
                            )
                            eligible = self.mem.read_byte(
                                base
                                + CGlowProperty.m_bEligibleForScreenHighlight
                            )
                            status = (
                                "OK"
                                if actual_type == int(glow_type) and glowing == 1
                                else "MISS"
                            )
                            lines.append(
                                f"  pawn={pawn:#x} team={team} "
                                f"type={actual_type}/{int(glow_type)} "
                                f"glowing={glowing} elig={eligible} {status}\n"
                            )
                        except Exception as exc:
                            lines.append(
                                f"  pawn={pawn:#x} team={team} "
                                f"READ_FAIL {exc}\n"
                            )
                    with (log_dir / "glow.log").open(
                        "a", encoding="utf-8"
                    ) as log_file:
                        log_file.writelines(lines)
                except Exception:
                    pass
                cycles = 0
                last_log = perf_counter()

    def stop(self) -> None:
        """Stop the writer thread. Call before program exit."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def clear_all(self) -> None:
        """Stop glow on every pawn and reset engine state (e.g. master off)."""
        with self._lock:
            self._target = []
            known = list(self._known)
            self._known.clear()
            self._last_seen.clear()
        for pawn in known:
            try:
                self._clear_pawn(pawn)
            except Exception:
                pass

    def _clear_pawn(self, pawn: int) -> None:
        """Zero CGlowProperty so the engine drops the highlight."""
        base = pawn + Netvars.m_Glow
        self.mem.write_int(base + CGlowProperty.m_iGlowType, CGlowProperty.NONE)
        self.mem.write_uint(base + CGlowProperty.m_glowColorOverride, 0)
        self.mem.write_byte(base + CGlowProperty.m_bEligibleForScreenHighlight, 0)
        self.mem.write_byte(base + CGlowProperty.m_bGlowing, 0)
