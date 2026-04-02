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

This project uses [uv](https://docs.astral.sh/uv/) for fast Python package management and [ruff](https://docs.astral.sh/ruff/) for linting and formatting, as well as [ty](https://docs.astral.sh/ty/) for type checking.

### Setup

1. Install uv if you haven't already. (notice that uv requires classic confinement)
   ```bash
   sudo snap install astral-uv --classic
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
