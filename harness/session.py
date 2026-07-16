"""
Core WebSocket session manager for the Deepgram Voice Agent API.

Uses raw websockets (not the Deepgram SDK) for precise control over
message timing and event capture needed for UpdateListen measurement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import websockets

log = logging.getLogger(__name__)

AGENT_ENDPOINT = "wss://agent.deepgram.com/v1/agent/converse"
AUDIO_CHUNK_MS = 20          # send 20 ms of audio per websocket frame
SAMPLE_RATE    = 16_000
BYTES_PER_SAMPLE = 2         # linear16
CHUNK_BYTES    = int(SAMPLE_RATE * AUDIO_CHUNK_MS / 1000) * BYTES_PER_SAMPLE


@dataclass
class SessionEvent:
    """A single timestamped event captured during a session."""
    ts: float               # epoch seconds (time.time())
    direction: str          # "rx" | "tx"
    msg_type: str           # e.g. "Settings", "UpdateListen", "AgentStartedSpeaking"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpdateListenResult:
    """Timing & ack data captured for a single UpdateListen round-trip."""
    sent_ts: float
    ack_ts: Optional[float]       = None   # when ListenUpdated was received
    round_trip_ms: Optional[float] = None
    next_agent_event: Optional[dict] = None  # first AgentStartedSpeaking after ack
    errors_in_window: list[dict]  = field(default_factory=list)  # errors within 5 s


def _build_settings(api_key: str, groq_api_key: str, system_prompt: str, greeting: str) -> dict:
    return {
        "type": "Settings",
        "audio": {
            "input":  {"encoding": "linear16", "sample_rate": SAMPLE_RATE},
            "output": {"encoding": "linear16", "sample_rate": SAMPLE_RATE, "container": "none"},
        },
        "agent": {
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "version": "v2",
                    "model": "flux-general-en",
                }
            },
            "think": {
                "provider": {
                    "type": "groq",
                    "model": "llama-3.3-70b-versatile",
                    "temperature": 0.5,
                },
                "endpoint": {
                    "url": "https://api.groq.com/openai/v1/chat/completions",
                    "headers": {
                        "authorization": f"Bearer {groq_api_key}",
                    },
                },
                "prompt": system_prompt,
            },
            "speak": {
                "provider": {"type": "deepgram", "model": "aura-2-asteria-en"}
            },
            "greeting": greeting,
        },
    }


def _build_update_listen(
    eot_threshold: Optional[float] = None,
    eager_eot_threshold: Optional[float] = None,
    eot_timeout_ms: Optional[int] = None,
    keyterms: Optional[list[str]] = None,
    model: str = "flux-general-en",
    version: str = "v2",
) -> dict:
    # type, version, model are required in every UpdateListen (even though immutable)
    provider: dict[str, Any] = {"type": "deepgram", "version": version, "model": model}
    if eot_threshold is not None:
        provider["eot_threshold"] = eot_threshold
    if eager_eot_threshold is not None:
        provider["eager_eot_threshold"] = eager_eot_threshold
    if eot_timeout_ms is not None:
        provider["eot_timeout_ms"] = eot_timeout_ms
    if keyterms is not None:
        provider["keyterms"] = keyterms
    return {"type": "UpdateListen", "listen": {"provider": provider}}


class DeepgramAgentSession:
    """
    Manages one WebSocket connection to the Deepgram Voice Agent API.

    Responsibilities:
    - Send audio frames continuously from a WAV file or mic stream
    - Capture all server events with high-resolution timestamps
    - Expose send_update_listen() and wait_for_ack() for test scenarios
    - Expose all captured events for post-run metric extraction
    """

    def __init__(
        self,
        api_key: str,
        groq_api_key: str,
        audio_source: "AudioSource",
        system_prompt: str = "You are a helpful voice assistant. Keep responses brief.",
        greeting: str = "Hello! I'm ready to chat.",
        on_event: Optional[Callable[[SessionEvent], None]] = None,
    ):
        self.api_key       = api_key
        self.groq_api_key  = groq_api_key
        self.audio_source  = audio_source
        self.system_prompt = system_prompt
        self.greeting      = greeting
        self.on_event      = on_event

        self.events: list[SessionEvent] = []
        self._ws: Optional[Any]         = None
        self._running                   = False
        self._pending_acks: dict[str, asyncio.Future] = {}  # keyed by msg_type we're waiting for
        self._update_listen_results: list[UpdateListenResult] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, duration_s: float = 60.0) -> list[SessionEvent]:
        """Connect, stream audio for duration_s, then close."""
        headers = {"Authorization": f"Token {self.api_key}"}
        async with websockets.connect(AGENT_ENDPOINT, additional_headers=headers) as ws:
            self._ws      = ws
            self._running = True
            log.info("WebSocket connected to %s", AGENT_ENDPOINT)

            await self._send_json(_build_settings(self.api_key, self.groq_api_key, self.system_prompt, self.greeting))

            recv_task  = asyncio.create_task(self._recv_loop())
            audio_task = asyncio.create_task(self._audio_loop(duration_s))

            await asyncio.wait(
                [recv_task, audio_task],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=duration_s + 5,
            )
            self._running = False
            recv_task.cancel()
            audio_task.cancel()
            for t in (recv_task, audio_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

        log.info("Session closed. %d events captured.", len(self.events))
        return self.events

    async def send_update_listen(
        self,
        eot_threshold: Optional[float] = None,
        eager_eot_threshold: Optional[float] = None,
        eot_timeout_ms: Optional[int] = None,
        keyterms: Optional[list[str]] = None,
        timeout_s: float = 5.0,
        model: str = "flux-general-en",
        version: str = "v2",
    ) -> UpdateListenResult:
        """
        Send UpdateListen and wait for ListenUpdated ack.
        Returns an UpdateListenResult with timing data.
        """
        msg    = _build_update_listen(eot_threshold, eager_eot_threshold, eot_timeout_ms, keyterms, model, version)
        result = UpdateListenResult(sent_ts=time.time())
        self._update_listen_results.append(result)

        # Register future for the ack
        fut = asyncio.get_event_loop().create_future()
        self._pending_acks["ListenUpdated"] = fut

        await self._send_json(msg)
        log.info("Sent UpdateListen: %s", json.dumps(msg))

        try:
            await asyncio.wait_for(fut, timeout=timeout_s)
            result.ack_ts         = fut.result()
            result.round_trip_ms  = (result.ack_ts - result.sent_ts) * 1000
            log.info("ListenUpdated ack received in %.1f ms", result.round_trip_ms)
        except asyncio.TimeoutError:
            log.warning("ListenUpdated ack timed out after %.1f s", timeout_s)

        # Collect any errors/warnings in a 5 s window after ack
        asyncio.get_event_loop().call_later(5.0, self._close_error_window, result)

        return result

    def get_update_listen_results(self) -> list[UpdateListenResult]:
        return list(self._update_listen_results)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _send_json(self, msg: dict) -> None:
        data = json.dumps(msg)
        await self._ws.send(data)
        evt = SessionEvent(ts=time.time(), direction="tx", msg_type=msg["type"], payload=msg)
        self.events.append(evt)
        if self.on_event:
            self.on_event(evt)

    async def _recv_loop(self) -> None:
        async for raw in self._ws:
            ts = time.time()
            if isinstance(raw, bytes):
                # Audio bytes from TTS — record as a minimal event
                evt = SessionEvent(ts=ts, direction="rx", msg_type="AudioBytes",
                                   payload={"bytes": len(raw)})
                self.events.append(evt)
                if self.on_event:
                    self.on_event(evt)
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON text frame: %s", raw[:120])
                continue

            msg_type = msg.get("type", "Unknown")
            evt = SessionEvent(ts=ts, direction="rx", msg_type=msg_type, payload=msg)
            self.events.append(evt)
            if self.on_event:
                self.on_event(evt)

            log.debug("RX [%s]: %s", msg_type, json.dumps(msg)[:200])

            # Resolve pending acks
            if msg_type in self._pending_acks:
                fut = self._pending_acks.pop(msg_type)
                if not fut.done():
                    fut.set_result(ts)

            # Attach AgentStartedSpeaking to the most recent UpdateListenResult
            if msg_type == "AgentStartedSpeaking":
                self._attach_agent_event(msg, ts)

            # Attach errors/warnings to open result windows
            if msg_type in ("Error", "Warning"):
                self._attach_error(msg, ts)

    async def _audio_loop(self, duration_s: float) -> None:
        end_time = time.time() + duration_s
        interval = AUDIO_CHUNK_MS / 1000.0
        async for chunk in self.audio_source.chunks():
            if time.time() >= end_time or not self._running:
                break
            await self._ws.send(chunk)
            await asyncio.sleep(interval)

    def _attach_agent_event(self, msg: dict, ts: float) -> None:
        # Find the most recent result that has an ack but no next_agent_event yet
        for result in reversed(self._update_listen_results):
            if result.ack_ts is not None and result.next_agent_event is None:
                result.next_agent_event = {"ts": ts, **msg}
                return

    def _attach_error(self, msg: dict, ts: float) -> None:
        window = 5.0
        for result in reversed(self._update_listen_results):
            if result.sent_ts and abs(ts - result.sent_ts) <= window:
                result.errors_in_window.append({"ts": ts, **msg})

    def _close_error_window(self, result: UpdateListenResult) -> None:
        pass  # placeholder; window is based on timestamps, not state


# ------------------------------------------------------------------
# Audio source abstractions
# ------------------------------------------------------------------

class WavFileAudioSource:
    """
    Streams audio from a 16-bit mono WAV file at real-time rate.
    If loop=True the file repeats indefinitely (useful for long test runs).
    """

    def __init__(self, path: str | Path, loop: bool = True):
        self.path = Path(path)
        self.loop = loop

    async def chunks(self):
        import wave
        while True:
            with wave.open(str(self.path), "rb") as wf:
                assert wf.getsampwidth() == 2, "WAV must be 16-bit"
                assert wf.getnchannels() == 1, "WAV must be mono"
                assert wf.getframerate() == SAMPLE_RATE, f"WAV must be {SAMPLE_RATE} Hz"
                while True:
                    data = wf.readframes(CHUNK_BYTES // BYTES_PER_SAMPLE)
                    if not data:
                        break
                    yield data
            if not self.loop:
                break


class MicAudioSource:
    """
    Streams live microphone audio using sounddevice.
    Requires: pip install sounddevice
    """

    def __init__(self):
        try:
            import sounddevice as sd  # noqa: F401
        except ImportError as e:
            raise ImportError("sounddevice is required for mic input: pip install sounddevice") from e

    async def chunks(self):
        import sounddevice as sd
        import numpy as np
        q: asyncio.Queue[bytes] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if status:
                log.warning("sounddevice status: %s", status)
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            loop.call_soon_threadsafe(q.put_nowait, pcm)

        frames_per_chunk = CHUNK_BYTES // BYTES_PER_SAMPLE
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=frames_per_chunk,
            callback=callback,
        ):
            while True:
                yield await q.get()


class SilenceAudioSource:
    """Emits silent audio frames — useful for quick smoke-testing without a real audio file."""

    async def chunks(self):
        while True:
            yield b"\x00" * CHUNK_BYTES
            await asyncio.sleep(AUDIO_CHUNK_MS / 1000.0)
