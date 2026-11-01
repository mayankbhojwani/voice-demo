"""The one place that knows which TTS backend is in use (TTS_BACKEND in .env)."""

from voice_demo import config


def make_tts():
    if config.TTS_BACKEND == "local":
        from pipecat.services.kokoro.tts import KokoroTTSService

        # First run downloads the model (~300 MB) into ~/.cache/pipecat/kokoro-onnx/.
        return KokoroTTSService(voice_id="af_heart")
    if config.TTS_BACKEND == "gnani":
        from pipecat_gnani.tts import GnaniTTSService

        if not config.GNANI_API_KEY:
            raise RuntimeError("TTS_BACKEND=gnani but GNANI_API_KEY is empty in .env")
        # WebSocket streaming; the class Gnani recommends for interruption handling (M4).
        return GnaniTTSService(
            api_key=config.GNANI_API_KEY, voice_id=config.GNANI_TTS_VOICE or None
        )
    raise ValueError(f"Unknown TTS_BACKEND: {config.TTS_BACKEND!r}")
