"""CNT-T02: the four classes of a usage limit and their reset time, per adapter, from sanitized synthetic messages.

Nothing here starts a real program or reads an application folder: the classifiers are pure (a fixed clock and a fixed
machine zone, UTC-6 like the real ``codex-2.log`` case, are passed in a ``LimitContext``) and the three adapters'
``launch`` is driven with ``process.run`` replaced by a function that returns a canned outcome. The messages follow the
formats recorded in USO-T01 (Claude, Codex, Copilot), with the real U+2019 apostrophe of Codex.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from multiagent.executors import process
from multiagent.executors.claude import ClaudeAdapter, classify_claude_limit
from multiagent.executors.codex import CodexExecutorAdapter, classify_codex_limit, parse_stream
from multiagent.executors.contract import (
    CommandRecord, ExecutorApplicationAdapter, ExecutorLaunchRequest, ExecutorRunResult, FailureKind, LimitClassifier,
    LimitInfo, LimitWindow, Permission, ResetConfidence, ResetSource, RunStatus,
)
from multiagent.executors.copilot import CopilotAdapter, classify_copilot_limit
from multiagent.executors.limits import (
    LimitContext, epoch_to_utc, evidence_line, find_reset_text, make_limit, next_month_start, resolve_iana_zone,
    resolve_reset_text, utc_iso, with_configured_reset,
)
from multiagent.executors.process import ProcessOutcome
from multiagent.tasks.envelopes import build_task_envelope

Z6 = timezone(timedelta(hours=-6))                      # America/Mexico_City today: UTC-6, no daylight-saving time
UTC = timezone.utc
RESOLVABLE = {"America/Mexico_City": Z6, "UTC": UTC}


def resolver(name: str) -> tzinfo | None:
    return RESOLVABLE.get(name)


def local(day: int, hour: int, minute: int = 0, second: int = 0, month: int = 10) -> datetime:
    """A moment of October 2026 on the wall clock of the machine (UTC-6). 11 October 2026 is a Sunday."""
    return datetime(2026, month, day, hour, minute, second, tzinfo=Z6)


def context(observed: datetime | None = None, zone: tzinfo | None = Z6) -> LimitContext:
    kwargs = {"now": (lambda: observed.astimezone(UTC))} if observed is not None else {}
    return LimitContext(zone=zone, resolve_zone=resolver, **kwargs)


def iso(value: datetime) -> str:
    return utc_iso(value)


def epoch_of(value: datetime) -> int:
    return int(value.timestamp())


# --------------------------------------------------------------------------- synthetic messages (USO-T01 section 1-3)

CLAUDE_SESSION = "You've hit your session limit · resets 3:45pm"
CLAUDE_DESKTOP = "You've hit your session limit · resets 3:30am (America/Mexico_City)"
CLAUDE_WEEKLY = "You've hit your weekly limit · resets Mon 12:00am"
CLAUDE_OPUS = "You've hit your Opus limit · resets 3:45pm"
CLAUDE_SPEND = "You've hit your monthly spend limit · raise it at https://claude.ai/settings/usage?from=cc_cli_limit_message"
CLAUDE_429 = "API Error: 429 · Server is temporarily limiting requests (not your usage limit)"
CLAUDE_OLD = "Claude AI usage limit reached|1760000000"
CODEX_LIMIT = ("ERROR: You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
               "https://chatgpt.com/settings/usage to purchase more credits or try again at {when}.")
CODEX_1612 = CODEX_LIMIT.format(when="4:12 PM")
CODEX_0340 = CODEX_LIMIT.format(when="3:40 AM")
CODEX_NO_TIME = "ERROR: You’ve hit your usage limit. Visit https://chatgpt.com/settings/usage to purchase more credits."
COPILOT_QUOTA = "You have exceeded your monthly quota (Request ID: 0000:000000:0000000:0000000:00000000)"
COPILOT_JSON = '{"errorType":"quota","statusCode":402,"errorCode":"quota_exceeded"}'

# The Codex rollout record of the real case (13:07 local): 5-hour window at 100 %, weekly at 41 %, no credits.
CODEX_RATE_LIMITS = {
    "limit_id": "codex", "limit_name": None, "plan_type": "plus",
    "primary": {"used_percent": 100.0, "window_minutes": 300, "resets_at": 1791756752},
    "secondary": {"used_percent": 41.0, "window_minutes": 10080, "resets_at": 1792300000},
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    "individual_limit": None, "spend_control_reached": None, "rate_limit_reached_type": None,
}
CLAUDE_QUOTA_LIMITS = {"status": "rejected", "resetsAt": 1791711000, "rateLimitType": "five_hour",
                       "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled",
                       "isUsingOverage": False, "unifiedRateLimitFallbackAvailable": False, "upgradePaths": []}


# =========================================================================== the contract

def test_the_four_classes_replace_quota_and_the_old_value_still_parses():
    assert [kind.value for kind in FailureKind if kind.is_limit] == [
        "rate_limited", "plan_limit", "credits_exhausted", "limit_unknown"]
    assert "QUOTA" not in {kind.name for kind in FailureKind}               # deprecated alias is not iterated
    assert FailureKind.QUOTA is FailureKind.LIMIT_UNKNOWN and FailureKind.QUOTA.is_limit   # code that still returns it
    assert FailureKind("quota") is FailureKind.LIMIT_UNKNOWN               # an old stored value
    assert {kind.legacy_value for kind in FailureKind if kind.is_limit} == {"quota"}
    assert FailureKind.AUTHENTICATION.legacy_value == "authentication" and not FailureKind.OTHER.is_limit
    assert not FailureKind.PERMISSION_DENIED.is_limit
    assert FailureKind.PERMISSION_DENIED.legacy_value == "permission_denied"
    assert RunStatus.QUOTA_EXHAUSTED.value == "quota_exhausted"            # the status of all four classes is unchanged


def test_limit_info_shape_validation_and_json():
    info = LimitInfo(FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR, "2026-10-11T22:12:32Z", ResetSource.APP_EPOCH,
                     ResetConfidence.HIGH, "UTC", "hit your usage limit", ("reset_mismatch",))
    assert info.reset_mismatch
    data = info.to_dict()
    assert list(data) == ["kind", "window", "reset_at", "reset_source", "reset_confidence", "reset_zone", "evidence",
                          "notes"]
    assert data["kind"] == "plan_limit" and data["reset_source"] == "app_epoch" and data["notes"] == ["reset_mismatch"]
    assert len(json.dumps(data).encode()) < 600                             # fits an event payload (<= 2048 bytes)
    with pytest.raises(ValueError):
        LimitInfo(FailureKind.OTHER)
    with pytest.raises(ValueError):
        LimitInfo(FailureKind.PLAN_LIMIT, reset_at="2026-10-11T22:12:32+00:00")
    assert ExecutorRunResult(RunStatus.COMPLETED, None, None, None, CommandRecord((), "", "")).limit is None


def test_the_limit_classifier_is_a_separate_optional_face_of_an_adapter():
    for adapter in (ClaudeAdapter(), CodexExecutorAdapter(), CopilotAdapter()):
        assert isinstance(adapter, ExecutorApplicationAdapter) and isinstance(adapter, LimitClassifier)

    class Minimal:                      # a test double that has the nine methods but no classify_limit
        adapter_id, version = "x", "1"

        def describe(self, config): ...
        def build_command(self, request): ...
        def build_resume_command(self, request, session_id): ...
        def render_prompt(self, request): ...
        def parse_output(self, stdout): ...
        def classify_failure(self, exit_code, stderr): ...
        def open_target(self, session_id): ...
        def launch(self, request): ...
        def resume(self, request, session_id): ...

    assert isinstance(Minimal(), ExecutorApplicationAdapter) and not isinstance(Minimal(), LimitClassifier)


# =========================================================================== reading and placing a reset time

@pytest.mark.parametrize("text, expected", [
    ("or try again at 4:12 PM.", dict(hour=16, minute=12)),
    ("try again at 3:40 AM.", dict(hour=3, minute=40)),
    ("You've hit your session limit · resets 3:45pm", dict(hour=15, minute=45)),
    ("resets 3pm", dict(hour=15, minute=0)),
    ("resets 12:00am", dict(hour=0, minute=0)),
    ("resets 12pm", dict(hour=12, minute=0)),
    ("resets 15:45", dict(hour=15, minute=45)),
    ("resets Mon 12:00am", dict(hour=0, minute=0, weekday=0)),
    ("resets Sunday 9pm", dict(hour=21, minute=0, weekday=6)),
    ("Weekly limit reached ∙ resets Oct 14", dict(hour=None, month=10, day=14)),
    ("resets Oct 14 at 3pm", dict(hour=15, month=10, day=14)),
    ("try again at Mar 5th, 2026 4:12 PM", dict(hour=16, minute=12, month=3, day=5, year=2026)),
    ("resets 3:30am (America/Mexico_City)", dict(hour=3, minute=30, zone_name="America/Mexico_City")),
    ("resets 5pm (America/Argentina/Buenos_Aires)", dict(hour=17, zone_name="America/Argentina/Buenos_Aires")),
    ("resets 3pm (UTC)", dict(hour=15, zone_name="UTC")),
    ("resets 3pm (local time)", dict(hour=15, zone_name=None)),
])
def test_a_reset_time_is_read_from_the_message(text, expected):
    found = find_reset_text(text)
    assert found is not None
    for name, value in expected.items():
        assert getattr(found, name) == value, name


@pytest.mark.parametrize("text", [
    "", "try again later", "resets monthly", "resets soon", "resets 25:00", "resets 13pm", "resets 0am", "resets 3:75pm",
    "You've hit your usage limit.", "reset the counter at 3pm",
])
def test_a_message_without_a_reset_time_gives_none(text):
    assert find_reset_text(text) is None


def test_a_countdown_is_read_as_a_duration():
    found = find_reset_text("You've hit your usage limit, try again in 3 days 2 hours 4 minutes.")
    assert found is not None and found.relative == timedelta(days=3, hours=2, minutes=4)
    placed = resolve_reset_text(found, local(11, 13, 7), context())
    assert placed.at == local(11, 13, 7) + timedelta(days=3, hours=2, minutes=4) and placed.in_range


@pytest.mark.parametrize("text, observed, expected_utc", [
    # the real Codex case: 4:12 PM seen at 13:07 local is 22:12 UTC (the rollout epoch said 22:12:32)
    ("try again at 4:12 PM.", local(11, 13, 7), "2026-10-11T22:12:00Z"),
    ("try again at 3:40 AM.", local(11, 23, 50), "2026-10-12T09:40:00Z"),             # past midnight: the next day
    ("resets 3:45pm", local(11, 13, 7), "2026-10-11T21:45:00Z"),
    ("resets 3:45pm", local(11, 16, 0), "2026-10-12T21:45:00Z"),                       # already passed today: +1 day
    ("resets 3:45pm", local(11, 15, 45, 0), "2026-10-12T21:45:00Z"),                   # the same instant is not after T
    ("resets 3:45pm", local(11, 15, 44, 59), "2026-10-11T21:45:00Z"),
    ("resets 12:00am", local(11, 13, 7), "2026-10-12T06:00:00Z"),
    ("resets 12pm", local(11, 13, 7), "2026-10-12T18:00:00Z"),
    ("resets 15:45", local(11, 13, 7), "2026-10-11T21:45:00Z"),
    ("resets Mon 12:00am", local(11, 13, 7), "2026-10-12T06:00:00Z"),                  # Sunday -> the next Monday
    ("resets Mon 12:00am", local(12, 9, 0), "2026-10-19T06:00:00Z"),                   # Monday morning -> next week
    ("resets Sun 9pm", local(11, 13, 7), "2026-10-12T03:00:00Z"),                      # today, still ahead
    ("resets Oct 14 at 3pm", local(11, 13, 7), "2026-10-14T21:00:00Z"),
    ("try again at Oct 14th, 2026 4:12 PM", local(11, 13, 7), "2026-10-14T22:12:00Z"),
    ("resets Jan 3 at 9am", local(30, 20, 0, month=12), "2027-01-03T15:00:00Z"),       # no year: the next January
    ("resets 3:30am (America/Mexico_City)", local(11, 13, 7), "2026-10-12T09:30:00Z"),
    ("resets 3:30am (America/Mexico_City)", local(11, 2, 0), "2026-10-11T09:30:00Z"),
    ("resets 3pm (UTC)", datetime(2026, 10, 11, 10, 0, tzinfo=UTC), "2026-10-11T15:00:00Z"),
])
def test_a_clock_time_is_anchored_to_the_observation_and_converted_to_utc(text, observed, expected_utc):
    placed = resolve_reset_text(find_reset_text(text), observed.astimezone(UTC), context())
    assert placed.at is not None and iso(placed.at) == expected_utc
    assert placed.in_range and placed.confidence is ResetConfidence.MEDIUM and placed.notes == ()


def test_the_zone_label_is_the_iana_name_of_the_text_or_the_machine_offset():
    start = local(11, 13, 7).astimezone(UTC)
    assert resolve_reset_text(find_reset_text("resets 3pm"), start, context()).zone == "UTC-06:00"
    assert resolve_reset_text(find_reset_text("resets 3pm (America/Mexico_City)"), start, context()).zone == \
        "America/Mexico_City"
    assert resolve_reset_text(find_reset_text("resets 3pm (UTC)"), start, context()).zone == "UTC"


def test_a_zone_that_cannot_be_resolved_uses_the_machine_zone_and_lowers_the_confidence():
    placed = resolve_reset_text(find_reset_text("resets 5pm (America/Bogota)"), local(11, 13, 7).astimezone(UTC), context())
    assert iso(placed.at) == "2026-10-11T23:00:00Z"                          # read on the machine's UTC-6 clock
    assert placed.confidence is ResetConfidence.LOW and placed.notes == ("zone_unresolved:America/Bogota",)


def test_a_date_without_a_time_of_day_is_low_confidence_and_starts_at_midnight():
    placed = resolve_reset_text(find_reset_text("Weekly limit reached ∙ resets Oct 14"), local(11, 13, 7).astimezone(UTC),
                                context())
    assert iso(placed.at) == "2026-10-14T06:00:00Z" and placed.confidence is ResetConfidence.LOW
    assert placed.notes == ("time_missing",)


def test_eight_days_ahead_is_the_limit():
    start = local(11, 13, 7).astimezone(UTC)
    assert resolve_reset_text(find_reset_text("resets Oct 19 at 1pm"), start, context()).in_range            # 7 d 23 h 53 m
    assert resolve_reset_text(find_reset_text("resets Oct 19 at 1:07pm"), start, context()).in_range        # exactly 8 d
    assert resolve_reset_text(find_reset_text("resets Oct 19 at 1:08pm"), start, context()).in_range is False   # 8 d + 1 min
    assert resolve_reset_text(find_reset_text("resets Oct 19 at 3pm"), start, context()).in_range is False
    assert resolve_reset_text(find_reset_text("resets Oct 4th, 2026 at 3pm"), start, context()).in_range is False  # past
    assert resolve_reset_text(find_reset_text("resets Feb 31 at 3pm"), start, context()).at is None


class FallBackZone(tzinfo):
    """Central US with clocks going back on 2026-11-01: 02:00 CDT (07:00 UTC) becomes 01:00 CST. 01:30 happens twice."""

    CUT = datetime(2026, 11, 1, 7, 0)

    def utcoffset(self, dt):
        wall = dt.replace(tzinfo=None)
        if wall < datetime(2026, 11, 1, 1, 0):
            return timedelta(hours=-5)
        if wall < datetime(2026, 11, 1, 2, 0):
            return timedelta(hours=-5) if dt.fold == 0 else timedelta(hours=-6)
        return timedelta(hours=-6)

    def dst(self, dt):
        return timedelta(hours=1) if self.utcoffset(dt) == timedelta(hours=-5) else timedelta(0)

    def tzname(self, dt):
        return "CDT" if self.dst(dt) else "CST"

    def fromutc(self, dt):
        naive = dt.replace(tzinfo=None)
        if naive < self.CUT:
            return (naive - timedelta(hours=5)).replace(tzinfo=self)
        return (naive - timedelta(hours=6)).replace(tzinfo=self, fold=1 if naive < self.CUT + timedelta(hours=1) else 0)


def test_an_ambiguous_local_time_takes_its_first_occurrence():
    zone = FallBackZone()
    ctx = LimitContext(zone=zone, resolve_zone=resolver)
    start = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)                      # 00:30 CDT
    placed = resolve_reset_text(find_reset_text("resets 1:30am"), start, ctx)
    assert iso(placed.at) == "2026-11-01T06:30:00Z"                        # the CDT 01:30, not the CST one at 07:30
    later = resolve_reset_text(find_reset_text("resets 3:00am"), start, ctx)
    assert iso(later.at) == "2026-11-01T09:00:00Z"                         # after the change: UTC-6


def test_the_machine_zone_is_read_through_the_operating_system_when_none_is_given():
    start = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    placed = resolve_reset_text(find_reset_text("resets 3:45pm"), start, LimitContext(zone=None, resolve_zone=resolver))
    wall = placed.at.astimezone().replace(tzinfo=None)                     # the same instant on this machine's clock
    assert (wall.hour, wall.minute) == (15, 45) and start < placed.at <= start + timedelta(days=1)


# =========================================================================== epoch against text

def test_default_resolver_knows_utc_and_refuses_what_it_cannot_resolve():
    assert resolve_iana_zone("UTC") is UTC and resolve_iana_zone("gmt") is UTC
    for name in ("", "../etc/passwd", "Nowhere/Atlantis", "America/Mexico City", "A" * 200, None):
        assert resolve_iana_zone(name) is None                              # type: ignore[arg-type]


def test_an_epoch_is_read_in_seconds_or_milliseconds_and_junk_is_refused():
    assert iso(epoch_to_utc(1791756752)) == "2026-10-11T22:12:32Z"
    assert iso(epoch_to_utc(1791756752000)) == "2026-10-11T22:12:32Z"
    assert iso(epoch_to_utc("1791756752")) == "2026-10-11T22:12:32Z"
    for junk in (None, True, 0, -5, float("nan"), float("inf"), "soon", "", [], {}):
        assert epoch_to_utc(junk) is None


def test_the_epoch_wins_over_the_text_and_a_big_difference_is_noted():
    start = local(11, 13, 7).astimezone(UTC)
    text = find_reset_text("try again at 4:12 PM.")
    close = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR, observed_at=start, context=context(),
                       epoch=1791756752, text=text)
    assert close.reset_at == "2026-10-11T22:12:32Z" and close.reset_source is ResetSource.APP_EPOCH
    assert close.reset_confidence is ResetConfidence.HIGH and close.reset_zone == "UTC" and not close.reset_mismatch
    far = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR, observed_at=start, context=context(),
                     epoch=1791756752 + 89, text=text)                                   # 121 s from the text
    assert far.reset_at == "2026-10-11T22:14:01Z" and far.reset_mismatch and far.reset_source is ResetSource.APP_EPOCH
    edge = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR, observed_at=start, context=context(),
                      epoch=1791756752 + 88, text=text)                                  # 120 s exactly: still agreed
    assert not edge.reset_mismatch


def test_a_past_or_invalid_epoch_is_ignored_and_the_text_is_the_fallback():
    start = local(11, 13, 7).astimezone(UTC)
    text = find_reset_text("try again at 4:12 PM.")
    stale = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN, observed_at=start, context=context(),
                       epoch=1791700000, text=text)
    assert stale.reset_at == "2026-10-11T22:12:00Z" and stale.reset_source is ResetSource.APP_TEXT
    assert "epoch_stale" in stale.notes and stale.kind is FailureKind.PLAN_LIMIT
    nothing = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN, observed_at=start, context=context(), epoch=1791700000)
    assert nothing.reset_at is None and nothing.reset_source is ResetSource.NONE
    assert nothing.reset_confidence is ResetConfidence.NONE and nothing.kind is FailureKind.PLAN_LIMIT
    junk = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN, observed_at=start, context=context(), epoch="soon")
    assert junk.reset_at is None and "epoch_invalid" in junk.notes


def test_a_plan_limit_whose_only_time_is_beyond_eight_days_becomes_unknown():
    start = local(11, 13, 7).astimezone(UTC)
    by_epoch = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.WEEKLY, observed_at=start, context=context(),
                          epoch=epoch_of(local(11, 13, 7) + timedelta(days=9)))
    assert by_epoch.kind is FailureKind.LIMIT_UNKNOWN and by_epoch.reset_at is None
    by_text = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.WEEKLY, observed_at=start, context=context(),
                         text=find_reset_text("resets Oct 25 at 3pm"))
    assert by_text.kind is FailureKind.LIMIT_UNKNOWN and "reset_out_of_range" in by_text.notes
    credits = make_limit(FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND, observed_at=start, context=context(),
                         text=find_reset_text("resets Oct 25 at 3pm"))
    assert credits.kind is FailureKind.CREDITS_EXHAUSTED and credits.reset_at is None   # only a plan limit is demoted


def test_a_rate_limit_never_gets_a_reset_time():
    start = local(11, 13, 7).astimezone(UTC)
    info = make_limit(FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN, observed_at=start, context=context(),
                      epoch=1791756752, text=find_reset_text("resets 3pm"), wants_reset=False)
    assert info.reset_at is None and info.reset_source is ResetSource.NONE


def test_the_configured_pause_is_marked_as_such_and_never_replaces_a_real_time():
    start = local(11, 13, 7).astimezone(UTC)
    bare = make_limit(FailureKind.LIMIT_UNKNOWN, LimitWindow.UNKNOWN, observed_at=start, context=context())
    marked = with_configured_reset(bare, observed_at=start, pause_minutes=300)
    assert marked.reset_at == "2026-10-12T00:07:00Z" and marked.reset_source is ResetSource.CONFIGURED
    assert marked.reset_confidence is ResetConfidence.LOW and marked.reset_zone == "UTC"
    known = make_limit(FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN, observed_at=start, context=context(), epoch=1791756752)
    assert with_configured_reset(known, observed_at=start, pause_minutes=300) is known
    assert with_configured_reset(bare, observed_at=start, pause_minutes=0) is bare


def test_the_next_month_starts_at_midnight_utc():
    assert iso(next_month_start(datetime(2026, 10, 11, 19, 7, tzinfo=UTC))) == "2026-11-01T00:00:00Z"
    assert iso(next_month_start(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))) == "2027-01-01T00:00:00Z"
    assert iso(next_month_start(datetime(2026, 11, 1, 0, 0, tzinfo=UTC))) == "2026-12-01T00:00:00Z"   # strictly after
    assert iso(next_month_start(local(30, 20, month=11))) == "2027-01-01T00:00:00Z"   # 02:00 UTC on 1 Dec: next is January


def test_evidence_is_one_short_line_without_secrets():
    text = "line one\nYou've hit your usage limit. token ghp_AbCdEfGhIjKlMnOpQrStUv and Bearer abcdefghijklmnop1234\nlast"
    line = evidence_line(text, text.index("hit"))
    assert line.startswith("You've hit your usage limit.") and "ghp_" not in line and "Bearer abc" not in line
    assert line.count("[redacted]") == 2 and "line one" not in line and "last" not in line
    assert len(evidence_line("x" * 5000, 10)) == 240 and evidence_line("x" * 5000, 10).endswith("...")
    assert "\x07" not in evidence_line("bell \x07 here", 0)


# =========================================================================== Claude

@pytest.mark.parametrize("text, kind, window", [
    (CLAUDE_SESSION, FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR),
    (CLAUDE_DESKTOP, FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR),
    (CLAUDE_WEEKLY, FailureKind.PLAN_LIMIT, LimitWindow.WEEKLY),
    (CLAUDE_OPUS, FailureKind.PLAN_LIMIT, LimitWindow.MODEL_WEEKLY),
    ("You've hit your Fable limit · resets 3:45pm", FailureKind.PLAN_LIMIT, LimitWindow.MODEL_WEEKLY),
    ("You've hit your limit · resets 5pm", FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    ("5-hour limit reached ∙ resets 3pm", FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR),
    ("Weekly limit reached ∙ resets Oct 14", FailureKind.PLAN_LIMIT, LimitWindow.WEEKLY),
    ("You've reached your weekly usage limit", FailureKind.PLAN_LIMIT, LimitWindow.WEEKLY),
    (CLAUDE_OLD, FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    (CLAUDE_SPEND, FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("You've hit your org's monthly spend limit", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("Your credit balance is too low to access the Anthropic API", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("You're out of extra usage · resets 3pm", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ('{"error": {"type": "insufficient_quota", "message": "You exceeded your current quota"}}',
     FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    (CLAUDE_429, FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ('API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed your rate '
     'limit"}}', FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("HTTP 429 Too Many Requests", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("Request rejected (429)", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("API Error: 429", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("You've exceeded your quota", FailureKind.LIMIT_UNKNOWN, LimitWindow.UNKNOWN),
])
def test_claude_messages_are_classified(text, kind, window):
    info = classify_claude_limit(text, observed_at=local(11, 13, 7), context=context())
    assert info is not None and (info.kind, info.window) == (kind, window)
    assert info.evidence and "\n" not in info.evidence
    assert ClaudeAdapter(limit_context=context(local(11, 13, 7))).classify_failure(1, text) is kind


@pytest.mark.parametrize("text", [
    "", "boom", "Prompt is too long", "Error: File size limit exceeded", "exceeded the context window limit",
    "Invalid API key · Please run /login", "API Error: 529 overloaded_error", "Reached max turns (5)",
    "Session ID 11111111 is already in use",
])
def test_claude_text_that_is_not_a_limit_gives_none(text):
    assert classify_claude_limit(text, observed_at=local(11, 13, 7), context=context()) is None
    assert not ClaudeAdapter().classify_failure(1, text).is_limit
    assert classify_claude_limit(None, context=context()) is None                         # type: ignore[arg-type]


def test_claude_today_format_resets_3_45pm():
    info = classify_claude_limit(CLAUDE_SESSION, observed_at=local(11, 13, 7), context=context())
    assert info.reset_at == "2026-10-11T21:45:00Z" and info.reset_source is ResetSource.APP_TEXT
    assert info.reset_confidence is ResetConfidence.MEDIUM and info.reset_zone == "UTC-06:00" and info.notes == ()
    after = classify_claude_limit(CLAUDE_SESSION, observed_at=local(11, 16, 30), context=context())
    assert after.reset_at == "2026-10-12T21:45:00Z"


def test_claude_desktop_format_carries_its_zone():
    info = classify_claude_limit(CLAUDE_DESKTOP, observed_at=local(11, 13, 7), context=context())
    assert info.reset_at == "2026-10-12T09:30:00Z" and info.reset_zone == "America/Mexico_City"
    assert info.reset_source is ResetSource.APP_TEXT and info.reset_confidence is ResetConfidence.MEDIUM
    # a zone the machine cannot resolve is not guessed: the time is read on the machine clock and marked low
    other = classify_claude_limit(CLAUDE_DESKTOP.replace("America/Mexico_City", "Nowhere/Atlantis"),
                                  observed_at=local(11, 13, 7), context=context())
    assert other.reset_confidence is ResetConfidence.LOW and other.notes == ("zone_unresolved:Nowhere/Atlantis",)


def test_claude_desktop_zone_with_the_default_resolver_does_not_need_the_zone_database_for_utc():
    default = LimitContext(zone=Z6)                                         # the real resolver: UTC always works
    info = classify_claude_limit("You've hit your session limit · resets 3:45pm (UTC)",
                                 observed_at=datetime(2026, 10, 11, 10, 0, tzinfo=UTC), context=default)
    assert info.reset_at == "2026-10-11T15:45:00Z" and info.reset_zone == "UTC"


def test_claude_weekly_with_a_weekday():
    info = classify_claude_limit(CLAUDE_WEEKLY, observed_at=local(11, 13, 7), context=context())
    assert info.reset_at == "2026-10-12T06:00:00Z" and info.window is LimitWindow.WEEKLY


def test_claude_quota_limits_epoch_beats_the_text():
    observed = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)                     # 02:00 local
    assert epoch_to_utc(1791711000) == datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
    info = classify_claude_limit(CLAUDE_DESKTOP, observed_at=observed, context=context(),
                                 quota_limits=CLAUDE_QUOTA_LIMITS)
    assert info.reset_at == "2026-10-11T09:30:00Z" and info.reset_source is ResetSource.APP_EPOCH
    assert info.reset_confidence is ResetConfidence.HIGH and info.reset_zone == "UTC" and not info.reset_mismatch
    skewed = {**CLAUDE_QUOTA_LIMITS, "resetsAt": 1791711000 + 600}
    info = classify_claude_limit(CLAUDE_DESKTOP, observed_at=observed, context=context(), quota_limits=skewed)
    assert info.reset_at == "2026-10-11T09:40:00Z" and info.reset_mismatch and info.reset_source is ResetSource.APP_EPOCH


def test_claude_quota_limits_alone_give_the_window_and_the_time():
    text = "You've hit your limit"
    info = classify_claude_limit(text, observed_at=datetime(2026, 10, 11, 8, 0, tzinfo=UTC), context=context(),
                                 quota_limits=CLAUDE_QUOTA_LIMITS)
    assert info.window is LimitWindow.FIVE_HOUR and info.reset_at == "2026-10-11T09:30:00Z"
    opus = classify_claude_limit(text, observed_at=datetime(2026, 10, 11, 8, 0, tzinfo=UTC), context=context(),
                                 quota_limits={**CLAUDE_QUOTA_LIMITS, "rateLimitType": "seven_day_opus"})
    assert opus.window is LimitWindow.MODEL_WEEKLY
    week = classify_claude_limit(text, observed_at=datetime(2026, 10, 11, 8, 0, tzinfo=UTC), context=context(),
                                 quota_limits={**CLAUDE_QUOTA_LIMITS, "rateLimitType": "seven_day"})
    assert week.window is LimitWindow.WEEKLY


def test_claude_quota_limits_that_are_not_a_rejection_or_are_stale_are_not_used():
    observed = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
    allowed = classify_claude_limit(CLAUDE_SESSION, observed_at=observed, context=context(),
                                    quota_limits={**CLAUDE_QUOTA_LIMITS, "status": "allowed_warning"})
    assert allowed.reset_source is ResetSource.APP_TEXT and allowed.window is LimitWindow.FIVE_HOUR
    past = classify_claude_limit(CLAUDE_SESSION, observed_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC), context=context(),
                                 quota_limits=CLAUDE_QUOTA_LIMITS)
    assert past.reset_source is ResetSource.APP_TEXT and "epoch_stale" in past.notes


def test_claude_a_bare_429_is_lifted_by_a_rejected_quota_record_but_an_explicit_rate_limit_is_not():
    observed = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
    lifted = classify_claude_limit("API Error: 429", observed_at=observed, context=context(),
                                   quota_limits=CLAUDE_QUOTA_LIMITS)
    assert lifted.kind is FailureKind.PLAN_LIMIT and lifted.reset_confidence is ResetConfidence.HIGH
    explicit = classify_claude_limit(CLAUDE_429, observed_at=observed, context=context(), quota_limits=CLAUDE_QUOTA_LIMITS)
    assert explicit.kind is FailureKind.RATE_LIMITED and explicit.reset_at is None       # a stale record of an earlier hit
    unknown = classify_claude_limit("You've exceeded your quota", observed_at=observed, context=context(),
                                    quota_limits=CLAUDE_QUOTA_LIMITS)
    assert unknown.kind is FailureKind.PLAN_LIMIT


def test_claude_old_format_with_an_epoch_after_the_bar():
    before = datetime(2025, 10, 9, 5, 0, tzinfo=UTC)
    info = classify_claude_limit(CLAUDE_OLD, observed_at=before, context=context())
    assert info.reset_at == "2025-10-09T08:53:20Z" and info.reset_source is ResetSource.APP_EPOCH
    assert info.reset_confidence is ResetConfidence.HIGH
    stale = classify_claude_limit(CLAUDE_OLD, observed_at=local(11, 13, 7), context=context())      # a year later
    assert stale.reset_at is None and "epoch_stale" in stale.notes and stale.kind is FailureKind.PLAN_LIMIT


def test_claude_credits_message_keeps_a_time_when_it_has_one_and_has_none_otherwise():
    with_time = classify_claude_limit("You're out of extra usage · resets 3pm", observed_at=local(11, 13, 7),
                                      context=context())
    assert with_time.kind is FailureKind.CREDITS_EXHAUSTED and with_time.reset_at == "2026-10-11T21:00:00Z"
    without = classify_claude_limit(CLAUDE_SPEND, observed_at=local(11, 13, 7), context=context())
    assert without.reset_at is None and without.reset_confidence is ResetConfidence.NONE


def test_claude_dates_too_far_away_make_the_limit_unknown():
    info = classify_claude_limit("Weekly limit reached ∙ resets Oct 25 at 3pm", observed_at=local(11, 13, 7),
                                 context=context())
    assert info.kind is FailureKind.LIMIT_UNKNOWN and info.window is LimitWindow.WEEKLY and info.reset_at is None


# =========================================================================== Codex

@pytest.mark.parametrize("text, kind, window", [
    (CODEX_1612, FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    (CODEX_0340, FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    (CODEX_NO_TIME, FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    ('{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached"}}',
     FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    ("ERROR: You've hit your usage limit. Upgrade to Pro or try again at 6:01 PM.", FailureKind.PLAN_LIMIT,
     LimitWindow.UNKNOWN),
    ("stream error: exceeded retry limit, last status: 429 Too Many Requests", FailureKind.RATE_LIMITED,
     LimitWindow.UNKNOWN),
    ("HTTP 429 from the provider", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("Rate limit exceeded", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("You exceeded your current quota, please check your plan and billing details.", FailureKind.CREDITS_EXHAUSTED,
     LimitWindow.SPEND),
    ("You are out of credits", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("Something about the quota", FailureKind.LIMIT_UNKNOWN, LimitWindow.UNKNOWN),
])
def test_codex_messages_are_classified(text, kind, window):
    info = classify_codex_limit(text, observed_at=local(11, 13, 7), context=context())
    assert info is not None and (info.kind, info.window) == (kind, window)
    assert CodexExecutorAdapter(limit_context=context(local(11, 13, 7))).classify_failure(1, text) is kind
    assert CodexExecutorAdapter().classify_failure(0, text) is FailureKind.OTHER             # exit 0 is never a failure


@pytest.mark.parametrize("text", [
    "", "thread 'main' panicked at core/src/exec.rs:42: something broke", "line 4290 of the log",
    "Not logged in. Run codex login", "WARN something else",
])
def test_codex_text_that_is_not_a_limit_gives_none(text):
    assert classify_codex_limit(text, observed_at=local(11, 13, 7), context=context()) is None


def test_codex_today_format_try_again_at_4_12_pm_is_medium_without_the_rollout():
    info = classify_codex_limit(CODEX_1612, observed_at=local(11, 13, 7), context=context())
    assert info.reset_at == "2026-10-11T22:12:00Z" and info.reset_source is ResetSource.APP_TEXT
    assert info.reset_confidence is ResetConfidence.MEDIUM and info.reset_zone == "UTC-06:00"
    assert info.evidence.startswith("ERROR: You’ve hit your usage limit") and len(info.evidence) <= 240


def test_codex_try_again_at_3_40_am_means_tomorrow_when_it_is_nearly_midnight():
    info = classify_codex_limit(CODEX_0340, observed_at=local(11, 23, 50), context=context())
    assert info.reset_at == "2026-10-12T09:40:00Z"


def test_codex_rollout_epoch_beats_the_text_and_names_the_window():
    info = classify_codex_limit(CODEX_1612, observed_at=local(11, 13, 7), context=context(),
                                rate_limits=CODEX_RATE_LIMITS)
    assert info.reset_at == "2026-10-11T22:12:32Z" and info.reset_source is ResetSource.APP_EPOCH
    assert info.reset_confidence is ResetConfidence.HIGH and info.window is LimitWindow.FIVE_HOUR
    assert not info.reset_mismatch and info.kind is FailureKind.PLAN_LIMIT       # 32 s apart, and has_credits false


def test_codex_epoch_of_the_exhausted_window_only():
    limits = {**CODEX_RATE_LIMITS, "primary": {**CODEX_RATE_LIMITS["primary"], "used_percent": 63.0},
              "secondary": {**CODEX_RATE_LIMITS["secondary"], "used_percent": 100.0}}
    info = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(), rate_limits=limits)
    assert info.window is LimitWindow.WEEKLY and info.reset_at == iso(epoch_to_utc(1792300000))
    both = {**CODEX_RATE_LIMITS, "secondary": {**CODEX_RATE_LIMITS["secondary"], "used_percent": 100.0}}
    info = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(), rate_limits=both)
    assert info.window is LimitWindow.WEEKLY and info.reset_at == iso(epoch_to_utc(1792300000))   # the one that resets last
    below = {**CODEX_RATE_LIMITS, "primary": {**CODEX_RATE_LIMITS["primary"], "used_percent": 97.0}}
    info = classify_codex_limit(CODEX_1612, observed_at=local(11, 13, 7), context=context(), rate_limits=below)
    assert info.reset_source is ResetSource.APP_TEXT and info.window is LimitWindow.UNKNOWN


def test_codex_a_window_that_already_reset_is_stale_and_the_text_is_the_fallback():
    stale = {**CODEX_RATE_LIMITS, "primary": {**CODEX_RATE_LIMITS["primary"], "resets_at": 1791700000}}
    info = classify_codex_limit(CODEX_1612, observed_at=local(11, 13, 7), context=context(), rate_limits=stale)
    assert info.reset_source is ResetSource.APP_TEXT and "epoch_stale" in info.notes
    none = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(), rate_limits=stale)
    assert none.reset_at is None and none.kind is FailureKind.CREDITS_EXHAUSTED         # no time and no credits


def test_codex_no_time_and_no_credits_is_credits_exhausted_but_a_time_keeps_it_a_plan_limit():
    no_credits = {**CODEX_RATE_LIMITS, "primary": None, "secondary": None}
    info = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(), rate_limits=no_credits)
    assert info.kind is FailureKind.CREDITS_EXHAUSTED and info.window is LimitWindow.SPEND
    assert "has_credits_false" in info.notes and info.reset_at is None
    with_credits = {**no_credits, "credits": {"has_credits": True, "unlimited": False, "balance": "12"}}
    info = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(), rate_limits=with_credits)
    assert info.kind is FailureKind.PLAN_LIMIT and info.reset_confidence is ResetConfidence.NONE
    info = classify_codex_limit(CODEX_1612, observed_at=local(11, 13, 7), context=context(), rate_limits=no_credits)
    assert info.kind is FailureKind.PLAN_LIMIT and info.reset_at == "2026-10-11T22:12:00Z"
    unlimited = {**no_credits, "credits": None}
    info = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(), rate_limits=unlimited)
    assert info.kind is FailureKind.PLAN_LIMIT                                           # nothing says credits ran out


def test_codex_the_empty_premium_record_and_junk_structures_do_not_break_anything():
    premium = {"limit_id": "premium", "limit_name": None, "primary": None, "secondary": None, "credits": None}
    assert classify_codex_limit(CODEX_1612, observed_at=local(11, 13, 7), context=context(),
                                rate_limits=premium).reset_source is ResetSource.APP_TEXT
    for junk in (None, [], "x", {"primary": "x", "secondary": {"used_percent": "100", "resets_at": "soon"}},
                 {"primary": {"used_percent": float("nan"), "resets_at": True}}):
        info = classify_codex_limit(CODEX_NO_TIME, observed_at=local(11, 13, 7), context=context(),
                                    rate_limits=junk)                                   # type: ignore[arg-type]
        assert info.kind is FailureKind.PLAN_LIMIT and info.reset_at is None


def test_codex_a_bare_429_is_lifted_by_an_exhausted_window_but_too_many_requests_is_not():
    lifted = classify_codex_limit("HTTP 429 from the provider", observed_at=local(11, 13, 7), context=context(),
                                  rate_limits=CODEX_RATE_LIMITS)
    assert lifted.kind is FailureKind.PLAN_LIMIT and lifted.reset_confidence is ResetConfidence.HIGH
    explicit = classify_codex_limit("429 Too Many Requests", observed_at=local(11, 13, 7), context=context(),
                                    rate_limits=CODEX_RATE_LIMITS)
    assert explicit.kind is FailureKind.RATE_LIMITED and explicit.reset_at is None


def test_codex_full_date_and_countdown_forms_are_read_too():
    dated = classify_codex_limit(CODEX_LIMIT.format(when="Oct 14th, 2026 4:12 PM"), observed_at=local(11, 13, 7),
                                 context=context())
    assert dated.reset_at == "2026-10-14T22:12:00Z"
    countdown = classify_codex_limit("You've hit your usage limit, try again in 3 hours 4 minutes.",
                                     observed_at=local(11, 13, 7), context=context())
    assert countdown.reset_at == "2026-10-11T22:11:00Z"


def test_codex_stream_keeps_the_last_rate_limits_of_the_token_count_events():
    first = {"type": "event_msg", "payload": {"type": "token_count", "info": None,
                                              "rate_limits": {"primary": {"used_percent": 10.0}}}}
    last = {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 5}},
                                             "rate_limits": CODEX_RATE_LIMITS}}
    stream = parse_stream("\n".join(json.dumps(item) for item in (first, last, {"type": "turn.started"})))
    assert stream.rate_limits == CODEX_RATE_LIMITS and stream.usage == {"input_tokens": 5}
    assert parse_stream(json.dumps({"type": "turn.started"})).rate_limits is None


# =========================================================================== Copilot

@pytest.mark.parametrize("text, kind, window", [
    (COPILOT_QUOTA, FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    (COPILOT_JSON, FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("quota HTTP 402 quota_exceeded You have exceeded your monthly quota", FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("402 You have no quota remaining", FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("You have exceeded your monthly premium request allowance.", FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("Quota exceeded for premium requests", FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("You've used all of your premium requests for this billing period", FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("Monthly usage limit exceeded", FailureKind.PLAN_LIMIT, LimitWindow.MONTHLY),
    ("HTTP 402 Payment Required", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("You have run out of AI credits", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("Insufficient AI credits to run this model", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("Session AI credit limit reached. Increase or unset the session limit before trying again.",
     FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("Error: You've hit your rate limit. Please try again later.", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("Sorry, you've hit a rate limit that restricts the number of Copilot model requests you can make",
     FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("Error: 429 Too Many Requests", FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ('{"errorType":"rate_limit","statusCode":429}', FailureKind.RATE_LIMITED, LimitWindow.UNKNOWN),
    ("QUOTA EXHAUSTED", FailureKind.LIMIT_UNKNOWN, LimitWindow.UNKNOWN),
])
def test_copilot_messages_are_classified(text, kind, window):
    info = classify_copilot_limit(text, observed_at=local(11, 13, 7), context=context())
    assert info is not None and (info.kind, info.window) == (kind, window)
    assert CopilotAdapter(limit_context=context(local(11, 13, 7))).classify_failure(1, text) is kind
    assert CopilotAdapter().classify_failure(0, text) is FailureKind.OTHER


@pytest.mark.parametrize("text", [
    "", "Error: Unknown model 'gpt-9'", "Segmentation fault", "cannot read quota.json", "premium feature disabled",
    "error code 500", "Error: Not authenticated. Run `copilot login` to authenticate.", "HTTP 401 Unauthorized",
])
def test_copilot_text_that_is_not_a_limit_gives_none(text):
    assert classify_copilot_limit(text, observed_at=local(11, 13, 7), context=context()) is None


def test_copilot_monthly_quota_is_a_plan_limit_that_resets_on_the_first_at_midnight_utc():
    info = classify_copilot_limit(COPILOT_QUOTA, observed_at=local(11, 13, 7), context=context())
    assert info.kind is FailureKind.PLAN_LIMIT and info.window is LimitWindow.MONTHLY
    assert info.reset_at == "2026-11-01T00:00:00Z" and info.reset_source is ResetSource.RULE
    assert info.reset_confidence is ResetConfidence.MEDIUM and info.reset_zone == "UTC"
    december = classify_copilot_limit(COPILOT_QUOTA, observed_at=datetime(2026, 12, 31, 23, 0, tzinfo=UTC), context=context())
    assert december.reset_at == "2027-01-01T00:00:00Z"


def test_copilot_session_error_body_is_read_through_the_error_mapping():
    error = {"errorType": "quota", "statusCode": 402, "errorCode": "quota_exceeded",
             "message": "You have exceeded your monthly quota (Request ID: 0000:000000)"}
    info = classify_copilot_limit("", observed_at=local(11, 13, 7), context=context(), error=error)
    assert info.kind is FailureKind.PLAN_LIMIT and info.reset_at == "2026-11-01T00:00:00Z"
    bare = classify_copilot_limit("", observed_at=local(11, 13, 7), context=context(),
                                  error={"errorType": "quota", "statusCode": 402, "errorCode": "quota_exceeded"})
    assert bare.kind is FailureKind.PLAN_LIMIT
    payment = classify_copilot_limit("", observed_at=local(11, 13, 7), context=context(), error={"statusCode": 402})
    assert payment.kind is FailureKind.CREDITS_EXHAUSTED and payment.reset_at is None
    assert classify_copilot_limit("", observed_at=local(11, 13, 7), context=context(), error={"statusCode": 500}) is None


def test_copilot_credits_and_rate_limits_have_no_reset():
    for text in ("HTTP 402 Payment Required", "Error: 429 Too Many Requests"):
        info = classify_copilot_limit(text, observed_at=local(11, 13, 7), context=context())
        assert info.reset_at is None and info.reset_source is ResetSource.NONE


def test_copilot_a_time_written_in_the_message_beats_the_monthly_rule():
    info = classify_copilot_limit("You have exceeded your monthly quota. It resets Oct 14 at 3pm",
                                  observed_at=local(11, 13, 7), context=context())
    assert info.reset_at == "2026-10-14T21:00:00Z" and info.reset_source is ResetSource.APP_TEXT


# =========================================================================== launch: the result carries the limit

NOW = datetime(2026, 10, 11, 19, 7, tzinfo=UTC)                 # 13:07 on the machine clock
SESSION = "11111111-2222-4333-8444-555555555555"


def envelope(assignee: str) -> dict:
    return build_task_envelope(task_id="CNT-T02", group_id="CNT", revision=2, attempt=1, assignee=assignee,
                               status="running", objective="Classify the limit.", file_scope=["multiagent/x.py"],
                               constraints=[], gate=["tests"], base_commit="6b876fa", events_after=3)


def request(folder: Path, assignee: str, **kwargs) -> ExecutorLaunchRequest:
    fields = dict(task_id="CNT-T02", task_envelope=envelope(assignee), workdir=folder, timeout_seconds=60,
                  permission=Permission.READ)
    fields.update(kwargs)
    return ExecutorLaunchRequest(**fields)


@pytest.fixture()
def run_returns(monkeypatch):
    """Replace ``process.run`` (the only way a program starts) with a canned outcome."""
    def install(*, stdout="", stderr="", exit_code=1):
        def fake_run(argv, *, cwd, **kwargs):
            record = CommandRecord(tuple(argv), str(cwd), NOW.isoformat(), NOW.isoformat(), exit_code)
            return ProcessOutcome(exit_code=exit_code, stdout_tail=stdout, stderr_tail=stderr, stdout_truncated=False,
                                  stderr_truncated=False, timed_out=False, cancelled=False, command=record)
        monkeypatch.setattr(process, "run", fake_run)
    return install


def test_a_claude_plan_limit_run_carries_the_limit_and_keeps_the_old_status_and_note(run_returns, tmp_path):
    answer = {"type": "result", "subtype": "success", "is_error": True, "session_id": SESSION, "result": CLAUDE_SESSION,
              "quotaLimits": {**CLAUDE_QUOTA_LIMITS, "resetsAt": epoch_of(local(11, 15, 45))}}
    run_returns(stdout=json.dumps(answer), exit_code=1)
    result = ClaudeAdapter(limit_context=context(local(11, 13, 7))).launch(request(tmp_path, "claude_cli", session_id=SESSION))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.result_envelope is None
    assert any(note.startswith("failure: quota ") for note in result.notes)               # the pre-CNT-T02 token
    assert result.limit.kind is FailureKind.PLAN_LIMIT and result.limit.window is LimitWindow.FIVE_HOUR
    assert result.limit.reset_at == "2026-10-11T21:45:00Z" and result.limit.reset_confidence is ResetConfidence.HIGH


def test_a_claude_limit_without_a_quota_record_uses_the_message_time(run_returns, tmp_path):
    run_returns(stderr=CLAUDE_SESSION, exit_code=1)
    result = ClaudeAdapter(limit_context=context(local(11, 13, 7))).launch(request(tmp_path, "claude_cli", session_id=SESSION))
    assert result.limit.reset_at == "2026-10-11T21:45:00Z" and result.limit.reset_confidence is ResetConfidence.MEDIUM


def test_a_claude_rate_limit_run_is_still_quota_exhausted_with_a_rate_limited_class(run_returns, tmp_path):
    run_returns(stderr=CLAUDE_429, exit_code=1)
    result = ClaudeAdapter(limit_context=context(local(11, 13, 7))).launch(request(tmp_path, "claude_cli", session_id=SESSION))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.limit.kind is FailureKind.RATE_LIMITED
    assert result.limit.reset_at is None


def test_claude_failures_that_are_not_limits_have_no_limit(run_returns, tmp_path):
    run_returns(stderr="Invalid API key · Please run /login", exit_code=1)
    result = ClaudeAdapter().launch(request(tmp_path, "claude_cli", session_id=SESSION))
    assert result.status is RunStatus.FAILED and result.limit is None


def test_a_codex_plan_limit_run_uses_the_rate_limits_of_its_own_stream(run_returns, tmp_path):
    stream = "\n".join(json.dumps(item) for item in (
        {"type": "thread.started", "thread_id": SESSION},
        {"type": "event_msg", "payload": {"type": "token_count", "info": None, "rate_limits": CODEX_RATE_LIMITS}},
        {"type": "error", "message": CODEX_1612},
        {"type": "turn.failed", "error": {"message": CODEX_1612}}))
    run_returns(stdout=stream, exit_code=1)
    result = CodexExecutorAdapter(limit_context=context(local(11, 13, 7))).launch(request(tmp_path, "codex_cli"))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.session_id == SESSION
    assert any(note.startswith("failure_kind=quota:") for note in result.notes)
    assert result.limit.kind is FailureKind.PLAN_LIMIT and result.limit.window is LimitWindow.FIVE_HOUR
    assert result.limit.reset_at == "2026-10-11T22:12:32Z" and result.limit.reset_confidence is ResetConfidence.HIGH


def test_a_codex_run_with_exit_zero_but_only_error_events_also_reports_the_limit(run_returns, tmp_path):
    stream = "\n".join(json.dumps(item) for item in (
        {"type": "thread.started", "thread_id": SESSION}, {"type": "turn.failed", "error": {"message": CODEX_1612}}))
    run_returns(stdout=stream, exit_code=0)
    result = CodexExecutorAdapter(limit_context=context(local(11, 13, 7))).launch(request(tmp_path, "codex_cli"))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.limit.reset_at == "2026-10-11T22:12:00Z"


def test_a_copilot_quota_run_carries_the_monthly_rule(run_returns, tmp_path):
    run_returns(stdout=json.dumps({"type": "session.error", "data": {
        "errorType": "quota", "statusCode": 402, "errorCode": "quota_exceeded", "message": COPILOT_QUOTA}}), exit_code=1)
    result = CopilotAdapter((sys.executable,), limit_context=context(local(11, 13, 7))).launch(
        request(tmp_path, "copilot_cli", model="gpt-6-luna"))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.notes == ("failure:quota",)
    assert result.limit.kind is FailureKind.PLAN_LIMIT and result.limit.reset_at == "2026-11-01T00:00:00Z"
    assert result.limit.reset_source is ResetSource.RULE


def test_a_copilot_failure_that_is_not_a_limit_has_none(run_returns, tmp_path):
    run_returns(stderr="Segmentation fault", exit_code=139)
    result = CopilotAdapter((sys.executable,)).launch(request(tmp_path, "copilot_cli", model="gpt-6-luna"))
    assert result.status is RunStatus.FAILED and result.limit is None
