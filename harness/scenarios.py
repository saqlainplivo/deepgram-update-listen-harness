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
