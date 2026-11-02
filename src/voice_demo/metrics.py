"""Per-turn latency logger. Watches frames flow through the pipeline and writes one JSONL line per turn.

Timeline of a turn (all wall-clock, seconds):
  speech_end -> stt_final -> llm_request -> llm_first -> tts_first_audio -> bot_started
              stt_ms      turn_wait_ms    llm_ttft_ms   tts_first_audio_ms
  Barge-in: interrupt_to_silence_ms = InterruptionFrame -> bot stopped speaking;
  llm_text_after_interrupt = LLM tokens emitted after the interruption (should be 0).
  llm_gen_ms is first token -> last token; tok_per_s is completion_tokens over that window.
"""

import json
import time
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed

def _ms(later, earlier):
    if later is None or earlier is None:
        return None
    return round((later - earlier) * 1000)


class LatencyObserver(BaseObserver):
    def __init__(self, path: str, labels: dict):
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._labels = labels
        self._t = {}
        self._pending = None  # an interrupted turn waiting for its bot-stopped event

    async def on_push_frame(self, data: FramePushed):
        f, now, t = data.frame, time.time(), self._t

        if isinstance(f, VADUserStoppedSpeakingFrame):
            if "llm_request" in t:  # previous turn still open: close it, start a new one
                self._flush()
            t["speech_end"] = f.timestamp - f.stop_secs  # when you actually went quiet
        elif isinstance(f, TranscriptionFrame):
            if "llm_request" not in t:
                t["stt_final"] = now  # last transcript before the LLM was called wins
        elif isinstance(f, LLMContextFrame):
            if self._pending is not None:  # next turn began; the interrupted one is complete
                self._write(self._take_pending())
            t.setdefault("llm_request", now)
        elif isinstance(f, LLMTextFrame):
            if "llm_request" in t:
                t.setdefault("llm_first", now)
                t["llm_last"] = now
            elif self._pending is not None and data.first_push:
                # Tokens still coming from the cancelled generation (should stay 0).
                self._pending["llm_text_after_interrupt"] += 1
        elif isinstance(f, InterruptionFrame):
            # Only a barge-in if Priya was mid-reply; the aggregator also interrupts at every turn start.
            # Move the old turn aside so the new turn's frames do not mix into it.
            if "llm_first" in t and self._pending is None:
                t["interrupted"] = now
                t["llm_text_after_interrupt"] = 0
                self._pending, self._t = t, {}
        elif isinstance(f, TTSAudioRawFrame):
            t.setdefault("tts_first_audio", now)
        elif isinstance(f, BotStartedSpeakingFrame):
            t.setdefault("bot_started", now)
        elif isinstance(f, MetricsFrame):
            for m in f.data:
                if isinstance(m, LLMUsageMetricsData):
                    t["completion_tokens"] = m.value.completion_tokens
                    t["prompt_tokens"] = m.value.prompt_tokens
                elif isinstance(m, TTFBMetricsData):
                    kind = "llm" if "LLM" in m.processor.upper() else "tts" if "TTS" in m.processor.upper() else "other"
                    t[f"pipecat_{kind}_ttfb_ms"] = round(m.value * 1000)
        elif isinstance(f, BotStoppedSpeakingFrame):
            if self._pending is not None and "bot_started" in self._pending:
                self._pending["bot_stopped"] = now  # how long she kept talking after the interrupt
                self._write(self._take_pending())
            else:
                t.setdefault("bot_stopped", now)
                self._flush()
        elif isinstance(f, (EndFrame, CancelFrame)):
            if self._pending is not None:
                self._write(self._take_pending())
            self._flush()

    def _take_pending(self):
        t, self._pending = self._pending, None
        return t

    def _flush(self):
        t, self._t = self._t, {}
        self._write(t)

    def _write(self, t):
        # Skip lines where the LLM never produced a token: with a clean mic these were one stray
        # extra record per turn (an LLMContextFrame seen again after the reply), not real turns.
        if "llm_first" not in t:
            return
        gen_s = (t["llm_last"] - t["llm_first"]) if "llm_first" in t else None
        tokens = t.get("completion_tokens")
        record = {
            "ts": round(time.time()),
            **self._labels,
            "stt_ms": _ms(t.get("stt_final"), t.get("speech_end")),
            "turn_wait_ms": _ms(t.get("llm_request"), t.get("stt_final")),
            "llm_ttft_ms": _ms(t.get("llm_first"), t["llm_request"]),
            "llm_gen_ms": round(gen_s * 1000) if gen_s is not None else None,
            "completion_tokens": tokens,
            "tok_per_s": round((tokens - 1) / gen_s, 1) if tokens and tokens > 1 and gen_s else None,
            "tts_first_audio_ms": _ms(t.get("tts_first_audio"), t.get("llm_first")),
            "total_ms": _ms(t.get("bot_started"), t.get("speech_end")),
            "pipecat_llm_ttfb_ms": t.get("pipecat_llm_ttfb_ms"),
            "pipecat_tts_ttfb_ms": t.get("pipecat_tts_ttfb_ms"),
            "bot_spoke": "bot_started" in t,
            "interrupted": "interrupted" in t,
            "llm_text_after_interrupt": t.get("llm_text_after_interrupt", 0) if "interrupted" in t else None,
            "interrupted_mid_speech": ("interrupted" in t and "bot_started" in t) or None,
            "interrupt_to_silence_ms": _ms(t.get("bot_stopped"), t.get("interrupted")),
        }
        with self._path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
