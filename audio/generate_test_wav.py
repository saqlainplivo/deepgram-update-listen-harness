"""
Generate a synthetic test WAV file for the UpdateListen harness.

The generated file contains a repeating pattern of:
  - 2 s of silence (simulates a pause / natural gap)
  - 2 s of a 440 Hz tone (simulates speech-like audio energy)

This gives the Deepgram STT engine something to process without
needing a real human voice.  It will NOT produce meaningful
transcriptions, but it is sufficient for:
  - Verifying WebSocket connectivity
  - Verifying UpdateListen round-trip timing
  - Smoke-testing the harness before attaching real audio

For accurate keyterm-injection testing you MUST replace or supplement
this file with a real WAV recording of the reference phrase.

Usage:
  python audio/generate_test_wav.py
  # → writes audio/test_speech.wav  (30 s, mono, 16-bit, 16 kHz)

License: generated audio is original (no external samples used).
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE   = 16_000
DURATION_S    = 30        # total length in seconds
TONE_HZ       = 440       # sine wave frequency (Hz)
SILENCE_S     = 2         # silent segment length
TONE_S        = 2         # tone segment length
AMPLITUDE     = 20_000    # peak amplitude out of 32767


def generate_wav(output_path: Path) -> None:
    total_samples = SAMPLE_RATE * DURATION_S
    samples: list[int] = []
    t = 0.0
    dt = 1.0 / SAMPLE_RATE

    phase = "silence"
    phase_start_sample = 0

    for i in range(total_samples):
        elapsed_in_phase = (i - phase_start_sample) / SAMPLE_RATE
        if phase == "silence" and elapsed_in_phase >= SILENCE_S:
            phase = "tone"
            phase_start_sample = i
        elif phase == "tone" and elapsed_in_phase >= TONE_S:
            phase = "silence"
            phase_start_sample = i

        if phase == "tone":
            value = int(AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * t))
        else:
            value = 0

        samples.append(value)
        t += dt

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)          # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    print(f"Written: {output_path}  ({DURATION_S} s, mono, 16-bit, {SAMPLE_RATE} Hz)")


if __name__ == "__main__":
    out = Path(__file__).parent / "test_speech.wav"
    generate_wav(out)
