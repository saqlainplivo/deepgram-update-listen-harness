"""
Metrics collection, aggregation, and export for the UpdateListen harness.

Writes two output files per run:
  results/run_<timestamp>_events.jsonl  — raw event log (one JSON object per line)
  results/run_<timestamp>_metrics.json  — structured summary
  results/run_<timestamp>_metrics.csv   — CSV summary for quick spreadsheet review
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent.parent / "results"


# ──────────────────────────────────────────────────────────────────────────────
# Data classes for individual measurements
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TurnSnapshot:
    """Metrics captured around one UpdateListen(eot_threshold=...) call."""
    scenario:                str
    eot_threshold:           float
    eager_eot_threshold:     float
    sent_ts:                 float
    ack_ts:                  Optional[float]
    round_trip_ms:           Optional[float]
    baseline_latencies:      list[float]      = field(default_factory=list)
    post_update_total_latency: Optional[float] = None
    post_update_tts_latency:   Optional[float] = None
    post_update_ttt_latency:   Optional[float] = None
    errors_in_window:        list[dict]        = field(default_factory=list)
    subjective_note:         str               = ""

    def baseline_mean_ms(self) -> Optional[float]:
        if not self.baseline_latencies:
            return None
        return statistics.mean(self.baseline_latencies)

    def latency_delta_ms(self) -> Optional[float]:
        bm = self.baseline_mean_ms()
        if bm is None or self.post_update_total_latency is None:
            return None
        return self.post_update_total_latency - bm


@dataclass
class KeytermAccuracySample:
    """Metrics captured around one UpdateListen(keyterms=[...]) call."""
    scenario:            str
    keyterm:             str
    reference_phrase:    str
    sent_ts:             float
    ack_ts:              Optional[float]
    round_trip_ms:       Optional[float]
    pre_utterance_count:  int
    pre_hit_count:        int
    post_utterance_count: int
    post_hit_count:       int
    errors_in_window:    list[dict]  = field(default_factory=list)

    def pre_recall(self) -> Optional[float]:
        if self.pre_utterance_count == 0:
            return None
        return self.pre_hit_count / self.pre_utterance_count

    def post_recall(self) -> Optional[float]:
        if self.post_utterance_count == 0:
            return None
        return self.post_hit_count / self.post_utterance_count

    def recall_delta(self) -> Optional[float]:
        pre  = self.pre_recall()
        post = self.post_recall()
        if pre is None or post is None:
            return None
        return post - pre


# ──────────────────────────────────────────────────────────────────────────────
# Collector
# ──────────────────────────────────────────────────────────────────────────────

class MetricsCollector:
    def __init__(self, run_label: str = ""):
        ts_str = time.strftime("%Y%m%dT%H%M%S")
        slug   = f"{ts_str}_{run_label}" if run_label else ts_str
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._events_path  = RESULTS_DIR / f"run_{slug}_events.jsonl"
        self._metrics_path = RESULTS_DIR / f"run_{slug}_metrics.json"
        self._csv_path     = RESULTS_DIR / f"run_{slug}_metrics.csv"
        self._turns:    list[TurnSnapshot]        = []
        self._keyterms: list[KeytermAccuracySample] = []
        self._events_fh = self._events_path.open("w")
        log.info("Events log: %s", self._events_path)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def record_event(self, evt) -> None:
        """Write a raw SessionEvent to the JSONL log."""
        line = {"ts": evt.ts, "direction": evt.direction, "type": evt.msg_type}
        if evt.msg_type != "AudioBytes":   # skip noise
            line["payload"] = evt.payload
        self._events_fh.write(json.dumps(line) + "\n")
        self._events_fh.flush()

    def add_turn_snapshot(self, snap: TurnSnapshot) -> None:
        self._turns.append(snap)

    def add_keyterm_sample(self, sample: KeytermAccuracySample) -> None:
        self._keyterms.append(sample)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def finalize(self) -> dict:
        """Write JSON + CSV summary. Returns the summary dict."""
        self._events_fh.close()

        summary = self._build_summary()
        self._metrics_path.write_text(json.dumps(summary, indent=2))
        log.info("Metrics JSON: %s", self._metrics_path)

        self._write_csv(summary)
        log.info("Metrics CSV:  %s", self._csv_path)

        return summary

    def _build_summary(self) -> dict:
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "turn_snapshots": [self._turn_to_dict(t) for t in self._turns],
            "keyterm_samples": [self._keyterm_to_dict(k) for k in self._keyterms],
            "aggregate": self._aggregate(),
        }

    def _turn_to_dict(self, t: TurnSnapshot) -> dict:
        d = asdict(t)
        d["baseline_mean_ms"]  = t.baseline_mean_ms()
        d["latency_delta_ms"]  = t.latency_delta_ms()
        d["error_count"]       = len(t.errors_in_window)
        return d

    def _keyterm_to_dict(self, k: KeytermAccuracySample) -> dict:
        d = asdict(k)
        d["pre_recall"]    = k.pre_recall()
        d["post_recall"]   = k.post_recall()
        d["recall_delta"]  = k.recall_delta()
        d["error_count"]   = len(k.errors_in_window)
        return d

    def _aggregate(self) -> dict:
        rt_values = [t.round_trip_ms for t in self._turns if t.round_trip_ms is not None]
        rt_kt     = [k.round_trip_ms for k in self._keyterms if k.round_trip_ms is not None]
        all_rt    = rt_values + rt_kt

        agg: dict = {
            "update_listen_round_trip_ms": _stats(all_rt),
            "eot_sweep": {},
            "keyterm": {},
        }

        for t in self._turns:
            key = f"eot_{t.eot_threshold}"
            agg["eot_sweep"][key] = {
                "round_trip_ms":             t.round_trip_ms,
                "baseline_mean_latency_ms":  t.baseline_mean_ms(),
                "post_update_total_latency": t.post_update_total_latency,
                "latency_delta_ms":          t.latency_delta_ms(),
                "errors":                    len(t.errors_in_window),
                "ack_received":              t.ack_ts is not None,
                "subjective_note":           t.subjective_note,
            }

        for k in self._keyterms:
            agg["keyterm"][k.keyterm] = {
                "round_trip_ms":     k.round_trip_ms,
                "pre_recall":        k.pre_recall(),
                "post_recall":       k.post_recall(),
                "recall_delta":      k.recall_delta(),
                "errors":            len(k.errors_in_window),
                "ack_received":      k.ack_ts is not None,
            }

        return agg

    def _write_csv(self, summary: dict) -> None:
        rows = []
        for t in summary["turn_snapshots"]:
            rows.append({
                "type":              "eot_sweep",
                "scenario":          t["scenario"],
                "eot_threshold":     t["eot_threshold"],
                "eager_eot":         t["eager_eot_threshold"],
                "round_trip_ms":     t.get("round_trip_ms", ""),
                "ack_received":      t["ack_ts"] is not None,
                "baseline_mean_ms":  t.get("baseline_mean_ms", ""),
                "post_total_lat_ms": t.get("post_update_total_latency", ""),
                "post_tts_lat_ms":   t.get("post_update_tts_latency", ""),
                "post_ttt_lat_ms":   t.get("post_update_ttt_latency", ""),
                "latency_delta_ms":  t.get("latency_delta_ms", ""),
                "errors_in_window":  t.get("error_count", 0),
                "subjective_note":   t.get("subjective_note", ""),
            })
        for k in summary["keyterm_samples"]:
            rows.append({
                "type":              "keyterm",
                "scenario":          k["scenario"],
                "keyterm":           k["keyterm"],
                "round_trip_ms":     k.get("round_trip_ms", ""),
                "ack_received":      k["ack_ts"] is not None,
                "pre_utterances":    k["pre_utterance_count"],
                "pre_hits":          k["pre_hit_count"],
                "pre_recall":        k.get("pre_recall", ""),
                "post_utterances":   k["post_utterance_count"],
                "post_hits":         k["post_hit_count"],
                "post_recall":       k.get("post_recall", ""),
                "recall_delta":      k.get("recall_delta", ""),
                "errors_in_window":  k.get("error_count", 0),
            })

        if not rows:
            return

        fieldnames = sorted({k for r in rows for k in r})
        with self._csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n":      len(values),
        "min":    min(values),
        "max":    max(values),
        "mean":   statistics.mean(values),
        "median": statistics.median(values),
        "stdev":  statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def print_summary_table(summary: dict) -> None:
    """Pretty-print a results summary table to stdout."""
    agg = summary.get("aggregate", {})

    print("\n" + "═" * 70)
    print("  UpdateListen Test Harness — Results Summary")
    print("═" * 70)

    # EOT sweep table
    eot = agg.get("eot_sweep", {})
    if eot:
        print("\n┌─ Scenario 1: eot_threshold sweep ─────────────────────────────────┐")
        print(f"  {'eot':>5}  {'rt_ms':>8}  {'ack':>5}  {'base_ms':>8}  {'post_ms':>8}  {'Δms':>7}  {'errs':>4}")
        print("  " + "─" * 60)
        for key, v in sorted(eot.items()):
            eot_val   = key.replace("eot_", "")
            rt        = f"{v['round_trip_ms']:.1f}" if v["round_trip_ms"] else "—"
            ack       = "✓" if v["ack_received"] else "✗"
            base      = f"{v['baseline_mean_latency_ms']:.0f}" if v["baseline_mean_latency_ms"] else "—"
            post      = f"{v['post_update_total_latency']:.0f}" if v["post_update_total_latency"] else "—"
            delta     = f"{v['latency_delta_ms']:+.0f}" if v["latency_delta_ms"] is not None else "—"
            errs      = str(v["errors"])
            print(f"  {eot_val:>5}  {rt:>8}  {ack:>5}  {base:>8}  {post:>8}  {delta:>7}  {errs:>4}")
        print()

    # Keyterm table
    kt = agg.get("keyterm", {})
    if kt:
        print("┌─ Scenario 2: keyterm injection ────────────────────────────────────┐")
        print(f"  {'keyterm':>20}  {'rt_ms':>8}  {'ack':>5}  {'pre_R':>6}  {'post_R':>7}  {'ΔR':>6}  {'errs':>4}")
        print("  " + "─" * 64)
        for kw, v in kt.items():
            rt   = f"{v['round_trip_ms']:.1f}" if v["round_trip_ms"] else "—"
            ack  = "✓" if v["ack_received"] else "✗"
            pre  = f"{v['pre_recall']:.0%}" if v["pre_recall"] is not None else "—"
            post = f"{v['post_recall']:.0%}" if v["post_recall"] is not None else "—"
            delt = f"{v['recall_delta']:+.0%}" if v["recall_delta"] is not None else "—"
            errs = str(v["errors"])
            print(f"  {kw:>20}  {rt:>8}  {ack:>5}  {pre:>6}  {post:>7}  {delt:>6}  {errs:>4}")
        print()

    rt_stats = agg.get("update_listen_round_trip_ms", {})
    if rt_stats.get("n", 0) > 0:
        print(f"Overall UpdateListen round-trip: "
              f"mean={rt_stats['mean']:.1f} ms  "
              f"min={rt_stats['min']:.1f}  "
              f"max={rt_stats['max']:.1f}  "
              f"n={rt_stats['n']}")
    print("═" * 70 + "\n")
