"""Print median / p90 per stage from the latency JSONL, grouped by backend combo.

Usage: uv run python scripts/summarize_latency.py [logs/latency.jsonl]
"""

import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

STAGES = ["stt_ms", "turn_wait_ms", "llm_ttft_ms", "llm_gen_ms", "tts_first_audio_ms", "total_ms", "tok_per_s"]
# My guesses from FINDINGS.md, recorded before the first M3 run.
PREDICTED = {"stt_ms": 10, "llm_ttft_ms": 1000, "tts_first_audio_ms": 500}


def p90(values):
    return sorted(values)[math.ceil(0.9 * len(values)) - 1]


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/latency.jsonl")
    if not path.exists():
        sys.exit(f"No log at {path}. Run a conversation first.")
    groups = defaultdict(list)
    for line in path.read_text().splitlines():
        r = json.loads(line)
        groups[(r["stt_backend"], r["tts_backend"], r["llm_model"])].append(r)

    for (stt, tts, model), rows in groups.items():
        print(f"\nSTT={stt}  TTS={tts}  LLM={model}  ({len(rows)} turns)")
        print(f"{'stage':<20}{'n':>4}{'median':>10}{'p90':>10}{'my guess':>10}")
        for s in STAGES:
            vals = [r[s] for r in rows if r.get(s) is not None]
            if not vals:
                print(f"{s:<20}{0:>4}{'-':>10}{'-':>10}")
                continue
            guess = PREDICTED.get(s, "")
            print(f"{s:<20}{len(vals):>4}{statistics.median(vals):>10.0f}{p90(vals):>10.0f}{guess:>10}")


if __name__ == "__main__":
    main()
