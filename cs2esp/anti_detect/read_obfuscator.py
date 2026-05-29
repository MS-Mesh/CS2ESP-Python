"""Interleave configurable noise reads with real cross-process reads."""

from dataclasses import dataclass, field
from random import Random
from typing import Any, Callable


# Userspace address range for bogus reads - high enough to skip null/guard pages, low enough to usually land in committed regions (RPM on uncommitted addresses fails fast, which is fine).
_LOW = 0x0000_0001_0000_0000
_HIGH = 0x0000_7FFF_0000_0000


@dataclass
class ReadObfuscator:
    """Wrap an ``(address, size)`` reader and emit noise before each read."""

    reader: Callable[[int, int], bytes]
    bogus_per_real: int = 4
    size_range: tuple[int, int] = (16, 256)
    rng_seed: int | None = None
    _rng: Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = Random(self.rng_seed)

    def read(self, address: int, size: int) -> bytes:
        """Read through the wrapped callable after emitting noise reads."""
        if self.bogus_per_real > 0:
            rng = self._rng
            lower_size, upper_size = self.size_range
            for _ in range(self.bogus_per_real):
                noise_address = rng.randint(_LOW, _HIGH)
                noise_size = rng.randint(lower_size, upper_size)
                try:
                    self.reader(noise_address, noise_size)
                except Exception:
                    pass
        return self.reader(address, size)


def install_global(
    pymem_instance: Any,
    *,
    bogus_per_real: int = 4,
    size_range: tuple[int, int] = (16, 256),
) -> ReadObfuscator:
    """Replace an object's ``read_bytes`` method with an obfuscated reader."""
    original = pymem_instance.read_bytes
    obf = ReadObfuscator(
        reader=original,
        bogus_per_real=bogus_per_real,
        size_range=size_range,
    )
    pymem_instance.read_bytes = obf.read
    return obf


__all__ = [
    "ReadObfuscator",
    "install_global",
]
