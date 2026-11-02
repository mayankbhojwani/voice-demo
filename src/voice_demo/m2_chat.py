"""M2: mic -> VAD -> Whisper -> LLM (Priya) -> printed text reply. No speech output yet.

Run: uv run python -m voice_demo.m2_chat
"""

import asyncio
import time

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from voice_demo.llm import make_llm
from voice_demo.stt import make_stt


class PrintReply(FrameProcessor):
    """Prints what the user said and the LLM's reply, with time to first token."""

    def __init__(self):
        super().__init__()
        self._heard_at = None
        self._first_token = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self._heard_at = time.monotonic()
            self._first_token = True
            print(f"\nYou:   {frame.text}", flush=True)
        elif isinstance(frame, LLMTextFrame):
            if self._first_token and self._heard_at:
                ms = (time.monotonic() - self._heard_at) * 1000
                print(f"Priya: (first token +{ms:.0f} ms) ", end="", flush=True)
                self._first_token = False
            print(frame.text, end="", flush=True)
        elif isinstance(frame, LLMFullResponseEndFrame):
            print("\n", flush=True)
        await self.push_frame(frame, direction)


async def main():
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=False)
    )
    stt = make_stt()
    llm = make_llm()

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            PrintReply(),
            aggregators.assistant(),
        ]
    )
    print("Listening... speak, then pause. Ctrl+C to quit.")
    await PipelineRunner().run(PipelineTask(pipeline))


if __name__ == "__main__":
    asyncio.run(main())
