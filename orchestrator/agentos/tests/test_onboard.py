"""Onboarding — discovery (alias match, global skip), scaffold, curate dispatch."""

from __future__ import annotations

import types

from agentos.core import config, onboard as ob


def _seed_central(d):
    files = {
        "example-shop_project.md": "ExampleShop is a content pipeline.",
        "feedback_shop_dev_localhost.md": "shop dev runs on localhost:3030.",
        "feedback_subagent_prefix.md": "prefix agents.",           # global-promoted → skip
        "user_setup.md": "laptop swap notes.",                     # global-promoted → skip
        "interface_notes.md": "the interface design language.",    # 'shop' not a token → no match
    }
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")


def test_discover_unknown_slug_raises(monkeypatch):
    monkeypatch.setattr(config, "project_config", lambda s: {})
    try:
        ob.discover("nope")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Unknown project" in str(e)


def test_discover_matches_aliases_and_skips_global(tmp_path, monkeypatch):
    central = tmp_path / "central"
    central.mkdir()
    _seed_central(central)
    empty_repo = tmp_path / "repo"
    empty_repo.mkdir()
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": str(empty_repo),
        "memory_path": "projects/example-shop", "aliases": ["example-shop", "shop"],
    })

    plan = ob.discover("example-shop", central_dir=central)
    names = {s.label for s in plan.sources}
    assert "central:example-shop_project.md" in names
    assert "central:feedback_shop_dev_localhost.md" in names
    assert "central:feedback_subagent_prefix.md" not in names      # global-promoted
    assert "central:user_setup.md" not in names                    # global-promoted
    assert "central:interface_notes.md" not in names               # 'shop' not a token


def test_discover_picks_up_repo_docs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("repo guidance here", encoding="utf-8")
    central = tmp_path / "central"
    central.mkdir()
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": str(repo),
        "memory_path": "projects/x", "aliases": ["x"],
    })
    plan = ob.discover("x", central_dir=central)
    assert any(s.label == "repo:CLAUDE.md" for s in plan.sources)


def test_discover_filename_match_not_content(tmp_path, monkeypatch):
    # Regression: slug 'vehicles' must NOT match another project's content that
    # mentions a "My Drive/Vehicles/" path (filename-only matching).
    central = tmp_path / "central"
    central.mkdir()
    (central / "garage_project.md").write_text(
        "Asset stored in My Drive/Vehicles/2007_sports_car/", encoding="utf-8"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "personal", "repo_path": str(repo),
        "memory_path": "projects/vehicles", "aliases": ["vehicles"],
    })
    plan = ob.discover("vehicles", central_dir=central)
    assert all("garage" not in s.label for s in plan.sources)


def test_discover_reads_more_repo_doc_types(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("readme", encoding="utf-8")
    (repo / "Matchmaker_SOP.md").write_text("sop", encoding="utf-8")
    (repo / "AGENT_DESIGN.md").write_text("design", encoding="utf-8")
    central = tmp_path / "central"
    central.mkdir()
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "personal", "repo_path": str(repo),
        "memory_path": "projects/x", "aliases": ["x"],
    })
    plan = ob.discover("x", central_dir=central)
    labels = {s.label for s in plan.sources}
    assert {"repo:README.md", "repo:Matchmaker_SOP.md", "repo:AGENT_DESIGN.md"} <= labels


def test_scaffold_stages_sources_not_into_memory(tmp_path):
    plan = ob.OnboardPlan(
        slug="demo", workspace="business", repo_path=None,
        memory_path="projects/demo", aliases=["demo"],
        sources=[ob.Source("central:demo_project.md", tmp_path / "x.md", "DEMOFACT")],
    )
    written = ob.scaffold(plan, root=tmp_path)
    pdir = tmp_path / "projects" / "demo"
    # raw source staged under sources/ (NOT memory/, so the loader won't inject it)
    src_files = list((pdir / "sources").glob("*.md"))
    assert src_files and "DEMOFACT" in src_files[0].read_text()
    assert (pdir / "AGENTS.md").exists()
    assert (pdir / "memory" / ".gitkeep").exists()
    assert list((pdir / "memory").glob("*.md")) == []  # nothing curated yet
    assert any(p.name == "AGENTS.md" for p in written)


def test_scaffold_idempotent_without_overwrite(tmp_path):
    plan = ob.OnboardPlan(
        slug="demo", workspace="business", repo_path=None,
        memory_path="projects/demo", aliases=["demo"],
        sources=[ob.Source("central:a.md", tmp_path / "a.md", "FACT")],
    )
    ob.scaffold(plan, root=tmp_path)
    written2 = ob.scaffold(plan, root=tmp_path)
    assert all(p.name != "central-a.md.md" for p in written2)  # source not rewritten


def test_curate_writes_curated_via_dispatch(tmp_path, monkeypatch):
    from agentos.core import router

    plan = ob.OnboardPlan(
        slug="demo", workspace="business", repo_path=None,
        memory_path="projects/demo", aliases=["demo"],
        sources=[ob.Source("central:a.md", tmp_path / "a.md", "raw fact text")],
    )
    monkeypatch.setattr(
        router, "dispatch",
        lambda *a, **k: types.SimpleNamespace(ok=True, text="- curated fact", error=""),
    )
    msg = ob.curate(plan, root=tmp_path)
    curated = tmp_path / "projects" / "demo" / "memory" / "curated.md"
    assert curated.exists()
    assert "- curated fact" in curated.read_text()
    assert "Curated →" in msg


def test_ensure_work_project_creates_once(monkeypatch):
    from agentos.storage import file_store as local_store
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": "~/x", "memory_path": "projects/x", "aliases": ["x"],
    })
    p1 = ob.ensure_work_project("x")
    p2 = ob.ensure_work_project("x")
    assert p1["id"] == p2["id"]
    assert sum(1 for p in local_store.list_projects() if p["slug"] == "x") == 1


def test_import_tasks_all_statuses_token_match_idempotent(tmp_path, monkeypatch):
    from agentos.storage import file_store as local_store
    monkeypatch.setattr(config, "project_config", lambda s: {
        "workspace": "business", "repo_path": "~/x", "memory_path": "projects/example-shop",
        "aliases": ["example-shop", "shop"],
    })
    mc = tmp_path / "MISSION_CONTROL.md"
    mc.write_text(
        "| ID | Task | Status | Owner | Priority |\n"
        "| T001 | shop: ship SEO | Done | Gary | High |\n"
        "| T002 | shop: newsletter | To-Do | Gary | High |\n"
        "| T003 | Job Hunt: Facebook poster | To-Do | Gary | Medium |\n"
        "| T004 | shop: fix a bug | Blocked | Gary | Low |\n",
        encoding="utf-8",
    )
    res = ob.import_tasks("example-shop", mc_path=mc)
    assert res["imported"] == 3  # T001 (done), T002 (ready), T004 (blocked) — NOT T003 (no 'shop' token)
    proj = next(p for p in local_store.list_projects() if p["slug"] == "example-shop")
    tasks = local_store.list_tasks(project_id=proj["id"])
    titles = [t["title"] for t in tasks]
    assert any("ship SEO" in t for t in titles)                  # Done task imported (history)
    assert any(t["status"] == "done" for t in tasks)
    assert not any("Facebook" in t for t in titles)              # token match excludes Facebook
    # idempotent
    res2 = ob.import_tasks("example-shop", mc_path=mc)
    assert res2["imported"] == 0 and res2["skipped"] == 3


def test_curate_no_sources_is_noop(tmp_path):
    plan = ob.OnboardPlan(
        slug="demo", workspace="business", repo_path=None,
        memory_path="projects/demo", aliases=["demo"], sources=[],
    )
    assert ob.curate(plan, root=tmp_path) == "No sources to curate."
