"""M3: mic -> STT -> LLM (Priya) -> TTS -> speakers, with per-turn latency logging.

Interruptions are switched OFF here on purpose; M4 turns them on. Use headphones, otherwise Priya's
own voice leaks into the mic and gets transcribed as you.

Run: uv run python -m voice_demo.m3_voice
"""

import asyncio

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_start import (
    MinWordsUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from voice_demo import config
from voice_demo.llm import make_llm, warm_up
from voice_demo.prompts import BASIC_PROMPT
from voice_demo.m2_chat import PrintReply
from voice_demo.metrics import LatencyObserver
from voice_demo.stt import make_stt
from voice_demo.tts import make_tts


def start_strategies(interruptions: bool, min_words: int):
    """How a user turn begins, and whether that also cuts Priya off (barge-in)."""
    if interruptions and min_words > 0:
        # Needs N recognised words before cutting in: ignores coughs and "mm-hmm", but is slower.
        return [MinWordsUserTurnStartStrategy(min_words=min_words)]
    return [
        VADUserTurnStartStrategy(enable_interruptions=interruptions),
        TranscriptionUserTurnStartStrategy(enable_interruptions=interruptions),
    ]


async def main(interruptions: bool = False, min_words: int = 0, prompt=None, tools=None, watchers=(),
               idle_secs=None, on_idle_hangup=None):
    """idle_secs: after this much silence from both sides, ask 'are you still there?'; if nobody speaks
    after that either, say on_idle_hangup()'s goodbye and end the call. None keeps Pipecat's default
    (cancel silently after 300 s)."""
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    stt, llm, tts = make_stt(), (make_llm(prompt) if prompt else make_llm()), make_tts()

    print("Warming up the model...", flush=True)
    secs = await asyncio.to_thread(warm_up, prompt or BASIC_PROMPT, tools)
    if secs is not None:
        print(f"Model ready ({secs:.1f}s).", flush=True)

    context = LLMContext(tools=tools) if tools else LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            user_turn_strategies=UserTurnStrategies(
                start=start_strategies(interruptions, min_words)
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            *watchers,
            PrintReply(),
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    labels = {
        "stt_backend": config.STT_BACKEND,
        "tts_backend": config.TTS_BACKEND,
        "llm_backend": config.LLM_BACKEND,
        "llm_model": config.OLLAMA_MODEL,
        "interruptions": interruptions,
        "min_words": min_words,
    }
    idle_kwargs = {"idle_timeout_secs": idle_secs, "cancel_on_idle_timeout": False} if idle_secs else {}
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[LatencyObserver(config.LATENCY_LOG, labels)],
        **idle_kwargs,
    )

    if idle_secs:
        users_at_last_prompt = {"n": -1}

        @task.event_handler("on_idle_timeout")
        async def _on_idle(worker):
            users = sum(1 for m in context.messages if isinstance(m, dict) and m.get("role") == "user")
            if users == users_at_last_prompt["n"]:  # we already asked and the customer still said nothing
                goodbye = TTSSpeakFrame(on_idle_hangup() if on_idle_hangup else "Goodbye.")
                goodbye.interruptible = False
                await worker.queue_frames([goodbye, EndFrame()])
            else:
                users_at_last_prompt["n"] = users
                await worker.queue_frame(TTSSpeakFrame("Hello, are you still there?"))
    print(f"Listening (headphones please)... logging to {config.LATENCY_LOG}. Ctrl+C to quit.")
    await PipelineRunner().run(task)


if __name__ == "__main__":
    asyncio.run(main())
