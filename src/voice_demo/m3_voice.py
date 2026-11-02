"""M3: mic -> STT -> LLM (Priya) -> TTS -> speakers, with per-turn latency logging.

Interruptions are switched OFF here on purpose; M4 turns them on. Use headphones, otherwise Priya's
own voice leaks into the mic and gets transcribed as you.

Run: uv run python -m voice_demo.m3_voice
"""

import asyncio

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from voice_demo import config
from voice_demo.llm import make_llm
from voice_demo.m2_chat import PrintReply
from voice_demo.metrics import LatencyObserver
from voice_demo.stt import make_stt
from voice_demo.tts import make_tts


async def main():
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    stt, llm, tts = make_stt(), make_llm(), make_tts()

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            user_turn_strategies=UserTurnStrategies(
                start=[
                    VADUserTurnStartStrategy(enable_interruptions=False),
                    TranscriptionUserTurnStartStrategy(enable_interruptions=False),
                ]
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
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
    }
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[LatencyObserver(config.LATENCY_LOG, labels)],
    )
    print(f"Listening (headphones please)... logging to {config.LATENCY_LOG}. Ctrl+C to quit.")
    await PipelineRunner().run(task)


if __name__ == "__main__":
    asyncio.run(main())
