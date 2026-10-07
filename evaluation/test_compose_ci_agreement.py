"""
Compose and CI must agree about bind mounts, or the live stack dies in CI only.

A git-ignored bind-mount directory does not exist in a fresh checkout. Docker
then creates it owned by root, while the agents run as an unprivileged user, and
the service that needed it cannot write there. Nothing about that shows up in a
build: the image is fine, the container *starts*, and the healthcheck can even
pass before the first write is attempted. What you get is a service that
crash-loops in CI and works perfectly on a developer machine, where the directory
was created by the user.

That is not hypothetical. The conversation store added `./data/conversations`
and CI only pre-created `./data/booking`, so the red-team job — the one that runs
the real stack — failed while every other job passed.

These tests assert the agreement, so the next bind mount cannot be added without
being listed in CI.

Run:
    pytest evaluation/test_compose_ci_agreement.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX_CONF = ROOT / "frontend" / "nginx.conf"
COMPOSE = (ROOT / "docker-compose.yml").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
GITIGNORE = (ROOT / ".gitignore").read_text()


def bind_mounted_data_dirs() -> set[str]:
    """Every ./data/... host directory bind-mounted into a container."""
    return {m.lstrip("./") for m in re.findall(r"(\./data/[A-Za-z0-9_./-]+):/", COMPOSE)}


def ci_precreated_dirs() -> set[str]:
    """Directories the workflow creates before `docker compose up`."""
    created: set[str] = set()
    for line in re.findall(r"mkdir -p ([^\n#]+)", WORKFLOW):
        for part in line.split():
            if part.startswith("data/"):
                created.add(part.strip())
    return created


def test_every_bind_mounted_data_dir_is_pre_created_in_ci() -> None:
    """
    The failure this prevents is invisible until CI, and takes down the one job
    that tests the running system.
    """
    missing = bind_mounted_data_dirs() - ci_precreated_dirs()

    assert not missing, (
        "bind-mounted but not created in CI (Docker would make them root-owned "
        f"and the agent could not write to them): {sorted(missing)}"
    )


def test_the_precreated_dirs_are_all_actually_mounted() -> None:
    """The reverse direction, so the list cannot rot into folklore."""
    extra = ci_precreated_dirs() - bind_mounted_data_dirs()

    assert not extra, f"CI creates directories nothing mounts: {sorted(extra)}"


def test_bind_mounted_data_dirs_are_gitignored() -> None:
    """
    If these were committed, a developer's local files would shadow CI's
    container-owned ones — or a database would end up in git.
    """
    for directory in sorted(bind_mounted_data_dirs()):
        assert f"{directory}/" in GITIGNORE, f"{directory}/ is bind-mounted but not ignored"


def published_host_ports() -> dict[str, str]:
    """service -> the host port compose publishes for it."""
    ports: dict[str, str] = {}
    service = None
    for line in COMPOSE.splitlines():
        heading = re.match(r"^  ([a-z][\w-]*):\s*$", line)
        if heading:
            service = heading.group(1)
            continue
        mapping = re.match(r'\s*- "((\d+):(\d+))"\s*$', line)
        if mapping and service:
            ports[service] = mapping.group(2)  # host side of "host:container"
    return ports


def test_the_red_team_job_targets_the_published_host_ports() -> None:
    """
    The harness must use the port compose publishes, not the container's own.

    It once defaulted to the in-container pair, which on a developer machine
    pointed at an unrelated service that answered /health and 404'd everything
    else — and the run still reported every probe secure.
    """
    ports = published_host_ports()
    assert {"orchestrator", "booking", "planner"} <= set(ports), f"parsed: {ports}"

    for env_var, service in (
        ("RT_ORCHESTRATOR_URL", "orchestrator"),
        ("RT_BOOKING_URL", "booking"),
        ("RT_PLANNER_URL", "planner"),
    ):
        match = re.search(rf"{env_var}: (\S+)", WORKFLOW)
        assert match, f"{env_var} is not set for the red-team job"

        targeted = match.group(1).rsplit(":", 1)[-1]
        assert targeted == ports[service], (
            f"{env_var} targets port {targeted}, but compose publishes "
            f"{ports[service]} for {service}"
        )


def test_the_harness_defaults_match_the_published_host_ports() -> None:
    """Same agreement, checked on the harness's own defaults rather than CI's."""
    from evaluation.redteam_prompt_injection import BOOKING, ORCHESTRATOR

    ports = published_host_ports()

    assert ORCHESTRATOR.endswith(f":{ports['orchestrator']}")
    assert BOOKING.endswith(f":{ports['booking']}")


def test_the_healthcheck_wait_counts_services_rather_than_hardcoding() -> None:
    """
    A hardcoded count silently stops covering a newly added service, which is
    how an unhealthy agent passes CI.
    """
    assert "hardcoding" in WORKFLOW or "--format json" in WORKFLOW
    assert re.search(r"healthcheck", WORKFLOW), "the health wait must derive the count from the config"


# ── The proxy must survive a backend restart ─────────────────────────────────


def test_the_api_proxy_resolves_the_backend_at_request_time() -> None:
    """
    A static `proxy_pass http://orchestrator:8000/` breaks on every rebuild.

    nginx resolves that hostname once, when it loads the config, and keeps the
    address for the life of the process. Recreating a container gives it a new
    IP, so the proxy keeps dialling an address that no longer exists and answers
    502 — while the backend is healthy, its own healthcheck passes, and a direct
    request from inside the frontend container succeeds. It looks exactly like a
    broken backend, and restarting nginx is the only thing that fixes it until
    the next rebuild.
    """
    config = NGINX_CONF.read_text()

    assert "resolver 127.0.0.11" in config, (
        "no resolver configured, so the upstream address is resolved once at startup"
    )
    assert "proxy_pass $orchestrator_upstream" in config, (
        "proxy_pass has a variable upstream, which forces per-request resolution"
    )


def test_the_api_prefix_is_stripped_before_proxying() -> None:
    """
    Regression from the fix above: a variable proxy_pass stops nginx replacing
    the matched prefix, so /api/chat reaches the backend as /api/chat and 404s.

    A regex location with the remainder captured is what keeps /api/chat -> /chat
    while still resolving the upstream per request.
    """
    config = NGINX_CONF.read_text()

    assert "location ~ ^/api/" in config, "the /api location must capture the remainder"
    assert "proxy_pass $orchestrator_upstream/$1" in config
