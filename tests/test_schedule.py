from datetime import datetime, timezone

from jackpot_predictor.scheduler import jackpot_schedule as sched


def _cfg():
    return {"schedule": {"timezone": "Africa/Nairobi",
                         "stages": {"preview": {"days_before": 2, "send_hour_eat": 20},
                                    "final": {"days_before": 0, "send_hour_eat": 11}}}}


sched.jackpot_config = _cfg

# First kickoff Saturday 2026-09-19 16:00 UTC = 19:00 EAT.
JP = {"first_kickoff_utc": "2026-09-19T16:00:00Z", "betting_status": "Open"}


def _at(iso_utc):
    return datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)


def test_preview_fires_two_days_out_at_its_hour():
    assert sched.stage_due(JP, _at("2026-09-17T17:00:00")) == "preview"   # 20:00 EAT
    assert sched.stage_due(JP, _at("2026-09-17T08:00:00")) is None        # 11:00 EAT, too early


def test_final_fires_on_match_day_morning_only():
    assert sched.stage_due(JP, _at("2026-09-19T08:00:00"), sent={"preview"}) == "final"
    assert sched.stage_due(JP, _at("2026-09-19T05:00:00"), sent={"preview"}) is None


def test_missed_preview_is_caught_up_next_firing():
    # VPS was down Thursday evening: Friday 11:00 EAT still sends the preview.
    assert sched.stage_due(JP, _at("2026-09-18T08:00:00")) == "preview"


def test_final_wins_when_both_are_due_on_match_day():
    assert sched.stage_due(JP, _at("2026-09-19T08:00:00")) == "final"


def test_sent_stages_are_not_repeated():
    assert sched.stage_due(JP, _at("2026-09-17T17:00:00"), sent={"preview"}) is None
    assert sched.stage_due(JP, _at("2026-09-19T08:00:00"), sent={"preview", "final"}) is None


def test_closed_betting_never_sends():
    closed = {**JP, "betting_status": "Closed"}
    assert sched.stage_due(closed, _at("2026-09-19T08:00:00")) is None
    assert sched.should_send_today(closed, _at("2026-09-17T17:00:00")) is False
