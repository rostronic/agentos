"""Linear-backed TaskStore adapter.

Maps AgentOS Work concepts onto Linear:
    Project  → Linear Project
    Sprint   → Linear Cycle
    Task     → Linear Issue
    status   → Linear workflow state (mapped both ways)
    depends_on → issue "blocks/blocked-by" relations
    run link → a comment posted on the issue

Talks to Linear's GraphQL API with a personal API key (LINEAR_API_KEY in
config/credentials/.env). The HTTP layer is injectable so it can be tested
without network. Linear is the source of truth for Linear-backed projects;
the dashboard mirrors it into Convex/sqlite via a periodic sync.

This implements the READ + status-update slice of TaskStore that the sprint
executor needs. Project/sprint creation stays in Linear's own UI.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

from agentos.core.config import get_api_key

LINEAR_API = "https://api.linear.app/graphql"

# Linear workflow-state *type* → AgentOS status. Linear state types are:
# backlog, unstarted, started, completed, canceled. Teams also have named
# states; we map by type first, then refine by name where useful.
_STATE_TYPE_MAP = {
    "backlog": "backlog",
    "unstarted": "ready",
    "started": "in_progress",
    "completed": "done",
    "canceled": "cancelled",
}
# AgentOS status → Linear state *name* hints (matched case-insensitively).
_STATUS_TO_STATE_NAME = {
    "backlog": ["backlog"],
    "ready": ["todo", "ready", "unstarted"],
    "in_progress": ["in progress", "started"],
    "blocked": ["blocked"],
    "review": ["in review", "review"],
    "done": ["done", "completed"],
    "cancelled": ["canceled", "cancelled"],
}


def _default_http(query: str, variables: dict, api_key: str) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        LINEAR_API, data=body,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


class LinearStore:
    """A TaskStore backed by Linear. Only the read/update slice is implemented."""

    def __init__(self, api_key: str | None = None, http: Callable | None = None):
        self._api_key = api_key or get_api_key("linear") or ""
        self._http = http or _default_http

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        if not self._api_key:
            raise RuntimeError("LINEAR_API_KEY not set in config/credentials/.env")
        data = self._http(query, variables or {}, self._api_key)
        if data.get("errors"):
            raise RuntimeError(f"Linear API error: {data['errors']}")
        return data.get("data", {})

    # --- mapping helpers ----------------------------------------------------
    @staticmethod
    def issue_to_task(issue: dict, project_id: str = "") -> dict:
        """Convert a Linear issue node into an AgentOS task dict."""
        state = issue.get("state") or {}
        status = _STATE_TYPE_MAP.get(state.get("type"), "backlog")
        # refine 'review'/'blocked' from the state name if present
        name = (state.get("name") or "").lower()
        if "review" in name:
            status = "review"
        elif "block" in name:
            status = "blocked"
        labels = [n["name"] for n in (issue.get("labels", {}) or {}).get("nodes", [])]
        assignee = next((lbl.split(":", 1)[1] for lbl in labels if lbl.startswith("agent:")), None)
        prio_map = {1: "high", 2: "high", 3: "medium", 4: "low", 0: "medium"}
        return {
            "id": issue["id"],
            "project_id": project_id,
            "sprint_id": (issue.get("cycle") or {}).get("id"),
            "title": issue.get("title", ""),
            "description": issue.get("description"),
            "status": status,
            "assignee": assignee,
            "priority": prio_map.get(issue.get("priority", 0), "medium"),
            "depends_on": [],  # populated from relations on demand
            "acceptance_criteria": None,
            "last_run_id": None,
            "url": issue.get("url"),
            "_linear_state_id": state.get("id"),
        }

    # --- reads --------------------------------------------------------------
    def list_tasks(self, project_id: str | None = None, sprint_id: str | None = None,
                   status: str | None = None) -> list[dict]:
        q = """
        query($filter: IssueFilter) {
          issues(filter: $filter, first: 250) {
            nodes { id title description priority url
              state { id name type }
              cycle { id }
              labels { nodes { name } }
            }
          }
        }"""
        filt: dict = {}
        if project_id:
            filt["project"] = {"id": {"eq": project_id}}
        data = self._gql(q, {"filter": filt})
        tasks = [self.issue_to_task(n, project_id or "") for n in data.get("issues", {}).get("nodes", [])]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        if sprint_id:
            tasks = [t for t in tasks if t["sprint_id"] == sprint_id]
        return tasks

    def get_task(self, task_id: str) -> dict | None:
        q = """
        query($id: String!) {
          issue(id: $id) { id title description priority url
            state { id name type } cycle { id } labels { nodes { name } } }
        }"""
        data = self._gql(q, {"id": task_id})
        issue = data.get("issue")
        return self.issue_to_task(issue) if issue else None

    def ready_tasks(self, sprint_id: str) -> list[dict]:
        return [t for t in self.list_tasks(sprint_id=sprint_id) if t["status"] == "ready"]

    # --- updates ------------------------------------------------------------
    def _find_state_id(self, team_id: str, status: str) -> str | None:
        q = """query($id: String!) { team(id: $id) {
          states { nodes { id name type } } } }"""
        data = self._gql(q, {"id": team_id})
        states = data.get("team", {}).get("states", {}).get("nodes", [])
        wanted = _STATUS_TO_STATE_NAME.get(status, [])
        for st in states:
            if st["name"].lower() in wanted:
                return st["id"]
        return None

    def update_task_status(self, task_id: str, status: str, reason: str | None = None) -> None:
        # Resolve the issue's team, find the matching state, then update.
        q = """query($id: String!) { issue(id: $id) { team { id } } }"""
        team_id = self._gql(q, {"id": task_id}).get("issue", {}).get("team", {}).get("id")
        state_id = self._find_state_id(team_id, status) if team_id else None
        if not state_id:
            return
        m = """mutation($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) { success } }"""
        self._gql(m, {"id": task_id, "stateId": state_id})
        if reason:
            self._comment(task_id, f"Status → {status}: {reason}")

    def link_run(self, task_id: str, run_id: str) -> None:
        self._comment(task_id, f"AgentOS run `{run_id}` — see dashboard.")

    def _comment(self, task_id: str, body: str) -> None:
        m = """mutation($id: String!, $body: String!) {
          commentCreate(input: { issueId: $id, body: $body }) { success } }"""
        self._gql(m, {"id": task_id, "body": body})


def get_store(**kw) -> LinearStore:
    return LinearStore(**kw)
