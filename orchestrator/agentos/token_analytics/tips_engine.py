"""Rule-based token-saving tips (after Nate Herk's token-dashboard Tips tab).

Each rule scans the parsed sessions and emits actionable suggestions:
  - repeated file reads (same file Read many times in one session)
  - low cache-hit rate (you're re-sending context instead of caching it)
  - oversized sessions (a single session dwarfing the rest)
"""

from __future__ import annotations

REPEAT_READ_THRESHOLD = 8      # same file read >= this many times in a session
LOW_CACHE_THRESHOLD = 60.0     # cache-hit % below this is flagged
BIG_SESSION_MULTIPLE = 5       # a session > Nx the median is flagged


def _median(nums: list[float]) -> float:
    if not nums:
        return 0.0
    s = sorted(nums)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def generate_tips(sessions: list[dict], totals: dict) -> list[dict]:
    tips: list[dict] = []

    # 1. Repeated file reads
    for s in sessions:
        for path, count in s.get("file_reads", {}).items():
            if count >= REPEAT_READ_THRESHOLD:
                tips.append({
                    "kind": "repeated_read",
                    "severity": "medium",
                    "message": f"Read `{path.split('/')[-1]}` {count}× in one session "
                               f"({s['project']}). Consider reading once and reusing, "
                               f"or narrowing the read range.",
                    "evidence": s["session_id"],
                })

    # 2. Low cache-hit rate (global)
    pct = totals.get("cache_hit_pct", 0.0)
    if pct and pct < LOW_CACHE_THRESHOLD:
        tips.append({
            "kind": "low_cache",
            "severity": "high",
            "message": f"Overall cache-hit rate is {pct}% — below {LOW_CACHE_THRESHOLD:.0f}%. "
                       f"Long, stable context (system prompts, files) caches well; "
                       f"frequently-changing prompts don't. Keep stable context up front.",
            "evidence": "global",
        })

    # 3. Oversized sessions (cost outliers)
    costs = [s["cost_usd"] for s in sessions if s["cost_usd"] > 0]
    med = _median(costs)
    if med > 0:
        for s in sessions:
            if s["cost_usd"] >= med * BIG_SESSION_MULTIPLE:
                tips.append({
                    "kind": "big_session",
                    "severity": "low",
                    "message": f"Session in `{s['project']}` cost "
                               f"${s['cost_usd']:.2f} API-equiv — {s['cost_usd']/med:.0f}× "
                               f"the median session. Large sessions often involve big tool "
                               f"results; consider splitting the work.",
                    "evidence": s["session_id"],
                })

    # Cap so the tab stays actionable; most-severe first.
    order = {"high": 0, "medium": 1, "low": 2}
    tips.sort(key=lambda t: order.get(t["severity"], 3))
    return tips[:25]
