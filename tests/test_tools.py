"""Offline tests for the tool handlers (no LLM, no audio)."""

from datetime import date

import pytest

from voice_demo.tools import CallSession

TODAY = date(2026, 11, 2)


@pytest.fixture(autouse=True)
def _no_logs(tmp_path, monkeypatch):
    monkeypatch.setattr("voice_demo.tools.EVENTS_LOG", tmp_path / "events.jsonl")


def session():
    return CallSession("c1", today=TODAY)  # Ashok Mehta, policy SS-48213907


def test_lookup_returns_details_only_for_right_digits():
    s = session()
    r = s.lookup_policy({"policy_last4": "3907"})
    assert r["renewal_date"] == "2026-11-14" and r["premium_inr"] == 18450 and s.verified


def test_wrong_digits_reveal_nothing_and_lock_out():
    s = session()
    for _ in range(3):
        assert s.lookup_policy({"policy_last4": "0000"})["error"] == "wrong_digits"
    assert s.lookup_policy({"policy_last4": "3907"})["error"] == "too_many_attempts"
    assert not s.verified


@pytest.mark.parametrize("args", [{}, {"policy_last4": 3907}, {"policy_last4": "39"}, {"policy_last4": "three nine"}])
def test_bad_args(args):
    assert session().lookup_policy(args)["error"] == "bad_args"


def test_booking_requires_verification():
    assert session().book_callback({"date": "2026-11-03", "slot": "evening"})["error"] == "not_verified"


def test_booking_flow_and_slot_taken():
    s = session()
    s.lookup_policy({"policy_last4": "3907"})
    assert s.book_callback({"date": "2026-11-03", "slot": "morning"})["error"] == "slot_taken"
    ok = s.book_callback({"date": "2026-11-03", "slot": "evening"})
    assert ok["confirmed"] and ok["when_spoken"] == "Tuesday, 3 November, evening"
    assert s.book_callback({"date": "2026-11-03", "slot": "evening"})["error"] == "slot_taken"  # now taken


@pytest.mark.parametrize("args", [{"date": "tomorrow", "slot": "evening"}, {"date": "2026-11-03", "slot": "night"}, {"date": "2026-12-01", "slot": "morning"}])
def test_invalid_slot(args):
    s = session()
    s.lookup_policy({"policy_last4": "3907"})
    assert s.book_callback(args)["error"] == "invalid_slot"
