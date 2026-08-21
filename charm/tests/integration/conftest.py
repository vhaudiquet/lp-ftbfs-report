"""Pytest fixtures shared by all integration test modules."""

from __future__ import annotations

import os
import pathlib

import pytest


@pytest.fixture(scope="session")
def charm() -> pathlib.Path:
    """Return the path of the packed .charm file under test.

    Set CHARM_PATH to point at a specific .charm file; otherwise the
    fixture looks for one in the charm/ directory (one level up from
    this file).
    """
    charm_path = os.environ.get("CHARM_PATH")
    if not charm_path:
        charm_dir = pathlib.Path(__file__).resolve().parents[2]
        charms = sorted(charm_dir.glob("*.charm"))
        assert charms, f"No .charm file found in {charm_dir}; run 'charmcraft pack' first"
        assert len(charms) == 1, f"Found multiple charms in {charm_dir}: {charms}"
        charm_path = str(charms[0])
    path = pathlib.Path(charm_path).resolve()
    assert path.is_file(), f"{path} is not a file"
    return path


@pytest.fixture(scope="session")
def oci_image() -> str:
    """Return the registry reference for the OCI image resource.

    The rock must be built and pushed to a registry reachable by the
    Juju controller before running integration tests.  Set OCI_IMAGE to
    the full registry path, e.g. 'localhost:32000/lp-ftbfs-report:1.0.0'.
    """
    oci_image = os.environ.get("OCI_IMAGE")
    assert oci_image, (
        "OCI_IMAGE environment variable not set. "
        "Build the rock, push it to a registry, and export OCI_IMAGE."
    )
    return oci_image
