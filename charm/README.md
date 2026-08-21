# lp-ftbfs-report charm

A [Juju](https://juju.is/) Kubernetes charm that generates a Launchpad
**FTBFS** (Failed To Build From Source) report once a day and serves it over
HTTP — suitable for powering `ftbfs.ubuntu.com`.

It wraps the [`lp-ftbfs-report`](../README.md) CLI and runs two Pebble
services inside a single workload container:

| Service            | Role                                                                |
| ------------------ | ------------------------------------------------------------------ |
| `ftbfs-scheduler`  | Regenerates the report once a day (and once on startup if stale).   |
| `ftbfs-server`     | `python3 -m http.server` serving the persistent report directory.  |

The Launchpad login is **anonymous**, so no credentials are required — the
charm only needs network access to `api.launchpad.net`.

## Layout

```
charm/
  metadata.yaml      # charm metadata (containers, storage, actions, relations)
  config.yaml        # user-facing config options
  actions.yaml       # the `generate` action
  charmcraft.yaml    # charm build recipe (uv plugin)
  pyproject.toml     # charm dependencies (ops) and test deps
  uv.lock            # locked dependency versions
  tox.ini            # unit and integration test environments
  src/
    charm.py         # the charm (ops 3.x)
    scheduler.py     # daily scheduler run inside the workload container
  tests/
    test_charm.py    # unit tests (ops.testing State/Context)
    test_scheduler.py
    integration/
      conftest.py
      test_charm.py  # integration tests (jubilant)
```

The OCI image for the `ftbfs-report` container is built with
[rockcraft](https://canonical-rockcraft.readthedocs-hosted.com/) from
[`../rockcraft.yaml`](../rockcraft.yaml). It bundles
Python 3, the `lp-ftbfs-report` package and
`scheduler.py` at `/opt/scheduler.py`.

## Build

### 1. Build the OCI image (rock)

```bash
# From the repo root:
rockcraft pack
# -> lp-ftbfs-report_1.0.0_amd64.rock

# Import into your registry, e.g.:
skopeo copy oci-archive:lp-ftbfs-report_1.0.0_amd64.rock \
    docker://registry.example.com/lp-ftbfs-report:1.0.0
```

### 2. Build the charm

```bash
cd charm
charmcraft pack
# -> lp-ftbfs-report_ubuntu@24.04-<rev>.charm
```

## Deploy

```bash
# Resource must point at the rock pushed to a registry Juju can reach.
juju deploy ./lp-ftbfs-report_ubuntu@24.04-amd64.charm \
    --resource oci-image=registry.example.com/lp-ftbfs-report:1.0.0 \
    --config series=noble \
    --config architectures=amd64,arm64,armhf,ppc64el,s390x,riscv64,i386 \
    --config filename=index \
    --config schedule-hour=2

# Expose the workload's HTTP port (default 8080) e.g. via an ingress or
# a charm providing the `http` interface over the `website` relation.
```

With `filename=index` the page is served at the site root, so wiring the unit
behind `ftbfs.ubuntu.com` yields `https://ftbfs.ubuntu.com/`.

## Config options

| Option                | Type    | Default                                                            | Description                                            |
| --------------------- | ------- | ------------------------------------------------------------------ | ------------------------------------------------------ |
| `archive`             | string  | `primary`                                                          | Launchpad archive name (ignored in PPA mode).          |
| `series`              | string  | `noble`                                                            | Ubuntu series name.                                    |
| `architectures`       | string  | `amd64,arm64,armhf,ppc64el,s390x,riscv64,i386`                     | Comma-separated arch tags.                             |
| `ppa`                 | string  | `""`                                                               | Optional PPA `owner/name`; switches to PPA mode.        |
| `filename`            | string  | `index`                                                            | Output file prefix; `index` serves at `/`.             |
| `updates-archive`     | string  | `""`                                                               | Optional updates archive name.                         |
| `reference-series`    | string  | `""`                                                               | Reference series for regression detection.             |
| `regressions-only`    | boolean | `false`                                                            | Only report regressions (ignored in PPA mode).         |
| `release-only`        | boolean | `false`                                                            | Only include release-pocket sources.                   |
| `schedule-hour`       | int     | `2`                                                                | UTC hour of the daily run.                             |
| `schedule-minute`     | int     | `0`                                                                | UTC minute of the daily run.                           |
| `stale-after-hours`   | int     | `24`                                                               | Regenerate on startup if report older than this; `0` disables. |
| `server-port`         | int     | `8080`                                                             | HTTP server port.                                       |
| `report-command-verbose` | boolean | `false`                                                         | Pass `-v` to `lp-ftbfs-report`.                         |

## Actions

```bash
juju run lp-ftbfs-report/0 generate
```

Regenerates the report immediately instead of waiting for the next scheduled
slot.

## Storage

The `reports` storage is mounted at `/srv/reports` and holds the generated
`index.html`, `index.csv` and static assets (`style.css`, `filters.js`). It
persists across pod restarts, so the report remains available while the next
daily run is in progress.

## Testing

### Unit tests

```bash
cd charm
uv sync --group dev
PYTHONPATH=src uv run python -m pytest tests/ -v
```

Or with tox:

```bash
cd charm
tox -e unit
```

### Integration tests

The integration tests deploy the charm to a real Juju/K8s cluster, verify
the report is generated and served over HTTP, test the `generate` action,
and check config changes.

### Prerequisites

The script does not install microk8s for you. Set it up once:

```bash
sudo snap install microk8s --channel=1.36-strict/stable
sudo usermod -a -G snap_microk8s $USER
# log out and back in (or run 'newgrp snap_microk8s')
sudo microk8s enable dns hostpath-storage registry
juju bootstrap microk8s mk8s-ftbfs-test
```

### Running

```bash
./scripts/run-integration-test.sh
```

To keep the Juju model alive after the test for manual inspection:

```bash
KEEP_MODEL=1 ./scripts/run-integration-test.sh
```

If you already have a Juju K8s controller and the artifacts built, run
the tests directly:

```bash
cd charm
uv sync --group integration

# Set these env vars:
export CHARM_PATH=/path/to/lp-ftbfs-report_ubuntu@24.04-amd64.charm
export OCI_IMAGE=localhost:32000/lp-ftbfs-report:1.0.0

PYTHONPATH=src uv run python -m pytest tests/integration/ -v -s
```

## License

GPL-2.0-or-later.
