"""Pipelines control-plane loader + summary (synthetic cron files)."""

from __future__ import annotations

import json

import pytest

from agentos.pipelines import loader


@pytest.fixture
def fake_cron(tmp_path, monkeypatch):
    cron = tmp_path / "cron"
    cron.mkdir(parents=True)
    jobs_file = cron / "jobs.json"
    state_file = cron / "jobs-state.json"

    jobs = {
        "version": 1,
        "jobs": [
            {
                "id": "uuid-shop-ok",
                "name": "shop_talent_scout",
                "description": "Enrich pending comedian stubs",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "30 5 * * *", "tz": "America/Los_Angeles"},
            },
            {
                "id": "uuid-shop-err",
                "name": "shop_historian_daily",
                "description": "This-day-in-comedy milestones",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 6 * * *", "tz": "America/Los_Angeles"},
            },
            {
                "id": "uuid-news-none",
                "name": "news_pipeline_daily",
                "description": "Daily F1 content pipeline",
                "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 7 * * *", "tz": "America/New_York"},
            },
            {
                "id": "uuid-news-disabled",
                "name": "news_driver_profile",
                "description": "Driver profile generator",
                "enabled": False,
                "schedule": {"kind": "cron", "expr": "0 9 * * 1", "tz": "America/New_York"},
            },
        ],
    }

    state = {
        "version": 1,
        "jobs": {
            "uuid-shop-ok": {
                "state": {
                    "lastRunStatus": "ok",
                    "lastStatus": "ok",
                    "lastRunAtMs": 1780258581720,
                    "nextRunAtMs": 1780317000000,
                    "consecutiveErrors": 0,
                    "lastError": None,
                }
            },
            "uuid-shop-err": {
                "state": {
                    "lastRunStatus": "error",
                    "lastStatus": "error",
                    "lastRunAtMs": 1780258581721,
                    "nextRunAtMs": 1780318800000,
                    "consecutiveErrors": 2,
                    "lastError": "Delivering to Telegram requires target <chatId>",
                }
            },
            # uuid-news-none has no state entry -> last_run_status == "none"
            # uuid-news-disabled also has no state entry
        },
    }

    jobs_file.write_text(json.dumps(jobs))
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(loader, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(loader, "STATE_FILE", state_file)
    return cron


def test_join_definition_and_health(fake_cron):
    jobs = loader.load_jobs()
    assert len(jobs) == 4
    by_id = {j["id"]: j for j in jobs}
    ok = by_id["uuid-shop-ok"]
    assert ok["name"] == "shop_talent_scout"
    assert ok["schedule"] == "30 5 * * *"
    assert ok["tz"] == "America/Los_Angeles"
    assert ok["description"] == "Enrich pending comedian stubs"
    assert ok["last_run_status"] == "ok"
    assert ok["consecutive_errors"] == 0


def test_project_derivation(fake_cron):
    by_id = {j["id"]: j for j in loader.load_jobs()}
    assert by_id["uuid-shop-ok"]["project"] == "ExampleShop"
    assert by_id["uuid-news-none"]["project"] == "ExampleNews"


def test_unknown_prefix_falls_back_to_title():
    assert loader._project_from_name("gary_briefing") == "Gary"
    assert loader._project_from_name("newsite_ingest") == "Newsite"
    assert loader._project_from_name("job_cleanup") == "General"


def test_error_detection(fake_cron):
    by_id = {j["id"]: j for j in loader.load_jobs()}
    err = by_id["uuid-shop-err"]
    assert err["last_run_status"] == "error"
    assert err["consecutive_errors"] == 2
    assert "Telegram" in err["last_error"]


def test_missing_state_is_none(fake_cron):
    by_id = {j["id"]: j for j in loader.load_jobs()}
    none_job = by_id["uuid-news-none"]
    assert none_job["last_run_status"] == "none"
    assert none_job["last_error"] is None
    assert none_job["last_run_at"] is None
    assert none_job["next_run_at"] is None


def test_epoch_to_iso_conversion(fake_cron):
    by_id = {j["id"]: j for j in loader.load_jobs()}
    ok = by_id["uuid-shop-ok"]
    # 1780258581720 ms -> 2026-... UTC ISO string with second precision
    assert ok["last_run_at"] is not None
    assert ok["last_run_at"].startswith("2026-")
    assert ok["last_run_at"].endswith("+00:00")
    assert "T" in ok["last_run_at"]


def test_enabled_flag(fake_cron):
    by_id = {j["id"]: j for j in loader.load_jobs()}
    assert by_id["uuid-shop-ok"]["enabled"] is True
    assert by_id["uuid-news-disabled"]["enabled"] is False


def test_summary_counts(fake_cron):
    s = loader.summary()
    assert s["total"] == 4
    assert s["by_project"] == {"ExampleShop": 2, "ExampleNews": 2}
    assert s["enabled"] == 3  # one disabled
    assert s["erroring"] == 1  # only shop_historian_daily
    assert s["ok"] == 1  # only shop_talent_scout (none jobs are not "ok")


def test_summary_accepts_prebuilt_jobs(fake_cron):
    jobs = loader.load_jobs()
    assert loader.summary(jobs) == loader.summary()


def test_missing_files_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "JOBS_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(loader, "STATE_FILE", tmp_path / "nope-state.json")
    assert loader.load_jobs() == []
    s = loader.summary()
    assert s["total"] == 0
    assert s["erroring"] == 0


def test_unparseable_file_safe(tmp_path, monkeypatch):
    bad = tmp_path / "jobs.json"
    bad.write_text("{ this is not valid json ")
    monkeypatch.setattr(loader, "JOBS_FILE", bad)
    monkeypatch.setattr(loader, "STATE_FILE", tmp_path / "missing.json")
    assert loader.load_jobs() == []
