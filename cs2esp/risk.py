"""
Capability evidence and compact, per-platform community-informed estimates. Public reports do not establish detection probabilities for this build.
Source retrieval timestamps are retained; policy metadata was reviewed on 2026-10-29. Supplied configuration is read without runtime actions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from html import escape, unescape
from re import IGNORECASE, compile as compile_pattern, sub
from types import MappingProxyType

from cs2esp.risk_catalog import REVIEW_SOURCES, RISK_RULES


AUDIT_DATE = "2026-10-29"
COMMUNITY_SOURCES = REVIEW_SOURCES


@dataclass(frozen=True, slots=True)
class PlatformRisk:
	"""A transparent qualitative estimate, not a measured detector probability or a confirmed detection of this build."""
	platform: str
	level: str
	reason: str
	confidence: str
	source_ids: tuple[str, ...]

	@property
	def bucket(self) -> str:
		return self.level.lower()

DETECTION_UNKNOWN = "Unknown; no reliable probability or detection guarantee is available."


@dataclass(frozen=True, slots=True)
class PolicySource:
	"""A short official quotation, its revision, and the independently recorded retrieval time."""
	id: str
	title: str
	url: str
	revision: str
	retrieved_at_utc: str
	quote: str


POLICY_SOURCES: Mapping[str, PolicySource] = MappingProxyType({
	"steam_agreement": PolicySource(
		"steam_agreement", "Steam Subscriber Agreement, sections 4.B–D", "https://store.steampowered.com/subscriber_agreement/", "2026-09-10",
		"2026-10-28T18:04:02Z", "You may not use any form of scripts, bots, macros, or other non-human-controlled systems"),
	"steam_vac": PolicySource(
		"steam_vac", "Valve Anti-Cheat (VAC) System", "https://help.steampowered.com/en/faqs/view/571A-97DA-70E9-FF74", "2021-07-12 (FAQ payload)",
		"2026-10-28T18:04:03Z", "VAC bans are permanent, non-negotiable, and cannot be removed by Steam Support."),
	"steam_vac_banned": PolicySource(
		"steam_vac_banned", "I've been VAC banned", "https://help.steampowered.com/en/faqs/view/647C-5CC1-7EA9-3C29", "Not displayed",
		"2026-10-28T18:07:56Z", "We do not disclose the cheats that were detected while connected to a VAC-secured server that resulted in a VAC ban."),
	"steam_game_bans": PolicySource(
		"steam_game_bans", "Banned by Game Developer (Game Ban)", "https://help.steampowered.com/en/faqs/view/46DB-4CEC-F7E9-49E5", "Not displayed",
		"2026-10-28T18:07:56Z", "The game developer is solely responsible for the decision to apply a game ban."),
	"cs2_automation": PolicySource(
		"cs2_automation", "Counter-Strike 2: Side-stepping Skill", "https://store.steampowered.com/news/app/730/view/6500469346429600836", "2024-08-19",
		"2026-10-28T18:04:06Z", "We are no longer going to allow automation (via scripting or hardware) that circumvent these core skills"),
	"faceit_cheat": PolicySource(
		"faceit_cheat", "FACEIT: What is deemed to be a cheat?", "https://support.faceit.com/hc/en-us/articles/360015788779-What-is-deemed-to-be-a-cheat",
		"2025-10-23", "2026-10-28T18:16:12Z", "External scripting/macros designed to gain an advantage, such as no recoil scripts or bunny-hop macros"),
	"faceit_conduct": PolicySource(
		"faceit_conduct", "FACEIT Code of Conduct", "https://support.faceit.com/hc/en-us/articles/7479123364892-Code-of-Conduct", "2026-07-24",
		"2026-10-28T18:16:12Z", "Cheats, or third-party software or hardware that gives a clear unfair edge"),
	"faceit_banning": PolicySource(
		"faceit_banning", "FACEIT Banning Policy", "https://support.faceit.com/hc/en-us/articles/208282375-FACEIT-Banning-Policy", "2026-07-29",
		"2026-10-28T18:04:04Z", "5 years, and 2 years more for every ban evasion attempt escalating to permanent."),
	"faceit_overview": PolicySource(
		"faceit_overview", "What is FACEIT Anti-cheat and how does it work?",
		"https://support.faceit.com/hc/en-us/articles/9394666828188-What-is-FACEIT-Anti-cheat-and-how-does-it-work", "2025-10-23",
		"2026-10-28T18:16:13Z", "A kernel mode driver, loaded upon PC boot"),
	"windows_capture": PolicySource(
		"windows_capture", "Microsoft: SetWindowDisplayAffinity limitations",
		"https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity", "2024-02-22",
		"2026-10-28T18:07:58Z", "there is no guarantee that using SetWindowDisplayAffinity and GetWindowDisplayAffinity"),
})

_CHEAT_SOURCES = ("steam_agreement", "steam_vac", "steam_vac_banned", "faceit_cheat", "faceit_banning")
_AUTOMATION_SOURCES = (*_CHEAT_SOURCES, "cs2_automation")
_MEMORY_SOURCES = ("steam_agreement", "steam_vac_banned", "steam_game_bans", "faceit_cheat")
_UI_SOURCES = ("steam_agreement", "faceit_conduct")
_LIMIT_SOURCES = ("steam_agreement", "steam_vac_banned", "faceit_cheat", "faceit_overview")

_POLICY_COPY = {
	"prohibited": ("High", "high", "Competitive use of this unfair-information or automation capability is prohibited by Steam/Valve and FACEIT rules."),
	"ui_only": ("", "neutral",
		"This passive setting changes app presentation or configuration only; it does not certify the rest of the app or any gameplay feature."),
	"check_rules": ("", "neutral", "No specific approval for this overlay aid was found. Tournament/platform rules and the app's other behavior still apply."),
	"memory_access": ("Medium", "medium", "External game-memory access is not proof of policy approval. Using it to gain an unfair advantage is prohibited."),
	"limited_effect": ("", "neutral", "This setting is not an anti-cheat bypass or approval. Cheats and interference with anti-cheat remain prohibited."),
	"policy_warning": ("", "neutral",
		"A legacy warning cannot establish detector coverage. Cheats, automation, and unauthorized anti-cheat interference are prohibited."),
	"unassessed": ("", "neutral", "No capability-specific assessment is available. Do not treat a missing or legacy label as approval."),
}
_EXPOSURE_LABELS = {
	"ui_only": "Passive app UI/configuration", "game_memory_read": "External game-memory reads", "game_memory_write": "Game-memory writes",
	"write_capable_handle": "Write-capable process handle", "overlay": "External overlay", "unfair_information": "Extra gameplay information",
	"automated_aiming": "Automated aiming", "automated_firing": "Automated firing", "automated_recoil": "Automated recoil compensation",
	"automated_movement": "Automated movement", "synthetic_input": "Synthetic input", "file_reads": "Local game/content reads",
	"file_output": "Generated file/clipboard output", "system_metrics": "System/ETW metrics", "capture_exclusion": "Window capture exclusion",
	"window_identity": "Window identity/chrome changes", "self_memory_write": "Own-process bookkeeping writes", "dll_copy_load": "DLL copy/load",
	"extra_memory_reads": "Additional external reads", "read_timing": "Read-loop timing changes", "write_guard": "Caller-side write/input gate",
}


@dataclass(frozen=True, slots=True)
class ModeEvidence:
	"""Technical behavior of one implemented mode, not an estimate of ban probability."""
	name: str
	detail: str
	exposures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Capability:
	"""Stable capability ID, settings scope, implementation evidence, and official policy references."""
	id: str
	title: str
	policy: str
	exposures: tuple[str, ...]
	modes: tuple[ModeEvidence, ...]
	limitations: str
	source_ids: tuple[str, ...]
	settings: tuple[str, ...] = ()
	implementations: tuple[str, ...] = ()


_READ_VISUAL = ("game_memory_read", "overlay", "unfair_information")
_INPUT_FIRE = ("game_memory_read", "synthetic_input", "automated_firing")

_GAME_EVENT_NAMES = (
	"hit", "miss", "kill", "headshot", "knife", "zeus", "bhop", "triggerbot", "bomb_plant", "bomb_defusing", "bomb_defused", "bomb_exploded",
	"spectator_new", "rear_threat",
)
_APP_EVENT_NAMES = ("offsets_updated", "hotkey_toggled", "config_loaded")
_GAME_SOUND_FAMILIES = ("hit", "kill", "headshot", "headshot_hit", "headshot_kill", "one_tap", "miss", "bomb", "spectator")
_APP_SOUND_FAMILIES = ("system", "toggle")


_CAPABILITY_DATA = (
	Capability("wallhack", "Wallhack master", "prohibited", (*_READ_VISUAL, "game_memory_write"), (
		ModeEvidence("overlay", "Player ESP uses externally read positions, bones, health and names in the own overlay; it does not write game memory.", _READ_VISUAL),
		ModeEvidence("with_glow", "Player ESP plus enabled native Glow can include repeated game-memory writes. The master is not an anti-cheat approval.",
			(*_READ_VISUAL, "game_memory_write"))),
		"Turning the master off does not certify other features or the write-capable memory attachment.", _CHEAT_SOURCES,
		("wallhack", "visuals.team_esp", "visuals.distance_cap"), ("main.py:wallhack_on", "cs2esp/visuals/esp.py:draw_player_esp")),
	Capability("box", "Box ESP", "prohibited", _READ_VISUAL, (
		ModeEvidence("bones", "Projects externally read player bones into fitted boxes in the own overlay.", _READ_VISUAL),
		ModeEvidence("bounds", "Projects externally read collision bounds into boxes in the own overlay.", _READ_VISUAL),
		ModeEvidence("fixed", "Uses externally read player positions and a fixed-size approximation in the own overlay.", _READ_VISUAL)),
		"Geometry/fill/color choices do not make extra enemy information permissible or establish detection certainty.", _CHEAT_SOURCES,
		("visuals.box",), ("cs2esp/entity.py:EntitySystem.get_players", "cs2esp/visuals/esp.py:draw_player_esp")),
	Capability("skeleton", "Skeleton ESP", "prohibited", _READ_VISUAL,
		(ModeEvidence("wireframe", "Projects externally read player bones into a wireframe in the own overlay; no game-memory write is performed.", _READ_VISUAL),),
		"A read-only overlay still provides extra player information. Bone data and projected geometry do not establish local line-of-sight.", _CHEAT_SOURCES,
		("visuals.skeleton",), ("cs2esp/entity.py:EntitySystem.get_players", "cs2esp/visuals/skeleton.py:draw_skeleton")),
	Capability("head_outline", "Head outline", "prohibited", _READ_VISUAL,
		(ModeEvidence("ellipse", "Projects an estimated head outline from externally read bones; trigger modes may consume this geometry.", _READ_VISUAL),),
		"The outline is an estimate, not the authoritative game hitbox or an occlusion/line-of-sight proof.", _CHEAT_SOURCES,
		("visuals.skeleton.head_outline",), ("cs2esp/visuals/skeleton.py:compute_head_outline_polygon",)),
	Capability("glow", "Engine glow", "prohibited", (*_READ_VISUAL, "game_memory_write"), (
		ModeEvidence("always", "Repeatedly writes pawn CGlowProperty color, type, timers and highlight flags; the engine renders the result.",
			(*_READ_VISUAL, "game_memory_write")),
		ModeEvidence("visible", "Writes the same native glow fields with a spotted-mask filter and screen projection; this filter does not prove local line-of-sight.",
			(*_READ_VISUAL, "game_memory_write"))),
		"The read-only flag gates normal target publication in main.py, not every low-level writer. Glow cleanup can still write; detector outcomes are unknown.",
		_CHEAT_SOURCES, ("glow",), ("cs2esp/features/glow.py:GlowSystem._write_pawn", "cs2esp/features/glow.py:GlowSystem._clear_pawn")),
	Capability("chams", "Overlay chams", "prohibited", (*_READ_VISUAL, "file_reads"), (
		ModeEvidence("vpk",
			"VPK chams read local game agent meshes and external bone poses, skin them on the GPU, and draw in the own overlay without game-memory writes.",
			(*_READ_VISUAL, "file_reads")),
		ModeEvidence("legacy", "Legacy chams construct bone-following primitives and draw in the own overlay without game-memory writes.", _READ_VISUAL)),
		"Neither renderer is native Glow or an injected material modification. No writes does not mean approval, and overlay pixels do not prove game-world visibility.",
		_CHEAT_SOURCES, ("visuals.chams",), ("cs2esp/visuals/vpk_chams.py:VpkChamsRenderer", "cs2esp/visuals/chams.py:Chams3DRenderer")),
	Capability("player_labels", "Player labels", "prohibited", _READ_VISUAL,
		(ModeEvidence("labels", "Displays externally read player names, health and derived distances in the own overlay.", _READ_VISUAL),),
		"Labels expose game state outside the native HUD. Styling does not remove the extra-information capability.", _CHEAT_SOURCES,
		("visuals.name",), ("cs2esp/visuals/esp.py:draw_player_esp",)),
	Capability("health", "Enemy health", "prohibited", _READ_VISUAL,
		(ModeEvidence("health", "Displays externally read enemy health as a bar, percentage, or both; the off mode suppresses the display only.", _READ_VISUAL),),
		"Health visibility choices do not approve use of external enemy information.", _CHEAT_SOURCES,
		("visuals.health",), ("cs2esp/visuals/esp.py:draw_player_esp",)),
	Capability("distance", "Player distance", "prohibited", _READ_VISUAL,
		(ModeEvidence("distance", "Derives player distance from externally read positions and displays it in the own overlay.", _READ_VISUAL),),
		"The display does not test wall occlusion; external reads remain part of the information feature.", _CHEAT_SOURCES,
		("visuals.distance",), ("cs2esp/visuals/esp.py:draw_player_esp",)),
	Capability("snapline", "Threat lines", "prohibited", _READ_VISUAL,
		(ModeEvidence("snapline", "Draws directional lines to externally read player positions in the own overlay.", _READ_VISUAL),),
		"The line is not an authoritative visibility or threat test.", _CHEAT_SOURCES,
		("visuals.snapline",), ("cs2esp/visuals/esp.py:draw_player_esp",)),
	Capability("radar", "External radar", "prohibited", _READ_VISUAL,
		(ModeEvidence("radar", "Maps externally read enemy/team positions into a local-player-centered overlay; no game-memory writes are performed.", _READ_VISUAL),),
		"FACEIT's allowed 'Simple radar' entry is not approval of this external enemy-position reader. Similar names do not imply equivalent behavior.", _CHEAT_SOURCES,
		("radar",), ("cs2esp/visuals/radar.py:Radar.render", "main.py:radar.render")),
	Capability("awareness", "Threat vignette", "prohibited", _READ_VISUAL,
		(ModeEvidence("vignette", "Combines externally read enemy positions/eye angles into an approximate directional threat vignette.", _READ_VISUAL),),
		"The view-cone approximation does not raycast walls or establish reliable local line-of-sight.", _CHEAT_SOURCES,
		("awareness",), ("cs2esp/visuals/overlays.py:Awareness.update",)),
	Capability("crosshair", "Static overlay crosshair", "check_rules", ("overlay",),
		(ModeEvidence("static", "Draws a static screen-center marker; this renderer does not read enemy state, aim, fire, or write game memory.", ("overlay",)),),
		"A static marker is separate from ESP and automation. No specific official approval of this app was found; the running app may still read/write game state.",
		_UI_SOURCES, ("visuals.crosshair",), ("cs2esp/visuals/esp.py:draw_crosshair",)),
	Capability("triggerbot", "Triggerbot", "prohibited", (*_INPUT_FIRE, "overlay", "game_memory_write"), (
		ModeEvidence("feedback",
			"Feedback mode reads the engine target/health and delivers automated firing via SendInput; the trigger itself does not write game memory.", _INPUT_FIRE),
		ModeEvidence("vanilla_default", "Vanilla default mode reads the engine target and applies bone-angle gates, then delivers automated firing via SendInput.",
			_INPUT_FIRE),
		ModeEvidence("vanilla_skeleton", "Vanilla skeleton mode adds projected head geometry to engine-target/bone gates and fires via SendInput.",
			(*_INPUT_FIRE, "overlay")),
		ModeEvidence("vanilla_chams_vpk",
			"Vanilla VPK-chams mode probes pixels in the own overlay, with geometry fallback, then fires via SendInput; chams adds no game-memory write.",
			(*_INPUT_FIRE, "overlay", "file_reads")),
		ModeEvidence("vanilla_chams_legacy",
			"Vanilla Legacy-chams mode probes pixels in the own overlay, with geometry fallback, then fires via SendInput; chams adds no game-memory write.",
			(*_INPUT_FIRE, "overlay")),
		ModeEvidence("vanilla_glow", "Vanilla Glow mode reads the engine target and fires via SendInput; coupled Glow is a separate game-memory writer.",
			(*_INPUT_FIRE, "game_memory_write"))),
		"All paths automate firing and remain prohibited in competitive use. Delay, jitter, head filters, and a read-only trigger do not establish "
		"approval or detection rates.",
		_AUTOMATION_SOURCES, ("triggerbot",), ("cs2esp/features/triggerbot_feedback.py:FeedbackTriggerbot.update",
			"cs2esp/features/triggerbot_vanilla.py:Triggerbot.update", "cs2esp/features/triggerbot_vanilla.py:_click")),
	Capability("aimbot", "Aimbot", "prohibited", ("game_memory_read", "game_memory_write", "automated_aiming"), (
		ModeEvidence("visible",
			"Writes dwViewAngles to automate aiming. m_bSpotted is a team-visibility signal, not reliable local line-of-sight; mask/grace fallback can accept targets.",
			("game_memory_read", "game_memory_write", "automated_aiming")),
		ModeEvidence("recent", "Writes dwViewAngles with spotted-state plus recent-target grace; m_bSpotted and the mask are not reliable local line-of-sight.",
			("game_memory_read", "game_memory_write", "automated_aiming")),
		ModeEvidence("any", "Writes dwViewAngles toward selected targets without the spotted-state filter; this is automated aiming, not a passive overlay.",
			("game_memory_read", "game_memory_write", "automated_aiming"))),
		"The normal aim writer checks the live write guard. Humanized paths/smoothing do not change prohibition or prove detector avoidance; "
		"no VAC/FACEIT ranking is justified.",
		_AUTOMATION_SOURCES, ("aimbot",), ("cs2esp/features/aimbot.py:WPMAimbot._smooth_write", "cs2esp/features/aimbot.py:WPMAimbot._track_write",
			"cs2esp/features/aimbot.py:WPMAimbot._is_visible")),
	Capability("rapidfire", "Rapid-fire", "prohibited", ("synthetic_input", "automated_firing"),
		(ModeEvidence("clicker", "Synthesizes repeated left-button press/release events through mouse_event at a configured rate; no game-memory write is performed.",
			("synthetic_input", "automated_firing")),),
		"Click jitter changes timing, not the automation policy or undisclosed detection outcome.", _AUTOMATION_SOURCES,
		("rapidfire",), ("cs2esp/features/autoclick.py:RapidFire", "cs2esp/features/autoclick.py:_ClickerBase.update")),
	Capability("autopistol", "Auto-pistol", "prohibited", ("synthetic_input", "automated_firing"),
		(ModeEvidence("clicker", "Uses the same mouse_event click synthesizer with a pistol cadence preset; no game-memory write is performed.",
			("synthetic_input", "automated_firing")),),
		"A held hotkey or timing variation does not make repeated automated firing permissible.", _AUTOMATION_SOURCES,
		("autopistol",), ("cs2esp/features/autoclick.py:AutoPistol",)),
	Capability("bhop", "BHop / auto-jump", "prohibited", ("game_memory_read", "synthetic_input", "automated_movement"),
		(ModeEvidence("auto_jump", "Reads ground flags, movement state and vertical motion, then synthesizes SPACE events with SendInput and landing prediction.",
			("game_memory_read", "synthetic_input", "automated_movement")),),
		"Jump prediction is not perfect. Jitter/skips do not approve automation. Community servers can also use VAC and impose their own bans; "
		"no server-type safety is promised.",
		_AUTOMATION_SOURCES, ("bhop",), ("cs2esp/features/bhop.py:BHop.update", "cs2esp/features/bhop.py:_send_space_down")),
	Capability("recoil_control", "In-game recoil control", "prohibited", ("game_memory_read", "synthetic_input", "automated_recoil", "file_reads"),
		(ModeEvidence("live_punch",
			"Reads live aim punch/shots and weapon metadata, reads local sensitivity, and automates compensation with mouse_event relative movement.",
			("game_memory_read", "synthetic_input", "automated_recoil", "file_reads")),),
		"No game-memory writes does not make automated recoil permissible. The read-only flag does not stop this normal input path. "
		"Offsets and compensation accuracy can change.",
		_AUTOMATION_SOURCES, ("recoil_control",), ("cs2esp/features/recoil_control.py:RecoilControl.update", "cs2esp/features/recoil_control.py:_send_mouse_move")),
	Capability("recoil_script_logitech", "Logitech recoil macro", "prohibited", ("synthetic_input", "automated_recoil", "file_output"),
		(ModeEvidence("lua", "The generated G HUB Lua script replays fixed relative mouse movements and can synthesize weapon-selection keys when executed separately.",
			("synthetic_input", "automated_recoil", "file_output")),),
		"Generating text does not execute it. Competitive use of the generated macro is prohibited even without game-memory reads/writes; "
		"vendor drivers do not establish approval.",
		_AUTOMATION_SOURCES, (), ("cs2esp/features/norecoil_scripts.py:_build_logitech_lua",)),
	Capability("recoil_script_ahk", "AutoHotkey recoil macro", "prohibited", ("synthetic_input", "automated_recoil", "file_output"),
		(ModeEvidence("ahk", "The generated AHK v2 script replays fixed mouse_event movement and weapon-selection input when executed separately.",
			("synthetic_input", "automated_recoil", "file_output")),),
		"Generating/copying text does not execute it. External macros remain prohibited in competitive use; no driver/HID or no-memory safety claim is supported.",
		_AUTOMATION_SOURCES, (), ("cs2esp/features/norecoil_scripts.py:_build_ahk_universal",)),
	Capability("script_generator", "Script file/clipboard actions", "ui_only", ("ui_only", "file_output"),
		(ModeEvidence("text", "Builds script text for a save dialog or clipboard; these app actions do not execute the generated macro.", ("ui_only", "file_output")),),
		"The generated recoil macros have a separate PROHIBITED assessment when used for gameplay; UI ONLY is not an approval to run them.", _UI_SOURCES,
		(), ("cs2esp/ui/qt/panels/norecoil.py:NoRecoilPanel._download", "cs2esp/ui/qt/panels/norecoil.py:NoRecoilPanel._copy")),
	Capability("event_feed", "Combat event feed", "prohibited", _READ_VISUAL,
		(ModeEvidence("events", "Displays events inferred from externally read enemy health/shots and tactical state, alongside ordinary app notifications.",
			_READ_VISUAL),),
		"Inferred hits/misses and reasons are not authoritative game telemetry. Styling app notifications alone is passive; the live combat feed is not.", _CHEAT_SOURCES,
		("event_visuals.filter_combat", "event_visuals.filter_tactical", "event_visuals.filter_c4", "event_visuals.filter_modules",
			*(f"event_visuals.ev_{event_name}_enabled" for event_name in _GAME_EVENT_NAMES)),
		("cs2esp/features/combat_events.py:CombatEventTracker.update", "cs2esp/visuals/event_feed.py:render_event_feed")),
	Capability("c4_timer", "C4 timer", "prohibited", _READ_VISUAL,
		(ModeEvidence("bomb", "Externally reads planted-C4 timing/site/defuse state and estimates local bomb damage for the own overlay.", _READ_VISUAL),),
		"The countdown and damage use fallbacks/approximations, not an official accuracy or permitted-use guarantee.", _CHEAT_SOURCES,
		("event_visuals.show_c4_timer",), ("cs2esp/visuals/c4_capsule.py:_read_bomb_state",)),
	Capability("spectators", "Spectator list", "prohibited", _READ_VISUAL,
		(ModeEvidence("observers", "Walks externally read controller/pawn observer targets and displays watchers' names in the own overlay.", _READ_VISUAL),),
		"This extra observer information is not evidence that anti-cheat/human review is absent, nor approval of other gameplay features.", _CHEAT_SOURCES,
		("event_visuals.show_spectators",), ("cs2esp/entity.py:EntitySystem.get_spectators_of_local",)),
	Capability("hud_suite", "HUD and event suite", "prohibited", _READ_VISUAL,
		(ModeEvidence("suite", "Groups combat events, bomb information, spectator data, hotkey state and status presentation in the own overlay.", _READ_VISUAL),),
		"Individual chrome controls are passive, but game-state-derived information in this suite is not certified by UI styling.", _CHEAT_SOURCES,
		("event_visuals.enabled",), ("main.py:HUD rendering",)),
	Capability("event_sounds", "Game-event sound cues", "prohibited", ("game_memory_read", "unfair_information"),
		(ModeEvidence("cues", "Plays local cues for events derived from external game state, including enemy health, bomb and spectator information.",
			("game_memory_read", "unfair_information")),),
		"File/volume selection and previews alone are passive. Live cues inherit the event's extra-information capability.", _CHEAT_SOURCES,
		("event_sounds.enabled", *(f"event_sounds.{family}_enabled" for family in _GAME_SOUND_FAMILIES)),
		("cs2esp/features/event_sounds.py:EventSounds", "cs2esp/features/event_system.py:EventSystem")),
	Capability("telemetry", "Game/system telemetry", "memory_access", ("game_memory_read", "system_metrics", "overlay"),
		(ModeEvidence("mixed", "Samples system/process metrics and ETW presentation data, with external reads for game frame counters and scoreboard ping.",
			("game_memory_read", "system_metrics", "overlay")),),
		"Telemetry labels do not certify the app. Hiding a metric does not necessarily stop background game-memory sampling; "
		"presentation delay is not physical input latency.",
		_MEMORY_SOURCES, ("fps_counter",), ("cs2esp/metrics.py:RealMetrics.update", "cs2esp/visuals/fps_counter.py:FpsCounter")),
	Capability("diagnostics", "Live health diagnostics", "memory_access", ("game_memory_read", "write_capable_handle"),
		(ModeEvidence("probe",
			"Attaches a write-capable MemoryReader, signature-scans/probes game state, and reports preconditions; the diagnostic checks do not write game memory.",
			("game_memory_read", "write_capable_handle")),),
		"PASS means a precondition check passed, not permitted use or anti-cheat compatibility. A successful probe is not a detection test.", _MEMORY_SOURCES,
		(), ("cs2esp/diagnostics.py:run_full_health_check",)),
	Capability("offsets", "Offset refresh/verification", "memory_access", ("game_memory_read", "write_capable_handle", "file_reads"),
		(ModeEvidence("refresh", "Fetches public offset data, may scan/verify the live game through external reads, and updates the app's offset cache.",
			("game_memory_read", "write_capable_handle", "file_reads")),),
		"Offset correctness is separate from policy compliance and detector exposure. A cache-only run is not proof that no later live access occurs.", _MEMORY_SOURCES,
		(), ("cs2esp/offsets.py:refresh_offsets_live", "cs2esp/ui/qt/panels/settings.py:_OffsetRefreshWorker")),
	Capability("memory_reader", "External memory attachment", "memory_access", ("game_memory_read", "write_capable_handle"),
		(ModeEvidence("external",
			"MemoryReader uses direct ReadProcessMemory; attach tries full access then a limited handle that still includes VM_WRITE and VM_OPERATION.",
			("game_memory_read", "write_capable_handle")),),
		"The reader also exposes write helpers. No-writes in one feature does not mean a read-only process handle or guaranteed anti-cheat compatibility.",
		_MEMORY_SOURCES,
		(), ("cs2esp/memory.py:MemoryReader.attach", "cs2esp/memory.py:MemoryReader.read_bytes", "cs2esp/memory.py:MemoryReader.write_byte")),
	Capability("capture_exclusion", "Overlay capture exclusion", "limited_effect", ("capture_exclusion", "overlay"),
		(ModeEvidence("affinity", "Requests SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) on the app's own overlay window; the API may fail.",
			("capture_exclusion", "overlay")),),
		"This covers a specific set of public OS capture APIs, not every capture path or detector. It is not a security boundary, anti-cheat bypass, or ban guarantee.",
		(*_LIMIT_SOURCES, "windows_capture"), ("anti_detection.hide_overlay_from_capture",),
		("cs2esp/win32_utils.py:set_capture_protect", "cs2esp/visuals/overlay.py:Overlay.set_capture_protect")),
	Capability("capture_reapply", "Capture-affinity reapply", "limited_effect", ("capture_exclusion", "overlay"),
		(ModeEvidence("reapply", "The normal loop reapplies capture affinity roughly once per second while capture exclusion and this setting are enabled.",
			("capture_exclusion", "overlay")),),
		"The legacy setting name mentions focus loss, but the loop does not check focus here. Reapplying does not extend API coverage or prove an anti-cheat bypass.",
		(*_LIMIT_SOURCES, "windows_capture"), ("anti_detection.auto_capture_protect_on_focus_loss",), ("main.py:last_capture_reapply_t",)),
	Capability("taskbar_visibility", "Taskbar visibility", "limited_effect", ("window_identity",),
		(ModeEvidence("chrome", "Changes the overlay's tool-window/taskbar presentation; it does not hide the process or prevent process inspection.",
			("window_identity",)),),
		"Window chrome is not an anti-cheat bypass or approval. Other reads, writes, information displays and automation remain unchanged.", _LIMIT_SOURCES,
		("anti_detection.hide_from_taskbar",), ("main.py:want_no_taskbar", "cs2esp/win32_utils.py:apply_taskbar_invisibility")),
	Capability("window_title", "Window-title variation", "limited_effect", ("window_identity",),
		(ModeEvidence("startup", "Selects a different overlay title at startup; a config change does not rename the current window through this path.",
			("window_identity",)),),
		"Changing a title does not hide executable/module identity or prove detector avoidance. No anti-cheat bypass is established.", _LIMIT_SOURCES,
		("anti_detection.randomize_window_title",), ("cs2esp/visuals/overlay.py:Overlay.__init__",)),
	Capability("read_jitter", "Read-loop jitter", "limited_effect", ("game_memory_read", "read_timing"),
		(ModeEvidence("cadence", "Adds a configured random sleep to the normal render/read loop tail; it does not wrap each ReadProcessMemory call.",
			("game_memory_read", "read_timing")),),
		"Background-reader cadence can differ. No detector benefit or anti-cheat bypass is established; the target addresses and gameplay capabilities remain.",
		_LIMIT_SOURCES,
		("anti_detection.randomize_read_jitter_ms",), ("main.py:anti_detect.randomize_read_jitter_ms",)),
	Capability("write_guard", "Read-only scheduling guard", "limited_effect", ("write_guard",),
		(ModeEvidence("caller_gate",
			"The read-only flag gates normal Aimbot/Glow scheduling and automated fire/click/jump/recoil input; it releases owned pending input on entry.",
			("write_guard",)),),
		"Owned cleanup and already in-flight Glow writes can still finish; external reads/ESP and separately executed macros remain independent. "
		"The process handle stays write-capable; this is a scheduling guard, not low-level enforcement.",
		_LIMIT_SOURCES, ("anti_detection.read_only_mode",), ("main.py:writes_allowed", "cs2esp/features/aimbot.py:WPMAimbot._smooth_write",
			"cs2esp/features/glow.py:GlowSystem.clear_all", "cs2esp/memory.py:MemoryReader")),
	Capability("module_spoof", "Module display-path alias", "limited_effect", ("self_memory_write",),
		(ModeEvidence("startup",
			"Changes this interpreter's own loader display path, preserves its actual loader basename and optionally detaches its load-order entry.",
			("self_memory_write",)),),
		"No DLL is copied or loaded. The mapped image and other loader indexes retain their real identity; no detector avoidance or bypass is established. "
		"Complete values and list links are verified and restored on normal interpreter exit.",
		_LIMIT_SOURCES, ("anti_detection.module_spoof_enabled", "anti_detection.module_spoof_masquerade_name"),
		("cs2esp/anti_detect/module_spoof.py:spoof_python_module_name",)),
	Capability("module_unlink", "PEB load-order unlink option", "limited_effect", ("self_memory_write",),
		(ModeEvidence("load_order",
			"The existing module-spoof path optionally unlinks the matched entry from the PEB load-order list only; it does not unlink all three loader lists.",
			("self_memory_write",)),),
		"This option depends on module spoof at startup. It does not erase kernel records or prove concealment from kernel inspection; no bypass is established.",
		_LIMIT_SOURCES,
		("anti_detection.module_spoof_unlink_from_peb",), ("cs2esp/anti_detect/module_spoof.py:spoof_python_module_name",)),
	Capability("read_obfuscator", "Additional-read wrapper", "limited_effect", ("game_memory_read", "extra_memory_reads"),
		(ModeEvidence("native_reader",
			"Adds configured extra native read attempts before MemoryReader's typed and block reads, preserving the real call's arguments and result.",
			("game_memory_read", "extra_memory_reads")),
			ModeEvidence("pymem_wrapper", "The compatible legacy factory can separately wrap a Pymem object's read_bytes method.",
				("game_memory_read", "extra_memory_reads"))),
		"Coverage is per-reader, not a global RPM hook. Separate direct Pymem calls require their own wrapper. Extra attempts can fail and add CPU cost. "
		"No detector benefit or bypass is established.",
		_LIMIT_SOURCES, ("anti_detection.read_obfuscator_enabled", "anti_detection.read_obfuscator_bogus_per_real"),
		("cs2esp/anti_detect/read_obfuscator.py:install_global", "cs2esp/memory.py:MemoryReader.read_bytes")),
	Capability("window_class", "Window-class masquerade", "limited_effect", ("window_identity",),
		(ModeEvidence("startup",
			"Registers an app-owned native host under the selected Win32 class and embeds the Qt menu; Windows class-name readback verifies the result.",
			("window_identity",)),),
		"No external matching window is needed. Embedded child windows retain their actual Qt classes; process and image identity are unchanged. "
		"This is not an anti-cheat bypass or proof of a protective effect.",
		_LIMIT_SOURCES, ("anti_detection.window_class_masquerade_enabled", "anti_detection.window_class_masquerade_target"),
		("cs2esp/anti_detect/window_class.py:masquerade_qt_window", "cs2esp/ui/qt/menu_app.py:launch_menu")),
)

_PASSIVE_CAPABILITIES = (
	("appearance", "App appearance", ("general.ui_theme", "general.ui_scale", "general.custom_theme_color")),
	("window_behavior", "App window behavior", ("general.focus_cs2_on_launch", "general.hide_terminal", "general.start_menu_visible")),
	("persistence", "App configuration", ("general.autosave", "general.autosave_interval_sec", "general.toggle_menu_key", "general.exit_key", "meta")),
	("keybinds", "Binding editor", ("general.master_toggle_key",)),
	("renderer", "Renderer/cadence preference", ("overlay", "general.target_fps")),
	("hud_style", "HUD presentation", (
		"hud_positions", "event_visuals.theme", "event_visuals.custom_color", "event_visuals.font_size", "event_visuals.max_events",
		"event_visuals.event_duration_sec", "event_visuals.corner_radius", "event_visuals.bloom_intensity", "event_visuals.shadow_intensity",
		"event_visuals.icon_outline_width", "event_visuals.icon_accent_color", "event_visuals.icon_text_color", "event_visuals.edit_lock",
		"event_visuals.edit_mode", "event_visuals.filter_system",
		*(f"event_visuals.ev_{event_name}_{field_name}" for event_name in _GAME_EVENT_NAMES + _APP_EVENT_NAMES
			for field_name in ("color", "glyph", "label")),
		*(f"event_visuals.ev_{event_name}_enabled" for event_name in _APP_EVENT_NAMES),
	)),
	("sound_style", "Sound selection/volume", (
		"event_sounds.volume", "event_sounds.ui_order",
		*(f"event_sounds.{family}_{field_name}" for family in _GAME_SOUND_FAMILIES + _APP_SOUND_FAMILIES for field_name in ("file", "volume")),
		*(f"event_sounds.{family}_enabled" for family in _APP_SOUND_FAMILIES),
	)),
	("hotkeys_hud", "Hotkey state HUD", ("event_visuals.show_hotkeys",)),
	("status_hud", "Feature-state HUD", ("event_visuals.show_status_pills",)),
)

CAPABILITIES: Mapping[str, Capability] = MappingProxyType({
	**{entry.id: entry for entry in _CAPABILITY_DATA},
	**{capability_id: Capability(
		capability_id, title, "ui_only", ("ui_only",),
		(ModeEvidence("passive", "Changes app presentation/configuration or displays local app state; it does not add gameplay automation or enemy information.",
			("ui_only",)),),
		"This classification applies to the setting alone. It does not certify other game-state readers, writes, overlays, cues or macros in the running app.",
		_UI_SOURCES, settings, ("cs2esp/ui/qt/panels",)) for capability_id, title, settings in _PASSIVE_CAPABILITIES},
})

LEGACY_RISK_ALIASES: Mapping[str, str] = MappingProxyType({
	"safe": "unassessed", "medium": "policy_warning", "medium_faceit_only": "policy_warning",
	"high": "policy_warning", "high_faceit": "policy_warning", "high_both": "policy_warning",
})
_CAPABILITY_ALIASES = {"names": "player_labels", "recoil": "recoil_control", "norecoil": "recoil_script_ahk", "capture": "capture_exclusion"}
_ENABLED_SETTINGS = {
	"wallhack": "wallhack.enabled", "box": "visuals.box.enabled", "skeleton": "visuals.skeleton.enabled",
	"head_outline": "visuals.skeleton.head_outline_enabled", "glow": "glow.enabled", "chams": "visuals.chams.enabled",
	"triggerbot": "triggerbot.enabled", "aimbot": "aimbot.enabled", "rapidfire": "rapidfire.enabled", "autopistol": "autopistol.enabled",
	"bhop": "bhop.enabled", "recoil_control": "recoil_control.enabled", "module_spoof": "anti_detection.module_spoof_enabled",
	"read_obfuscator": "anti_detection.read_obfuscator_enabled", "window_class": "anti_detection.window_class_masquerade_enabled",
}
_UNSUPPORTED_COPY = compile_pattern(
	r"\b(?:safe|safest|undetected|undetectable|unbannable|known[- ]flagged)\b|\bVAC\b.{0,100}\b(?:low|medium|higher|less)\b|"
	r"\bFACEIT[- ]only\b|\bdefeats?\b|\bblinds?\b|\b(?:breaks?|avoid|bypass)\b.{0,70}\b(?:detection|detector|analysis|analysers|anti-cheat)\b|"
	r"\b(?:engine|mask)\b.{0,40}\bline[- ]of[- ]sight\b|(?<!read-)(?<!read )\bonly\b.{0,50}\b(?:Glow|writing feature)\b|\bno documented bans\b", IGNORECASE)


_GRADE_ORDER = {"": -1, "SAFE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_EVENT_GROUPS = {
	"event_combat": ("filter_combat", ("hit", "miss", "kill", "headshot", "knife", "zeus")),
	"event_bomb": ("filter_c4", ("bomb_plant", "bomb_defusing", "bomb_defused", "bomb_exploded")),
	"event_observer_threat": ("filter_tactical", ("spectator_new", "rear_threat")),
}
_WALLHACK_SELECTION = {
	"box": "visuals.box.enabled", "skeleton": "visuals.skeleton.enabled", "chams": "visuals.chams.enabled", "glow": "glow.enabled",
	"player_labels": "visuals.name.enabled", "distance": "visuals.distance.enabled", "snapline": "visuals.snapline.enabled",
}
_SETTING_OVERRIDES = {
	"general.menu_external_window": "window_behavior", "general.qt_menu_enabled": "window_behavior",
	"general.menu_window_width": "window_behavior", "general.menu_window_height": "window_behavior",
	"general.menu_pre_max_width": "window_behavior", "general.menu_pre_max_height": "window_behavior",
	"general.master_enabled": "runtime_master", "general.last_panel_key": "persistence", "visuals.font_size": "appearance",
	"event_visuals.filter_modules": "hud_style", "event_visuals.filter_combat": "event_combat",
	"event_visuals.filter_c4": "event_bomb", "event_visuals.filter_tactical": "event_observer_threat",
	"fps_counter.show_fps": "telemetry_game_fields", "fps_counter.show_ping": "telemetry_game_fields",
	"fps_counter.show_latency": "telemetry_etw_action", "event_visuals.filter_system": "hud_style",
}


def _extra_capabilities() -> dict[str, Capability]:
	'''Build the RISK_RULES variant and aggregate group entries appended to CAPABILITIES.'''
	extras = {}
	for variant_id in RISK_RULES.keys() - CAPABILITIES.keys():
		rule = RISK_RULES[variant_id]
		base = CAPABILITIES["event_feed" if rule[0][0] else "hud_style"]
		extras[variant_id] = replace(base, id=variant_id, title=variant_id.replace("_", " ").title(), settings=(),
			modes=(ModeEvidence("default", rule[0][2], base.exposures),))
	for group_id, base_id in (("labels_group", "player_labels"), ("runtime_master", "wallhack")):
		extras[group_id] = replace(CAPABILITIES[base_id], id=group_id, settings=())
	return extras


CAPABILITIES = MappingProxyType({**CAPABILITIES, **_extra_capabilities()})
_RISK_PATHS = tuple(dict.fromkeys((
	*_ENABLED_SETTINGS.values(), *_WALLHACK_SELECTION.values(), "visuals.health.mode", "visuals.skeleton.head_outline_enabled",
	"visuals.box.box_mode", "visuals.chams.mode", "glow.type", "triggerbot.version", "triggerbot.wh_compliant", "aimbot.visibility",
	"event_visuals.enabled", "event_visuals.show_c4_timer", "event_visuals.show_spectators", "event_visuals.show_hotkeys",
	"event_visuals.show_status_pills", "event_sounds.enabled", "anti_detection.read_only_mode",
	*(f"event_visuals.{group[0]}" for group in _EVENT_GROUPS.values()),
	*(f"event_visuals.ev_{name}_enabled" for group in _EVENT_GROUPS.values() for name in group[1]),
	*(f"event_sounds.{family}_enabled" for family in _GAME_SOUND_FAMILIES),
	*(f"fps_counter.{name}" for name in ("show_fps", "show_overlay_fps", "show_ping", "show_latency", "show_cpu", "show_ram", "show_time", "show_brand", "show_jitter")),
)))


@dataclass(frozen=True, slots=True)
class RiskAssessment:
	"""One immutable assessment reused by badges, help and the alternative menu."""
	capability_id: str
	title: str
	policy: str
	exposures: tuple[str, ...]
	mode: str
	mode_detail: str
	state: str
	limitations: str
	source_ids: tuple[str, ...]
	badge: str
	bucket: str
	policy_detail: str
	detection: str = DETECTION_UNKNOWN
	audit_date: str = AUDIT_DATE
	_platform_risks: tuple[PlatformRisk, ...] = ()
	selected_children: tuple[str, ...] = ()

	@property
	def platform_risks(self) -> tuple[PlatformRisk, ...]:
		return self._platform_risks

	@property
	def heading(self) -> str:
		return f"{self.platform_risks[0].level} risk" if self.platform_risks else ""

	@property
	def detail(self) -> str:
		if not self.platform_risks:
			return ""
		levels = " · ".join(f"{item.platform}: {item.level}" for item in self.platform_risks)
		confidence = " · ".join(f"{item.platform} {item.confidence}" for item in self.platform_risks)
		summary = self.mode_detail.split(". ", 1)[0].rstrip(".") + "."
		if self.badge == "SAFE":
			summary += " Not platform approval."
		if self.capability_id == "triggerbot" and self.mode == "vanilla_glow":
			summary = "Automated firing uses coupled Glow, which separately writes game memory."
		return "\n".join((levels, summary, f"Confidence: {confidence}", f"Estimate · checked {self.audit_date}"))

	@property
	def sources(self) -> tuple[PolicySource, ...]:
		return tuple(POLICY_SOURCES[key] for key in self.source_ids if key in POLICY_SOURCES)

	@property
	def plain_text(self) -> str:
		return "\n".join(part for part in (self.heading, self.detail) if part)

	@property
	def sources_html(self) -> str:
		keys = dict.fromkeys(key for item in self.platform_risks for key in item.source_ids if key in REVIEW_SOURCES)
		return " · ".join(f'<a href="{escape(REVIEW_SOURCES[key][1], quote=True)}" title="{escape(REVIEW_SOURCES[key][0], quote=True)}">{key}</a>' for key in keys)


ModeProvider = str | Callable[[], str] | None
ConfigProvider = object | Callable[[], object] | None


def _config_value(config: object | None, path: str, default: object = None) -> object:
	'''Read a dotted configuration path and return the default as soon as any level misses.'''
	for name in path.split("."):
		config = config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)
		if config is default:
			return default
	return config


def risk_signature(config: object | None) -> tuple:
	'''Only mechanism/selection fields invalidate UI assessments; rendering styles and load do not.'''
	return tuple(_config_value(config, path) for path in _RISK_PATHS)


def _live_mode(capability_id: str, config: object | None) -> str | None:
	'''Derive the mode from configuration with the same priority as the feature itself.'''
	if config is None:
		return None
	if capability_id == "triggerbot":
		if str(_config_value(config, "triggerbot.version", "feedback")) != "vanilla":
			return "feedback"
		if not _config_value(config, "triggerbot.wh_compliant", True) or not _config_value(config, "wallhack.enabled", True):
			return "vanilla_default"
		if _config_value(config, "glow.enabled", False):
			return "vanilla_glow"
		if _config_value(config, "visuals.chams.enabled", False):
			return "vanilla_chams_legacy" if _config_value(config, "visuals.chams.mode", "vpk") == "legacy" else "vanilla_chams_vpk"
		if _config_value(config, "visuals.skeleton.enabled", False) and _config_value(config, "visuals.skeleton.head_outline_enabled", False):
			return "vanilla_skeleton"
		return "vanilla_default"
	if capability_id == "wallhack":
		return "with_glow" if _config_value(config, "glow.enabled", False) else "overlay"
	if capability_id == "glow":
		return "visible" if int(_config_value(config, "glow.type", 2)) == 1 else "always"
	path = {"box": "visuals.box.box_mode", "chams": "visuals.chams.mode", "aimbot": "aimbot.visibility"}.get(capability_id)
	return str(_config_value(config, path, CAPABILITIES[capability_id].modes[0].name)) if path else None


def _selected_children(capability_id: str, config: object) -> tuple[str, ...] | None:
	'''List the selected child capabilities that feed an aggregate assessment.'''
	if capability_id == "wallhack":
		children = [key for key, path in _WALLHACK_SELECTION.items() if _config_value(config, path, False)]
		if _config_value(config, "visuals.health.mode", "off") != "off":
			children.append("health")
		if "skeleton" in children and _config_value(config, "visuals.skeleton.head_outline_enabled", False):
			children.append("head_outline")
		return tuple(children)
	if capability_id == "labels_group":
		return tuple(key for key, path in (("player_labels", "visuals.name.enabled"), ("distance", "visuals.distance.enabled"))
			if _config_value(config, path, False)) + (("health",) if _config_value(config, "visuals.health.mode", "off") != "off" else ())
	if capability_id == "event_feed":
		return tuple(key for key, (filter_key, names) in _EVENT_GROUPS.items() if _config_value(config, f"event_visuals.{filter_key}", False)
			and any(_config_value(config, f"event_visuals.ev_{name}_enabled", True) for name in names))
	if capability_id == "hud_suite":
		children = list(_selected_children("event_feed", config) or ())
		children += [key for key, attr in (("c4_timer", "show_c4_timer"), ("spectators", "show_spectators"))
			if _config_value(config, f"event_visuals.{attr}", False)]
		return tuple(children)
	if capability_id == "event_sounds":
		return ("sound_game",) if any(_config_value(config, f"event_sounds.{family}_enabled", False) for family in _GAME_SOUND_FAMILIES) else ()
	if capability_id == "telemetry":
		return ("memory_reader",) if any(_config_value(config, path, False) for path in _RISK_PATHS if path.startswith("fps_counter.show_")) else ()
	if capability_id == "runtime_master":
		return ("wallhack", "hud_suite", "event_sounds", "telemetry") + tuple(key for key, path in _ENABLED_SETTINGS.items()
			if key != "wallhack" and _config_value(config, path, False))
	return None


def risk_for(capability_id: str, config: object | None = None, *, mode: str | None = None) -> RiskAssessment:
	"""Resolve the approved potential grade, selected modes and dependency maxima without executing any feature."""
	capability_id = _CAPABILITY_ALIASES.get(str(capability_id), str(capability_id))
	capability = CAPABILITIES.get(capability_id)
	if capability is None:
		return RiskAssessment(capability_id, "Additional capability", "unassessed", (), "unavailable", "A new mechanism needs evidence review.", "",
			"Missing metadata is not a safety claim.", (), "MEDIUM", "medium", "", _platform_risks=(
				PlatformRisk("VAC", "Medium", "Additional mechanism awaiting review.", "Low", ("V2",)),
				PlatformRisk("FACEIT", "Medium", "Additional mechanism awaiting review.", "Low", ("F2",))))
	requested_mode = str(mode) if mode is not None else _live_mode(capability_id, config)
	if capability_id == "glow" and requested_mode in ("1", "2"):
		requested_mode = "visible" if requested_mode == "1" else "always"
	selected = next((entry for entry in capability.modes if entry.name == requested_mode), None) if requested_mode else capability.modes[0]
	mode_name = selected.name if selected else str(requested_mode)
	mode_detail = selected.detail if selected else "Configured mechanism; mode-specific evidence is unavailable."
	exposures = selected.exposures if selected else capability.exposures
	rule = RISK_RULES.get(capability_id, RISK_RULES["wallhack"])
	if capability_id in ("wallhack", "hud_suite", "event_feed", "event_sounds") and config is None:
		rule = RISK_RULES["event_combat"]
	platforms = tuple(PlatformRisk(platform, level.title(), reason, confidence, source_ids)
		for platform, (level, confidence, reason, source_ids) in zip(("VAC", "FACEIT"), rule) if level)
	children = _selected_children(capability_id, config) if config is not None else None
	if capability_id == "triggerbot" and mode_name == "vanilla_glow":
		children = ("glow",)
	if children is not None:
		assessments = tuple(risk_for(child, config) for child in children)
		combined = []
		for platform in ("VAC", "FACEIT"):
			candidates = [item for child in assessments for item in child.platform_risks if item.platform == platform]
			if capability_id == "triggerbot":
				candidates += [item for item in platforms if item.platform == platform]
			if candidates:
				combined.append(max(candidates, key=lambda item: _GRADE_ORDER[item.level.upper()]))
		platforms = tuple(combined)
		if capability_id != "triggerbot":
			mode_detail = "Selected mechanisms: " + ", ".join(CAPABILITIES[child].title for child in children) + "." if children else ""
			exposures = tuple(dict.fromkeys(item for child in assessments for item in child.exposures))
	badge = platforms[0].level.upper() if platforms else ""
	state = "Configured potential mechanism; current game/input activity is not inferred."
	if config is not None and capability_id in _ENABLED_SETTINGS:
		state += " Enabled flag is " + ("on." if _config_value(config, _ENABLED_SETTINGS[capability_id], False) else "off.")
	if config is not None and _config_value(config, "anti_detection.read_only_mode", False):
		state += " Read-only write guard is configured; cleanup and independent input paths remain separate."
	return RiskAssessment(capability_id, capability.title, capability.policy, exposures, mode_name, mode_detail, state, capability.limitations,
		capability.source_ids, badge, badge.lower() if badge else "neutral", _POLICY_COPY[capability.policy][2],
		_platform_risks=platforms, selected_children=children or ())


@dataclass(frozen=True, slots=True)
class RiskBinding:
	"""A UI-only live binding; configuration providers also follow whole-object reloads."""
	capability_id: str
	config: ConfigProvider = None
	mode: ModeProvider = None

	def assessment(self) -> RiskAssessment:
		'''Evaluate the providers and fall back to the unavailable mode on any error.'''
		try:
			config = self.config() if callable(self.config) else self.config
			mode = self.mode() if callable(self.mode) else self.mode
			return risk_for(self.capability_id, config, mode=mode)
		except Exception:
			return replace(risk_for(self.capability_id, mode="unavailable"), state="Live selection is unavailable; potential mechanism retained.")


def bind_risk(capability_id: str, config: ConfigProvider = None, *, mode: ModeProvider = None) -> RiskBinding:
	'''Bind a capability to live configuration and optional mode providers.'''
	return RiskBinding(capability_id, config, mode)


RiskInput = str | RiskAssessment | RiskBinding | None


def resolve_risk(value: RiskInput) -> RiskAssessment:
	'''Resolve a capability name, assessment, binding or legacy alias to one assessment.'''
	if isinstance(value, RiskBinding):
		return value.assessment()
	if isinstance(value, RiskAssessment):
		return value
	name = str(value or "safe")
	if name in CAPABILITIES or name in _CAPABILITY_ALIASES:
		return risk_for(name)
	policy = LEGACY_RISK_ALIASES.get(name, "unassessed")
	return RiskAssessment("legacy", "Legacy label", policy, (), "legacy", "", "", "", (), "", "neutral", _POLICY_COPY[policy][2])


def capability_for_setting(path: str) -> str:
	"""Scope pure appearance separately from functional controls, including dynamic event and sound rows."""
	if path in _SETTING_OVERRIDES:
		return _SETTING_OVERRIDES[path]
	if path.startswith("fps_counter.show_") or path == "fps_counter.brand_text":
		return "appearance"
	if path.startswith("event_visuals.ev_") and path.endswith("_enabled"):
		name = path.removeprefix("event_visuals.ev_").removesuffix("_enabled")
		return next((key for key, (_, names) in _EVENT_GROUPS.items() if name in names),
			"hud_style" if name in _APP_EVENT_NAMES or name in ("triggerbot", "bhop") else "unassessed")
	if path.startswith("event_visuals.ev_") and path.endswith(("_color", "_glyph", "_label")):
		return "hud_style"
	if path.startswith("event_sounds.") and path.endswith("_enabled"):
		family = path.removeprefix("event_sounds.").removesuffix("_enabled")
		return "sound_game" if family in _GAME_SOUND_FAMILIES else "sound_style"
	field = path.rsplit(".", 1)[-1]
	if ("color" in field or "colour" in field or "font" in field or field in ("hotkey", "hotkey_toggle", "thickness", "size", "gap", "opacity",
		"tri_count", "quality", "rim_strength", "style", "team_colored", "bar_width", "bar_offset")
		or ".radii." in path or ".aspects." in path or path.startswith("hud_positions.")
		or path.startswith("visuals.skeleton.head_outline_") and field != "head_outline_enabled"):
		return "appearance"
	matches = ((len(scope), capability.id) for capability in CAPABILITIES.values() for scope in capability.settings
		if path == scope or path.startswith(scope + ".") or scope.endswith("head_outline") and path.startswith(scope + "_"))
	return max(matches, default=(0, "unassessed"))[1]


def help_html(text: str, assessment: RiskAssessment) -> str:
	"""Show audited behavior before contextual help. Unsupported legacy assurances are omitted rather than allowed to contradict the audit."""
	parts = []
	context = (text or "").strip()
	if context:
		plain = unescape(sub(r"<[^>]*>", " ", context))
		if _UNSUPPORTED_COPY.search(plain):
			context = ""
		parts.append(context)
	if not any(parts) and assessment.capability_id in CAPABILITIES:
		description = assessment.mode_detail.strip()
		if description and not _UNSUPPORTED_COPY.search(description):
			parts.append(escape(description))
	return "<br><br>".join(part for part in parts if part)


__all__ = ["AUDIT_DATE", "DETECTION_UNKNOWN", "POLICY_SOURCES", "CAPABILITIES", "LEGACY_RISK_ALIASES", "PolicySource", "Capability", "ModeEvidence",
	"RiskAssessment", "RiskBinding", "RiskInput", "PlatformRisk", "COMMUNITY_SOURCES", "bind_risk", "risk_for", "resolve_risk",
	"capability_for_setting", "help_html", "risk_signature"]
