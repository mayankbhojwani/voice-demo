"""M5: Priya's two tools (lookup_policy, book_callback) over the fake data, plus failure logging.

Security lives in the code, not the prompt: lookup_policy only returns details when the digits match
the customer being called, and book_callback refuses until that has happened. The model never
supplies a customer id.

Every call and every failure goes to logs/tool_events.jsonl. Failure types logged:
  bad_args, wrong_digits, too_many_attempts, not_verified, invalid_slot, slot_taken, repeated_call,
  bad_end_reason, end_reason_mismatch (from the handlers; repeated_call skips a customer re-saying
  digits we rejected);
  how each call ended is a "call_end" event with its reason; bad_json, unknown_tool (Pipecat warnings, caught by a log sink);
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
from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    FunctionCallResultProperties,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

DATA = Path(__file__).resolve().parents[2] / "data" / "customers.json"
EVENTS_LOG = Path("logs/tool_events.jsonl")
SLOTS = ("morning", "afternoon", "evening")
MAX_VERIFY_ATTEMPTS = 3

# Why a call can end, and what Priya says. Scripted so the exit is one clean sentence and costs no LLM call.
GOODBYES = {
    "callback_booked": "Thank you, {first}. Our advisor will call you then. Have a great day!",
    "declined": "No problem, {first}. Thank you for your time. Have a great day!",
    "busy": "Understood, we will try you another time. Thank you, and have a great day!",
    "wrong_person": "Sorry for the trouble. Have a good day!",
    "verification_failed": "I am sorry, I could not verify your details, so I cannot continue on this call. Thank you for your time. Goodbye.",
    "other": "Thank you for your time. Goodbye.",
    "no_response": "I cannot hear you, so I will try you again later. Goodbye.",  # set by the idle timeout, not the model
}
END_REASONS = tuple(r for r in GOODBYES if r != "no_response")  # what the model may pick


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
        self.ended = False
        self._last_call = None

    # -- helpers -------------------------------------------------------------------------------
    def bookable_days(self):
        return [(self.today + timedelta(days=n)).isoformat() for n in (1, 2, 3)]

    def bookable_days_spoken(self):
        return [f"{spoken(self.today + timedelta(days=n))} ({(self.today + timedelta(days=n)).isoformat()})" for n in (1, 2, 3)]

    def _fail(self, tool, args, ftype, message):
        log_event("failure", tool=tool, args=args, type=ftype, detail=message)
        return {"error": ftype, "message": message}

    def _seen_before(self, tool, args, retry_expected=False):
        repeated = self._last_call == (tool, json.dumps(args, sort_keys=True))
        self._last_call = (tool, json.dumps(args, sort_keys=True))
        # A customer repeating digits we rejected is a normal retry, not the model looping.
        if repeated and not retry_expected:
            log_event("failure", tool=tool, args=args, type="repeated_call", detail="identical call twice in a row")
        return repeated

    # -- tools ---------------------------------------------------------------------------------
    def lookup_policy(self, args: dict) -> dict:
        self._seen_before("lookup_policy", args, retry_expected=self.failed_attempts > 0 and not self.verified)
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

    def end_call(self, args: dict) -> tuple[str, str]:
        """Pick the end-of-call reason and the scripted goodbye; log how the call ended."""
        reason = args.get("reason")
        if reason not in END_REASONS:
            log_event("failure", tool="end_call", args=args, type="bad_end_reason", detail=f"reason must be one of {list(END_REASONS)}")
            reason = "other"
        elif reason == "callback_booked" and not self.bookings:
            log_event("failure", tool="end_call", args=args, type="end_reason_mismatch", detail="said callback_booked but nothing was booked")
            reason = "other"
        return reason, self._finish(reason)

    def idle_hangup(self) -> str:
        """The customer stayed silent after 'are you still there?': log it and return the goodbye."""
        return self._finish("no_response")

    def _finish(self, reason: str) -> str:
        self.ended = True
        log_event("call_end", reason=reason, verified=self.verified, bookings=len(self.bookings),
                  failed_attempts=self.failed_attempts)
        return GOODBYES[reason].format(first=self.customer["name"].split()[0])

    # -- wiring --------------------------------------------------------------------------------
    def tools_schema(self) -> ToolsSchema:
        async def hang_up(params, reason, goodbye):
            # Code says the goodbye (no extra LLM round trip, no chance of it being cut in half), then
            # EndWorkerFrame closes the pipeline after the queued speech has been played.
            say = TTSSpeakFrame(goodbye)
            say.interruptible = False
            await params.llm.push_frame(say)
            await params.llm.push_frame(EndWorkerFrame(reason=reason))

        async def lookup(params):
            result = self.lookup_policy(params.arguments)
            if "error" in result and self.failed_attempts >= MAX_VERIFY_ATTEMPTS:
                # Third wrong attempt: end now instead of asking the model for a fourth try.
                reason, goodbye = self.end_call({"reason": "verification_failed"})
                await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=False))
                await hang_up(params, reason, goodbye)
                return
            await params.result_callback(result)

        async def book(params):
            result = self.book_callback(params.arguments)
            if result.get("confirmed"):
                # Nothing left for the model to decide: say the confirmation from code. This skips a second
                # LLM call and the wait for its first sentence (about 1.8 s), and cannot misstate the slot.
                await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=False))
                await params.llm.push_frame(TTSSpeakFrame(
                    f"Done, your callback is booked for {result['when_spoken']}. Is there anything else I can help with?"))
                return
            await params.result_callback(result)  # failures still go to the model, which offers other slots

        async def end(params):
            reason, goodbye = self.end_call(params.arguments)
            await params.result_callback({"ended": True}, properties=FunctionCallResultProperties(run_llm=False))
            await hang_up(params, reason, goodbye)

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
            FunctionSchema(
                name="end_call",
                description="End the call. A goodbye is spoken for you, so do not say goodbye yourself. Use when the "
                            "customer declined, is busy, is the wrong person, or after a booked callback when they need nothing more.",
                properties={"reason": {"type": "string", "enum": list(END_REASONS), "description": "Why the call is ending"}},
                required=["reason"], handler=end),
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
            # Also catches argument JSON spoken as text, e.g. 'endid_call{"reason": "callback_booked"}'.
            if re.search(r"<tool_call>|\"name\"\s*:\s*\"(lookup_policy|book_callback|end_call)\"|```json"
                         r"|\{\s*\"(reason|policy_last4|date|slot)\"\s*:", text):
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
