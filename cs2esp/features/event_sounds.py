"""
Per-event notification sounds: shipped WAV collections when present, synthesized cues otherwise. Playback runs on a Win32 worker thread so a notification
never blocks the render loop.
"""

import ctypes
import math
import struct
from pathlib import Path
from collections.abc import Callable
from queue import Empty, Queue
from threading import Event, Thread
from time import perf_counter

from numpy import clip, frombuffer

from cs2esp.features.event_system import EventSystem, EventNotification, EventType


_SAMPLE_RATE = 22050
_winmm = ctypes.windll.winmm

# Shipped sample collection; missing or non-PCM files fall back to the synthesized cues below
_SOUNDS_DIR = Path(__file__).resolve().parents[1] / "sounds"
_SOUND_FILES: dict[str, str] = {
	"hit": "neverlose.wav",
	"headshot_hit": "stapler.wav",
	"kill": "sonic.wav",
	"one_tap": "plasmablaster.wav",
	"headshot_kill": "mcbow.wav",
	"miss": "bubble.wav",
	"bomb": "Beep1.wav",
	"spectator": "bell.wav",
	"system": "reminder.wav",
	"toggle": "mcbow.wav",
}

# Families without their own synthesized voice reuse one of the originals
_SYNTH_FALLBACK: dict[str, str] = {"headshot_kill": "headshot"}
_FILE_FALLBACK: dict[str, str] = {}


def _envelope(i: int, count: int, attack: float, release: float) -> float:
	'''Raised-cosine attack/release with a gentle exponential body; no clicks, no buzz.'''
	position = i / max(1, count)
	attack_n = max(1.0, attack * count)
	release_n = max(1.0, release * count)
	envelope = 1.0
	if i < attack_n:
		envelope *= 0.5 - 0.5 * math.cos(math.pi * (i / attack_n))
	remaining = count - i
	if remaining < release_n:
		envelope *= 0.5 - 0.5 * math.cos(math.pi * (remaining / release_n))
	# Gentle exponential body decay on top of the envelope
	envelope *= math.exp(-2.2 * position)
	return envelope


def _pad(partials: list[tuple[float, float]], seconds: float, volume: float = 0.30, attack: float = 0.06, release: float = 0.35) -> bytes:
	'''Render a soft tone from (frequency_hz, relative_amplitude) sine partials with a bell-like decay.'''
	count = int(_SAMPLE_RATE * seconds)
	frames = bytearray()
	amplitude_sum = sum(amplitude for frequency, amplitude in partials) or 1.0
	for sample_index in range(count):
		sample_time = sample_index / _SAMPLE_RATE
		sample = 0.0
		for frequency, amplitude in partials:
			sample += math.sin(2.0 * math.pi * frequency * sample_time) * amplitude
		sample = (sample / amplitude_sum) * _envelope(sample_index, count, attack, release) * volume
		frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
	return bytes(frames)


def _chime(root: float, seconds: float = 0.85, volume: float = 0.30) -> bytes:
	'''Bell-ish root + fifth + octave chord; the softest voice.'''
	return _pad([(root, 1.0), (root * 1.5, 0.45), (root * 2.0, 0.28)], seconds, volume=volume, attack=0.045, release=0.45)


def _thump(root: float, seconds: float = 0.55, volume: float = 0.30) -> bytes:
	'''Low body with a soft sine transient; never noise.'''
	count = int(_SAMPLE_RATE * seconds)
	frames = bytearray()
	for sample_index in range(count):
		sample_time = sample_index / _SAMPLE_RATE
		envelope = _envelope(sample_index, count, 0.03, 0.5)
		body = math.sin(2.0 * math.pi * root * sample_time)
		# Second harmonic far below the fundamental: weight, not bite
		body += 0.30 * math.sin(2.0 * math.pi * root * 2.0 * sample_time)
		body += 0.12 * math.sin(2.0 * math.pi * (root * 1.5) * sample_time)
		frames += struct.pack("<h", int(max(-1.0, min(1.0, body / 1.42 * envelope * volume)) * 32767))
	return bytes(frames)


def _reverb(pcm: bytes, seconds: float = 0.30, decay: float = 0.34, mix: float = 0.30) -> bytes:
	'''Cheap tail: decaying delayed copies add room and remove the abrupt stop that otherwise reads as a click.'''
	taps = int(_SAMPLE_RATE * seconds)
	source_samples = list(struct.unpack("<%dh" % (len(pcm) // 2), pcm))
	output_samples = [0] * (len(source_samples) + taps)
	for sample_index, sample in enumerate(source_samples):
		output_samples[sample_index] += sample
	for tap_index, tap_ms in enumerate((18.0, 31.0, 47.0), start=1):
		offset = int(_SAMPLE_RATE * tap_ms / 1000.0)
		gain = (decay**tap_index) * mix
		for sample_index, sample in enumerate(source_samples):
			output_samples[sample_index + offset] += int(sample * gain)
	# Fade the tail so the buffer never clips off mid-sample
	tail = min(taps, len(output_samples) - 1)
	for tail_index in range(tail):
		sample_index = len(output_samples) - tail + tail_index
		output_samples[sample_index] = int(output_samples[sample_index] * (1.0 - tail_index / max(1, tail)))
	return struct.pack("<%dh" % len(output_samples), *[max(-32768, min(32767, sample)) for sample in output_samples])


def _wav(pcm: bytes) -> bytes:
	'''Wrap raw 16-bit mono PCM in a minimal RIFF/WAVE header.'''
	byte_rate = _SAMPLE_RATE * 2
	return (
		b"RIFF"
		+ struct.pack("<I", 36 + len(pcm))
		+ b"WAVE"
		+ b"fmt "
		+ struct.pack("<IHHIIHH", 16, 1, 1, _SAMPLE_RATE, byte_rate, 2, 16)
		+ b"data"
		+ struct.pack("<I", len(pcm))
		+ pcm
	)


# Distinct, gentle voice per event family: app-style chimes and soft thumps
_SOUND_PCM: dict[str, bytes] = {
	"hit": _chime(880.0, 0.55, volume=0.20),
	"headshot_hit": _chime(1174.66, 0.35, volume=0.22),
	"kill": _reverb(_thump(196.0, 0.60, volume=0.30)),
	"one_tap": _reverb(_thump(261.63, 0.45, volume=0.30)),
	"headshot": _reverb(_chime(1046.5, 0.90, volume=0.30)),
	"miss": _pad([(330.0, 1.0), (440.0, 0.4)], 0.40, volume=0.16, attack=0.10, release=0.55),
	"bomb": _reverb(_pad([(392.0, 1.0), (523.25, 0.5), (659.25, 0.3)], 1.05, volume=0.26, attack=0.05, release=0.5)),
	"spectator": _chime(659.25, 0.75, volume=0.22),
	"system": _chime(587.33, 0.60, volume=0.20),
	"toggle": _pad([(784.0, 1.0), (1176.0, 0.35)], 0.30, volume=0.16, attack=0.05, release=0.45),
}

# Playable WAVs built once; the bytes stay referenced for the whole call
_SOUND_RECIPES: dict[str, bytes] = {k: _wav(v) for k, v in _SOUND_PCM.items()}


def _load_wav(name: str) -> bytes | None:
	'''Read a PCM WAV from the sounds folder; None when missing/invalid.'''
	if not name:
		return None
	try:
		path = _SOUNDS_DIR / name
		if path.is_file():
			data = path.read_bytes()
			# PlaySound accepts only PCM RIFF/WAVE (fmt tag 1); float WAVs fall back to the synthesized cue
			if len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE" and data[20:22] == b"\x01\x00":
				return data
			print(f"[sounds] {name}: not PCM WAV, using synthesized fallback")
	except Exception as e:
		print(f"[sounds] failed to load {name}: {e!r}")
	return None


def _scale_pcm16(data: bytes, gain: float) -> bytes:
	'''Scale 16-bit PCM samples in a RIFF/WAVE buffer (PlaySound has no volume).'''
	gain = max(0.0, min(1.0, float(gain)))
	if gain >= 0.999:
		return data
	index = data.find(b"data")
	if index < 0 or index + 8 > len(data):
		return data
	size = struct.unpack_from("<I", data, index + 4)[0]
	start = index + 8
	end = min(len(data), start + size)
	count = (end - start) // 2
	if count <= 0:
		return data
	samples = frombuffer(data, dtype="<i2", count=count, offset=start).astype("float32")
	scaled = clip(samples * gain, -32768, 32767).astype("<i2").tobytes()
	return data[:start] + scaled + data[end:]


def _wav_duration(data: bytes) -> float:
	'''Length of a RIFF/WAVE PCM buffer in seconds; 0.0 if unparseable.'''
	try:
		if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
			return 0.0
		format_chunk = data.find(b"fmt ")
		chunk = data.find(b"data")
		if format_chunk < 0 or chunk < 0:
			return 0.0
		byte_rate = struct.unpack_from("<I", data, format_chunk + 16)[0]
		size = struct.unpack_from("<I", data, chunk + 4)[0]
		size = min(size, len(data) - (chunk + 8))
		return size / byte_rate if byte_rate > 0 else 0.0
	except (struct.error, IndexError):
		return 0.0


def available_sound_files() -> list[str]:
	'''PCM WAV filenames in cs2esp/sounds/, for the per-event dropdowns.'''
	names: list[str] = []
	try:
		for path in sorted(_SOUNDS_DIR.glob("*.wav")):
			try:
				with path.open("rb") as handle:
					head = handle.read(44)
				if len(head) >= 22 and head[:4] == b"RIFF" and head[8:12] == b"WAVE" and head[20:22] == b"\x01\x00":
					names.append(path.name)
			except OSError:
				continue
	except OSError:
		pass
	return names


_SOUND_BY_TYPE: dict[str, str] = {
	"grenade_throw": "bomb", "headshot": "headshot_kill", "miss": "miss", "spectator_new": "spectator", "bhop": "toggle",
	"offsets_updated": "system", "config_loaded": "system", "hotkey_toggled": "system",
}


def _sound_for(event_type: EventType, data: dict | None = None) -> str:
	'''Map an event type and its payload to a sound family.'''
	data = data or {}
	type_name = getattr(event_type, "value", "")
	if type_name in ("knife", "zeus"):
		return "kill" if data.get("outcome") == "kill" else "hit"
	if type_name in ("hit", "triggerbot"):
		return "headshot_hit" if data.get("headshot") else "hit"
	if type_name in ("kill", "headshot"):
		if data.get("one_tap"):
			return "one_tap"
		return "headshot_kill" if type_name == "headshot" or data.get("headshot") else "kill"
	if type_name.startswith("bomb"):
		return "bomb"
	return _SOUND_BY_TYPE.get(type_name, "hit")


class _WavePlayer:
	"""Serialised waveOut playback on a daemon thread."""
	def __init__(self) -> None:
		self._queue: Queue[bytes | Callable[[], bytes | None]] = Queue(maxsize=8)
		self._stop = Event()
		self._playing = None
		self._thread = Thread(target=self._run, name="cs2esp.sound", daemon=True)
		self._thread.start()

	def play(self, pcm: bytes) -> None:
		try:
			self._queue.put_nowait(pcm)
		except Exception:
			pass  # queue full: drop audio rather than stall the render loop

	def play_lazy(self, load_pcm: Callable[[], bytes | None]) -> None:
		'''File reads and volume conversion belong to the audio worker, not EventSystem.emit's render-thread listener.'''
		try:
			self._queue.put_nowait(load_pcm)
		except Exception:
			pass

	def stop(self) -> None:
		self._stop.set()

	def stop_current(self) -> None:
		'''Cut the currently playing preview immediately.'''
		try:
			_winmm.PlaySoundW(None, None, 0)
		except Exception:
			pass

	def _run(self) -> None:
		while not self._stop.is_set():
			try:
				pcm = self._queue.get(timeout=0.2)
			except Empty:
				continue
			try:
				if callable(pcm):
					pcm = pcm()
				if not pcm:
					continue
				buf = ctypes.create_string_buffer(pcm, len(pcm))
				# SND_MEMORY | SND_NODEFAULT | SND_ASYNC
				_winmm.PlaySoundW(buf, None, 0x0004 | 0x0002 | 0x0001)
				self._playing = buf  # must outlive the async playback
			except Exception:
				pass


class EventSounds:
	"""Subscribes to the event bus and plays a sound per notification."""
	def __init__(self, config) -> None:
		self.config = config
		self._player = _WavePlayer()
		self._cache: dict[str, bytes] = {}
		self._last_played: dict[str, float] = {}
		self._attached = False
		self._clock = perf_counter

	def _settings(self):
		return getattr(self.config, "event_sounds", None)

	def _enabled(self, family: str) -> bool:
		sounds_config = self._settings()
		if sounds_config is None or not bool(getattr(sounds_config, "enabled", False)):
			return False
		return bool(getattr(sounds_config, f"{family}_enabled", True))

	def _pcm_for(self, family: str, *, ignore_mute: bool = False) -> bytes | None:
		'''Resolve the configured file + volume into playable PCM.'''
		sounds_config = self._settings()
		raw = getattr(sounds_config, f"{family}_file", None) if sounds_config is not None else None
		if raw is not None:
			file_name = str(raw).strip()
			if not ignore_mute and file_name.lower() in ("", "none", "muted"):
				return None
		else:
			file_name = _SOUND_FILES.get(family, "")
		if not file_name:
			return None

		gain = 1.0
		if sounds_config is not None:
			try:
				gain = float(getattr(sounds_config, "volume", 1.0)) * float(getattr(sounds_config, f"{family}_volume", 1.0))
			except (TypeError, ValueError):
				gain = 1.0
		gain = round(max(0.0, min(1.0, gain)), 2)
		if gain <= 0.001:
			return None

		cache_key = f"{family}|{file_name}|{gain:.2f}"
		pcm = self._cache.get(cache_key)
		if pcm is not None:
			return pcm

		pcm = _load_wav(file_name)
		if pcm is None:
			fallback = _FILE_FALLBACK.get(family) or _SOUND_FILES.get(family, "")
			if fallback and fallback != file_name:
				pcm = _load_wav(fallback)
		if pcm is None:
			synth_family = _SYNTH_FALLBACK.get(family, family)
			pcm = _SOUND_RECIPES.get(synth_family) or _SOUND_RECIPES.get(family) or _SOUND_RECIPES["hit"]
			if family in _SOUND_FILES:
				print(f"[sounds] {family}: {file_name or 'no file'}, using synthesized fallback")
		pcm = _scale_pcm16(pcm, gain)
		self._cache[cache_key] = pcm
		return pcm

	def on_event(self, notification: EventNotification) -> None:
		if (notification.data or {}).get("silent"):
			return
		family = _sound_for(notification.event_type, notification.data)
		if not self._enabled(family):
			return
		# Debounce: a triggerbot burst must not machine-gun the cue
		try:
			now = self._clock()
			if now - self._last_played.get(family, 0.0) < 0.09:
				return
			self._last_played[family] = now
		except Exception:
			pass
		self._player.play_lazy(lambda: self._pcm_for(family))

	def preview(self, family: str) -> float:
		'''Play the configured family sample; return its duration in seconds (0.0 when nothing plays).'''
		pcm = self._pcm_for(family, ignore_mute=True)
		if pcm is None:
			return 0.0
		self._player.play(pcm)
		return _wav_duration(pcm)

	def stop_preview(self) -> None:
		'''Stop whatever preview is currently playing.'''
		self._player.stop_current()

	def attach(self) -> None:
		'''Hook every event type on the bus (idempotent per process).'''
		if self._attached:
			return
		self._attached = True
		bus = EventSystem.get()
		for event_type in EventType:
			try:
				bus.subscribe(event_type, self.on_event)
			except Exception:
				pass

	def stop(self) -> None:
		self._player.stop()
