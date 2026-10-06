"""
The red team must be able to refuse to score the wrong thing.

A security report is evidence. Evidence gathered from a target that was never
actually reached is worse than no report, because it reads as a clean result.

There was a real version of this failure: the harness defaulted to a port that
was not the orchestrator. Something else on that port answered `/health` with
`{"status": "ok"}` and 404'd `/chat`, and the run still reported every live
probe SECURE — a perfect score for a system nobody had tested. These tests pin
the preflight that now stops that.

They stub *every* network call the preflight makes, including sign-in. That was
learned the hard way: two of these tests passed on a developer machine and failed
in CI, because they reached `authenticate()`, which does a real HTTP POST, and
the developer's stack happened to be running. A unit test that passes only when
something unrelated is up is not a unit test.

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

ORCHESTRATOR_HEALTH = {"status": "ok", "agent": "orchestrator", "version": "1.0.0"}
BOOKING_HEALTH = {"status": "ok", "agent": "booking", "version": "0.2.0"}


def load_harness():
    """Imports the harness under a private name so patching does not leak."""
    spec = importlib.util.spec_from_file_location("_rt_harness_under_test", _HARNESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, cookies: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.cookies = FakeCookies(cookies or {})

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeCookies(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class FakeClient:
    """
    Stands in for httpx.Client for one handler.

    Carries the cookies through, because the difference between a signed-in
    request and an anonymous one *is* the cookie — and the preflight cares about
    exactly that difference.
    """

    def __init__(self, handler, cookies=None):
        self._handler = handler
        self._cookies = cookies or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return self._handler("GET", url, self._cookies)

    def post(self, url, **kwargs):
        return self._handler("POST", url, self._cookies)


def stub_network(monkeypatch, rt, *, orchestrator_health=None, booking_health=None,
                 chat_body=None, chat_status=200, health_raises=False, anonymous_status=401):
    """
    Replaces every network call the preflight makes: health, sign-in and /chat.

    Defaults describe a healthy, signed-in system, so each test only has to state
    the one thing it is testing.
    """
    if orchestrator_health is None:
        orchestrator_health = ORCHESTRATOR_HEALTH
    if booking_health is None:
        booking_health = BOOKING_HEALTH

    def handler(method, url, cookies=None):
        cookies = cookies or {}
        if health_raises:
            raise ConnectionError("connection refused")
        if method == "GET":
            is_booking = "8102" in url or "booking" in url
            return FakeResponse(booking_health if is_booking else orchestrator_health)
        if "/auth/register" in url:
            return FakeResponse({"status": "OK", "user": {}}, cookies={"lankajourney_session": "t"})
        if anonymous_status is not None and not cookies:
            # No session cookie: this is the unauthenticated probe.
            return FakeResponse({"detail": "Please sign in to continue."}, status_code=anonymous_status)
        return FakeResponse(chat_body or {}, status_code=chat_status)

    monkeypatch.setattr(
        rt.httpx, "Client", lambda **kw: FakeClient(handler, cookies=kw.get("cookies"))
    )
    monkeypatch.setattr(rt.httpx, "get", lambda url, **kw: handler("GET", url))
    monkeypatch.setattr(rt.httpx, "post", lambda url, **kw: handler("POST", url))
    return handler


def test_it_refuses_a_service_that_is_not_the_orchestrator(monkeypatch) -> None:
    """
    A port answering /health is not proof of identity.

    The decoy answered with `{"status": "ok"}` and no `agent` field, which is
    exactly the shape a naive reachability check accepts.
    """
    rt = load_harness()
    stub_network(monkeypatch, rt, orchestrator_health={"status": "ok"})

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
    stub_network(monkeypatch, rt, chat_body={"response": "No bus service found.", "route_options": []})

    with pytest.raises(SystemExit) as exit_info:
        rt.preflight()

    assert "returned no routes" in str(exit_info.value)


def test_it_refuses_an_unreachable_service(monkeypatch) -> None:
    """Connection refused is a hard stop, never a silent skip."""
    rt = load_harness()
    stub_network(monkeypatch, rt, health_raises=True)

    with pytest.raises(SystemExit) as exit_info:
        rt.preflight()

    assert "unreachable" in str(exit_info.value)


def test_it_refuses_when_the_api_is_not_actually_protected(monkeypatch) -> None:
    """
    An open /chat means a whole run of "no injection observed" from a set of 401s.

    This is the assertion that keeps the report honest about the target it scored.
    """
    rt = load_harness()
    # An API that answers an anonymous request instead of refusing it.
    stub_network(monkeypatch, rt, chat_body={"route_options": [{"route_id": "RM-01"}]},
                 anonymous_status=200)

    with pytest.raises(SystemExit) as exit_info:
        rt.preflight()

    assert "expected 401" in str(exit_info.value)


def test_it_proceeds_when_the_target_answers_properly(monkeypatch, capsys) -> None:
    """The happy path must return normally and say what it confirmed."""
    rt = load_harness()
    stub_network(monkeypatch, rt, chat_body={"route_options": [{"route_id": "RM-01"},
                                                                 {"route_id": "RM-02"}]})

    rt.preflight()

    out = capsys.readouterr().out
    assert "Target confirmed" in out
    assert "401" in out


def test_the_default_urls_are_the_published_host_ports() -> None:
    """
    The default must match what docker-compose publishes.

    These are not the agents' internal container ports, which are a different set
    of numbers; that mismatch is what silently pointed the run at another service.
    """
    rt = load_harness()

    assert rt.ORCHESTRATOR == "http://localhost:8100"
    assert rt.BOOKING == "http://localhost:8102"

    compose = (Path(__file__).with_name("..") / "docker-compose.yml").read_text()
    assert '"8100:8000"' in compose, "orchestrator host port moved"
    assert '"8102:8002"' in compose, "booking host port moved"
