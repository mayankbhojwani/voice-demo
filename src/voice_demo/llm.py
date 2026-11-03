"""The one place that knows which LLM backend is in use."""

import json
import time
import urllib.request

from voice_demo import config
from voice_demo.prompts import BASIC_PROMPT


def _post(url: str, body: dict):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=120).read()


def warm_up(prompt: str, tools=None) -> float | None:
    """Load the model and prime its prompt cache before the call starts; returns seconds taken.

    Ollama unloads an idle model after 5 minutes, and the first turn then pays the load (we saw a 12 s
    first turn). Step 1 loads it and keeps it for 30 minutes. Step 2 sends the system prompt and tool
    definitions once, so Ollama's prefix cache already holds them when the customer first speaks.
    """
    if config.LLM_BACKEND != "ollama":
        return None
    native = config.OLLAMA_BASE_URL.removesuffix("/v1")
    started = time.time()
    try:
        _post(f"{native}/api/generate", {"model": config.OLLAMA_MODEL, "keep_alive": "30m"})
        body = {"model": config.OLLAMA_MODEL, "max_tokens": 1,
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": "hi"}]}
        if tools:
            from pipecat.adapters.llm.services.open_ai_adapter import OpenAILLMAdapter

            body["tools"] = OpenAILLMAdapter().to_provider_tools_format(tools)
        _post(f"{config.OLLAMA_BASE_URL}/chat/completions", body)
    except Exception as exc:  # warming is an optimisation; never block the call on it
        print(f"(warm-up skipped: {exc})")
        return None
    return time.time() - started


def make_llm(prompt: str = BASIC_PROMPT):
    if config.LLM_BACKEND == "ollama":
        from pipecat.services.ollama.llm import OLLamaLLMService

        return OLLamaLLMService(
            base_url=config.OLLAMA_BASE_URL,
            settings=OLLamaLLMService.Settings(
                model=config.OLLAMA_MODEL, system_instruction=prompt
            ),
        )
    if config.LLM_BACKEND == "openrouter":
        # Wired up in M7 (pinned model, provider sort, reasoning off, request counting).
        raise NotImplementedError("openrouter backend arrives in M7")
    raise ValueError(f"Unknown LLM_BACKEND: {config.LLM_BACKEND!r}")
