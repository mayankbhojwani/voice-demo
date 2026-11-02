"""M4: same pipeline as M3, but you can interrupt Priya mid-sentence (barge-in).

INTERRUPT_MIN_WORDS in .env: 0 = any detected speech cuts her off; N = needs N recognised words.

Run: uv run python -m voice_demo.m4_interrupt
"""

import asyncio

from voice_demo import config
from voice_demo.m3_voice import main

if __name__ == "__main__":
    asyncio.run(main(interruptions=True, min_words=config.INTERRUPT_MIN_WORDS))
