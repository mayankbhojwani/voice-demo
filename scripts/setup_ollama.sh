#!/usr/bin/env bash
# Create the derived Ollama model used by the agent (re-run after editing ollama/Modelfile).
set -euo pipefail
cd "$(dirname "$0")/.."
ollama create priya-qwen3 -f ollama/Modelfile
