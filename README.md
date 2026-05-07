# LP FTBFS Report

Launchpad FTBFS (Failed To Build From Source) report generator.

## Description

This tool generates reports about packages that failed to build from source on Launchpad.

## Usage

```bash
uv run lp-ftbfs-report [options] <archive> <series> <arch> [<arch> ...]
```

### Basic Examples

Generate FTBFS report for primary archive:
```bash
uv run lp-ftbfs-report primary resolute amd64 arm64 armhf ppc64el s390x riscv64 i386
```

Generate FTBFS report for a PPA:
```bash
uv run lp-ftbfs-report --ppa owner/ppa_name resolute amd64 arm64 armhf ppc64el s390x riscv64 i386
```

Generate FTBFS report for dummy data (for testing or frontend development):
```bash
uv run lp-ftbfs-report --dummy-data tests/fixtures/sample.json oracular amd64
```

### Command-Line Options

- `-f, --filename`: File name prefix for the result
- `-n, --notice`: HTML notice file to include in the page header
- `--regressions-only`: Only report build regressions compared to the main archive
- `--release-only`: Only include sources published in the release pocket
- `--updates-archive`: Name of an updates archive
- `--reference-series`: Series to look for successful builds

#### Data source

There are 4 data sources supported:
- `primary`: Ubuntu primary archive
- `archive-test-rebuild-name`: Ubuntu archive test rebuild
- `--ppa`: Any PPA (format: owner/ppa_name)
- `--dummy-data`: Dummy data from JSON fixture file, for testing

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

### Testing

Run the Python test suite:
```bash
uv run pytest tests/ -v
```

There are also frontend tests available. The testing environment uses the `bun` JavaScript runtime to directly execute the code that would be executed in a browser. To run frontend tests:
```bash
cd tests/frontend
bun install  # First time only
bun run ci   # Full CI pipeline: typecheck + lint + format + tests
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
