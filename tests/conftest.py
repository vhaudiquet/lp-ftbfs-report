"""Pytest configuration and shared fixtures."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir():
    """Return path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_fixture_data(fixture_dir):
    """Load sample fixture data."""
    with open(fixture_dir / "sample.json") as f:
        return json.load(f)


@pytest.fixture
def sample_fixture_path(fixture_dir):
    """Return path to sample fixture file."""
    return str(fixture_dir / "sample.json")


@pytest.fixture
def mock_launchpad(mocker):
    """Create a mock Launchpad object."""
    mock_lp = mocker.MagicMock()
    mock_lp.distributions = {"ubuntu": mocker.MagicMock()}
    return mock_lp


@pytest.fixture
def mock_ubuntu(mocker):
    """Create a mock Ubuntu distribution object."""
    mock_ubuntu = mocker.MagicMock()
    mock_ubuntu.name = "ubuntu"
    return mock_ubuntu
