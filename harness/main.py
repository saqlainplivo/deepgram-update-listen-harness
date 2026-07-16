"""
CLI entry point for the Deepgram Voice Agent UpdateListen test harness.

Usage
─────
  # Run all three scenarios with a WAV file:
  python -m harness.main --audio audio/test_speech.wav --scenario all

  # Run only the eot_threshold sweep (mic input):
  python -m harness.main --audio mic --scenario eot_sweep

  # Run keyterm injection with a custom keyterm:
  python -m harness.main --audio audio/test_speech.wav \
      --scenario keyterm --keyterm "Deepgram" \
      --reference "I am testing Deepgram voice integration"

  # Smoke-test with silence (no audio file needed — API will still send events):
  python -m harness.main --audio silence --scenario all --duration 90

Environment variables
─────────────────────
  DEEPGRAM_API_KEY   (required)
  GROQ_API_KEY       (required — used for the LLM think provider)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Make the package importable when run as `python -m harness.main` from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from harness.session import (
    DeepgramAgentSession,
    MicAudioSource,
    SilenceAudioSource,
    WavFileAudioSource,
)
from harness.metrics import MetricsCollector, print_summary_table
from harness import scenarios as sc

log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deepgram Voice Agent UpdateListen test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--audio", default="audio/test_speech.wav",
        help="Path to a mono 16-bit 16 kHz WAV file, 'mic' for live mic, "
             "or 'silence' for silent frames (default: audio/test_speech.wav)",
    )
    p.add_argument(
        "--scenario", default="all",
        choices=["all", "eot_sweep", "keyterm", "combined"],
        help="Which scenario(s) to run (default: all)",
    )
    p.add_argument(
        "--keyterm", default=sc.DEFAULT_KEYTERM,
        help=f"Keyterm to inject in scenario 2 (default: {sc.DEFAULT_KEYTERM!r})",
    )
    p.add_argument(
        "--reference", default=sc.REFERENCE_PHRASE,
        help="Reference phrase containing the keyterm (default: built-in phrase)",
    )
    p.add_argument(
        "--duration", type=float, default=120.0,
        help="Total session duration in seconds (default: 120). "
             "Increase if running all scenarios.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    p.add_argument(
        "--label", default="",
        help="Optional label appended to result file names",
    )
    p.add_argument(
        "--system-prompt", default="You are a helpful voice assistant. Keep responses brief.",
        help="System prompt for the agent think provider",
    )
    p.add_argument(
        "--greeting", default="Hello! I'm your test assistant. Let's chat.",
        help="Agent greeting utterance",
    )
    return p.parse_args()


def build_audio_source(audio_arg: str):
    if audio_arg.lower() == "mic":
        log.info("Audio source: live microphone")
        return MicAudioSource()
    if audio_arg.lower() == "silence":
        log.info("Audio source: silence (smoke-test mode)")
        return SilenceAudioSource()
    path = Path(audio_arg)
    if not path.exists():
        log.error("WAV file not found: %s", path)
        sys.exit(1)
    log.info("Audio source: WAV file %s (looped)", path)
    return WavFileAudioSource(path, loop=True)


async def run(args: argparse.Namespace) -> None:
    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not deepgram_key:
        log.error("DEEPGRAM_API_KEY environment variable is not set.")
        sys.exit(1)

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not groq_key:
        log.error("GROQ_API_KEY environment variable is not set.")
        sys.exit(1)

    audio_src = build_audio_source(args.audio)
    metrics   = MetricsCollector(run_label=args.label or args.scenario)

    session = DeepgramAgentSession(
        api_key=deepgram_key,
        groq_api_key=groq_key,
        audio_source=audio_src,
        system_prompt=args.system_prompt,
        greeting=args.greeting,
        on_event=metrics.record_event,
    )

    # Build the scenario coroutines to run sequentially when "all" is chosen,
    # so only one UpdateListen is in-flight at a time.
    async def run_all_scenarios():
        if args.scenario in ("all", "eot_sweep"):
            await sc.eot_threshold_sweep(session, metrics)
        if args.scenario in ("all", "keyterm"):
            await sc.keyterm_injection(
                session, metrics,
                keyterm=args.keyterm,
                reference_phrase=args.reference,
            )
        if args.scenario in ("all", "combined"):
            await sc.combined_update(session, metrics)

    scenario_tasks = [run_all_scenarios()]

    log.info("Starting session (duration=%.0f s, scenarios=%s)", args.duration, args.scenario)

    # Run the session and scenario tasks concurrently.
    # Scenarios interact with the session object directly (via send_update_listen).
    # The session.run() coroutine handles audio streaming + receive loop.
    async def run_scenarios():
        # Small initial delay to let the session handshake complete
        await asyncio.sleep(3.0)
        await asyncio.gather(*scenario_tasks, return_exceptions=True)

    await asyncio.gather(
        session.run(duration_s=args.duration),
        run_scenarios(),
        return_exceptions=True,
    )

    summary = metrics.finalize()
    print_summary_table(summary)
    log.info("Done. Results written to results/")


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
