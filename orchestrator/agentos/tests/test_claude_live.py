"""Phase 1 — live Claude provider test.

Gated behind AGENTOS_RUN_INTEGRATION=1 so it doesn't run on every commit.
Requires a real ANTHROPIC_API_KEY. Uses Haiku + tiny token budget to stay cheap.

Run with:
    AGENTOS_RUN_INTEGRATION=1 pytest agentos/tests/test_claude_live.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

_SKIP = os.environ.get("AGENTOS_RUN_INTEGRATION") != "1"
_REASON = "set AGENTOS_RUN_INTEGRATION=1 and ANTHROPIC_API_KEY to run live tests"


@pytest.mark.skipif(_SKIP, reason=_REASON)
def test_claude_roundtrip():
    from agentos.providers.claude import ClaudeProvider

    provider = ClaudeProvider()
    result = provider.dispatch(
        model="claude-haiku-4-5",
        system_prompt="You are a calculator. Answer with only the number.",
        user_message="What is 17 + 25?",
        temperature=0.0,
        max_tokens=16,
    )
    assert "42" in result.text
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.cost_usd > 0
    assert result.provider == "claude"


@pytest.mark.skipif(_SKIP, reason=_REASON)
def test_router_live_dispatch():
    from agentos.core import router

    outcome = router.dispatch(
        "researcher",
        "Reply with exactly the word: ACKNOWLEDGED",
        model_override="claude-haiku-4-5",
    )
    assert outcome.ok, outcome.error
    assert outcome.cost_usd > 0
    assert outcome.run_id
