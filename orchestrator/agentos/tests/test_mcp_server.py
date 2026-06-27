"""Phase 3 — MCP server tool registration and behavior.

Tests the underlying tool functions (via FunctionTool.fn) without spinning up
the stdio transport. Dispatch/workflow tools use stubbed routing.
"""

from __future__ import annotations

import asyncio


from agentos.entrypoints import mcp_server


def _tool_fn(name: str):
    """Get the underlying python function for a registered MCP tool."""
    tool = asyncio.run(mcp_server.mcp.get_tool(name))
    return tool.fn


def test_all_expected_tools_registered():
    names = asyncio.run(mcp_server.mcp.list_tools())
    registered = {t.name for t in names}
    expected = {
        "list_agents", "list_workflows", "dispatch",
        "run_workflow", "get_run", "recent_runs", "budget_status",
    }
    assert expected.issubset(registered), f"Missing: {expected - registered}"


def test_list_agents_returns_eight():
    fn = _tool_fn("list_agents")
    agents = fn()
    assert len(agents) == 8
    assert all("name" in a and "model" in a for a in agents)
    names = {a["name"] for a in agents}
    assert "researcher" in names


def test_list_workflows_returns_shipped():
    fn = _tool_fn("list_workflows")
    workflows = fn()
    names = {w["name"] for w in workflows}
    assert "deep-research" in names
    assert "ship-feature" in names


def test_dispatch_tool_delegates_to_router(monkeypatch):
    from agentos.core import router
    from agentos.core.router import DispatchOutcome

    monkeypatch.setattr(
        router, "dispatch",
        lambda agent, task, **kw: DispatchOutcome(
            ok=True, run_id="abc", text="done", cost_usd=0.02, model="claude-haiku-4-5"
        ),
    )
    fn = _tool_fn("dispatch")
    result = fn(agent="researcher", task="hi")
    assert result["ok"]
    assert result["run_id"] == "abc"
    assert result["output"] == "done"
    assert result["cost_usd"] == 0.02


def test_budget_status_tool(monkeypatch):
    from agentos.core import budget, config

    monkeypatch.setattr(budget, "today_spend", lambda: 5.0)
    monkeypatch.setattr(
        config, "budget_for_project", lambda project=None: {"daily_usd": 25.0}
    )
    fn = _tool_fn("budget_status")
    status = fn()
    assert status["spent_usd"] == 5.0
    assert status["daily_cap_usd"] == 25.0
    assert status["pct_used"] == 20.0


def test_full_protocol_roundtrip():
    """End-to-end through the actual MCP protocol via an in-memory client.

    This is what Claude Desktop does: list tools, then call one.
    """
    from fastmcp import Client

    async def run():
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert "list_agents" in names
            assert "dispatch" in names

            result = await client.call_tool("list_agents", {})
            data = result.data if hasattr(result, "data") else result
            assert len(data) == 8

    asyncio.run(run())
