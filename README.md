# Deepgram Voice Agent — UpdateListen Test Harness

An open-source test harness that evaluates whether the Deepgram Voice Agent API's
**mid-call `UpdateListen` reconfiguration** works as claimed: changing STT provider
settings (end-of-turn sensitivity, keyword boosting) live inside a call, with no
audio glitch and no session restart.

> **Status: complete with real measured results.**  
> All three `UpdateListen` scenarios have been validated — including a live Plivo phone call.
> See [Results](#results) for numbers and [Quick Start](#quick-start-5-minutes) for the fastest path to run it yourself.

---

## Table of Contents

1. [Quick Start (5 minutes)](#quick-start-5-minutes)
2. [Background](#background)
3. [Repo structure](#repo-structure)
4. [Setup](#setup)
5. [Audio source](#audio-source)
6. [Running the tests](#running-the-tests)
7. [Plivo telephony bridge](#plivo-telephony-bridge)
8. [Metrics logged](#metrics-logged)
9. [Results](#results)
10. [Claims vs. measured](#claims-vs-measured)
11. [What we did NOT test](#what-we-did-not-test)
12. [Contributing](#contributing)

---

## Quick Start (5 minutes)

> **No coding experience required for this section.** Just follow the steps in order.

### What you need before starting

- A computer running macOS or Linux
- A [Deepgram account](https://console.deepgram.com/) — sign up free, get an API key
- A [Groq account](https://console.groq.com/) — sign up free, get an API key
- Python 3.10 or newer (`python3 --version` to check)

### Step 1 — Download the code

```bash
git clone https://github.com/plivodev/deepgram-update-listen-harness.git
cd deepgram-update-listen-harness
```

### Step 2 — Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3 — Add your API keys

Create a file called `.env` in the project folder and paste this in, replacing the placeholders:

```
DEEPGRAM_API_KEY=your_deepgram_key_here
GROQ_API_KEY=your_groq_key_here
```

### Step 4 — Run the smoke test (30 seconds, no audio needed)

```bash
python3 -m harness.main --audio silence --scenario eot_sweep --duration 60
```

You'll see output like this — it means everything is working:

```
═══════════════════════════════════════════════════
  UpdateListen Scenario Results
═══════════════════════════════════════════════════
  eot  eager    rt_ms  ack  errs
  0.5   0.45    314    ✓      0
  0.7   0.65    371    ✓      0
  0.9   0.85    338    ✓      0
═══════════════════════════════════════════════════
```

Results are saved automatically to the `results/` folder.

### Step 5 — Run with real speech (optional but recommended)

```bash
python3 -m harness.main --audio audio/tts_conversation.wav --scenario all --duration 180
```

This streams a pre-recorded conversation through the AI agent and measures keyterm
boosting accuracy alongside the eot sweep.

### Step 6 — Run from a real phone call (advanced)

See [Plivo telephony bridge](#plivo-telephony-bridge) below.

---

## Background

Deepgram's Voice Agent API exposes a `UpdateListen` WebSocket message that lets a
caller change STT configuration mid-call without tearing down the session:

```json
{
  "type": "UpdateListen",
  "listen": {
    "provider": {
      "type": "deepgram",
      "eot_threshold": 0.9,
      "eager_eot_threshold": 0.85,
      "keyterms": ["Plivo"]
    }
  }
}
```

The server acknowledges with `{"type": "ListenUpdated"}`.

Deepgram claims this reconfiguration is **seamless** — no restart, no dropped audio,
no glitch.  This harness measures whether that claim holds.

---

## Repo structure

```
.
├── harness/
│   ├── __init__.py
│   ├── session.py      # WebSocket session manager (raw websockets, not SDK)
│   ├── scenarios.py    # Test scenario implementations
│   ├── metrics.py      # Metrics collection & export (JSONL + JSON + CSV)
│   └── main.py         # CLI entry point
├── audio/
│   ├── test_speech.wav         # Synthetic 30-s test audio (generated)
│   ├── generate_test_wav.py    # Regenerate the WAV
│   └── README.md               # Audio source documentation
├── results/
│   ├── schema.md               # Field-by-field schema for output files
│   └── (run_*.jsonl / .json / .csv written here at runtime)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Setup

### Prerequisites

- Python 3.10+
- A [Deepgram API key](https://console.deepgram.com/) with Voice Agent access
- A [Groq API key](https://console.groq.com/) (used for the LLM "think" provider — model: `llama-3.3-70b-versatile`)

### Install

```bash
git clone https://github.com/YOUR_ORG/deepgram-update-listen-harness.git
cd deepgram-update-listen-harness
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
export DEEPGRAM_API_KEY="your_deepgram_key_here"
export GROQ_API_KEY="your_groq_key_here"
```

Or copy `.env` and fill in `DEEPGRAM_API_KEY`:

```bash
cp .env .env.local
# edit .env.local, then:
source .env.local
```

Never commit API keys — `.env` is in `.gitignore`.

---

## Audio source

The harness supports three audio input modes:

| Mode | Flag | Description |
|------|------|-------------|
| WAV file (default) | `--audio audio/test_speech.wav` | Streams a pre-recorded WAV, looped. See `audio/README.md`. |
| Live microphone | `--audio mic` | Requires `sounddevice` (`pip install sounddevice`). |
| Silence | `--audio silence` | Sends silent frames — useful for connectivity smoke-tests. |

**Audio used in the published results:**
> Synthetic audio (`audio/test_speech.wav` — alternating 2 s silence / 2 s 440 Hz tone,
> 30 s duration, mono 16-bit 16 kHz).  A Plivo SIP trunk was **not** available in the
> test environment; if you have one, pass `--audio mic` while on a live call or supply
> a WAV exported from a real phone call.  See `audio/README.md` for details and caveats.

---

## Running the tests

### Quick smoke-test (no audio file needed)

```bash
python3 -m harness.main --audio silence --scenario eot_sweep --duration 60
```

### Full test suite (all three scenarios, WAV file)

```bash
python3 -m harness.main \
  --audio audio/test_speech.wav \
  --scenario all \
  --duration 180 \
  --label my_run
```

### Individual scenarios

```bash
# Scenario 1 — eot_threshold sweep (0.5 → 0.7 → 0.9)
python3 -m harness.main --audio audio/test_speech.wav --scenario eot_sweep

# Scenario 2 — keyterm injection
python3 -m harness.main --audio audio/test_speech.wav \
  --scenario keyterm \
  --keyterm "Plivo" \
  --reference "I am testing Plivo voice integration"

# Scenario 3 — combined update (both eot_threshold + keyterms in one message)
python3 -m harness.main --audio audio/test_speech.wav --scenario combined
```

### Live mic (macOS/Linux)

```bash
pip install sounddevice
python3 -m harness.main --audio mic --scenario all --duration 180
```

### All CLI options

```
--audio PATH|mic|silence    Audio source (default: audio/test_speech.wav)
--scenario SCENARIO         all | eot_sweep | keyterm | combined (default: all)
--keyterm WORD              Keyterm for scenario 2 (default: Plivo)
--reference PHRASE          Reference phrase containing the keyterm
--duration SECONDS          Total session length (default: 120)
--log-level LEVEL           DEBUG | INFO | WARNING | ERROR (default: INFO)
--label STRING              Appended to output file names
--system-prompt TEXT        Agent system prompt
--greeting TEXT             Agent greeting utterance
```

---

## Plivo telephony bridge

The `bridge/` folder contains a FastAPI server that connects a live Plivo phone call to
the Deepgram Voice Agent, automatically running the UpdateListen eot sweep during the call.

### What you need

- A [Plivo account](https://www.plivo.com/) with a phone number
- [ngrok](https://ngrok.com/) (free) to expose your local server to the internet
- The `.env` file with `DEEPGRAM_API_KEY`, `GROQ_API_KEY`, and `WEBHOOK_BASE_URL`

### Step-by-step

**Terminal 1 — start the bridge server:**
```bash
source .venv/bin/activate
uvicorn bridge.server:app --port 5000
```

**Terminal 2 — expose it to the internet:**
```bash
ngrok http 5000
# Copy the https://xxxx.ngrok-free.app URL shown
```

**Add to your `.env`:**
```
WEBHOOK_BASE_URL=https://xxxx.ngrok-free.app
```
Then restart the bridge server (Ctrl+C and re-run).

**In Plivo console:**
- Go to your phone number's settings
- Set Answer URL to: `https://xxxx.ngrok-free.app/answer` (POST)
- Set Hangup URL to: `https://xxxx.ngrok-free.app/hangup` (POST)

**Place the call:**  
Call your Plivo number. The agent will greet you, then the bridge will automatically send
three `UpdateListen` messages (eot=0.5, 0.7, 0.9) at 20-second intervals.  
Results are saved to `results/live_call_{uuid}.json` when the call ends.

---

## Metrics logged

All output files are written to `results/`.  See `results/schema.md` for the
complete field reference.

| Metric | Source |
|--------|--------|
| `round_trip_ms` | `time(UpdateListen sent)` → `time(ListenUpdated received)` |
| `ack_received` | Whether `ListenUpdated` arrived within 5 s timeout |
| `baseline_mean_ms` | Mean `total_latency` from `AgentStartedSpeaking` events before update |
| `post_update_total_latency` | `total_latency` from first `AgentStartedSpeaking` after ack |
| `post_update_tts_latency` | `tts_latency` from same event |
| `post_update_ttt_latency` | `ttt_latency` from same event |
| `latency_delta_ms` | `post_update_total_latency − baseline_mean_ms` |
| `errors_in_window` | Count of `Error`/`Warning` events within 5 s of UpdateListen |
| `pre_recall` | Fraction of user utterances containing keyterm **before** injection |
| `post_recall` | Fraction of user utterances containing keyterm **after** injection |
| `recall_delta` | `post_recall − pre_recall` |
| `subjective_note` | Human annotation about turn-taking feel per threshold (eot_sweep) |

---

## Results

> **These results will be populated when you run the harness with a real
> Deepgram API key and real (or synthetic) audio.  The table below is a
> template; figures marked `TBD` must be replaced with actual measured values.**

Two runs were performed: one with **synthetic audio** (idle baseline, no agent activity) and one with **TTS speech audio** (macOS `say` voice, real conversations). Results are presented for both.

---

### Scenario 1 — eot_threshold sweep

#### Run A — Synthetic audio (idle baseline)
> Date: 2026-07-16 · Audio: `test_speech.wav` (440 Hz sine, no speech) · Model: `flux-general-en` + Groq `llama-3.3-70b-versatile`

| eot | eager_eot | RT ack (ms) | Ack | Errors | Notes |
|:---:|:---------:|:-----------:|:---:|:------:|-------|
| 0.50 | 0.45 | **313.9** | ✅ | 0 | Ack prompt — no competing agent activity |
| 0.70 | 0.65 | **371.1** | ✅ | 0 | Ack prompt |
| 0.90 | 0.85 | **338.2** | ✅ | 0 | Ack prompt |

Idle baseline: **mean 341 ms**, min 314 ms, max 371 ms.

#### Run B — TTS speech audio (real conversations, macOS Samantha voice)
> Date: 2026-07-16 · Audio: `tts_conversation.wav` (38 s, looped) · 44 user utterances captured

| eot | eager_eot | Sent at | Ack at | RT ack (ms) | Ack | Errors |
|:---:|:---------:|:-------:|:------:|:-----------:|:---:|:------:|
| 0.50 | 0.45 | t+16.9 s | t+23.8 s | **6,876** | ✅ | 0 |
| 0.70 | 0.65 | t+36.9 s | t+55.1 s | **18,188** | ✅ | 0 |
| 0.90 | 0.85 | t+56.9 s | t+61.4 s | **4,454** | ✅ | 0 |

Under real load: **mean 9,839 ms** — acks are delayed because the server queues them behind in-flight speech processing. All eventually arrived; no errors.

> **Subjective turn-taking observation (TTS audio):** The TTS voice sentences are short and clearly delimited, so turn boundaries were generally clean at all three thresholds — a human trial with continuous overlapping speech would be needed to distinguish the thresholds perceptually.

#### Run C — Live Plivo phone call (real human speaker, telephony path)
> Date: 2026-07-16 · Call UUID: `f2f702f9-7673-46fe-840c-e43f7f2f8209`  
> Audio: live human voice over Plivo SIP trunk → µ-law 8kHz → bridge → PCM16 16kHz  
> Full telephony stack: caller phone → Plivo PSTN → Plivo WebSocket → bridge → Deepgram Voice Agent

| eot | eager_eot | RT ack (ms) | Ack | Errors | Conversation turns |
|:---:|:---------:|:-----------:|:---:|:------:|:------------------:|
| 0.50 | 0.45 | **433** | ✅ | 0 | Active (UserStartedSpeaking detected) |
| 0.70 | 0.65 | **343** | ✅ | 0 | Active (multiple conversation turns) |
| 0.90 | 0.85 | **244** | ✅ | 0 | Active (agent responding naturally) |

Live call summary: **mean 340 ms**, min 244 ms, max 433 ms — comparable to the idle synthetic baseline.
All three acks arrived well within 1 second. Zero errors. The full session log is at
[`results/live_call_f2f702f9-7673-46fe-840c-e43f7f2f8209.json`](results/live_call_f2f702f9-7673-46fe-840c-e43f7f2f8209.json).

> **Subjective observation (live call):** The human caller could interact naturally with the agent throughout all three eot phases. The agent responded appropriately and the audio quality was clear over the Plivo telephony path. No perceivable audio glitch at any reconfiguration point.

---

### Scenario 2 — keyterm injection

> Date: 2026-07-16 · Keyterm: `Plivo` · Audio: `tts_conversation.wav` (macOS Samantha voice saying "Plivo" repeatedly)
> `UpdateListen(keyterms=["Plivo"])` sent at t+86.4 s · `ListenUpdated` ack at t+88.7 s (RT: **2,274 ms**)

| Window | Utterances | Correct "Plivo" | Recall | Mis-transcriptions observed |
|:------:|:----------:|:---------------:|:------:|-----------------------------|
| **Pre-injection** (t=0 → t+86 s) | 15 | 5 | **33%** | "PLEVO", "Plevo", "Playvovo", "Flavio", "Plivovoice" |
| **Post-injection** (t+86 s → end) | 28 | 21 | **75%** | "Plevo" (×3), "Plivo voice" merged (×1) |
| **Δ recall** | | | **+42 pp** | Keyterm boosting measurably improved recognition |

**Keyterm injection works.** Before `UpdateListen`, the model transcribed the TTS voice saying "Plivo" as "PLEVO", "Playvovo", or "Flavio" — all wrong. After injection the same audio loop was correctly transcribed as "Plivo" in 75% of utterances (up from 33%). Zero errors in the 5 s window around the update.

---

### Scenario 3 — combined update (eot_threshold + keyterms in one message)

> Date: 2026-07-16 · `eot_threshold=0.6` + `keyterms=["Plivo"]` sent simultaneously

| eot | eager_eot | Keyterm | Sent at | Ack at | RT ack (ms) | Ack | Errors | Single ack? |
|:---:|:---------:|:-------:|:-------:|:------:|:-----------:|:---:|:------:|:-----------:|
| 0.60 | 0.55 | Plivo | t+108.7 s | t+115.1 s | **6,428** | ✅ | 0 | ✅ exactly 1 `ListenUpdated` |

Combined update confirmed: one message, one ack. No double-firing.

---

## Claims vs. measured

Deepgram's documentation states that `UpdateListen` enables
**"seamless mid-call reconfiguration"** with no session restart and no audio glitch.

The table below maps each claim to a specific harness measurement.  Fill in
`Outcome` once you have real results.

| Deepgram claim | Harness measurement | Outcome |
|----------------|---------------------|---------|
| `ListenUpdated` ack is always returned | All 8 `UpdateListen` calls across both runs eventually received a `ListenUpdated` ack | ✅ **CONFIRMED** — ack always arrived eventually |
| Reconfiguration is seamless / prompt | Idle session: mean **341 ms**. TTS load: mean **9,839 ms** (range 2–18 s). Live call: mean **340 ms** | ⚠️ **PARTIALLY CONFIRMED** — acks are prompt when the agent is idle or between turns (~340 ms); significantly delayed when the agent is mid-utterance. Not instant as "seamless" implies, but recovers quickly. |
| No session restart required | Single WebSocket stayed alive through all 8 updates across two full runs (4,161 events, ~4 min session) | ✅ **CONFIRMED** — no reconnection required |
| No audio glitch / errors at reconfiguration point | `errors_in_window` = 0 for all 8 `UpdateListen` calls in both runs | ✅ **CONFIRMED** — zero errors, zero `POOR_AUDIO_QUALITY` warnings |
| keyterms boost recognition of injected terms | Recall of "Plivo" rose from **33% → 75% (+42 pp)** after `UpdateListen(keyterms=["Plivo"])` in the TTS run | ✅ **CONFIRMED** — measurable improvement within the same call. Pre-injection mis-transcriptions: "PLEVO", "Playvovo", "Flavio". |
| eot_threshold changes affect turn-taking | Timing of agent response relative to eot value | ⚠️ **PARTIALLY OBSERVED** — TTS audio produced clean short turns; perceptual difference between 0.5/0.7/0.9 requires a human live-mic trial to assess |
| Combined eot + keyterms update produces single ack | Exactly 1 `ListenUpdated` received for the combined message | ✅ **CONFIRMED** — one message → one ack |

---

## What we did NOT test

- **UpdateThink / UpdateSpeak / UpdatePrompt** — out of scope; changing the LLM
  or TTS provider mid-call is a separate capability.
- **InjectUserMessage / InjectAgentMessage** — not used in this harness.
- **Multi-language switching** — `language_hints` field was not exercised.
- **Concurrent UpdateListen calls** — we send one update at a time and wait for ack.
- **Production call volumes** — this harness tests a single session at a time.
- **Specific telephony carriers** — Plivo SIP trunk was not available in the
  initial test environment.  Results are from a direct WebSocket connection, not
  an inbound phone call.
- **Non-Deepgram STT providers** — `UpdateListen` was only tested with
  `"type": "deepgram"` as the listen provider.
- **eot_timeout_ms** — this parameter was not swept independently (could be a
  future scenario).

---

## Implementation notes

- **Raw WebSockets, not the SDK** — We use the `websockets` library directly
  rather than the Deepgram Python SDK.  This gives sub-millisecond timestamp
  precision on every send and receive, which the SDK's abstraction would obscure.
  The SDK is perfectly valid for production use; raw WS is better for measurement.
- **LLM think provider** — uses Groq `llama-3.3-70b-versatile` via Deepgram's
  hosted routing.  Edit `harness/session.py:_build_settings` to switch to
  another provider Deepgram supports.
- **Audio chunking** — 20 ms frames (640 bytes) sent at real-time rate, matching
  the typical SIP RTP packet cadence.

---

## Contributing

Issues and PRs welcome.  If you have access to a Plivo trunk or another real
telephony audio source, adding results from that environment would be especially
valuable.

Please do not commit API keys.  The `.gitignore` excludes `.env` and `*.key` files.

---

## License

MIT
