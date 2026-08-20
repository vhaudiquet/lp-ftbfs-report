"""Unit tests for the scheduler script (command building and freshness)."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCHEDULER_PATH = Path(__file__).resolve().parents[1] / "src" / "scheduler.py"


def _load_scheduler(monkeypatch, env: dict[str, str], tmp_path: Path):
    """Load scheduler.py as a module with the given env and importable lp_ftbfs_report stub."""
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    for k in list(os.environ):
        if k.startswith("FTBFS_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("scheduler_under_test", SCHEDULER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_build_command_standard_mode(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {
            "FTBFS_ARCHIVE": "primary",
            "FTBFS_SERIES": "noble",
            "FTBFS_ARCHS": "amd64,arm64,ppc64el",
            "FTBFS_FILENAME": "index",
            "FTBFS_REPORTS_DIR": str(tmp_path),
        },
        tmp_path,
    )
    cmd = mod.build_command(str(tmp_path))
    # Standard mode: archive series arch...
    assert cmd[0] == sys.executable
    assert "-m" in cmd and "lp_ftbfs_report.build_status" in cmd
    assert "--filename" in cmd
    assert "primary" in cmd
    assert "noble" in cmd
    assert "amd64" in cmd and "arm64" in cmd and "ppc64el" in cmd
    assert "--output-dir" in cmd


def test_build_command_ppa_mode_omits_archive_positional(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {
            "FTBFS_PPA": "ubuntu-toolchain-r/test",
            "FTBFS_SERIES": "noble",
            "FTBFS_ARCHS": "amd64",
            "FTBFS_FILENAME": "index",
            "FTBFS_ARCHIVE": "primary",  # must be ignored in PPA mode
            "FTBFS_REPORTS_DIR": str(tmp_path),
        },
        tmp_path,
    )
    cmd = mod.build_command(str(tmp_path))
    assert "--ppa" in cmd
    ppa_idx = cmd.index("--ppa")
    assert cmd[ppa_idx + 1] == "ubuntu-toolchain-r/test"
    # After the options, positionals should be: <series> <arch> (no archive).
    # Find the positionals: everything after the last option that takes a value.
    assert "noble" in cmd
    assert "amd64" in cmd
    # 'primary' must NOT appear as a positional in PPA mode.
    assert "primary" not in cmd


def test_build_command_passes_optional_flags(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {
            "FTBFS_ARCHIVE": "primary",
            "FTBFS_SERIES": "noble",
            "FTBFS_ARCHS": "amd64",
            "FTBFS_FILENAME": "index",
            "FTBFS_UPDATES_ARCHIVE": "updates",
            "FTBFS_REFERENCE_SERIES": "mantic",
            "FTBFS_REGRESSIONS_ONLY": "1",
            "FTBFS_RELEASE_ONLY": "1",
            "FTBFS_VERBOSE": "1",
            "FTBFS_REPORTS_DIR": str(tmp_path),
        },
        tmp_path,
    )
    cmd = mod.build_command(str(tmp_path))
    assert "--updates-archive" in cmd and "updates" in cmd
    assert "--reference-series" in cmd and "mantic" in cmd
    assert "--regressions-only" in cmd
    assert "--release-only" in cmd
    assert "--verbose" in cmd


def test_build_command_missing_series_raises(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {
            "FTBFS_ARCHIVE": "primary",
            "FTBFS_ARCHS": "amd64",
            "FTBFS_FILENAME": "index",
            "FTBFS_REPORTS_DIR": str(tmp_path),
        },
        tmp_path,
    )
    with pytest.raises(RuntimeError):
        mod.build_command(str(tmp_path))


def test_is_fresh_false_when_missing(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {"FTBFS_FILENAME": "index", "FTBFS_REPORTS_DIR": str(tmp_path)},
        tmp_path,
    )
    assert mod.is_fresh(str(tmp_path), 24) is False


def test_is_fresh_true_when_recent(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {"FTBFS_FILENAME": "index", "FTBFS_REPORTS_DIR": str(tmp_path)},
        tmp_path,
    )
    (tmp_path / "index.html").write_text("<html></html>")
    assert mod.is_fresh(str(tmp_path), 24) is True


def test_is_fresh_false_when_stale(monkeypatch, tmp_path):
    mod = _load_scheduler(
        monkeypatch,
        {"FTBFS_FILENAME": "index", "FTBFS_REPORTS_DIR": str(tmp_path)},
        tmp_path,
    )
    path = tmp_path / "index.html"
    path.write_text("<html></html>")
    # Backdate the file by 48 hours.
    import os as _os
    import time as _time

    old = _time.time() - 48 * 3600
    _os.utime(path, (old, old))
    assert mod.is_fresh(str(tmp_path), 24) is False


def test_seconds_until_next_run_in_future(monkeypatch, tmp_path):
    mod = _load_scheduler(monkeypatch, {}, tmp_path)
    wait = mod.seconds_until_next_run(hour=23, minute=59)
    assert 0 < wait <= 24 * 3600
