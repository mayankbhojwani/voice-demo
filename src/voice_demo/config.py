"""Settings loaded from .env (see .env.example)."""

import os

from dotenv import load_dotenv

load_dotenv()

LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "priya-qwen3")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "")

STT_BACKEND = os.getenv("STT_BACKEND", "local")  # local | gnani
GNANI_API_KEY = os.getenv("GNANI_API_KEY", "")

TTS_BACKEND = os.getenv("TTS_BACKEND", "local")  # local (Kokoro) | gnani
GNANI_TTS_VOICE = os.getenv("GNANI_TTS_VOICE", "")  # empty = Gnani's default voice
LATENCY_LOG = os.getenv("LATENCY_LOG", "logs/latency.jsonl")

INTERRUPT_MIN_WORDS = int(os.getenv("INTERRUPT_MIN_WORDS", "0"))  # 0 = cut in on any speech (VAD)
