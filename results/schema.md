# Results Schema

Each test run produces three files in this directory:

```
run_<YYYYMMDDTHHMMSS>_<label>_events.jsonl   # raw event log
run_<YYYYMMDDTHHMMSS>_<label>_metrics.json   # structured summary
run_<YYYYMMDDTHHMMSS>_<label>_metrics.csv    # flat CSV for spreadsheets
```

---

## events.jsonl

One JSON object per line, representing every WebSocket message sent or
received during the session (audio bytes are collapsed to byte counts).

```jsonc
{
  "ts":        1720000000.123,   // Unix epoch seconds (float)
  "direction": "rx" | "tx",
  "type":      "AgentStartedSpeaking",   // Deepgram message type
  "payload":   { ... }                   // full message body
}
```

Key `type` values (rx = from server):

| type | direction | Notes |
|------|-----------|-------|
| `Settings` | tx | Initial configuration sent on connect |
| `UpdateListen` | tx | Mid-call reconfiguration message |
| `ListenUpdated` | rx | Acknowledgement of UpdateListen |
| `AgentStartedSpeaking` | rx | Includes `total_latency`, `tts_latency`, `ttt_latency` (ms) |
| `AgentThinking` | rx | LLM processing started |
| `AgentAudioDone` | rx | TTS audio stream complete |
| `ConversationText` | rx | Transcript with `role` = "user" or "assistant" |
| `UserStartedSpeaking` | rx | VAD detected user speech |
| `Error` | rx | Error code and message |
| `Warning` | rx | Non-fatal warning (e.g. POOR_AUDIO_QUALITY) |
| `AudioBytes` | rx | Condensed: `{"bytes": N}` — TTS audio payload |

---

## metrics.json

Top-level structure:

```jsonc
{
  "generated_at": "2026-07-14T10:00:00Z",

  "turn_snapshots": [
    {
      "scenario":                "eot_sweep",
      "eot_threshold":           0.5,
      "eager_eot_threshold":     0.45,
      "sent_ts":                 1720000010.0,
      "ack_ts":                  1720000010.073,
      "round_trip_ms":           73.4,          // null if ack timed out
      "baseline_latencies":      [340, 290, 310],
      "baseline_mean_ms":        313.3,
      "post_update_total_latency": 295.0,       // from next AgentStartedSpeaking
      "post_update_tts_latency":   120.0,
      "post_update_ttt_latency":   175.0,
      "latency_delta_ms":        -18.3,         // post - baseline_mean
      "error_count":             0,
      "errors_in_window":        [],
      "subjective_note":         "..."
    }
  ],

  "keyterm_samples": [
    {
      "scenario":            "keyterm_injection",
      "keyterm":             "Plivo",
      "reference_phrase":    "I am testing Plivo voice integration",
      "sent_ts":             1720000030.0,
      "ack_ts":              1720000030.068,
      "round_trip_ms":       68.1,
      "pre_utterance_count": 5,
      "pre_hit_count":       1,
      "pre_recall":          0.2,               // 1/5
      "post_utterance_count": 4,
      "post_hit_count":      3,
      "post_recall":         0.75,              // 3/4
      "recall_delta":        0.55,              // post - pre
      "error_count":         0,
      "errors_in_window":    []
    }
  ],

  "aggregate": {
    "update_listen_round_trip_ms": {
      "n": 4, "min": 62.1, "max": 89.3, "mean": 73.2,
      "median": 72.5, "stdev": 11.1
    },
    "eot_sweep": {
      "eot_0.5": { "round_trip_ms": 73.4, "ack_received": true,
                   "baseline_mean_latency_ms": 313.3,
                   "post_update_total_latency": 295.0,
                   "latency_delta_ms": -18.3,
                   "errors": 0,
                   "subjective_note": "..." },
      "eot_0.7": { ... },
      "eot_0.9": { ... }
    },
    "keyterm": {
      "Plivo": { "round_trip_ms": 68.1, "pre_recall": 0.2,
                 "post_recall": 0.75, "recall_delta": 0.55,
                 "errors": 0, "ack_received": true }
    }
  }
}
```

---

## metrics.csv

Flat CSV with columns drawn from both `turn_snapshots` and
`keyterm_samples` (columns not applicable to a row are left empty).

Key columns:

| Column | Meaning |
|--------|---------|
| `type` | `eot_sweep` or `keyterm` |
| `round_trip_ms` | UpdateListen → ListenUpdated latency (ms) |
| `ack_received` | `True` if ListenUpdated was received within timeout |
| `baseline_mean_ms` | Mean total_latency before the update (ms) |
| `post_total_lat_ms` | total_latency from next AgentStartedSpeaking (ms) |
| `latency_delta_ms` | `post - baseline` (negative = faster) |
| `pre_recall` | Keyterm hit rate before injection |
| `post_recall` | Keyterm hit rate after injection |
| `recall_delta` | `post - pre` |
| `errors_in_window` | Count of Error/Warning events within 5 s of UpdateListen |
| `subjective_note` | Human annotation for eot_threshold behaviour |

---

## Interpreting null / missing values

- `round_trip_ms = null` — the `ListenUpdated` ack did not arrive
  within the 5-second timeout.  This is a **test failure** for the
  "seamless mid-call reconfiguration" claim.

- `post_update_total_latency = null` — no `AgentStartedSpeaking`
  event was received after the update (agent may not have spoken yet
  during the observation window).

- `pre_recall = null` / `post_recall = null` — no user utterances
  were transcribed in that window.  This is expected when running with
  synthetic (non-speech) audio.
