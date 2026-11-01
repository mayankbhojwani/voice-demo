"""The one place that knows which STT backend is in use (STT_BACKEND in .env)."""

from voice_demo import config


def make_stt():
    if config.STT_BACKEND == "local":
        from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX

        # Segment-based: needs VAD frames, transcribes after you stop speaking.
        return WhisperSTTServiceMLX(settings=WhisperSTTServiceMLX.Settings(model=MLXModel.TINY))
    if config.STT_BACKEND == "gnani":
        from pipecat.transcriptions.language import Language
        from pipecat_gnani.stt import GnaniSTTService

        if not config.GNANI_API_KEY:
            raise RuntimeError("STT_BACKEND=gnani but GNANI_API_KEY is empty in .env")
        # WebSocket streaming. 'transcribe' format normalises numbers (e.g. policy numbers).
        return GnaniSTTService(
            api_key=config.GNANI_API_KEY,
            settings=GnaniSTTService.Settings(language=Language.EN_IN, format="transcribe"),
        )
    raise ValueError(f"Unknown STT_BACKEND: {config.STT_BACKEND!r}")
