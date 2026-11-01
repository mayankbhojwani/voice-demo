"""Proves Priya's model generates no hidden reasoning (the ~240-token problem from the baseline).

Needs Ollama running and `scripts/setup_ollama.sh` already run. Checks both the native API and the
OpenAI-compatible /v1 endpoint, because the pipeline uses /v1.
"""

import json
import urllib.request

import pytest

from voice_demo import config

HOST = config.OLLAMA_BASE_URL.removesuffix("/v1")
MODEL = config.OLLAMA_MODEL
MAX_TOKENS_FOR_GREETING = 60  # a thinking model spent ~250 on "hii"


def _post(path, body):
    req = urllib.request.Request(
        f"{HOST}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except OSError as e:
        pytest.skip(f"Ollama/model not reachable: {e}")


def test_native_api_has_no_reasoning():
    d = _post("/api/chat", {"model": MODEL, "stream": False,
                            "messages": [{"role": "user", "content": "hii"}]})
    msg = d["message"]
    assert not msg.get("thinking"), "model produced a separate thinking field"
    assert "<think>" not in msg["content"] and "</think>" not in msg["content"]
    assert d["eval_count"] < MAX_TOKENS_FOR_GREETING, f"{d['eval_count']} tokens for 'hii'"


def test_openai_endpoint_has_no_reasoning():
    d = _post("/v1/chat/completions", {"model": MODEL,
                                       "messages": [{"role": "user", "content": "hii"}]})
    msg = d["choices"][0]["message"]
    assert not msg.get("reasoning"), "model produced reasoning on /v1"
    assert "<think>" not in msg["content"]
    assert d["usage"]["completion_tokens"] < MAX_TOKENS_FOR_GREETING
