"""
Pure-numpy audio utilities to replace audioop (removed in Python 3.13+).

Implements:
  ulaw2lin(data) → PCM-16 bytes
  lin2ulaw(data) → µ-law bytes
  resample(data, from_rate, to_rate) → PCM-16 bytes
"""
from __future__ import annotations
import numpy as np

_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635


def ulaw2lin(data: bytes) -> bytes:
    """µ-law bytes → signed 16-bit PCM bytes."""
    u = np.frombuffer(data, dtype=np.uint8).astype(np.int32)
    u = ~u
    sign = (u & 0x80)
    exp  = (u >> 4) & 0x07
    mant = u & 0x0F
    val  = ((mant << 1) + 33) << exp
    val  = np.where(sign != 0, -val, val)
    return val.astype(np.int16).tobytes()


def lin2ulaw(data: bytes) -> bytes:
    """Signed 16-bit PCM bytes → µ-law bytes."""
    pcm = np.frombuffer(data, dtype=np.int16).astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0x00)
    pcm  = np.abs(pcm)
    pcm  = np.clip(pcm, 0, _ULAW_CLIP)
    pcm += _ULAW_BIAS
    exp  = np.floor(np.log2(pcm)).astype(np.int32) - 6
    exp  = np.clip(exp, 0, 7)
    mant = (pcm >> (exp + 3)) & 0x0F
    ulaw = ~(sign | (exp << 4) | mant)
    return ulaw.astype(np.uint8).tobytes()


def resample(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample PCM-16 data from from_rate to to_rate using linear interpolation."""
    if from_rate == to_rate:
        return data
    pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    n_out = int(len(pcm) * to_rate / from_rate)
    if n_out == 0:
        return b""
    x_old = np.linspace(0, 1, len(pcm))
    x_new = np.linspace(0, 1, n_out)
    out   = np.interp(x_new, x_old, pcm)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()
