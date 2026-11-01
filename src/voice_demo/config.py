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
