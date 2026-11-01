"""M1: mic -> VAD -> Whisper (MLX) -> print transcript. Nothing else.

Run: uv run python -m voice_demo.m1_transcribe
"""

import asyncio
import time

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import Frame, TranscriptionFrame, VADUserStoppedSpeakingFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams


class PrintTranscript(FrameProcessor):
    """Prints each final transcript and how long after end-of-speech it arrived."""

    def __init__(self):
        super().__init__()
        self._speech_stopped_at = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speech_stopped_at = time.monotonic()
        elif isinstance(frame, TranscriptionFrame):
            delay = ""
            if self._speech_stopped_at:
                delay = f"  (+{(time.monotonic() - self._speech_stopped_at) * 1000:.0f} ms after VAD stop)"
            print(f"\n>> {frame.text}{delay}\n", flush=True)
        await self.push_frame(frame, direction)


async def main():
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=False)
    )
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)))
    stt = WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(model=MLXModel.TINY))

    pipeline = Pipeline([transport.input(), vad, stt, PrintTranscript()])
    print("Listening... speak, then pause. Ctrl+C to quit.")
    await PipelineRunner().run(PipelineTask(pipeline))


if __name__ == "__main__":
    asyncio.run(main())
