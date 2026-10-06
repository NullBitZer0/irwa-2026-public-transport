"""
The red team must be able to refuse to score the wrong thing.

A security report is evidence. Evidence gathered from a target that was never
actually reached is worse than no report, because it reads as a clean result.

There was a real version of this failure: the harness defaulted to a port that
was not the orchestrator. Something else on that port answered `/health` with
`{"status": "ok"}` and 404'd `/chat`, and the run still reported every live
probe SECURE — a perfect score for a system nobody had tested. These tests pin
the preflight that now stops that.

Run:
    pytest evaluation/test_redteam_preflight.py -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_HARNESS = Path(__file__).with_name("redteam_prompt_injection.py")


def load_harness():
    """Imports the harness under a private name so patching does not leak."""
    spec = importlib.util.spec_from_file_location("_rt_harness_under_test", _HARNESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for httpx.Client for one URL."""

    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return self._handler("GET", url)

    def post(self, url, **kwargs):
        return self._handler("POST", url)


def test_it_refuses_a_service_that_is_not_the_orchestrator(monkeypatch) -> None:
    """
    A port answering /health is not proof of identity.

    The decoy answered with `{"status": "ok"}` and no `agent` field, which is
    exactly the shape a naive reachability check accepts.
    """
    rt = load_harness()

    def handler(method, url):
        if url.endswith("/health") and "8100" in url:
            return FakeResponse({"status": "ok"})  # no agent: not ours
        return FakeResponse({"status": "ok", "agent": "booking"})

    monkeypatch.setattr(rt.httpx, "Client", lambda **kw: FakeClient(handler))
    monkeypatch.setattr(rt.httpx, "get", lambda url, **kw: handler("GET", url))

    with pytest.raises(SystemExit) as exit_info:
        rt.preflight()

    assert "not the orchestrator" in str(exit_info.value)


def test_it_refuses_a_healthy_orchestrator_that_answers_no_routes(monkeypatch) -> None:
    """
    Identity alone is not enough.

    Correct name on `/health`, but the planner returns nothing: the retrieval and
    rendering probes would then "pass" by observing a dead pipeline.
    """
    rt = load_harness()
    health = {
        "8100": {"status": "ok", "agent": "orchestrator"},
        "8102": {"status": "ok", "agent": "booking"},
    }

    def handler(method, url):
        if method == "GET":
            return FakeResponse(health["8100"] if "8100" in url else health["8102"])
        return FakeResponse(
            {"response": "No bus service found.", "route_options": []}, status_code=200
        )

    monkeypatch.setattr(rt.httpx, "Client", lambda **kw: FakeClient(handler))
    monkeypatch.setattr(rt.httpx, "get", lambda url, **kw: handler("GET", url))

    with pytest.raises(SystemExit) as exit_info:
        rt.preflight()

    assert "returned no routes" in str(exit_info.value)


def test_it_refuses_an_unreachable_service(monkeypatch) -> None:
    """Connection refused is a hard stop, never a silent skip."""

    def handler(method, url):
        raise ConnectionError("connection refused")

    rt = load_harness()
    monkeypatch.setattr(rt.httpx, "Client", lambda **kw: FakeClient(handler))
    monkeypatch.setattr(rt.httpx, "get", lambda url, **kw: handler("GET", url))

    with pytest.raises(SystemExit) as exit_info:
        rt.preflight()

    assert "unreachable" in str(exit_info.value)


def test_it_proceeds_when_the_target_answers_properly(monkeypatch, capsys) -> None:
    """The happy path must stay silent about nothing and return normally."""
    rt = load_harness()
    health = {
        "8100": {"status": "ok", "agent": "orchestrator"},
        "8102": {"status": "ok", "agent": "booking"},
    }

    def handler(method, url):
        if method == "GET":
            return FakeResponse(health["8100"] if "8100" in url else health["8102"])
        return FakeResponse({"route_options": [{"route_id": "RM-01"}, {"route_id": "RM-02"}]})

    monkeypatch.setattr(rt.httpx, "Client", lambda **kw: FakeClient(handler))
    monkeypatch.setattr(rt.httpx, "get", lambda url, **kw: handler("GET", url))

    rt.preflight()

    assert "Target confirmed" in capsys.readouterr().out


def test_the_default_urls_are_the_published_host_ports() -> None:
    """
    The default must match what docker-compose publishes.

    These are the orchestrator's internal container ports, which are not the same
    thing; that mismatch is what silently pointed the run at another service.
    """
    rt = load_harness()

    assert rt.ORCHESTRATOR == "http://localhost:8100"
    assert rt.BOOKING == "http://localhost:8102"

    compose = (Path(__file__).with_name("..") / "docker-compose.yml").read_text()
    assert '"8100:8000"' in compose, "orchestrator host port moved"
    assert '"8102:8002"' in compose, "booking host port moved"
