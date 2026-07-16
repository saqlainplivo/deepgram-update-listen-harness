"""
Plivo ↔ Deepgram Voice Agent bridge server.

Architecture
────────────
  Plivo call (µ-law 8 kHz)
      ↕  WebSocket  (Plivo Stream protocol)
  This bridge  ←→  audio conversion (audioop)
      ↕  WebSocket  (Deepgram Voice Agent protocol)
  Deepgram Voice Agent (PCM-16 16 kHz, STT + LLM + TTS all-in-one)

Audio path:
  Plivo → us:  base64-decode → µ-law 8kHz → ulaw2lin → PCM16 8kHz
            → ratecv(8000→16000) → send binary to Deepgram
  Deepgram → us: PCM16 16kHz binary → ratecv(16000→8000) → lin2ulaw
            → base64 → playAudio event → Plivo

Scenario runner runs concurrently in the same process and sends
UpdateListen messages at timed intervals, then logs the results.

Run:
    uvicorn bridge.server:app --port 5000 --reload
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from typing import Optional

from bridge.audio import ulaw2lin, lin2ulaw, resample

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

log = logging.getLogger("bridge.server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)

# ── Config ────────────────────────────────────────────────────────────────────

DEEPGRAM_API_KEY  = os.environ["DEEPGRAM_API_KEY"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
WEBHOOK_BASE_URL  = os.environ.get("WEBHOOK_BASE_URL", "http://localhost:5000").rstrip("/")
DG_ENDPOINT       = "wss://agent.deepgram.com/v1/agent/converse"

PLIVO_SR   = 8_000   # Plivo stream sample rate
DG_SR      = 16_000  # Deepgram required sample rate
EOT_SWEEP  = [0.5, 0.7, 0.9]   # thresholds to test in order

app = FastAPI(title="Deepgram UpdateListen Bridge")

# shared state per call
_bridges: dict[str, "CallBridge"] = {}


# ── Plivo XML ─────────────────────────────────────────────────────────────────

def _stream_xml(call_uuid: str) -> str:
    ws_url = (
        WEBHOOK_BASE_URL
        .replace("https://", "wss://")
        .replace("http://", "ws://")
        + f"/ws/stream/{call_uuid}"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" streamTimeout="600"
            contentType="audio/x-mulaw;rate=8000">{ws_url}</Stream>
</Response>"""


# ── Webhooks ──────────────────────────────────────────────────────────────────

@app.post("/answer")
async def answer(request: Request):
    form = await request.form()
    call_uuid = form.get("CallUUID", str(uuid.uuid4()))
    log.info("Call answered: %s", call_uuid)
    return Response(content=_stream_xml(call_uuid), media_type="application/xml")

@app.post("/hangup")
async def hangup(request: Request):
    form = await request.form()
    call_uuid = form.get("CallUUID", "")
    bridge = _bridges.pop(call_uuid, None)
    if bridge:
        bridge.stop()
    log.info("Call hung up: %s", call_uuid)
    return PlainTextResponse("OK")

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Audio bridge WebSocket ────────────────────────────────────────────────────

@app.websocket("/ws/stream/{call_uuid}")
async def plivo_ws(websocket: WebSocket, call_uuid: str):
    await websocket.accept()
    log.info("Plivo stream connected: %s", call_uuid)

    bridge = CallBridge(call_uuid, websocket)
    _bridges[call_uuid] = bridge
    await bridge.run()
    _bridges.pop(call_uuid, None)
    log.info("Bridge done: %s", call_uuid)


# ── Bridge ────────────────────────────────────────────────────────────────────

class CallBridge:
    """
    Manages the full lifecycle of one call:
      - Plivo WebSocket  ↔  Deepgram Voice Agent WebSocket
      - Concurrent scenario runner (UpdateListen sweep)
    """

    def __init__(self, call_uuid: str, plivo_ws: WebSocket):
        self.call_uuid   = call_uuid
        self.plivo_ws    = plivo_ws
        self._stop       = False

        # Audio queues
        self._to_dg: asyncio.Queue[Optional[bytes]]    = asyncio.Queue()   # PCM16 16kHz → Deepgram
        self._to_plivo: asyncio.Queue[Optional[bytes]] = asyncio.Queue()  # PCM16 16kHz → Plivo

        # Scenario tracking
        self.events: list[dict] = []
        self._dg_ws = None

    def stop(self):
        self._stop = True

    # ── Main entry ────────────────────────────────────────────────────────────

    async def run(self):
        try:
            headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
            async with websockets.connect(DG_ENDPOINT, additional_headers=headers) as dg_ws:
                self._dg_ws = dg_ws
                log.info("[%s] Deepgram WS connected", self.call_uuid)

                # Send Settings
                await dg_ws.send(json.dumps(self._settings()))
                log.info("[%s] Settings sent", self.call_uuid)

                await asyncio.gather(
                    self._plivo_reader(),
                    self._dg_reader(dg_ws),
                    self._dg_writer(dg_ws),
                    self._plivo_writer(),
                    self._scenario_runner(dg_ws),
                    return_exceptions=True,
                )
        except Exception as e:
            log.error("[%s] Bridge error: %s", self.call_uuid, e)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _settings(self) -> dict:
        return {
            "type": "Settings",
            "audio": {
                "input":  {"encoding": "linear16", "sample_rate": DG_SR},
                "output": {"encoding": "linear16", "sample_rate": DG_SR, "container": "none"},
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
                        "temperature": 0.7,
                    },
                    "endpoint": {
                        "url": "https://api.groq.com/openai/v1/chat/completions",
                        "headers": {"authorization": f"Bearer {GROQ_API_KEY}"},
                    },
                    "prompt": (
                        "You are a friendly voice assistant helping test Deepgram's "
                        "UpdateListen API. Keep responses short — 1-2 sentences max. "
                        "Be conversational and natural. "
                        "If the user says they can hear a difference in how quickly you "
                        "respond, encourage them to keep talking."
                    ),
                },
                "speak": {
                    "provider": {"type": "deepgram", "model": "aura-2-asteria-en"}
                },
                "greeting": (
                    "Hi! I'm connected through Plivo. We're testing Deepgram's UpdateListen — "
                    "I'll adjust my end-of-turn sensitivity a few times during this call. "
                    "Just chat naturally. Go ahead!"
                ),
            },
        }

    # ── Plivo reader: Plivo → audio queue ─────────────────────────────────────

    async def _plivo_reader(self):
        try:
            while not self._stop:
                raw = await self.plivo_ws.receive_text()
                msg = json.loads(raw)
                ev = msg.get("event")

                if ev == "start":
                    log.info("[%s] Plivo stream started", self.call_uuid)

                elif ev == "media":
                    mulaw = base64.b64decode(msg["media"]["payload"])
                    pcm8  = ulaw2lin(mulaw)                          # µ-law → PCM16 8kHz
                    pcm16 = resample(pcm8, PLIVO_SR, DG_SR)          # 8kHz → 16kHz
                    await self._to_dg.put(pcm16)

                elif ev == "stop":
                    log.info("[%s] Plivo stream stopped", self.call_uuid)
                    self._stop = True
                    break

        except (WebSocketDisconnect, Exception) as e:
            log.info("[%s] Plivo reader ended: %s", self.call_uuid, e)
        finally:
            await self._to_dg.put(None)

    # ── Deepgram writer: audio queue → Deepgram ───────────────────────────────

    async def _dg_writer(self, dg_ws):
        try:
            while not self._stop:
                chunk = await self._to_dg.get()
                if chunk is None:
                    break
                await dg_ws.send(chunk)
        except Exception as e:
            log.debug("[%s] DG writer ended: %s", self.call_uuid, e)

    # ── Deepgram reader: Deepgram → plivo queue + event log ──────────────────

    async def _dg_reader(self, dg_ws):
        try:
            async for raw in dg_ws:
                if self._stop:
                    break

                if isinstance(raw, bytes):
                    # TTS audio from Deepgram (PCM16 16kHz)
                    await self._to_plivo.put(raw)
                    continue

                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                ts = time.time()

                self.events.append({"ts": ts, "type": msg_type, "payload": msg})
                log.info("[%s] DG ← %s", self.call_uuid, msg_type)

                if msg_type == "ConversationText":
                    role    = msg.get("role", "")
                    content = msg.get("content", "")
                    log.info("[%s]   [%s]: %s", self.call_uuid, role, content)

                elif msg_type == "AgentStartedSpeaking":
                    log.info("[%s]   latency total=%s tts=%s ttt=%s",
                             self.call_uuid,
                             msg.get("total_latency"),
                             msg.get("tts_latency"),
                             msg.get("ttt_latency"))

                elif msg_type in ("Error", "Warning"):
                    log.warning("[%s]   %s: %s", self.call_uuid, msg_type, msg.get("description"))

        except Exception as e:
            log.info("[%s] DG reader ended: %s", self.call_uuid, e)
        finally:
            await self._to_plivo.put(None)

    # ── Plivo writer: plivo queue → Plivo (TTS audio back to call) ────────────

    async def _plivo_writer(self):
        try:
            while not self._stop:
                chunk = await self._to_plivo.get()
                if chunk is None:
                    break
                # PCM16 16kHz → 8kHz → µ-law
                pcm8  = resample(chunk, DG_SR, PLIVO_SR)
                mulaw = lin2ulaw(pcm8)
                payload = base64.b64encode(mulaw).decode()
                await self.plivo_ws.send_json({
                    "event": "playAudio",
                    "media": {
                        "contentType": "audio/x-mulaw",
                        "sampleRate": PLIVO_SR,
                        "payload": payload,
                    },
                })
        except Exception as e:
            log.debug("[%s] Plivo writer ended: %s", self.call_uuid, e)

    # ── Scenario runner: UpdateListen sweep ───────────────────────────────────

    async def _scenario_runner(self, dg_ws):
        """
        Waits for the call to settle, then steps through EOT_SWEEP thresholds
        one by one, recording round-trip times.
        """
        # Wait for SettingsApplied before doing anything
        await self._wait_for_event("SettingsApplied", timeout=15.0)
        log.info("[%s] Scenario runner: SettingsApplied, waiting 20 s for conversation to start", self.call_uuid)
        await asyncio.sleep(20.0)

        results = []

        for eot in EOT_SWEEP:
            eager = round(max(0.1, eot - 0.05), 2)
            msg = {
                "type": "UpdateListen",
                "listen": {
                    "provider": {
                        "type": "deepgram",
                        "version": "v2",
                        "model": "flux-general-en",
                        "eot_threshold": eot,
                        "eager_eot_threshold": eager,
                    }
                },
            }
            sent_ts = time.time()
            await dg_ws.send(json.dumps(msg))
            log.info("[%s] → UpdateListen eot=%.2f eager=%.2f", self.call_uuid, eot, eager)

            ack_ts = await self._wait_for_event("ListenUpdated", timeout=15.0, after_ts=sent_ts)
            rt_ms  = (ack_ts - sent_ts) * 1000 if ack_ts else None

            errors = [
                e for e in self.events
                if e["type"] in ("Error", "Warning")
                and abs(e["ts"] - sent_ts) < 5.0
            ]

            log.info("[%s] ListenUpdated ack: %.0f ms | errors=%d", self.call_uuid, rt_ms or -1, len(errors))
            results.append({
                "eot_threshold":       eot,
                "eager_eot_threshold": eager,
                "sent_ts":             sent_ts,
                "ack_ts":              ack_ts,
                "round_trip_ms":       rt_ms,
                "errors_in_window":    errors,
            })

            # Let the user experience this threshold for 20 s before moving on
            await asyncio.sleep(20.0)

        # Print summary
        print("\n" + "═" * 60)
        print("  LIVE CALL — UpdateListen Scenario Results")
        print("═" * 60)
        print(f"  {'eot':>5}  {'eager':>5}  {'rt_ms':>8}  {'ack':>5}  {'errs':>4}")
        print("  " + "─" * 40)
        for r in results:
            rt  = f"{r['round_trip_ms']:.0f}" if r["round_trip_ms"] else "TIMEOUT"
            ack = "✓" if r["ack_ts"] else "✗"
            print(f"  {r['eot_threshold']:>5}  {r['eager_eot_threshold']:>5}  {rt:>8}  {ack:>5}  {len(r['errors_in_window']):>4}")
        print("═" * 60 + "\n")

        # Save to results/
        import pathlib, json as _json
        out = pathlib.Path(__file__).parent.parent / "results" / f"live_call_{self.call_uuid}.json"
        out.write_text(_json.dumps({
            "call_uuid":      self.call_uuid,
            "eot_sweep":      results,
            "all_events":     [
                {k: v for k, v in e.items() if k != "payload"}
                for e in self.events
            ],
        }, indent=2, default=str))
        log.info("[%s] Results saved to %s", self.call_uuid, out)

    async def _wait_for_event(self, msg_type: str, timeout: float = 10.0,
                               after_ts: float = 0.0) -> Optional[float]:
        """Poll self.events until msg_type appears after after_ts, return its timestamp."""
        deadline = time.time() + timeout
        seen_count = 0
        while time.time() < deadline:
            new_events = self.events[seen_count:]
            for e in new_events:
                if e["type"] == msg_type and e["ts"] >= after_ts:
                    return e["ts"]
            seen_count = len(self.events)
            await asyncio.sleep(0.1)
        log.warning("[%s] Timed out waiting for %s", self.call_uuid, msg_type)
        return None
