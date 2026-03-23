# LP FTBFS Report

Launchpad FTBFS (Failed To Build From Source) report generator.

## Description

This tool generates reports about packages that failed to build from source on Launchpad.

## Usage

```bash
uv run lp-ftbfs-report
```

## Developing

### Prerequisites

This project uses [uv](https://docs.astral.sh/uv/) for fast Python package management and [ruff](https://docs.astral.sh/ruff/) for linting and formatting.

### Setup

1. Install uv and ruff if you haven't already.
   ```bash
   sudo snap install astral-uv
   sudo snap install ruff
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

### Linting, formatting, cleanup

```bash
uv run poe lint
uv run poe format
uv run poe clean
```

### Dependencies

Add a runtime dependency:

```bash
uv add <package-name>
```

Add a development dependency:

```bash
uv add --dev <package-name>
```

## License

GPL-2.0-or-later
