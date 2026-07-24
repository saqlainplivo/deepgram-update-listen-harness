# Deepgram Voice Agent — UpdateListen Test Harness

An open-source test harness that evaluates whether the Deepgram Voice Agent API's
**mid-call `UpdateListen` reconfiguration** works as claimed: changing STT provider
settings (end-of-turn sensitivity, keyword boosting) live inside a call, with no
audio glitch and no session restart.

> **Status: complete with real measured results.**  
> Nine scenarios tested end-to-end — including a live Plivo phone call. Covers `UpdateListen` (eot sweep, keyterms, concurrent, eot_timeout_ms), `UpdateSpeak`, `InjectAgentMessage`, and two API gaps (`UpdateThink`, `InjectUserMessage` — not supported by current endpoint).  
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
git clone https://github.com/saqlainplivo/deepgram-update-listen-harness.git
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
git clone https://github.com/saqlainplivo/deepgram-update-listen-harness.git
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
> Four audio sources were used across runs: synthetic silence (smoke tests), `test_speech.wav` (440 Hz sine / silence, 30 s), `tts_conversation.wav` (macOS Samantha TTS, 38 s conversational speech about Plivo), and `varied_pauses.wav` (Samantha TTS with 0.3 s / 0.8 s / 1.5 s inter-sentence pauses for eot stress testing). Run C used a live Plivo SIP call with a human speaker. See `audio/README.md` for details.

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
--scenario SCENARIO         all | eot_sweep | keyterm | combined |
                            eot_timeout_sweep | concurrent | update_think |
                            update_speak | inject_agent | inject_user |
                            all_extended  (default: all)
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

Three runs were performed: one with **synthetic audio** (idle baseline, no agent activity) and one with **TTS speech audio** (macOS `say` voice, real conversations). Results are presented for both.

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

### Extended Scenarios — Run 2026-07-24

> All extended scenarios run against the Deepgram Voice Agent API.  
> Audio: `tts_conversation.wav` for UpdateListen scenarios; `silence` for all others.  
> Model: `flux-general-en` v2 + Groq `llama-3.3-70b-versatile`

#### Scenario 4 — `eot_timeout_ms` sweep (500 / 1000 / 2000 ms)

> Date: 2026-07-24 · Audio: `tts_conversation.wav` (looped) · Duration: 90 s

| eot_timeout_ms | Sent at | Ack at | RT ack (ms) | Ack | Errors | Notes |
|:--------------:|:-------:|:------:|:-----------:|:---:|:------:|-------|
| 500 | t+18.1 s | t+18.4 s | **306.8** | ✅ | 0 | Fast ack, parameter accepted |
| 1000 | t+33.4 s | t+33.7 s | **304.2** | ✅ | 0 | Fast ack |
| 2000 | t+48.7 s | t+49.0 s | **338.7** | ✅ | 0 | Fast ack |

Mean RT: **316.6 ms**. All three values accepted without error. The parameter is silently absorbed — no observable difference in session behavior at this load level (would require a human live-mic trial to assess perceptual turn-taking differences).

#### Scenario 5 — Concurrent `UpdateListen` (two messages before first ack)

> Date: 2026-07-24 · Audio: `tts_conversation.wav` · Duration: 90 s

| | Sent at | Gap between sends (ms) | Acks received | First ack RT (ms) | Errors |
|-|:-------:|:----------------------:|:-------------:|:-----------------:|:------:|
| Send #1 (eot=0.5) | t+15.0 s | — | — | — | 0 |
| Send #2 (eot=0.9) | t+15.0 s | **1.2 ms** | **1 total** | **271** | 0 |

**Finding:** Deepgram returned only **1** `ListenUpdated` ack for 2 concurrent `UpdateListen` messages sent 1.2 ms apart. The second message silently won (last-write-wins semantics inferred). No error, no session disruption.

#### Scenario 6 — `UpdateThink` mid-call

> Date: 2026-07-24 · Audio: silence · Duration: 60 s

| Sent at | Ack event | RT (ms) | Ack | Status | Error |
|:-------:|:---------:|:-------:|:---:|:------:|-------|
| t+15.0 s | `ThinkUpdated` | — | ✗ | **NOT SUPPORTED** | `INVALID_SETTINGS: Invalid agent.think settings - model not available` |

**Finding:** `UpdateThink` with Groq `llama-3.3-70b-versatile` was rejected by the API with `INVALID_SETTINGS`. The session terminated immediately after the error. Changing the LLM provider mid-call via `UpdateThink` is **not supported** on this endpoint (at least not with a Groq model routed through Deepgram's think provider API at time of test).

#### Scenario 7 — `UpdateSpeak` mid-call

> Date: 2026-07-24 · Audio: silence · Duration: 60 s

| Sent at | Ack event | RT (ms) | Ack | Status | Errors |
|:-------:|:---------:|:-------:|:---:|:------:|:------:|
| t+15.0 s | `SpeakUpdated` | **222.5** | ✅ | OK | 0 |

**Finding:** `UpdateSpeak` (switching TTS voice from `aura-2-asteria-en` to `aura-2-luna-en`) works correctly. Ack arrives in **222 ms** — faster than `UpdateListen`. No errors.

#### Scenario 8 — `InjectAgentMessage`

> Date: 2026-07-24 · Audio: silence · Duration: 60 s

| Sent at | Ack event | RT (ms) | Ack | Status | Errors |
|:-------:|:---------:|:-------:|:---:|:------:|:------:|
| t+15.0 s | `ConversationText` (role=assistant) | **289.0** | ✅ | OK | 0 |

**Finding:** `InjectAgentMessage` works. The injected text `"This is a mid-call injected agent message for testing."` appeared immediately in the conversation as a `ConversationText` event (role=assistant) in **289 ms**, and audio bytes were sent. No `AgentStartedSpeaking` event fired in silence mode (audio is streamed but not routed back as a speaking event), but `ConversationText` confirms the message was processed and spoken.

#### Scenario 9 — `InjectUserMessage`

> Date: 2026-07-24 · Audio: silence · Duration: 60 s

| Sent at | Ack event | RT (ms) | Ack | Status | Error |
|:-------:|:---------:|:-------:|:---:|:------:|-------|
| t+15.0 s | `ConversationText` | — | ✗ | **NOT SUPPORTED** | `UNPARSABLE_CLIENT_MESSAGE: Text message received from client did not match any of the formats we expect.` |

**Finding:** `InjectUserMessage` is **not supported** by the current Deepgram Voice Agent API endpoint. The server rejected the message type with `UNPARSABLE_CLIENT_MESSAGE`, and the session closed immediately.

#### Run D — EOT perceptual stress-test with varied-pause audio (`varied_pauses.wav`)

> Date: 2026-07-24 · Audio: `audio/varied_pauses.wav` (macOS Samantha voice, 25 s, looped)  
> Pause lengths in audio: short (0.3 s), medium (0.8 s), long (1.5 s) between sentences

| eot | eager_eot | RT ack (ms) | Ack | Errors | Notes |
|:---:|:---------:|:-----------:|:---:|:------:|-------|
| 0.50 | 0.45 | — | ✗ | 0 | Timeout — agent was mid-utterance (agent responds to speech) |
| 0.70 | 0.65 | — | ✗ | 0 | Timeout — same as above |
| 0.90 | 0.85 | **342.0** | ✅ | 0 | Ack arrived during a pause segment |

**Finding:** Under active speech from the varied-pause audio, acks at eot=0.5 and 0.7 timed out (5 s default). Only eot=0.9 (sent last, coinciding with a longer pause in the audio loop) received an ack. This mirrors Run B behavior — acks are delayed or dropped when the agent is actively processing speech. The audio file itself is at `audio/varied_pauses.wav`.

---

## Claims vs. measured

Deepgram's documentation states that `UpdateListen` enables
**"seamless mid-call reconfiguration"** with no session restart and no audio glitch.

The table below maps each claim to a specific harness measurement.  Fill in
`Outcome` once you have real results.

| Deepgram claim | Harness measurement | Outcome |
|----------------|---------------------|---------|
| `ListenUpdated` ack is always returned | All 8 `UpdateListen` calls across both runs eventually received a `ListenUpdated` ack | ✅ **CONFIRMED** — ack always arrived eventually |
| Reconfiguration is seamless / prompt | Idle session: mean **341 ms**. TTS load: mean **9,839 ms**, max **18,188 ms**. Live call: mean **340 ms** | ❌ **NOT CONFIRMED under load** — idle and between-turn acks are fast (~340 ms), but under active speech processing the reconfiguration took up to **18 seconds**. That is not seamless by any reasonable definition. Deepgram's claim holds only when the agent has nothing in flight. |
| No session restart required | Single WebSocket stayed alive through all 8 updates across two full runs (4,161 events, ~4 min session) | ✅ **CONFIRMED** — no reconnection required |
| No audio glitch / errors at reconfiguration point | `errors_in_window` = 0 for all 8 `UpdateListen` calls in both runs | ✅ **CONFIRMED** — zero errors, zero `POOR_AUDIO_QUALITY` warnings |
| keyterms boost recognition of injected terms | Recall of "Plivo" rose from **33% → 75% (+42 pp)** after `UpdateListen(keyterms=["Plivo"])` in the TTS run | ✅ **CONFIRMED** — measurable improvement within the same call. Pre-injection mis-transcriptions: "PLEVO", "Playvovo", "Flavio". |
| eot_threshold changes affect turn-taking | Timing of agent response relative to eot value | ⚠️ **PARTIALLY OBSERVED** — TTS audio produced clean short turns; perceptual difference between 0.5/0.7/0.9 requires a human live-mic trial to assess |
| Combined eot + keyterms update produces single ack | Exactly 1 `ListenUpdated` received for the combined message | ✅ **CONFIRMED** — one message → one ack |
| `eot_timeout_ms` can be changed mid-call | Swept 500 / 1000 / 2000 ms; all 3 acks received, mean **317 ms** | ✅ **CONFIRMED** — parameter accepted, ack fast (~317 ms). Perceptual effect requires human trial. |
| `UpdateSpeak` changes TTS voice mid-call | `SpeakUpdated` ack received in **222 ms** switching from `aura-2-asteria-en` to `aura-2-luna-en` | ✅ **CONFIRMED** — voice change works live, ack in 222 ms |
| `UpdateThink` changes LLM mid-call | `ThinkUpdated` not received; server returned `INVALID_SETTINGS` | ❌ **NOT SUPPORTED** — `UpdateThink` with Groq model rejected by current API endpoint |
| `InjectAgentMessage` causes agent to speak injected text | `ConversationText` (role=assistant) received in **289 ms**; audio bytes sent | ✅ **CONFIRMED** — injected message appears in conversation and is spoken |
| `InjectUserMessage` injects a user utterance into conversation | Server returned `UNPARSABLE_CLIENT_MESSAGE` | ❌ **NOT SUPPORTED** — message type not recognised by current API endpoint |
| Concurrent `UpdateListen` messages are both processed | 2 messages sent 1.2 ms apart; only 1 `ListenUpdated` ack received | ⚠️ **LAST-WRITE-WINS** — second message silently overwrites first; only one ack returned. No error. |

---

## What we did NOT test

- ~~**UpdateThink / UpdateSpeak / UpdatePrompt**~~ — `UpdateSpeak` ✅ now tested; `UpdateThink` ❌ tested (NOT SUPPORTED); `UpdatePrompt` not yet exercised.
- ~~**InjectUserMessage / InjectAgentMessage**~~ — both tested: `InjectAgentMessage` ✅ works; `InjectUserMessage` ❌ NOT SUPPORTED.
- ~~**Concurrent UpdateListen calls**~~ — tested: last-write-wins, 1 ack for 2 concurrent sends.
- ~~**eot_timeout_ms**~~ — tested: all 3 values accepted, fast acks (~317 ms mean).
- **Multi-language switching** — `language_hints` field was not exercised.
- **Production call volumes** — this harness tests a single session at a time.
- **Other telephony carriers** — only Plivo was tested (Run C, call UUID `f2f702f9-7673-46fe-840c-e43f7f2f8209`). Behaviour on other PSTN carriers (Twilio, Vonage, etc.) has not been measured.
- **Non-Deepgram STT providers** — `UpdateListen` was only tested with
  `"type": "deepgram"` as the listen provider.
- **UpdatePrompt** — changing the system prompt mid-call was not tested.

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
