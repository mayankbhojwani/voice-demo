# Findings

A write-up of what I learned building a local voice agent ("Priya" from SafeShield Insurance) with Pipecat.

## Hardware

- Machine: Mac, Apple M2 (detected by Claude; please confirm)
- RAM: 8 GB (detected by Claude; please confirm)
- macOS: Darwin 25.6.0
- Audio setup (mic / speakers / headphones): _fill in_

## Baseline

- Model: `qwen3:4b` via Ollama
- Generation speed: ~27 tokens/s (measured by me before this project)
- Known issue: with thinking on, a plain "hii" produced ~253 tokens (~240 hidden reasoning) and took ~10 s. Thinking must be disabled (verified in M2).

## Latency predictions (filled in at M3, before running)

| Stage | My guess (ms) | Measured median (ms) |
|---|---|---|
| STT (end of speech -> final transcript) | 10 | |
| LLM first token | 1000 | |
| TTS first audio | 500 | |

## Milestone notes

### M0: Scaffold
_Nothing broke yet. Add notes here after setup._

### M1: Mic -> VAD -> STT -> transcript
- Pipecat 1.15.0 installed. `WhisperSTTServiceMLX` is a `SegmentedSTTService`: it needs VAD frames upstream, so the pipeline is mic -> `VADProcessor` (Silero) -> Whisper. It is not token-streaming.
- Broke: the `mlx-whisper` extra alone failed at import (`No module named 'faster_whisper'`). Pipecat's whisper module imports faster_whisper at top level, so the `whisper` extra is needed as well.
- The script prints the delay from VAD stop to transcript. That is a rough STT latency number, not yet the logged metric (M3).
- User result: it works, but Whisper tiny's transcription accuracy is mediocre ("fine I guess"). Accepted for now; bigger models (`LARGE_V3_TURBO_Q4`) are the fix, at a latency and RAM cost.
- _Your notes: what did you say, what did it get wrong, what was the delay?_

### M2: LLM (Priya) replies in text
- **Big surprise:** `qwen3:4b` on Ollama is now the *thinking-only* 2507 build (`ollama show` lists `thinking`; context 262144). Its template always ends the prompt with `<think>`, so `think:false` and `/no_think` do nothing: `think:false` just leaks the reasoning into `content`, and `/no_think` in a system prompt produced 347 tokens (user message: 1843 tokens, 127 s) for "hii".
- Fix: use `qwen3:4b-instruct` (non-thinking build, same size). "hii" now costs 12 tokens. The model changed from what I planned, a deliberate tradeoff.
- `num_ctx` cannot be set per request on Ollama's `/v1` endpoint, so it's baked into a derived model (`priya-qwen3`, `ollama/Modelfile`, 4096) rather than relying on the default.
- `tests/test_no_thinking.py` checks both the native API and `/v1`. It failed first, which is how I caught that `.env` still pointed at `qwen3:4b` (a stale copy of the old `.env.example`). It passes after the fix.
- _Your notes: what was the first-token delay? Did Priya stay in character?_
- M2 result: user confirmed it works with `STT_BACKEND=gnani` (hosted streaming STT). Note: with Gnani on, the build is no longer fully local, since mic audio goes to Gnani's servers.

## Latency predictions (recorded before running M3; the table above is filled in from these)
User's guesses, in ms, from end of speech:
- STT: 10
- LLM first token: 1000
- TTS first audio: 500
- Total: not given (sum of the three = 1510)
