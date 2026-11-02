"""M5: Priya with tools. Same voice pipeline as M4 (barge-in on), plus lookup_policy and book_callback.

CUSTOMER_ID in .env picks which fake customer she is calling (c1..c5, see data/customers.json).
Tool calls and every failure are logged to logs/tool_events.jsonl.

Run: uv run python -m voice_demo.m5_tools
"""

import asyncio

from voice_demo import config
from voice_demo.m3_voice import main
from voice_demo.prompts import build_prompt
from voice_demo.tools import CallSession, ToolWatcher, install_pipecat_warning_sink

if __name__ == "__main__":
    session = CallSession(config.CUSTOMER_ID)
    prompt = build_prompt(session.customer["name"], session.today.isoformat(), session.bookable_days_spoken())
    install_pipecat_warning_sink()
    print(f"Calling {session.customer['name']} (policy ends {session.customer['policy_number'][-4:]}). "
          f"Say the last 4 digits when asked.")
    asyncio.run(main(interruptions=True, min_words=config.INTERRUPT_MIN_WORDS, prompt=prompt,
                     tools=session.tools_schema(), watchers=[ToolWatcher(session)]))
