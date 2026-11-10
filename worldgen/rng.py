"""Small deterministic helpers independent of Python's hash randomisation."""

from __future__ import annotations

import hashlib

_MASK64 = (1 << 64) - 1
_MASK32 = (1 << 32) - 1


def seed_for(*parts: object) -> int:
    text = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(text, digest_size=8).digest(), "little")


def _mix64(value: int) -> int:
    value &= _MASK64
    value ^= value >> 30
    value = (value * 0xBF58476D1CE4E5B9) & _MASK64
    value ^= value >> 27
    value = (value * 0x94D049BB133111EB) & _MASK64
    value ^= value >> 31
    return value


class ValueNoise:
    """Deterministic 2D value noise with fBm support."""

    def __init__(self, seed: int) -> None:
        self.seed = int(seed) & _MASK64

    def _hash01(self, ix: int, iy: int) -> float:
        h = (ix * 0x9E3779B1) & _MASK32
        h ^= (iy * 0x85EBCA77) & _MASK32
        h ^= self.seed & _MASK32
        return (_mix64(h) & 0xFFFFFF) / 0xFFFFFF

    def value(self, x: float, y: float) -> float:
        ix = int(x // 1)
        iy = int(y // 1)
        fx = x - ix
        fy = y - iy
        u = fx * fx * (3.0 - 2.0 * fx)
        v = fy * fy * (3.0 - 2.0 * fy)
        a = self._hash01(ix, iy)
        b = self._hash01(ix + 1, iy)
        c = self._hash01(ix, iy + 1)
        d = self._hash01(ix + 1, iy + 1)
        top = a + (b - a) * u
        bottom = c + (d - c) * u
        return top + (bottom - top) * v

    def fbm(
        self,
        x: float,
        y: float,
        octaves: int = 4,
        lacunarity: float = 2.0,
        gain: float = 0.5,
    ) -> float:
        total = 0.0
        amplitude = 1.0
        frequency = 1.0
        norm = 0.0
        for _ in range(max(1, octaves)):
            total += self.value(x * frequency, y * frequency) * amplitude
            norm += amplitude
            amplitude *= gain
            frequency *= lacunarity
        return total / max(norm, 1e-9)
