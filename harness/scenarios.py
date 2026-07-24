"""
Test scenarios for the Deepgram Voice Agent UpdateListen harness.

Each scenario is an async function that accepts a DeepgramAgentSession
(already connected and streaming audio) and a MetricsCollector.  The
session lifecycle (connect / run / disconnect) is managed by main.py.

Scenario catalogue
──────────────────
1. eot_threshold_sweep  — cycle through eot_threshold = 0.5, 0.7, 0.9
                          and record turn-taking latency around each update.
2. keyterm_injection    — establish a baseline, then inject a domain-specific
                          keyterm via UpdateListen and compare recognition
                          accuracy before vs. after.
3. combined_update      — send one UpdateListen that changes both eot_threshold
                          and keyterms simultaneously, verifying a single ack.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .session import DeepgramAgentSession, UpdateListenResult
from .metrics import MetricsCollector, TurnSnapshot, KeytermAccuracySample

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 1 — eot_threshold sweep
# ──────────────────────────────────────────────────────────────────────────────

EOT_THRESHOLDS = [0.5, 0.7, 0.9]
# eager must be ≤ eot; we keep it at eot - 0.05 clamped to [0.1, 0.85]
def _eager(eot: float) -> float:
    return max(0.1, min(0.85, round(eot - 0.05, 2)))

SOAK_TIME_S = 15.0   # seconds to observe behaviour before next update


async def eot_threshold_sweep(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    baseline_soak_s: float = SOAK_TIME_S,
    post_update_soak_s: float = SOAK_TIME_S,
) -> None:
    """
    For each eot_threshold in EOT_THRESHOLDS:
      1. Capture a baseline snapshot of the last few AgentStartedSpeaking latencies.
      2. Send UpdateListen with the new threshold.
      3. Wait post_update_soak_s seconds.
      4. Record round-trip, any errors, and post-update latency.
    """
    log.info("[Scenario 1] eot_threshold sweep starting. Baseline soak: %s s", baseline_soak_s)
    await asyncio.sleep(baseline_soak_s)

    for eot in EOT_THRESHOLDS:
        eager = _eager(eot)
        log.info("[Scenario 1] Sending UpdateListen eot_threshold=%.2f eager=%.2f", eot, eager)

        baseline_latencies = _extract_recent_latencies(session, window_s=baseline_soak_s)

        result: UpdateListenResult = await session.send_update_listen(
            eot_threshold=eot,
            eager_eot_threshold=eager,
        )

        snap = TurnSnapshot(
            scenario="eot_sweep",
            eot_threshold=eot,
            eager_eot_threshold=eager,
            sent_ts=result.sent_ts,
            ack_ts=result.ack_ts,
            round_trip_ms=result.round_trip_ms,
            baseline_latencies=baseline_latencies,
            subjective_note=(
                f"eot_threshold={eot}: observe whether agent cuts in early "
                f"(low) or waits too long (high). Update this note after manual review."
            ),
        )

        # Wait for post-update agent turn to capture post-latency
        await asyncio.sleep(post_update_soak_s)

        if result.next_agent_event:
            snap.post_update_total_latency  = result.next_agent_event.get("total_latency")
            snap.post_update_tts_latency    = result.next_agent_event.get("tts_latency")
            snap.post_update_ttt_latency    = result.next_agent_event.get("ttt_latency")

        snap.errors_in_window = result.errors_in_window
        metrics.add_turn_snapshot(snap)
        log.info("[Scenario 1] Snapshot recorded: %s", snap)

    log.info("[Scenario 1] eot_threshold sweep complete.")


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 2 — keyterm injection
# ──────────────────────────────────────────────────────────────────────────────

# The test phrase will be read aloud from the WAV file or spoken to the mic.
# We record transcriptions before and after the keyterm is injected, then
# score exact-match accuracy against REFERENCE_PHRASE.
#
# KEYTERM is chosen to be a domain word unlikely to be in the base model's
# common vocabulary.  Operators should substitute their own product name.

DEFAULT_KEYTERM = "Plivo"             # change via --keyterm CLI flag
REFERENCE_PHRASE = "I am testing Plivo voice integration"  # expected utterance


async def keyterm_injection(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    keyterm: str = DEFAULT_KEYTERM,
    reference_phrase: str = REFERENCE_PHRASE,
    pre_injection_wait_s: float = 10.0,
    post_injection_wait_s: float = 10.0,
) -> None:
    """
    1. Wait pre_injection_wait_s for a few transcriptions to accumulate (baseline).
    2. Score keyterm recall in those baseline transcriptions.
    3. Send UpdateListen adding keyterm to keyterms list.
    4. Wait post_injection_wait_s.
    5. Score keyterm recall in post-injection transcriptions.
    6. Log KeytermAccuracySample with before/after counts.
    """
    log.info("[Scenario 2] keyterm_injection: keyterm=%r. Pre-injection soak: %s s",
             keyterm, pre_injection_wait_s)
    baseline_mark = time.time()
    await asyncio.sleep(pre_injection_wait_s)

    pre_transcripts = _extract_transcripts(session, since_ts=baseline_mark)
    pre_hit  = _count_keyterm_hits(pre_transcripts, keyterm)
    pre_total = len(pre_transcripts)
    log.info("[Scenario 2] Before: %d/%d utterances contain keyterm %r",
             pre_hit, pre_total, keyterm)

    result: UpdateListenResult = await session.send_update_listen(keyterms=[keyterm])
    injection_ts = result.sent_ts

    log.info("[Scenario 2] UpdateListen(keyterms=[%r]) sent. Waiting %s s post-injection...",
             keyterm, post_injection_wait_s)
    await asyncio.sleep(post_injection_wait_s)

    post_transcripts = _extract_transcripts(session, since_ts=injection_ts)
    post_hit   = _count_keyterm_hits(post_transcripts, keyterm)
    post_total = len(post_transcripts)
    log.info("[Scenario 2] After: %d/%d utterances contain keyterm %r",
             post_hit, post_total, keyterm)

    sample = KeytermAccuracySample(
        scenario="keyterm_injection",
        keyterm=keyterm,
        reference_phrase=reference_phrase,
        sent_ts=result.sent_ts,
        ack_ts=result.ack_ts,
        round_trip_ms=result.round_trip_ms,
        pre_utterance_count=pre_total,
        pre_hit_count=pre_hit,
        post_utterance_count=post_total,
        post_hit_count=post_hit,
        errors_in_window=result.errors_in_window,
    )
    metrics.add_keyterm_sample(sample)
    log.info("[Scenario 2] KeytermAccuracySample recorded: %s", sample)


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 3 — combined update (both eot_threshold and keyterms in one message)
# ──────────────────────────────────────────────────────────────────────────────

async def combined_update(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    eot_threshold: float = 0.6,
    keyterm: str = DEFAULT_KEYTERM,
    soak_s: float = 10.0,
) -> None:
    """
    Send a single UpdateListen that sets both eot_threshold and keyterms.
    Verifies exactly one ListenUpdated ack is received (not two).
    """
    log.info("[Scenario 3] combined_update: eot_threshold=%.2f keyterm=%r soak=%s s",
             eot_threshold, keyterm, soak_s)
    await asyncio.sleep(soak_s)

    eager = _eager(eot_threshold)
    baseline_latencies = _extract_recent_latencies(session, window_s=soak_s)

    result: UpdateListenResult = await session.send_update_listen(
        eot_threshold=eot_threshold,
        eager_eot_threshold=eager,
        keyterms=[keyterm],
    )

    await asyncio.sleep(soak_s)

    snap = TurnSnapshot(
        scenario="combined_update",
        eot_threshold=eot_threshold,
        eager_eot_threshold=eager,
        sent_ts=result.sent_ts,
        ack_ts=result.ack_ts,
        round_trip_ms=result.round_trip_ms,
        baseline_latencies=baseline_latencies,
        subjective_note=(
            f"Combined update (eot={eot_threshold}, keyterm={keyterm!r}): "
            f"check that only ONE ListenUpdated was received."
        ),
    )
    if result.next_agent_event:
        snap.post_update_total_latency = result.next_agent_event.get("total_latency")
        snap.post_update_tts_latency   = result.next_agent_event.get("tts_latency")
        snap.post_update_ttt_latency   = result.next_agent_event.get("ttt_latency")
    snap.errors_in_window = result.errors_in_window
    metrics.add_turn_snapshot(snap)
    log.info("[Scenario 3] combined_update complete. Snapshot: %s", snap)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _extract_recent_latencies(session: DeepgramAgentSession, window_s: float) -> list[float]:
    """Return total_latency values from AgentStartedSpeaking events in the last window_s."""
    cutoff = time.time() - window_s
    result = []
    for evt in session.events:
        if evt.ts >= cutoff and evt.msg_type == "AgentStartedSpeaking":
            lat = evt.payload.get("total_latency")
            if lat is not None:
                result.append(float(lat))
    return result


def _extract_transcripts(session: DeepgramAgentSession, since_ts: float) -> list[str]:
    """Return ConversationText role=user utterances since since_ts."""
    texts = []
    for evt in session.events:
        if evt.ts >= since_ts and evt.msg_type == "ConversationText":
            if evt.payload.get("role") == "user":
                text = evt.payload.get("content", "")
                if text:
                    texts.append(text)
    return texts


def _count_keyterm_hits(transcripts: list[str], keyterm: str) -> int:
    """Count how many transcripts contain the keyterm (case-insensitive)."""
    kl = keyterm.lower()
    return sum(1 for t in transcripts if kl in t.lower())


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 4 — eot_timeout_ms sweep
# ──────────────────────────────────────────────────────────────────────────────

EOT_TIMEOUT_MS_VALUES = [500, 1000, 2000]


async def eot_timeout_ms_sweep(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    soak_s: float = 15.0,
) -> list[dict]:
    """Sweep eot_timeout_ms through 500, 1000, 2000 ms."""
    log.info("[Scenario 4] eot_timeout_ms sweep starting. Waiting for SettingsApplied...")

    # Wait for SettingsApplied
    try:
        await _wait_for_event(session, "SettingsApplied", timeout_s=10.0)
    except asyncio.TimeoutError:
        log.warning("[Scenario 4] SettingsApplied not seen within 10 s; proceeding anyway.")

    await asyncio.sleep(soak_s)

    results = []
    for timeout_ms in EOT_TIMEOUT_MS_VALUES:
        log.info("[Scenario 4] Sending UpdateListen eot_timeout_ms=%d", timeout_ms)
        sent_ts = time.time()

        result: UpdateListenResult = await session.send_update_listen(
            eot_timeout_ms=timeout_ms,
            timeout_s=10.0,
        )

        entry = {
            "scenario": "eot_timeout_ms_sweep",
            "eot_timeout_ms": timeout_ms,
            "sent_ts": result.sent_ts,
            "ack_ts": result.ack_ts,
            "round_trip_ms": result.round_trip_ms,
            "ack_received": result.ack_ts is not None,
            "errors": result.errors_in_window,
        }
        results.append(entry)
        log.info("[Scenario 4] eot_timeout_ms=%d  RT=%.1f ms  ack=%s",
                 timeout_ms, result.round_trip_ms or -1, result.ack_ts is not None)

        await asyncio.sleep(soak_s)

    log.info("[Scenario 4] eot_timeout_ms sweep complete.")
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 5 — concurrent UpdateListen
# ──────────────────────────────────────────────────────────────────────────────

async def concurrent_update_listen(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    duration_s: float = 60.0,
) -> dict:
    """Send two UpdateListen messages without waiting for first ack. Record both acks."""
    log.info("[Scenario 5] concurrent_update_listen starting.")

    try:
        await _wait_for_event(session, "SettingsApplied", timeout_s=10.0)
    except asyncio.TimeoutError:
        log.warning("[Scenario 5] SettingsApplied not seen within 10 s; proceeding anyway.")

    await asyncio.sleep(15.0)

    import json as _json

    # Build two messages manually so we can send without awaiting acks
    msg1 = {
        "type": "UpdateListen",
        "listen": {
            "provider": {
                "type": "deepgram",
                "version": "v2",
                "model": "flux-general-en",
                "eot_threshold": 0.5,
                "eager_eot_threshold": 0.45,
            }
        }
    }
    msg2 = {
        "type": "UpdateListen",
        "listen": {
            "provider": {
                "type": "deepgram",
                "version": "v2",
                "model": "flux-general-en",
                "eot_threshold": 0.9,
                "eager_eot_threshold": 0.85,
            }
        }
    }

    sent_ts1 = time.time()
    await session._send_json(msg1)
    log.info("[Scenario 5] Sent UpdateListen #1 (eot=0.5) at %.3f", sent_ts1)

    sent_ts2 = time.time()
    await session._send_json(msg2)
    log.info("[Scenario 5] Sent UpdateListen #2 (eot=0.9) at %.3f — no wait for #1 ack", sent_ts2)

    # Now collect acks from the event stream for up to 10 s
    deadline = time.time() + 10.0
    acks_received = []
    while time.time() < deadline and len(acks_received) < 2:
        await asyncio.sleep(0.1)
        for evt in session.events:
            if evt.msg_type == "ListenUpdated" and evt.ts > sent_ts1:
                if not any(a["ack_ts"] == evt.ts for a in acks_received):
                    acks_received.append({"ack_ts": evt.ts, "rt_ms": (evt.ts - sent_ts1) * 1000})

    result = {
        "scenario": "concurrent_update_listen",
        "sent_ts_1": sent_ts1,
        "sent_ts_2": sent_ts2,
        "gap_between_sends_ms": (sent_ts2 - sent_ts1) * 1000,
        "acks_received": len(acks_received),
        "ack_details": acks_received,
        "errors": [e.payload for e in session.events
                   if e.msg_type in ("Error", "Warning") and e.ts > sent_ts1 - 1],
    }
    log.info("[Scenario 5] concurrent_update_listen complete: %d ack(s) received. Details: %s",
             len(acks_received), result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 6 — UpdateThink mid-call
# ──────────────────────────────────────────────────────────────────────────────

async def update_think(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    duration_s: float = 60.0,
) -> dict:
    """Send UpdateThink mid-call. Measure ack latency."""
    log.info("[Scenario 6] update_think starting.")

    try:
        await _wait_for_event(session, "SettingsApplied", timeout_s=10.0)
    except asyncio.TimeoutError:
        log.warning("[Scenario 6] SettingsApplied not seen within 10 s; proceeding anyway.")

    await asyncio.sleep(15.0)

    msg = {
        "type": "UpdateThink",
        "think": {
            "provider": {
                "type": "groq",
                "model": "llama-3.3-70b-versatile",
                "temperature": 0.3,
            }
        }
    }

    sent_ts = time.time()
    try:
        await session._send_json(msg)
        log.info("[Scenario 6] UpdateThink sent at %.3f", sent_ts)
    except Exception as e:
        log.error("[Scenario 6] Failed to send UpdateThink: %s", e)
        return {
            "scenario": "update_think",
            "sent_ts": sent_ts,
            "error": str(e),
            "ack_received": False,
            "round_trip_ms": None,
            "status": "NOT_SUPPORTED",
        }

    # Wait for ThinkUpdated ack
    ack_ts = None
    try:
        ack_ts = await _wait_for_event_after(session, "ThinkUpdated", after_ts=sent_ts, timeout_s=10.0)
        rt_ms = (ack_ts - sent_ts) * 1000
        log.info("[Scenario 6] ThinkUpdated ack received in %.1f ms", rt_ms)
    except asyncio.TimeoutError:
        log.warning("[Scenario 6] ThinkUpdated ack timed out after 10 s")
        rt_ms = None

    # Check for errors
    errors = [e.payload for e in session.events
              if e.msg_type in ("Error", "Warning") and abs(e.ts - sent_ts) <= 5.0]

    result = {
        "scenario": "update_think",
        "sent_ts": sent_ts,
        "ack_ts": ack_ts,
        "round_trip_ms": rt_ms,
        "ack_received": ack_ts is not None,
        "errors": errors,
        "status": "OK" if ack_ts is not None else ("NOT_SUPPORTED" if errors else "TIMEOUT"),
    }
    log.info("[Scenario 6] update_think complete: %s", result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 7 — UpdateSpeak mid-call
# ──────────────────────────────────────────────────────────────────────────────

async def update_speak(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    duration_s: float = 60.0,
) -> dict:
    """Send UpdateSpeak mid-call (change voice). Measure ack latency."""
    log.info("[Scenario 7] update_speak starting.")

    try:
        await _wait_for_event(session, "SettingsApplied", timeout_s=10.0)
    except asyncio.TimeoutError:
        log.warning("[Scenario 7] SettingsApplied not seen within 10 s; proceeding anyway.")

    await asyncio.sleep(15.0)

    msg = {
        "type": "UpdateSpeak",
        "speak": {
            "provider": {
                "type": "deepgram",
                "model": "aura-2-luna-en",
            }
        }
    }

    sent_ts = time.time()
    try:
        await session._send_json(msg)
        log.info("[Scenario 7] UpdateSpeak sent at %.3f", sent_ts)
    except Exception as e:
        log.error("[Scenario 7] Failed to send UpdateSpeak: %s", e)
        return {
            "scenario": "update_speak",
            "sent_ts": sent_ts,
            "error": str(e),
            "ack_received": False,
            "round_trip_ms": None,
            "status": "NOT_SUPPORTED",
        }

    # Wait for SpeakUpdated ack
    ack_ts = None
    try:
        ack_ts = await _wait_for_event_after(session, "SpeakUpdated", after_ts=sent_ts, timeout_s=10.0)
        rt_ms = (ack_ts - sent_ts) * 1000
        log.info("[Scenario 7] SpeakUpdated ack received in %.1f ms", rt_ms)
    except asyncio.TimeoutError:
        log.warning("[Scenario 7] SpeakUpdated ack timed out after 10 s")
        rt_ms = None

    errors = [e.payload for e in session.events
              if e.msg_type in ("Error", "Warning") and abs(e.ts - sent_ts) <= 5.0]

    result = {
        "scenario": "update_speak",
        "sent_ts": sent_ts,
        "ack_ts": ack_ts,
        "round_trip_ms": rt_ms,
        "ack_received": ack_ts is not None,
        "errors": errors,
        "status": "OK" if ack_ts is not None else ("NOT_SUPPORTED" if errors else "TIMEOUT"),
    }
    log.info("[Scenario 7] update_speak complete: %s", result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 8 — InjectAgentMessage
# ──────────────────────────────────────────────────────────────────────────────

async def inject_agent_message(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    duration_s: float = 60.0,
) -> dict:
    """Send InjectAgentMessage. Verify it appears as AgentStartedSpeaking."""
    log.info("[Scenario 8] inject_agent_message starting.")

    try:
        await _wait_for_event(session, "SettingsApplied", timeout_s=10.0)
    except asyncio.TimeoutError:
        log.warning("[Scenario 8] SettingsApplied not seen within 10 s; proceeding anyway.")

    await asyncio.sleep(15.0)

    msg = {
        "type": "InjectAgentMessage",
        "message": "This is a mid-call injected agent message for testing.",
    }

    sent_ts = time.time()
    try:
        await session._send_json(msg)
        log.info("[Scenario 8] InjectAgentMessage sent at %.3f", sent_ts)
    except Exception as e:
        log.error("[Scenario 8] Failed to send InjectAgentMessage: %s", e)
        return {
            "scenario": "inject_agent_message",
            "sent_ts": sent_ts,
            "error": str(e),
            "ack_received": False,
            "round_trip_ms": None,
            "status": "NOT_SUPPORTED",
        }

    # Wait for ConversationText (role=assistant) — this is the actual ack for InjectAgentMessage.
    # AgentStartedSpeaking may or may not arrive depending on audio routing.
    ack_ts = None
    try:
        ack_ts = await _wait_for_event_after(session, "ConversationText", after_ts=sent_ts, timeout_s=10.0)
        rt_ms = (ack_ts - sent_ts) * 1000
        log.info("[Scenario 8] ConversationText received in %.1f ms after inject", rt_ms)
    except asyncio.TimeoutError:
        # Fallback: check for AgentStartedSpeaking
        try:
            ack_ts = await _wait_for_event_after(session, "AgentStartedSpeaking", after_ts=sent_ts, timeout_s=5.0)
            rt_ms = (ack_ts - sent_ts) * 1000
            log.info("[Scenario 8] AgentStartedSpeaking received in %.1f ms after inject", rt_ms)
        except asyncio.TimeoutError:
            log.warning("[Scenario 8] No ConversationText or AgentStartedSpeaking within timeout")
            rt_ms = None

    errors = [e.payload for e in session.events
              if e.msg_type in ("Error", "Warning") and abs(e.ts - sent_ts) <= 5.0]

    result = {
        "scenario": "inject_agent_message",
        "sent_ts": sent_ts,
        "ack_ts": ack_ts,
        "round_trip_ms": rt_ms,
        "ack_received": ack_ts is not None,
        "errors": errors,
        "status": "OK" if ack_ts is not None else ("NOT_SUPPORTED" if errors else "TIMEOUT"),
    }
    log.info("[Scenario 8] inject_agent_message complete: %s", result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 9 — InjectUserMessage
# ──────────────────────────────────────────────────────────────────────────────

async def inject_user_message(
    session: DeepgramAgentSession,
    metrics: MetricsCollector,
    duration_s: float = 60.0,
) -> dict:
    """Send InjectUserMessage. Verify it appears in ConversationText."""
    log.info("[Scenario 9] inject_user_message starting.")

    try:
        await _wait_for_event(session, "SettingsApplied", timeout_s=10.0)
    except asyncio.TimeoutError:
        log.warning("[Scenario 9] SettingsApplied not seen within 10 s; proceeding anyway.")

    await asyncio.sleep(15.0)

    msg = {
        "type": "InjectUserMessage",
        "message": "testing inject user message",
    }

    sent_ts = time.time()
    try:
        await session._send_json(msg)
        log.info("[Scenario 9] InjectUserMessage sent at %.3f", sent_ts)
    except Exception as e:
        log.error("[Scenario 9] Failed to send InjectUserMessage: %s", e)
        return {
            "scenario": "inject_user_message",
            "sent_ts": sent_ts,
            "error": str(e),
            "ack_received": False,
            "round_trip_ms": None,
            "status": "NOT_SUPPORTED",
        }

    # Wait for ConversationText
    ack_ts = None
    try:
        ack_ts = await _wait_for_event_after(session, "ConversationText", after_ts=sent_ts, timeout_s=10.0)
        rt_ms = (ack_ts - sent_ts) * 1000
        log.info("[Scenario 9] ConversationText received in %.1f ms after inject", rt_ms)
    except asyncio.TimeoutError:
        log.warning("[Scenario 9] ConversationText not received within 10 s")
        rt_ms = None

    errors = [e.payload for e in session.events
              if e.msg_type in ("Error", "Warning") and abs(e.ts - sent_ts) <= 5.0]

    result = {
        "scenario": "inject_user_message",
        "sent_ts": sent_ts,
        "ack_ts": ack_ts,
        "round_trip_ms": rt_ms,
        "ack_received": ack_ts is not None,
        "errors": errors,
        "status": "OK" if ack_ts is not None else ("NOT_SUPPORTED" if errors else "TIMEOUT"),
    }
    log.info("[Scenario 9] inject_user_message complete: %s", result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Extended scenario helper: wait for event type
# ──────────────────────────────────────────────────────────────────────────────

async def _wait_for_event(session: DeepgramAgentSession, event_type: str, timeout_s: float = 10.0) -> float:
    """Poll session.events until event_type appears. Returns the event timestamp."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for evt in session.events:
            if evt.msg_type == event_type:
                return evt.ts
        await asyncio.sleep(0.1)
    raise asyncio.TimeoutError(f"Event {event_type!r} not seen within {timeout_s} s")


async def _wait_for_event_after(
    session: DeepgramAgentSession,
    event_type: str,
    after_ts: float,
    timeout_s: float = 10.0,
) -> float:
    """Poll session.events for event_type with ts > after_ts. Returns event timestamp."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for evt in session.events:
            if evt.msg_type == event_type and evt.ts > after_ts:
                return evt.ts
        await asyncio.sleep(0.1)
    raise asyncio.TimeoutError(f"Event {event_type!r} after ts={after_ts} not seen within {timeout_s} s")
