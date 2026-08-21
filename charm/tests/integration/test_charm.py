"""Integration tests for the lp-ftbfs-report charm.

Prerequisites (the run-integration-test.sh script handles these):
  1. A Juju K8s controller is available.
  2. The rock is built and pushed to a registry (OCI_IMAGE env var).
  3. The charm is packed (CHARM_PATH env var or .charm in charm/).

Run manually:
  cd charm
  ../scripts/run-integration-test.sh
"""

from __future__ import annotations

import pathlib

import jubilant
import pytest
import requests
import yaml

APP = "lp-ftbfs-report"
REPORT_TIMEOUT = 180  # 3 min — allows for image pull + dummy-data report generation
HTTP_TIMEOUT = 10


def _load_metadata() -> dict:
    charm_dir = pathlib.Path(__file__).resolve().parents[2]
    return yaml.safe_load((charm_dir / "metadata.yaml").read_text())


@pytest.mark.juju_setup
def test_deploy(
    charm: pathlib.Path,
    oci_image: str,
    juju: jubilant.Juju,
):
    """Deploy the charm with the OCI image resource and wait for active status."""
    metadata = _load_metadata()
    app_name = metadata["name"]
    resources = dict.fromkeys(metadata.get("resources", {}), oci_image)

    juju.deploy(
        charm,
        app_name,
        resources=resources,
        config={
            "series": "oracular",
            "architectures": "amd64",
            "filename": "index",
            "schedule-hour": 2,
            "stale-after-hours": 24,
            "dummy-data": "/opt/sample.json",
        },
    )

    # Wait for the unit to reach active. With dummy-data, report generation
    # takes seconds, not hours.
    try:
        juju.wait(lambda status: jubilant.all_active(status, app_name), timeout=REPORT_TIMEOUT)
    except TimeoutError:
        # Print debug logs to help diagnose why the charm didn't reach active.
        print("\n=== DEBUG LOG (last 100 lines) ===")
        print(juju.debug_log(limit=100))
        raise


def test_report_is_served_over_http(juju: jubilant.Juju):
    """Verify that the HTTP server is serving the generated report."""
    status = juju.status()
    unit = status.apps[APP].units[f"{APP}/0"]
    assert unit.address, "Unit has no address"
    url = f"http://{unit.address}:8080/index.html"
    response = requests.get(url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    # The report is an HTML page with a title mentioning FTBFS.
    assert "<html" in response.text.lower()
    assert "ftbfs" in response.text.lower() or "failed" in response.text.lower()
    # Check that static assets are also served.
    css_url = f"http://{unit.address}:8080/style.css"
    css_response = requests.get(css_url, timeout=HTTP_TIMEOUT)
    assert css_response.status_code == 200
    assert "text/css" in css_response.headers.get("content-type", "")


def test_generate_action(juju: jubilant.Juju):
    """Run the generate action and verify it returns results."""
    result = juju.run(f"{APP}/0", "generate")
    assert result.success
    assert "reports-dir" in result.results
    assert result.results["reports-dir"] == "/srv/reports"
    assert "has-fresh-report" in result.results


def test_scheduler_service_running(juju: jubilant.Juju):
    """Check that both Pebble services are running in the workload container."""
    output = juju.cli(
        "ssh", "--container", "ftbfs-report", f"{APP}/0",
        "/usr/bin/pebble services",
    )
    assert "ftbfs-scheduler" in output
    assert "ftbfs-server" in output
    assert "active" in output.lower()

def test_config_change_updates_server_port(juju: jubilant.Juju):
    """Change the server port and verify the charm updates."""
    juju.config(APP, {"server-port": 9090})
    juju.wait(lambda status: jubilant.all_active(status, APP), timeout=120)

    status = juju.status()
    unit = status.apps[APP].units[f"{APP}/0"]
    assert unit.address, "Unit has no address"

    # Old port should no longer respond (give it a moment to restart).
    old_url = f"http://{unit.address}:8080/index.html"
    try:
        requests.get(old_url, timeout=HTTP_TIMEOUT)
        pytest.fail("Old port 8080 should not respond after config change")
    except requests.ConnectionError:
        pass  # expected — old port is closed

    # New port should serve the report.
    new_url = f"http://{unit.address}:9090/index.html"
    response = requests.get(new_url, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    assert "<html" in response.text.lower()

    # Restore default for any subsequent tests.
    juju.config(APP, {"server-port": 8080})
    juju.wait(lambda status: jubilant.all_active(status, APP), timeout=120)
