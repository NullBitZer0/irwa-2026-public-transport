"""The simulated-incident toggle: it has to go back off, not just on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from evaluation.conftest import sign_in
from src.orchestrator import server as orch


@pytest.fixture
def client() -> TestClient:
    return sign_in(TestClient(orch.app))


@pytest.fixture
def stub_conditions(monkeypatch: pytest.MonkeyPatch):
    """
    Stands in for the Conditions Agent.

    Its payload keys are the contract: the Orchestrator reads them, and a typo
    here is invisible until the toggle stops working for a real user.
    """

    async def set_simulated_incident(incident_id=None, active=True):
        class Response:
            if active:
                data = {
                    "active": [incident_id],
                    "label": "Accident at the Negombo highway entrance",
                    "simulated": True,
                    "simulated_incident_active": True,
                }
                message = "Simulated incident switched on"
            else:
                data = {"active": [], "simulated_incident_active": False}
                message = "Simulated incident off. Real headlines are still checked."

        return Response()

    monkeypatch.setattr(
        orch._bridge, "set_simulated_incident", set_simulated_incident
    )


class TestTheToggleRoundTrips:
    def test_switching_on_reports_armed(self, client: TestClient, stub_conditions) -> None:
        result = client.post(
            "/demo_incident",
            json={"incident_id": "negombo_highway_accident", "active": True},
        ).json()

        # The UI derives its button label from this. If it is false while the
        # incident is on, the button still reads "Simulate" and the traveller
        # cannot tell the toggle worked.
        assert result["incident_check_armed"] is True
        assert result["active"] == ["negombo_highway_accident"]

    def test_switching_off_reports_disarmed(
        self, client: TestClient, stub_conditions
    ) -> None:
        result = client.post(
            "/demo_incident", json={"incident_id": None, "active": False}
        ).json()

        # The regression: the Orchestrator asked the Conditions Agent for
        # `incident_check_armed`, which that agent never sends. So this was
        # always false — including straight after switching an incident ON, which
        # left the button showing "Simulate" with no way to press it again.
        assert result["incident_check_armed"] is False
        assert result["active"] == []

    def test_on_off_on_off_all_round_trip(
        self, client: TestClient, stub_conditions
    ) -> None:
        """
        The whole point of a toggle: it must come back.

        A control that can only be turned on is indistinguishable from a button
        that fires an action.
        """
        states = []
        for active in (True, False, True, False):
            result = client.post(
                "/demo_incident",
                json={
                    "incident_id": "negombo_highway_accident" if active else None,
                    "active": active,
                },
            ).json()
            states.append(result["incident_check_armed"])

        assert states == [True, False, True, False], states

    def test_the_ui_can_read_the_state_after_a_reload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The button must show the switch's real position, not a default.

        Otherwise a reload mid-demo shows "Simulate" over a live incident, and
        the next press switches it on again instead of clearing it.
        """
        active = {"armed": True, "ids": ["negombo_highway_accident"]}

        async def get_incident_state():
            class Response:
                data = {
                    "active": active["ids"],
                    "simulated_incident_active": active["armed"],
                }

            return Response()

        monkeypatch.setattr(orch._bridge, "get_incident_state", get_incident_state)

        client.post(
            "/demo_incident",
            json={"incident_id": "negombo_highway_accident", "active": True},
        )
        on_reload = client.get("/demo_incident").json()
        assert on_reload["incident_check_armed"] is True
        assert on_reload["active"] == ["negombo_highway_accident"]

        active["armed"] = False
        active["ids"] = []
        off_reload = client.get("/demo_incident").json()
        assert off_reload["incident_check_armed"] is False
        assert off_reload["active"] == []

    def test_an_unreachable_conditions_agent_is_reported_not_faked(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A failure must not read as "armed".

        Returning a fabricated armed state would let the UI claim an incident is
        live when nothing was switched on.
        """

        async def broken(*_args, **_kwargs):
            raise RuntimeError("conditions agent unreachable")

        monkeypatch.setattr(orch._bridge, "set_simulated_incident", broken)

        result = client.post(
            "/demo_incident",
            json={"incident_id": "negombo_highway_accident", "active": True},
        ).json()

        assert result["status"] == "UNAVAILABLE"
        assert result["active"] == []
        assert "incident_check_armed" not in result
