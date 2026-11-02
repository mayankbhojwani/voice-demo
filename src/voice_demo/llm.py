"""The one place that knows which LLM backend is in use."""

from voice_demo import config
from voice_demo.prompts import BASIC_PROMPT


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
