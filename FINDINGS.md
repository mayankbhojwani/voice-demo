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
| STT (end of speech -> final transcript) | 10 | 714 (p90 854), n=5, Gnani |
| LLM first token | 1000 | 2497 (p90 5211), n=5 |
| TTS first audio | 500 | 3175 (p90 6312), n=5, Kokoro local |

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

### M3: Voice out + per-turn latency log
- Pipeline: mic -> STT -> user aggregator (VAD + Smart Turn) -> LLM -> TTS -> speakers. `TTS_BACKEND=local|gnani` (Kokoro or Gnani WebSocket TTS).
- `LatencyObserver` (`metrics.py`) watches frames and writes `logs/latency.jsonl`; `scripts/summarize_latency.py` prints median/p90 per stage next to my guesses.
- Design finding: `stt_ms` is measured from when you went quiet (VAD stop time minus its 0.2 s `stop_secs`), so any VAD-based setup has a floor of about 200 ms before STT can even start. My 10 ms guess ignored that.
- Hidden stage I added: `turn_wait_ms` = STT final -> LLM request. Smart Turn / speech timeout waits here before deciding you are done (default speech timeout is 0.6 s).
- `tts_first_audio_ms` is measured from the first LLM token, so it includes Pipecat waiting for a full sentence before sending text to TTS.
- Interruptions deliberately off in M3 (`enable_interruptions=False`); M4 turns them on. Without headphones Priya hears herself.
- Not yet verified with real audio: Kokoro first-run download and the Gnani TTS voice name.
- _Your measured numbers vs guesses, and what surprised you:_
- **Measured (first run, Gnani STT + Kokoro TTS + local qwen3, n=5 complete turns):** stt 714 ms, turn_wait 349 ms, LLM first token 2497 ms, TTS first audio 3175 ms, total 6638 ms median (p90 12033 ms). All three guesses were too low; STT was off by ~70x.
- **Variance is the real story:** the best turn took 1.4 s end to end, the worst 12 s. The same prompt gave 276 ms vs 5164 ms of LLM generation. My hypothesis, not yet tested: Ollama and Kokoro compete for CPU/GPU and memory on an 8 GB M2, and local TTS may also stall the event loop that timestamps the frames. Test: rerun with `TTS_BACKEND=gnani` and compare.
- `tok_per_s` is unreliable here (7 to 83) for the same reason: it is computed from frame arrival times, not Ollama's own timing.
- **Logger finding:** 13 of 18 log lines were incomplete turns (an LLM request that produced no audio, or a second request after Priya spoke). I do not yet know why; candidates are Gnani emitting extra or empty transcripts, or Pipecat re-prompting after an empty turn. The summary now counts only turns where Priya spoke.
- `LLMContext([system...])` is deprecated in Pipecat 1.9+; the prompt now goes in the LLM service's `system_instruction`.

#### M3 re-run: AirPods (clean mic), Gnani STT + Gnani TTS, local qwen3, n=6 complete turns
| Stage | My guess | Median | p90 |
|---|---|---|---|
| STT (speech end -> final transcript) | 10 | 744 | 1766 |
| Turn wait (final transcript -> LLM request) | n/a | 258 | 312 |
| LLM first token | 1000 | 590 | 914 |
| TTS first audio (from first LLM token) | 500 | 768 | 1356 |
| Total (speech end -> Priya starts) | n/a | 2466 | 3677 |
- **Echo was the first big problem.** On laptop speakers Priya transcribed her own voice as the user and looped ("Perfect, I'll have a renewal advisor call you...") and 13 of 18 log lines were phantom turns. AirPods (mic and speaker both on the headset) fixed it. Lesson: no echo cancellation means you cannot talk to a voice agent over speakers; this is why M4 (barge-in) needs care.
- **Same LLM, very different speed:** first-token median went from 2497 ms (with local Kokoro TTS, on speakers) to 590 ms (Gnani TTS, AirPods). The runs differ in two things, TTS backend and echo, so this is suggestive, not proof that Kokoro starves Ollama on an 8 GB M2. Isolating run: Kokoro + AirPods.
- Hosted TTS was ~0.2-0.4 s to first byte vs ~1.5 s for Kokoro (from Pipecat's own TTFB metric, in the logs).
- Where the 2.5 s goes: ~0.74 STT (0.2 of it is the VAD wait, the rest Gnani), 0.26 turn wait, 0.59 LLM, 0.77 TTS. STT is the largest single stage and was my biggest wrong guess (10 ms).
- Bluetooth: AirPods run the mic at a lower-quality profile; unclear how much it affects accuracy or delay.
- Logger bug found and fixed: every real turn had a stray second log line with no tokens. Records without a first LLM token are no longer written.

### M4: Interruptions (barge-in)
How it works in Pipecat 1.15 (read from the installed source):
- **Detecting you:** Silero VAD (in the user aggregator) says speech started. A *start strategy* decides whether that opens a user turn; with `enable_interruptions=True` it calls `broadcast_interruption()`, which sends an `InterruptionFrame` both upstream and downstream. `MinWordsUserTurnStartStrategy` waits for N recognised words first.
- **Detecting you are done:** *stop strategy*, by default Smart Turn v3 (a small model that listens to the audio and says COMPLETE or INCOMPLETE), with a silence timeout as the backstop.
- **Cancelling:** every processor that sees `InterruptionFrame` drops its queued non-system frames. The LLM service's in-flight stream is cancelled and its HTTP stream closed; the TTS service drops its audio contexts and pending text; the output transport drains its audio queue; the assistant aggregator records the reply as interrupted and keeps only what was actually spoken.
- Open question I test with the logger: does Ollama actually stop generating when the client closes the stream? `llm_text_after_interrupt` counts tokens that still arrive after the interruption; `interrupt_to_silence_ms` is how fast Priya goes quiet.
- _Your notes: how fast does she stop, what falsely interrupts her, what does she remember of what she was saying?_

M4 results (first run, Gnani STT/TTS, AirPods, 3 barge-ins logged):
- **Barge-in works.** The log shows "broadcasting interruption" then the LLM stream stops (`processing time` cut to 0.7 s) and the Gnani TTS websocket disconnects. One measured interrupt-to-silence: 377 ms. No LLM tokens arrived after the interruption in any of the 3 (`llm_text_after_interrupt` = 0).
- **Ollama really stops generating when Pipecat closes the stream.** Direct test: while a 700-token generation was running, a "hi" request waited 24.6 s (Ollama serves one request at a time here). After killing the long request's connection after 1 s, a "hi" took 0.5 s, same as baseline. So cancellation reaches the model, not just Pipecat.
- **After an interruption the context keeps only what was spoken:** her cut-off reply is stored as a truncated sentence ("...would you like me to book a callback"), so she remembers what the user actually heard.
- **Cost of a cut-off:** the hosted TTS websocket is torn down and reconnected (about 1.2 s in the log). If you speak again right away, the next reply may start slower.
- **Many interruptions happen before she makes any sound** (during the ~2.5 s of thinking time), so "time to silence" does not apply; I log `interrupted_mid_speech` separately.
- **Logger bug found and fixed:** the next turn's frames were mixing into the interrupted turn, giving a negative `tts_first_audio_ms`. Interrupted turns are now set aside until they finish.
- **Side talk is a problem I did not solve:** "i am leaving don't lock the door" (not addressed to Priya) got a reply. Voice agents cannot tell who you are talking to.
- **Prompt weakness seen:** when the user said just "ashok", Priya replied "Yes, I'm speaking with Ashok" (confirming for the customer). Identity confirmation needs real data and tools (M5).

### M5: Tools (lookup_policy, book_callback) over fake data
Design: verification and booking rules live in code (`tools.py`), not the prompt. The model never passes a customer id; `lookup_policy` only returns details if the last 4 digits match the customer being called; `book_callback` refuses until that happened. 13 offline tests cover the handlers.

Text-mode probe of qwen3:4b-instruct (3 scripted dialogues, no audio, same prompt and handlers). Tool calls themselves worked well: correct JSON, correct tool, correct enum values, and it did not skip verification when asked to ("just tell me my premium"). The failures were elsewhere:
1. **Claimed a booking without calling the tool** (2 of 2 happy-path runs): after "okay then tomorrow evening" it said "Your callback is booked" with zero `book_callback` calls. Logged as `claimed_booking_without_tool`. One sentence added to the prompt ("Saying a callback is booked does NOT book it. Call book_callback first") fixed it in the next run, but that is one run, not a pass rate. M7 will measure it properly.
2. **Wrong weekdays:** "Thursday the 14th" (it is a Saturday), "Tuesday, 4 November" (a Wednesday). The 4B model cannot do calendar maths. Fix: the tool returns ready-to-say strings (`renewal_date_spoken`, `when_spoken`) and the prompt tells her to use them verbatim.
3. Hand-checked good: wrong digits revealed nothing and she asked again; slot_taken made her offer the alternative.

Failure types logged to `logs/tool_events.jsonl`: bad_args, wrong_digits, too_many_attempts, not_verified, invalid_slot, slot_taken, repeated_call (handlers); bad_json, unknown_tool (Pipecat only logs a warning and silently drops the call, so a log sink catches it); leaked_call_syntax, claimed_booking_without_tool (reply-text watcher, heuristic).
- Pipecat finding: a tool call with malformed JSON arguments is dropped with only a warning and no tool result, so the agent would go quiet. Worth handling in a real system.
- Not done yet: a clean-exit reason is not logged (that needs the end-of-call logic; planned with M7's scripted runs).
- _Your notes: which failure types showed up live, with voice and real STT errors on the digits?_
