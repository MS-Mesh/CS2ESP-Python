"""
Optional noise-read wrappers for Pymem and MemoryReader's native dispatch; extra attempts leave the real read's arguments, result and failure behavior unchanged.
"""

from ctypes import byref, c_size_t, create_string_buffer
from dataclasses import dataclass, field
from random import Random
from threading import local
from typing import Any, Callable


# Configured userspace range; failed noise reads never affect the real read
_LOW = 0x0000_0001_0000_0000
_HIGH = 0x0000_7FFF_0000_0000


def _read_options(bogus_per_real: int, size_range: tuple[int, int]) -> tuple[int, tuple[int, int]]:
	'''Normalize the attempt count and validate scratch-buffer bounds.'''
	lower_size, upper_size = (int(size) for size in size_range)
	if lower_size < 1 or upper_size < lower_size:
		raise ValueError('Read sizes must satisfy 1 <= lower <= upper')
	return max(0, int(bogus_per_real)), (lower_size, upper_size)


@dataclass
class ReadObfuscator:
	"""Wrap an ``(address, size)`` reader and emit noise before each read."""
	reader: Callable[[int, int], bytes]
	bogus_per_real: int = 4
	size_range: tuple[int, int] = (16, 256)
	rng_seed: int | None = None
	_rng: Random = field(init=False, repr=False)

	def __post_init__(self) -> None:
		self.bogus_per_real, self.size_range = _read_options(self.bogus_per_real, self.size_range)
		self._rng = Random(self.rng_seed)

	def read(self, address: int, size: int) -> bytes:
		'''Read through the wrapped callable after emitting noise reads.'''
		if self.bogus_per_real > 0:
			generator = self._rng
			lower_size, upper_size = self.size_range
			for _ in range(self.bogus_per_real):
				noise_address = generator.randint(_LOW, _HIGH)
				noise_size = generator.randint(lower_size, upper_size)
				try:
					self.reader(noise_address, noise_size)
				except Exception:
					pass
		return self.reader(address, size)


@dataclass
class NativeReadObfuscator:
	"""Wrap a five-argument native read with independent per-thread noise buffers and RNGs."""
	reader: Callable[[Any, int, Any, int, Any], Any]
	bogus_per_real: int = 4
	size_range: tuple[int, int] = (16, 256)
	rng_seed: int | None = None
	_tls: local = field(init=False, repr=False)

	def __post_init__(self) -> None:
		self.bogus_per_real, self.size_range = _read_options(self.bogus_per_real, self.size_range)
		self._tls = local()

	def read(self, handle: Any, address: int, buffer: Any, size: int, bytes_read: Any) -> Any:
		'''Send noise through the original callable, then forward the real call without intercepting it recursively.'''
		if self.bogus_per_real > 0:
			try:
				state = getattr(self._tls, 'scratch', None)
				if state is None:
					state = self._tls.scratch = (Random(self.rng_seed), create_string_buffer(self.size_range[1]), byref(c_size_t()))
				generator, noise_buffer, noise_count = state
				lower_size, upper_size = self.size_range
				for _ in range(self.bogus_per_real):
					noise_address = generator.randint(_LOW, _HIGH)
					noise_size = generator.randint(lower_size, upper_size)
					try:
						self.reader(handle, noise_address, noise_buffer, noise_size, noise_count)
					except Exception:
						pass
			except Exception:
				pass
		return self.reader(handle, address, buffer, size, bytes_read)


def install_global(target: Any, *, bogus_per_real: int = 4, size_range: tuple[int, int] = (16, 256)) -> ReadObfuscator | NativeReadObfuscator:
	'''Install on MemoryReader's native dispatch or the legacy Pymem read_bytes API without stacking wrappers.'''
	bogus_per_real, size_range = _read_options(bogus_per_real, size_range)
	native_dispatch = callable(getattr(target, '_native_read', None))
	attribute = '_native_read' if native_dispatch else 'read_bytes'
	original = getattr(target, attribute, None)
	if not callable(original):
		raise TypeError('Target must expose a native read dispatch or read_bytes callable')
	wrapper_type = NativeReadObfuscator if native_dispatch else ReadObfuscator
	existing = getattr(original, '__self__', None)
	if isinstance(existing, wrapper_type):
		if existing.bogus_per_real == bogus_per_real and existing.size_range == size_range:
			return existing
		original = existing.reader
	obfuscator = wrapper_type(reader=original, bogus_per_real=bogus_per_real, size_range=size_range)
	setattr(target, attribute, obfuscator.read)
	return obfuscator


def uninstall_global(target: Any) -> bool:
	'''Restore an installed callable without changing a separately replaced dispatch or any process handle.'''
	for attribute, wrapper_type in (('_native_read', NativeReadObfuscator), ('read_bytes', ReadObfuscator)):
		existing = getattr(getattr(target, attribute, None), '__self__', None)
		if isinstance(existing, wrapper_type):
			setattr(target, attribute, existing.reader)
			return True
	return False


__all__ = ["ReadObfuscator", "NativeReadObfuscator", "install_global"]
