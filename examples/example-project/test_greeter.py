"""Trivial test for greeter.greet — runnable with `python -m pytest` from this dir.

Standalone on purpose: no AgentOS imports, no fixtures. A newcomer can clone the
example, run the test, and see green before touching the orchestrator.
"""

from __future__ import annotations

from greeter import greet


def test_greet_with_name():
    assert greet("Ada") == "Hello, Ada!"


def test_greet_trims_whitespace():
    assert greet("  Grace  ") == "Hello, Grace!"


def test_greet_empty_falls_back():
    assert greet("") == "Hello there!"
    assert greet("   ") == "Hello there!"
