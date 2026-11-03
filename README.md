# Priya: a local voice agent demo

An outbound insurance-renewal reminder call, built to learn where the time goes in a voice AI pipeline. "Priya" from the fictional SafeShield Insurance calls a customer, confirms who they are, checks the last 4 digits of their policy, answers basic questions, and books a callback or exits politely. All data is fake and nothing dials a real phone: you talk to it through your mic and speakers.

The stack is [Pipecat](https://github.com/pipecat-ai/pipecat) with a cascaded pipeline:

```
mic -> VAD + turn detection -> STT -> LLM (+ tools) -> TTS -> speakers
        Silero + Smart Turn    Gnani   Ollama           Gnani
                               or MLX  qwen3:4b-instruct or Kokoro
                               Whisper
```

The LLM runs locally in Ollama. STT and TTS can each be local (MLX Whisper, Kokoro) or hosted (Gnani), switched in `.env`.

What it does, milestone by milestone:

| Run | What it adds |
|---|---|
| `m1_transcribe` | mic -> STT -> printed transcript |
| `m2_chat` | + LLM and Priya's prompt (text out) |
| `m3_voice` | + TTS, and per-turn latency logging to `logs/latency.jsonl` |
| `m4_interrupt` | barge-in: you can cut her off mid-sentence |
| `m5_tools` | `lookup_policy`, `book_callback`, `end_call`; verification and lockout enforced in code; every tool failure logged |

[FINDINGS.md](FINDINGS.md) has the notes on what broke and what I learned at each step.

## Setup

macOS with Apple Silicon is what this was built and measured on.

```bash
brew install uv portaudio ollama
uv sync
ollama pull qwen3:4b-instruct
scripts/setup_ollama.sh        # builds the derived model "priya-qwen3" (context window 4096)
cp .env.example .env           # then edit it
```

In `.env`, `STT_BACKEND=local` and `TTS_BACKEND=local` work with no keys (the first run downloads a Whisper model and a roughly 300 MB Kokoro model). To use Gnani, set `STT_BACKEND=gnani`, `TTS_BACKEND=gnani` and put your key in `GNANI_API_KEY`. Keys live only in `.env`, which is git-ignored.

**Wear headphones.** There is no echo cancellation, so on speakers Priya hears herself and answers her own voice.

## Run

```bash
uv run python -m voice_demo.m5_tools        # the full call
```

Startup first loads the model ("Warming up the model..." then "Model ready"), which takes 10 s or more if Ollama had unloaded it. Then Priya speaks first.

`CUSTOMER_ID` in `.env` picks the fake customer (`c1` to `c5`, see [data/customers.json](data/customers.json)). The program prints the last 4 digits of that customer's policy so you can play along. For `c1` they are `3907`.

Things worth trying:
- Give wrong digits three times. She reveals nothing, says goodbye, and the call ends.
- Ask for the premium before verifying.
- Interrupt her mid-sentence.
- Book a callback for a slot that is taken (`c1`: tomorrow morning or afternoon).
- Go silent after a question and see the "are you still there?" prompt, then the hang-up.

Outputs, all under `logs/`:
- `latency.jsonl`: one line per turn with each stage's time.
- `tool_events.jsonl`: every tool call, every failure (`wrong_digits`, `slot_taken`, `bad_json`, `claimed_booking_without_tool` and more), and a `call_end` line with the reason each call ended.

```bash
uv run python scripts/summarize_latency.py   # median and p90 per stage
uv run pytest                                # needs Ollama running for the no-thinking test
```

## Where the time goes

The question this project was built around: when you stop talking, how long until Priya starts, and what is that time made of?

Measured on a MacBook with an M2 chip and 8 GB RAM, AirPods, Gnani for STT and TTS, `qwen3:4b-instruct` in Ollama (about 20 tokens/s here, below the 27 I measured on its own before the project). 37 turns from real calls, barge-ins excluded. Times are milliseconds from the moment you stop speaking.

| Stage | Plain turn (n=25) | Tool turn (n=12) |
|---|---|---|
| STT: end of speech to final transcript | 752 | 698 |
| Turn wait: transcript to LLM request | 254 | 305 |
| LLM: request to first token | 754 | 2580 |
| TTS: first token to first audio | 872 | 980 |
| **Total until she starts speaking (median)** | **2950** | **4528** |

The four stages add up to the total, so each row is a slice of the wait. (The medians in a column do not sum exactly to its total median.) A "tool turn" is one where the model called a tool, which costs two LLM calls.

My guesses before running were STT 10 ms, LLM 1000 ms and TTS 500 ms. STT was off by 70x.

### What each slice is made of

**STT, about 750 ms.** I assumed a streaming STT would be done as I finished speaking. It is not: it still has to finalize after the last word, and Gnani's own reported time to first result was 0.7 to 1.2 s in the debug log. I have not split that into network time and model time. A few turns are far from the median (one took 2.6 s, and one transcript arrived before the VAD declared the end of speech, giving a negative number), so p90 is 1.2 s.

**Turn wait, about 250 ms.** After the final transcript arrives, Pipecat's turn-stop logic still waits before it calls the LLM. In the debug log this gap ranged from about 0 to 300 ms. I did not dig into exactly why.

**LLM, about 750 ms for a plain turn.** Time to first token for a 4B model on an M2 reading a prompt of 1,100 to 1,500 tokens (the system prompt, the tool definitions and the conversation so far). The prompt grows every turn.

**LLM on a tool turn, about 2,600 ms.** The model first has to write the tool call itself, 25 to 36 tokens of JSON, and nothing is spoken until that is done. At 20 tokens/s that is 1.2 to 1.8 s. Then the tool result goes back in and a second call writes the reply, which takes another 0.6 to 0.8 s to its first token. So a tool turn costs about 1.7 s more than a plain one, and the gap widens as the prompt grows.

**TTS, about 900 ms.** This is the stage I misjudged most. It is not mainly the speed of speech synthesis. TTS cannot start until the LLM has finished its first sentence, and that wait ("text aggregation" in the debug log) was 0.4 to 1.0 s. After that, Gnani took 0.15 to 0.5 s to the first audio, plus about 0.13 s of leading silence in the audio. Most of this stage is waiting for the LLM to finish a sentence at 20 tokens/s.

Taken together, a plain turn splits roughly into a third each for STT plus turn wait, the LLM, and TTS. All three are close in size, so there is no one stage to fix. Everything except the LLM is hosted, so the network is in those numbers.

### Other measurements

- **Barge-in works as intended.** In all 8 logged interruptions, the LLM produced 0 more tokens after the interrupt, and the cut-off reply is stored truncated in the context. I verified that Ollama aborts generation when the client disconnects.
- **Interrupt to silence is slower than I expected.** Three recent barge-ins took about 1.4 s from the interrupt to her going silent. An earlier one took 377 ms. I have not explained the difference.
- **Local TTS was much slower.** With Kokoro on the laptop, on speakers, over only 7 turns: LLM first token 2,497 ms, TTS 3,508 ms, total 9,721 ms. My guess is that the local TTS model and Ollama compete for 8 GB of memory, but I have not tested it (the test would be Kokoro with AirPods). These turns are also contaminated by echo from the speakers, so treat them as indicative only.

### Changes made after measuring

1. **Model warm-up at startup (measured, n=1).** Ollama unloads an idle model, and one first turn took 12 s. The program now loads the model (kept for 30 minutes) and sends the system prompt and tools once before the call starts. On a live call after `ollama stop`, startup printed "Model ready (12.3s)" and the first reply took 3039 ms in total, instead of the 5 to 12 s first turns seen before. The wait still exists; it now happens before the customer hears anything.
2. **Scripted booking confirmation and goodbye (measured, n=2).** After `book_callback` succeeds the code speaks the confirmation itself, with no second LLM call and no wait for the model's first sentence. The two scripted tool turns in one live call took 3514 ms and 3649 ms in total (a booking, and an `end_call` goodbye), against a median of 4528 ms for tool turns before. About 2.5 s of each is still the model writing the tool call (`scripted_tool_ms`: 2511 and 2645). A booking that fails (`slot_taken`) still needs the second LLM call and took 5.8 s in the same call: roughly 2.3 s to write the call plus 1.3 s for the reply's first token. Two samples is a direction, not a benchmark.

Measured directly against Ollama: once the prompt is cached, reading it costs almost nothing (44 ms), so shrinking the prompt would not help much. A tool call is slow because the model must write 25 to 44 tokens before anything can be spoken.

### Ideas not tried

1. Start TTS before the first sentence ends (token streaming), if Gnani sounds fine with it. Could save 0.5 to 1 s on every turn.
2. Compare local Whisper against Gnani for STT, which is the largest fixed cost at about 750 ms.
3. Say a short filler such as "one moment" while a tool runs.

### Caveats

One person, one laptop, one set of headphones, 37 turns, and they come from my own testing calls rather than a scripted benchmark. They are useful for seeing the shape of the problem, not for claiming a precise number. I dropped the planned comparison against a hosted model, so there is no hosted-LLM figure here.

## Layout

```
src/voice_demo/   config, llm/stt/tts factories, prompts, tools, metrics, m1..m5 entry points
data/             customers.json (5 fake customers, pre-booked slots)
ollama/           Modelfile for the derived model (fixed context window)
scripts/          setup_ollama.sh, summarize_latency.py
tests/            offline tool tests, no-thinking test
FINDINGS.md       notes per milestone
```

## Known limits

- No echo cancellation: use headphones.
- The 4B model sometimes writes a tool call as plain text instead of calling it. In one live call it said `endid_call{"reason": "callback_booked"}` out loud and the call did not end. The watcher now logs this as `leaked_call_syntax`, and the idle timeout (below) is the safety net, but the garbled speech itself is not prevented.
- Idle timeout: after `IDLE_SECS` (in `.env`) (default 20) of silence she asks "are you still there?", and a second silence ends the call with reason `no_response`. Tested with a no-audio pipeline, not yet on a live call.
- Shutdown after the goodbye takes about 4 s, and I have not found why.
- The 4B model sometimes invents small policy statements (for example, that discounts cannot be discussed by phone). The numbers and dates come from the tools, but free-text answers are not checked.
- Hindi/Hinglish and a hosted-LLM comparison are not implemented.
