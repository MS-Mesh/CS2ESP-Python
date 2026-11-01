"""
Custom lower-tier display matrix v5 (2026-10-29); grades are estimates, not measured ban probabilities.
"""

from types import MappingProxyType

RISK_RULES = MappingProxyType({
	'wallhack': (
		(None, 'Derived', 'Aggregate selected Box, Skeleton/head outline, Glow, Chams, player name, health, distance and snapline mechanisms. Keep the combination when its parent master is off.', ('V1', 'V2', 'F1', 'F2', 'R1')),
		(None, 'Derived', 'Aggregate selected Box, Skeleton/head outline, Glow, Chams, player name, health, distance and snapline mechanisms. Keep the combination when its parent master is off.', ('V1', 'V2', 'F1', 'F2', 'R1')),
	),
	'box': (
		('SAFE', 'Low', 'External player reads for boxes; no target writes.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'External player reads; current detection of this implementation is unverified.', ('F1', 'F2', 'R1')),
	),
	'skeleton': (
		('LOW', 'Low', 'External bone reads and own-window wireframe drawing.', ('V1', 'V2', 'R1')),
		('LOW', 'Low', 'Bone reads do not establish a higher detection class than external Chams.', ('F1', 'F2', 'R1')),
	),
	'head_outline': (
		('LOW', 'Low', 'External bone-derived head geometry; rendering alone does not fire.', ('V1', 'V2', 'R1')),
		('LOW', 'Low', 'Bone-derived geometry; firing consumers are assessed separately.', ('F1', 'F2', 'R1')),
	),
	'glow': (
		('HIGH', 'Low', 'Repeated native glow-state writes, including cleanup; elevated precautionary estimate.', ('V1', 'V2')),
		('HIGH', 'Low', 'Repeated target-state mutation; elevated estimate, not confirmed detection.', ('F1', 'F2')),
	),
	'chams': (
		('LOW', 'Low', 'External poses and own-window mesh rendering; no target writes.', ('V1', 'V2', 'R1')),
		('LOW', 'Low', 'External poses; VPK mesh files/GPU load do not establish different detection odds.', ('F1', 'F2', 'R1')),
	),
	'player_labels': (
		('SAFE', 'Low', 'Player names come from external game-state reads.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'Player-information reads; no isolated detection evidence for this implementation.', ('F1', 'F2', 'R1')),
	),
	'health': (
		('SAFE', 'Low', 'Health values come from external game-state reads.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'Health-information reads; bar/percentage styling does not alter the mechanism.', ('F1', 'F2', 'R1')),
	),
	'distance': (
		('SAFE', 'Low', 'Distances use externally read player positions.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'Position reads; text or unit changes do not establish different detection odds.', ('F1', 'F2', 'R1')),
	),
	'snapline': (
		('SAFE', 'Low', 'Lines use externally read player positions.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'Position reads; opacity and thickness do not change the access mechanism.', ('F1', 'F2', 'R1')),
	),
	'radar': (
		('SAFE', 'Low', 'Enemy/team positions are externally read.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'Position-reading radar, not the permitted replacement radar image called Simple radar.', ('F1', 'F2', 'R1')),
	),
	'awareness': (
		('SAFE', 'Low', 'Threat direction uses external positions and eye angles.', ('V1', 'V2', 'R1')),
		('SAFE', 'Low', 'Game-state threat information; FOV filters do not prove a lower detection class.', ('F1', 'F2', 'R1')),
	),
	'crosshair': (
		('SAFE', 'Moderate', 'Static own-window marker; no gameplay reads, writes or generated input in this action.', ('V1', 'V2')),
		('SAFE', 'Low', 'Lowest standalone mechanism estimate; not official approval of the whole application.', ('F1',)),
	),
	'triggerbot': (
		('MEDIUM', 'Low', 'Game-derived automatic firing via SendInput; specific detection is unverified.', ('V1', 'V2', 'V3', 'R1')),
		('MEDIUM', 'Low', 'Automatic firing is prohibited; HID exists, but no per-implementation detection rate is published.', ('F1', 'F2', 'F3', 'R1')),
	),
	'aimbot': (
		('HIGH', 'Low', 'Automated target aiming writes view angles; elevated precautionary estimate.', ('V1', 'V2')),
		('HIGH', 'Low', 'View-angle mutation plus automated aiming; elevated estimate, not confirmed detection.', ('F1', 'F2', 'F3')),
	),
	'rapidfire': (
		('MEDIUM', 'Low', 'Synthetic repeated clicks; kick/cooldown/permanent-ban outcomes are distinct.', ('V1', 'V2', 'V3')),
		('MEDIUM', 'Low', 'Repeated-click macro; policy prohibition alone does not establish HIGH detection risk.', ('F1', 'F3')),
	),
	'autopistol': (
		('MEDIUM', 'Low', 'Synthetic repeated clicks at a pistol cadence; no target writes in the clicker.', ('V1', 'V2', 'V3')),
		('MEDIUM', 'Low', 'Same click mechanism as Rapid-fire; no isolated evidence supports a different grade.', ('F1', 'F3')),
	),
	'bhop': (
		('MEDIUM', 'Low', 'Ground-state reads drive synthetic jumps; documented automation kicks are not VAC bans.', ('V1', 'V2', 'V3')),
		('MEDIUM', 'Low', 'BHop macros are prohibited; current detection of this implementation is unverified.', ('F1', 'F2', 'F3')),
	),
	'recoil_control': (
		('MEDIUM', 'Low', 'Live recoil reads drive synthetic mouse compensation; no view-angle writes in this path.', ('V1', 'V2', 'V3')),
		('MEDIUM', 'Low', "Recoil automation is prohibited; HID documentation does not establish this implementation's detection rate.", ('F1', 'F2', 'F3')),
	),
	'recoil_script_logitech': (
		('MEDIUM', 'Low', 'Execution replays a fixed recoil macro; export alone does not run it.', ('V1', 'V2', 'V3')),
		('MEDIUM', 'Low', 'Advantageous recoil macros are prohibited; no isolated current detection evidence was found.', ('F1', 'F3')),
	),
	'recoil_script_ahk': (
		('MEDIUM', 'Low', "Execution replays a fixed recoil macro; an AHK file's existence is not an execution result.", ('V1', 'V2', 'V3', 'R2')),
		('MEDIUM', 'Low', 'A firsthand report describes macro blocking, not a demonstrated permanent ban.', ('F1', 'F3', 'R2')),
	),
	'script_generator': (
		(None, 'High', 'Only generates/copies/saves local text. No macro execution follows from these actions.', ()),
		(None, 'High', 'Only generates/copies/saves local text. No macro execution follows from these actions.', ()),
	),
	'event_feed': (
		(None, 'Derived', 'Aggregate selected game-state event streams; system/config/hotkey notifications alone have no badge. Combat, C4, observer and rear-threat events inherit their actual readers, not names of unrelated features.', ('V1', 'V2', 'F1', 'F2', 'R1')),
		(None, 'Derived', 'Aggregate selected game-state event streams; system/config/hotkey notifications alone have no badge. Combat, C4, observer and rear-threat events inherit their actual readers, not names of unrelated features.', ('V1', 'V2', 'F1', 'F2', 'R1')),
	),
	'c4_timer': (
		('SAFE', 'Low', 'Bomb timing and defuse state are externally read.', ('V1', 'V2')),
		('SAFE', 'Low', 'Game-state reads; no isolated detection evidence for the timer implementation.', ('F1', 'F2')),
	),
	'spectators': (
		('SAFE', 'Low', 'Observer targets/controllers are externally read.', ('V1', 'V2')),
		('SAFE', 'Low', 'Observer-state reads; displaying names is not evidence of a specific current detector.', ('F1', 'F2')),
	),
	'hud_suite': (
		(None, 'Derived', 'Aggregate only the configured game-event feed, C4 and spectator mechanisms plus relevant dependencies. Hotkey/status-only presentation remains blank; parent master off preserves the selected combination.', ('V1', 'V2', 'F1', 'F2')),
		(None, 'Derived', 'Aggregate only the configured game-event feed, C4 and spectator mechanisms plus relevant dependencies. Hotkey/status-only presentation remains blank; parent master off preserves the selected combination.', ('V1', 'V2', 'F1', 'F2')),
	),
	'event_sounds': (
		(None, 'Derived', 'Game-derived hit/kill/headshot/one-tap/miss/bomb/spectator cues inherit their source-reader estimate. System/toggle sounds, previews, file/volume/order controls have no badge.', ('V1', 'V2', 'F1', 'F2')),
		(None, 'Derived', 'Game-derived hit/kill/headshot/one-tap/miss/bomb/spectator cues inherit their source-reader estimate. System/toggle sounds, previews, file/volume/order controls have no badge.', ('V1', 'V2', 'F1', 'F2')),
	),
	'telemetry': (
		('LOW', 'Low', 'The current shared sampler reads game globals and scoreboard ping.', ('V1', 'V2')),
		('LOW', 'Low', 'Game reads remain in the bundled provider; architecture alone does not justify HIGH.', ('F2',)),
	),
	'diagnostics': (
		('LOW', 'Low', 'Live checks attach, scan and read with a write-capable handle; checks do not write.', ('V1', 'V2')),
		('LOW', 'Low', 'Live attachment/read exposure; access blocking and an account ban are different outcomes.', ('F2',)),
	),
	'offsets': (
		('LOW', 'Low', 'The live refresh phase attaches/scans/verifies; download/cache-only actions are separate.', ('V1', 'V2')),
		('LOW', 'Low', 'Live read/scan exposure; no per-implementation ban evidence was established.', ('F2',)),
	),
	'memory_reader': (
		('LOW', 'Low', 'Direct external reads; attachment also requests write-capable rights.', ('V1', 'V2')),
		('LOW', 'Low', 'Kernel protection may affect access; that is not proof of HIGH ban probability.', ('F2',)),
	),
	'capture_exclusion': (
		(None, 'High', "Documented capture-affinity API on this app's own window. This action alone is not target access and must not claim to hide the process from VAC/FACEIT.", ('M1',)),
		(None, 'High', "Documented capture-affinity API on this app's own window. This action alone is not target access and must not claim to hide the process from VAC/FACEIT.", ('M1',)),
	),
	'capture_reapply': (
		(None, 'High', 'Reapplies the same own-window capture affinity; frequency does not establish an anti-cheat protection or detection grade.', ('M1',)),
		(None, 'High', 'Reapplies the same own-window capture affinity; frequency does not establish an anti-cheat protection or detection grade.', ('M1',)),
	),
	'taskbar_visibility': (
		(None, 'High', 'Own-window taskbar/tool-window presentation only. It does not remove the process, readers or target handles.', ()),
		(None, 'High', 'Own-window taskbar/tool-window presentation only. It does not remove the process, readers or target handles.', ()),
	),
	'window_title': (
		(None, 'High', "Changes only this app's startup window title. No measured anti-cheat benefit or target access follows from the string itself.", ()),
		(None, 'High', "Changes only this app's startup window title. No measured anti-cheat benefit or target access follows from the string itself.", ()),
	),
	'read_jitter': (
		('LOW', 'Low', 'Inherits the existing reader estimate; a sleep interval is not protection.', ('V1', 'V2')),
		('LOW', 'Low', 'Cadence changes do not establish lower or higher detection odds.', ('F2',)),
	),
	'write_guard': (
		('SAFE', 'High', 'The scheduling guard adds no game access. It suppresses normal writers and synthetic input; one-time cleanup, external reads and existing handle rights remain separate.', ('V2', 'F2')),
		('SAFE', 'High', 'The scheduling guard adds no game access. It does not certify the selected gameplay features or lower their individual grades.', ('V2', 'F2')),
	),
	'module_spoof': (
		('MEDIUM', 'Low', 'Own-process DLL/loader manipulation; no verified protective effect.', ('V1', 'V2')),
		('MEDIUM', 'Low', 'Own-process loader changes; no specific detection evidence justifies HIGH or CRITICAL.', ('F2',)),
	),
	'module_unlink': (
		('MEDIUM', 'Low', 'Own-process load-order unlinking, dependent on module spoof.', ('V1', 'V2')),
		('MEDIUM', 'Low', 'Loader metadata changes; no specific current detection evidence was established.', ('F2',)),
	),
	'read_obfuscator': (
		('LOW', 'Low', 'Additional native attempts now cover this MemoryReader\'s typed and block reads; a separately wrapped Pymem path remains compatible.', ('V1', 'V2')),
		('LOW', 'Low', 'Same read dependency; added reads provide no verified risk reduction.', ('F2',)),
	),
	'window_class': (
		('LOW', 'Low', 'Own-window class identity changes; this does not hide process memory access or establish a protective effect.', ('V2',)),
		('LOW', 'Low', 'Own-window identity operation; no current evidence establishes a detector benefit or outcome for this implementation.', ('F2',)),
	),
	'appearance': (
		(None, 'High', 'Theme, fonts, UI scaling, icons and colors are presentation-only.', ()),
		(None, 'High', 'Theme, fonts, UI scaling, icons and colors are presentation-only.', ()),
	),
	'window_behavior': (
		(None, 'High', 'Focus, terminal visibility and menu startup are ordinary local window behavior.', ()),
		(None, 'High', 'Focus, terminal visibility and menu startup are ordinary local window behavior.', ()),
	),
	'persistence': (
		(None, 'High', 'Save/load/reset/autosave and viewing resolved configuration are local app operations. Loaded gameplay choices retain their own assessments.', ()),
		(None, 'High', 'Save/load/reset/autosave and viewing resolved configuration are local app operations. Loaded gameplay choices retain their own assessments.', ()),
	),
	'keybinds': (
		(None, 'High', "Capturing or clearing a binding is configuration editing, not firing or aiming. Any displayed reference to the bound gameplay feature reuses that feature's assessment rather than inventing a new grade.", ()),
		(None, 'High', "Capturing or clearing a binding is configuration editing, not firing or aiming. Any displayed reference to the bound gameplay feature reuses that feature's assessment rather than inventing a new grade.", ()),
	),
	'renderer': (
		(None, 'High', 'Adapter preference, GPU/CPU rasterization, profile, FPS cap and V-Sync change rendering resources/cadence, not gameplay access.', ()),
		(None, 'High', 'Adapter preference, GPU/CPU rasterization, profile, FPS cap and V-Sync change rendering resources/cadence, not gameplay access.', ()),
	),
	'hud_style': (
		(None, 'High', 'Widget size/position, labels, glyphs, colors, bloom, duration and layout editing affect only presentation.', ()),
		(None, 'High', 'Widget size/position, labels, glyphs, colors, bloom, duration and layout editing affect only presentation.', ()),
	),
	'sound_style': (
		(None, 'High', 'Sound files, volume, previews, order and app-only notifications do not read new gameplay state.', ()),
		(None, 'High', 'Sound files, volume, previews, order and app-only notifications do not read new gameplay state.', ()),
	),
	'hotkeys_hud': (
		(None, 'High', 'Displays locally known bind/activation state without obtaining enemy information or generating input.', ()),
		(None, 'High', 'Displays locally known bind/activation state without obtaining enemy information or generating input.', ()),
	),
	'status_hud': (
		(None, 'High', "Displays local feature state. A feature-state icon does not execute or inherit that feature's gameplay action.", ()),
		(None, 'High', "Displays local feature state. A feature-state icon does not execute or inherit that feature's gameplay action.", ()),
	),
	'event_combat': (
		(None, 'Low', 'Current producers infer player health/shots and local combat context through shared external game reads.', ('V1', 'V2', 'F1', 'F2', 'R1')),
		(None, 'Low', 'Current producers infer player health/shots and local combat context through shared external game reads.', ('V1', 'V2', 'F1', 'F2', 'R1')),
	),
	'event_bomb': (
		(None, 'Low', 'Planted-bomb data and state transitions use the C4 reader.', ('V1', 'V2', 'F1', 'F2')),
		(None, 'Low', 'Planted-bomb data and state transitions use the C4 reader.', ('V1', 'V2', 'F1', 'F2')),
	),
	'event_observer_threat': (
		(None, 'Low', 'Observer or enemy direction data supplies gameplay information.', ('V1', 'V2', 'F1', 'F2')),
		(None, 'Low', 'Observer or enemy direction data supplies gameplay information.', ('V1', 'V2', 'F1', 'F2')),
	),
	'event_app': (
		(None, 'Low', 'Local completion/configuration notices; the offset-update action is assessed separately.', ()),
		(None, 'Low', 'Local completion/configuration notices; the offset-update action is assessed separately.', ()),
	),
	'event_modules': (
		(None, 'Low', 'Only enum/filter/render references currently exist; no runtime producers were found. The notification control itself does not start the named automation.', ()),
		(None, 'Low', 'Only enum/filter/render references currently exist; no runtime producers were found. The notification control itself does not start the named automation.', ()),
	),
	'sound_game': (
		(None, 'Low', 'Game-event source dependency is assessed, not the WAV playback API.', ('V1', 'V2', 'F1', 'F2')),
		(None, 'Low', 'Game-event source dependency is assessed, not the WAV playback API.', ('V1', 'V2', 'F1', 'F2')),
	),
	'sound_app_preview': (
		(None, 'Low', 'Local UI sounds or audio styling; no gameplay reader dependency belongs to playback alone.', ()),
		(None, 'Low', 'Local UI sounds or audio styling; no gameplay reader dependency belongs to playback alone.', ()),
	),
	'telemetry_local_fields': (
		(None, 'Low', 'Presentation of OS/app metrics is not a game-memory action; the current shared provider still performs game reads independently.', ()),
		(None, 'Low', 'Presentation of OS/app metrics is not a game-memory action; the current shared provider still performs game reads independently.', ()),
	),
	'telemetry_game_fields': (
		('LOW', 'Low', 'The existing FPS fallback and ping provider read game globals/controller state; choosing PresentMon display data does not stop those background reads.', ('V1', 'V2', 'F2')),
		('LOW', 'Low', 'The existing FPS fallback and ping provider read game globals/controller state; choosing PresentMon display data does not stop those background reads.', ('V1', 'V2', 'F2')),
	),
	'telemetry_etw_action': (
		(None, 'Low', "Present-to-display observation itself uses presentation telemetry, not aim/input-to-photon measurement; the shared sampler's game reads are assessed on its parent.", ()),
		(None, 'Low', "Present-to-display observation itself uses presentation telemetry, not aim/input-to-photon measurement; the shared sampler's game reads are assessed on its parent.", ()),
	),
	'offsets_offline_phase': (
		(None, 'Low', "Downloading public JSON and writing this app's cache alone does not access the game. The current button attempts a separate live phase.", ()),
		(None, 'Low', "Downloading public JSON and writing this app's cache alone does not access the game. The current button attempts a separate live phase.", ()),
	),
	'script_export_only': (
		(None, 'Low', 'Exporting or editing text does not execute the generated macro.', ()),
		(None, 'Low', 'Exporting or editing text does not execute the generated macro.', ()),
	),
})

REVIEW_SOURCES = MappingProxyType({
	'V1': ('Valve Anti-Cheat (VAC) System', 'https://help.steampowered.com/en/faqs/view/571A-97DA-70E9-FF74'),
	'V2': ("I've been VAC banned", 'https://help.steampowered.com/en/faqs/view/647C-5CC1-7EA9-3C29'),
	'V3': ('Valve: Side-stepping Skill / Release Notes for 2024-08-19', 'https://store.steampowered.com/news/posts/?appids=730&enddate=1727913040&feed=steam_community_announcements'),
	'F1': ('FACEIT: What is deemed to be a cheat?', 'https://support.faceit.com/hc/en-us/articles/360015788779-What-is-deemed-to-be-a-cheat'),
	'F2': ('FACEIT Anti-cheat architecture', 'https://support.faceit.com/hc/en-us/articles/9394666828188-What-is-FACEIT-Anti-cheat-and-how-does-it-work'),
	'F3': ('FACEIT Human Input Detection FAQ', 'https://support.faceit.com/hc/en-us/articles/28929483475612-Human-Input-Detection-FAQ'),
	'R1': ('External ESP issue #402: Recent cooldown bans', 'https://github.com/IMXNOOBX/cs2-external-esp/issues/402'),
	'R2': ('Macro for CS2', 'https://www.reddit.com/r/csgo/comments/1bmq33w/macro_for_cs2/'),
	'M1': ('Microsoft SetWindowDisplayAffinity', 'https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity'),
})
