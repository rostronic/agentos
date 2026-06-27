"""Weekly SEO digest loader + email sender (synthetic review files, no network)."""

from __future__ import annotations

import json

import pytest

from agentos.notify import email_sender
from agentos.seo import loader


@pytest.fixture
def fake_reviews(tmp_path, monkeypatch):
    """Two synthetic sites with docs/seo/reviews/, wired into the registry."""
    def make_site(slug, date, actionable, watch, digest_body):
        reviews = tmp_path / slug / "docs" / "seo" / "reviews"
        reviews.mkdir(parents=True)
        # An older review that must be ignored in favor of `date`.
        (reviews / "SEO_REVIEW_2026-01-01.md").write_text("# old\n")
        (reviews / "findings_2026-01-01.json").write_text(
            json.dumps({"actionable": [], "watch": []})
        )
        (reviews / f"findings_{date}.json").write_text(json.dumps({
            "run_date": date,
            "window": {"current": ["2026-06-16", "2026-06-22"]},
            "actionable": actionable,
            "watch": watch,
        }))
        (reviews / f"SEO_REVIEW_{date}.md").write_text(
            f"# {slug} review\n\n## Full digest\n\n```\n{digest_body}\n```\n"
        )
        return tmp_path / slug

    shop = make_site(
        "example-shop", "2026-06-25",
        actionable=[{"severity": "major", "area": "indexing", "detail": "0 of 6332 indexed"}],
        watch=[{"severity": "info", "area": "indexing", "detail": "/feed not indexed"}],
        digest_body="*ExampleShop — Weekly SEO digest*\nClicks: 0",
    )
    news = make_site(
        "example-news", "2026-06-25",
        actionable=[{"severity": "major", "area": "indexing", "detail": "0 of 590 indexed"}],
        watch=[],
        digest_body="*ExampleNews — Weekly SEO digest*\nClicks: 1",
    )

    monkeypatch.setattr(loader, "projects", lambda: {
        "example-shop": {"repo_path": str(shop), "label": "ExampleShop"},
        "example-news": {"repo_path": str(news), "label": "ExampleNews"},
        # A registry entry with no reviews dir must be silently skipped.
        "job-hunt": {"repo_path": str(tmp_path / "job-hunt")},
    })
    return tmp_path


def test_load_sites_picks_newest_per_site(fake_reviews):
    sites = loader.load_sites()
    assert [s["slug"] for s in sites] == ["example-shop", "example-news"]
    assert all(s["date"] == "2026-06-25" for s in sites)  # not the 2026-01-01 one
    shop = sites[0]
    assert shop["label"] == "ExampleShop"
    assert len(shop["actionable"]) == 1 and shop["actionable"][0]["severity"] == "major"
    assert len(shop["watch"]) == 1
    assert "ExampleShop" in shop["digest"] and "```" not in shop["digest"]


def test_summary_counts(fake_reviews):
    s = loader.summary()
    assert s == {"sites": 2, "actionable": 2, "watch": 1, "latest_date": "2026-06-25"}


def test_notification_text_lists_actionable(fake_reviews):
    txt = loader.notification_text()
    assert txt.startswith("Weekly SEO digest")
    assert "ExampleShop (2026-06-16 → 2026-06-22)" in txt
    assert "0 of 6332 indexed" in txt
    assert "0 of 590 indexed" in txt
    assert "[major/indexing]" in txt


def test_no_sites_yields_empty_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "projects", lambda: {"job-hunt": {"repo_path": str(tmp_path)}})
    assert loader.load_sites() == []
    assert loader.notification_text() == ""
    assert loader.summary()["latest_date"] is None


def test_bad_findings_json_degrades(tmp_path, monkeypatch):
    reviews = tmp_path / "example-shop" / "docs" / "seo" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "SEO_REVIEW_2026-06-25.md").write_text("# x\n")
    (reviews / "findings_2026-06-25.json").write_text("{ not valid json")
    monkeypatch.setattr(loader, "projects", lambda: {
        "example-shop": {"repo_path": str(tmp_path / "example-shop")}
    })
    sites = loader.load_sites()
    assert len(sites) == 1
    assert sites[0]["actionable"] == [] and sites[0]["watch"] == []


# --- email sender (no real SMTP) -------------------------------------------- #
def test_email_unconfigured_is_noop(monkeypatch):
    """No EMAIL_ADDRESS/PASSWORD → no-op, never raises, reason='unconfigured'."""
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    res = email_sender.send_email("subj", "body", env={})  # empty file env
    assert res.sent is False
    assert res.reason == "unconfigured"


def test_email_send_uses_smtp_when_configured(monkeypatch):
    """With creds present, it builds the message and calls SMTP (mocked)."""
    # Ensure the passed-in env dict — not a stray process-env value loaded from a
    # local credentials/.env — drives the recipient.
    for k in ("EMAIL_TO", "EMAIL_ADDRESS", "AGENTOS_EMAIL_TO"):
        monkeypatch.delenv(k, raising=False)
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            sent["starttls"] = True

        def login(self, user, password):
            sent["user"] = user  # password intentionally NOT recorded

        def send_message(self, msg):
            sent["to"] = msg["To"]
            sent["subject"] = msg["Subject"]

    monkeypatch.setattr(email_sender.smtplib, "SMTP", FakeSMTP)
    env = {
        "EMAIL_ADDRESS": "you@example.com",
        "EMAIL_PASSWORD": "app-pw",
        "EMAIL_SMTP_HOST": "smtp.gmail.com",
        "EMAIL_SMTP_PORT": "587",
        "EMAIL_TO": "you@example.com",
    }
    res = email_sender.send_email("Weekly SEO digest", "the body", env=env)
    assert res.sent is True and res.reason == "ok"
    assert sent["host"] == "smtp.gmail.com" and sent["port"] == 587
    assert sent["starttls"] is True
    assert sent["to"] == "you@example.com"
    assert sent["subject"] == "Weekly SEO digest"
