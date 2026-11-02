"""M5: Priya's two tools (lookup_policy, book_callback) over the fake data, plus failure logging.

Security lives in the code, not the prompt: lookup_policy only returns details when the digits match
the customer being called, and book_callback refuses until that has happened. The model never
supplies a customer id.

Every call and every failure goes to logs/tool_events.jsonl. Failure types logged:
  bad_args, wrong_digits, too_many_attempts, not_verified, invalid_slot, slot_taken, repeated_call
  (from the handlers); bad_json, unknown_tool (Pipecat warnings, caught by a log sink);
  leaked_call_syntax, claimed_booking_without_tool (from ToolWatcher, which reads the spoken text).
"""

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

DATA = Path(__file__).resolve().parents[2] / "data" / "customers.json"
EVENTS_LOG = Path("logs/tool_events.jsonl")
SLOTS = ("morning", "afternoon", "evening")
MAX_VERIFY_ATTEMPTS = 3


def spoken(d: date) -> str:
    """'Saturday, 14 November': weekdays computed here because the 4B model gets them wrong."""
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B')}"


def log_event(kind: str, **fields):
    EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_LOG.open("a") as fh:
        fh.write(json.dumps({"ts": round(time.time(), 2), "kind": kind, **fields}) + "\n")


def load_data():
    return json.loads(DATA.read_text())


class CallSession:
    """State of one call: who we are calling, whether they passed verification, what got booked."""

    def __init__(self, customer_id: str, today: date | None = None):
        data = load_data()
        self.customer = next(c for c in data["customers"] if c["id"] == customer_id)
        self.today = today or date.today()
        self.unavailable = {
            ((self.today + timedelta(days=u["day_offset"])).isoformat(), u["slot"])
            for u in data["unavailable_slots"]
        }
        self.verified = False
        self.failed_attempts = 0
        self.bookings = []
        self._last_call = None

    # -- helpers -------------------------------------------------------------------------------
    def bookable_days(self):
        return [(self.today + timedelta(days=n)).isoformat() for n in (1, 2, 3)]

    def bookable_days_spoken(self):
        return [f"{spoken(self.today + timedelta(days=n))} ({(self.today + timedelta(days=n)).isoformat()})" for n in (1, 2, 3)]

    def _fail(self, tool, args, ftype, message):
        log_event("failure", tool=tool, args=args, type=ftype, detail=message)
        return {"error": ftype, "message": message}

    def _seen_before(self, tool, args):
        repeated = self._last_call == (tool, json.dumps(args, sort_keys=True))
        self._last_call = (tool, json.dumps(args, sort_keys=True))
        if repeated:
            log_event("failure", tool=tool, args=args, type="repeated_call", detail="identical call twice in a row")
        return repeated

    # -- tools ---------------------------------------------------------------------------------
    def lookup_policy(self, args: dict) -> dict:
        self._seen_before("lookup_policy", args)
        digits = args.get("policy_last4")
        if not isinstance(digits, str) or not re.fullmatch(r"\d{4}", digits.strip()):
            return self._fail("lookup_policy", args, "bad_args",
                              "policy_last4 must be exactly 4 digits as a string. Ask the customer for the last 4 digits.")
        if self.failed_attempts >= MAX_VERIFY_ATTEMPTS:
            return self._fail("lookup_policy", args, "too_many_attempts",
                              "Too many failed attempts. Do not share anything; politely end the call.")
        c = self.customer
        if digits.strip() != c["policy_number"][-4:]:
            self.failed_attempts += 1
            return self._fail("lookup_policy", args, "wrong_digits",
                              "The digits do not match. Do not reveal any policy details. Ask them to repeat the last 4 digits.")
        self.verified = True
        log_event("call", tool="lookup_policy", args=args, ok=True)
        return {
            "policy_type": c["policy_type"], "plan": c["plan"], "premium_inr": c["premium_inr"],
            "cover_inr": c["cover_inr"], "status": c["status"],
            "renewal_date": (self.today + timedelta(days=c["renews_in_days"])).isoformat(),
            "renewal_date_spoken": spoken(self.today + timedelta(days=c["renews_in_days"])),
        }

    def book_callback(self, args: dict) -> dict:
        self._seen_before("book_callback", args)
        if not self.verified:
            return self._fail("book_callback", args, "not_verified",
                              "Customer is not verified yet. Verify with lookup_policy first.")
        day, slot = args.get("date"), args.get("slot")
        if day not in self.bookable_days() or slot not in SLOTS:
            return self._fail("book_callback", args, "invalid_slot",
                              f"date must be one of {self.bookable_days()} and slot one of {list(SLOTS)}.")
        if (day, slot) in self.unavailable:
            free = [(d, s) for d in self.bookable_days() for s in SLOTS if (d, s) not in self.unavailable]
            return self._fail("book_callback", args, "slot_taken",
                              f"That slot is taken. Offer one of these instead: {free[:4]}")
        self.unavailable.add((day, slot))
        self.bookings.append({"date": day, "slot": slot, "customer_id": self.customer["id"]})
        log_event("call", tool="book_callback", args=args, ok=True)
        return {"confirmed": True, "date": day, "slot": slot,
                "when_spoken": f"{spoken(date.fromisoformat(day))}, {slot}"}

    # -- wiring --------------------------------------------------------------------------------
    def tools_schema(self) -> ToolsSchema:
        async def lookup(params):
            await params.result_callback(self.lookup_policy(params.arguments))

        async def book(params):
            await params.result_callback(self.book_callback(params.arguments))

        return ToolsSchema(standard_tools=[
            FunctionSchema(
                name="lookup_policy",
                description="Verify the customer and fetch their policy details. Call this as soon as the customer "
                            "says the last 4 digits of their policy number. Never state policy details without it.",
                properties={"policy_last4": {"type": "string", "description": "Last 4 digits of the policy number, e.g. '3907'"}},
                required=["policy_last4"], handler=lookup),
            FunctionSchema(
                name="book_callback",
                description="Book a callback with a renewal advisor. Only after the customer agreed to a specific day and part of day.",
                properties={
                    "date": {"type": "string", "description": "Callback date as YYYY-MM-DD"},
                    "slot": {"type": "string", "enum": list(SLOTS), "description": "Part of the day"},
                },
                required=["date", "slot"], handler=book),
        ])


class ToolWatcher(FrameProcessor):
    """Reads Priya's reply text for tool-calling failures that never reach a handler."""

    def __init__(self, session: CallSession):
        super().__init__()
        self._session = session
        self._text = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self._text += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            text, self._text = self._text, ""
            if re.search(r"<tool_call>|\"name\"\s*:\s*\"(lookup_policy|book_callback)\"|```json", text):
                log_event("failure", type="leaked_call_syntax", detail=text[:200])
            if (re.search(r"\b(booked|scheduled|confirmed|arranged)\b", text, re.I)
                    and re.search(r"call ?back|advisor", text, re.I) and not self._session.bookings):
                log_event("failure", type="claimed_booking_without_tool", detail=text[:200])
        await self.push_frame(frame, direction)


def install_pipecat_warning_sink():
    """Pipecat only logs a warning for malformed or hallucinated tool calls; turn those into events."""
    def sink(message):
        text = message.record["message"]
        if "Failed to parse function call arguments" in text:
            log_event("failure", type="bad_json", detail=text[-200:])
        elif "not in the currently advertised tool set" in text:
            log_event("failure", type="unknown_tool", detail=text[-200:])
    logger.add(sink, level="WARNING")
