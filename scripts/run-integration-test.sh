#!/usr/bin/env bash
#
# End-to-end integration test for the lp-ftbfs-report charm.
#
# This script builds the rock and charm, pushes the image to the microk8s
# local registry, deploys the charm to a Juju model on microk8s, and runs
# the integration test suite.
#
# Design principles:
#   - Does NOT install or remove any snaps.
#   - Does NOT clobber ~/.kube/config — uses an isolated kubeconfig.
#   - Does NOT destroy or touch existing Juju controllers.
#   - Creates a temporary Juju model and destroys it on exit.
#   - Only cleans up what it creates.
#
# Prerequisites (the script checks for these and exits with instructions):
#   - microk8s (with dns, hostpath-storage, registry addons enabled)
#   - juju (with a controller bootstrapped on microk8s, or the script
#     will bootstrap one named 'mk8s-ftbfs-test')
#   - charmcraft, rockcraft, skopeo
#
# Usage:
#   ./scripts/run-integration-test.sh
#
# Environment variables (all optional):
#   JUJU_CONTROLLER  Name of an existing Juju controller on microk8s.
#                    Defaults to bootstrapping 'mk8s-ftbfs-test'.
#   KEEP_MODEL       Set to 1 to keep the jubilant test model after tests
#                    (passed to pytest as --no-juju-teardown).
#
set -euo pipefail

# --- Configuration ----------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHARM_DIR="$REPO_ROOT/charm"
CONTROLLER_NAME="${JUJU_CONTROLLER:-mk8s-ftbfs-test}"
REGISTRY_PORT="32000"
ROCK_TAG="$(date +%Y%m%d%H%M%S)"
ROCK_IMAGE="localhost:${REGISTRY_PORT}/lp-ftbfs-report:${ROCK_TAG}"

# --- Helpers ----------------------------------------------------------------

log() { echo -e "\033[1;34m[integration]\033[0m $*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

# (No model cleanup needed — jubilant creates and destroys its own temp model.)

# --- Preflight --------------------------------------------------------------

log "Preflight checks..."

# Collect all missing prerequisites first, then print a single set of
# instructions so the user doesn't have to iterate.
MISSING_TOOLS=()
INSTALL_SNAPS=()
INSTALL_APT=()

# Check CLI tools.
for tool in microk8s juju charmcraft rockcraft skopeo; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        MISSING_TOOLS+=("$tool")
    fi
done

# If microk8s is installed, verify it's running and addons are enabled.
MICROK8S_OK=false
if command -v microk8s >/dev/null 2>&1; then
    if sudo microk8s status --wait-ready >/dev/null 2>&1; then
        ADDONS=$(sudo microk8s status --format yaml 2>/dev/null || true)
        MISSING_ADDONS=()
        for addon in dns hostpath-storage registry; do
            echo "$ADDONS" | grep -A5 "name: $addon\$" | grep -q 'status: enabled' \
                || MISSING_ADDONS+=("$addon")
        done
        if [[ ${#MISSING_ADDONS[@]} -gt 0 ]]; then
            err "microk8s is running but these addons are not enabled: ${MISSING_ADDONS[*]}"
            err "  Run: sudo microk8s enable ${MISSING_ADDONS[*]}"
            die "Enable the missing microk8s addons and re-run."
        fi
        MICROK8S_OK=true
    else
        die "microk8s is not running. Start it with: sudo microk8s start"
    fi
fi

# Build install instructions for any missing tools.
for tool in "${MISSING_TOOLS[@]}"; do
    case "$tool" in
        microk8s)
            INSTALL_SNAPS+=("sudo snap install microk8s --channel=1.36-strict/stable")
            ;;
        juju)
            INSTALL_SNAPS+=("sudo snap install juju --classic")
            ;;
        charmcraft)
            INSTALL_SNAPS+=("sudo snap install charmcraft --classic")
            ;;
        rockcraft)
            INSTALL_SNAPS+=("sudo snap install rockcraft --classic")
            ;;
        skopeo)
            INSTALL_APT+=("sudo apt-get install -y skopeo")
            ;;
    esac
done

# If anything is missing, print unified instructions and exit.
if [[ ${#MISSING_TOOLS[@]} -gt 0 ]]; then
    err "Missing prerequisites: ${MISSING_TOOLS[*]}"
    err ""
    err "Install them with:"
    for cmd in "${INSTALL_SNAPS[@]}" "${INSTALL_APT[@]}"; do
        err "  $cmd"
    done
    if printf '%s\n' "${MISSING_TOOLS[@]}" | grep -q microk8s; then
        err "  sudo usermod -a -G snap_microk8s \$USER"
        err "  # log out and back in (or run 'newgrp snap_microk8s') for the group to take effect"
        err "  sudo microk8s enable dns hostpath-storage registry"
        err "  juju bootstrap microk8s $CONTROLLER_NAME"
        err ""
        err "Or set JUJU_CONTROLLER to use an existing controller."
    fi
    die "Install the missing prerequisites and re-run."
fi

if [[ "$MICROK8S_OK" == true ]]; then
    log "  microk8s: running, addons OK"
fi
log "  juju, charmcraft, rockcraft, skopeo: OK"

# --- Isolated kubeconfig ----------------------------------------------------

# Create a dedicated kubeconfig so we don't touch the user's ~/.kube/config.
KUBECONFIG_FILE=$(mktemp --suffix=.yaml)
trap 'rm -f "$KUBECONFIG_FILE"' EXIT
sudo microk8s config > "$KUBECONFIG_FILE"
chmod 600 "$KUBECONFIG_FILE"
export KUBECONFIG="$KUBECONFIG_FILE"

log "  kubeconfig: $KUBECONFIG_FILE (isolated, user config untouched)"

# --- Juju controller --------------------------------------------------------

# Check if the controller already exists.
EXISTING_CONTROLLERS=$(juju controllers --format json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
controllers = data.get('controllers', {})
if isinstance(controllers, dict):
    print(' '.join(controllers.keys()))
else:
    print(' '.join(c['name'] for c in controllers))
" 2>/dev/null || echo "")

if echo "$EXISTING_CONTROLLERS" | grep -qw "$CONTROLLER_NAME"; then
    log "Using existing Juju controller: $CONTROLLER_NAME"
elif [[ -n "${JUJU_CONTROLLER:-}" ]]; then
    die "JUJU_CONTROLLER='$CONTROLLER_NAME' not found. Available: ${EXISTING_CONTROLLERS:-none}"
else
    log "Bootstrapping Juju controller '$CONTROLLER_NAME' on microk8s..."
    log "  (this only happens once; set JUJU_CONTROLLER to reuse an existing one)"
    juju bootstrap microk8s "$CONTROLLER_NAME" --no-switch
fi
# Unset JUJU_CONTROLLER so it doesn't override juju switch / juju models.
# We've already used it to set CONTROLLER_NAME above.
unset JUJU_CONTROLLER

# Switch to the microk8s controller so jubilant creates its temp model
# on the right cloud (k8s, not LXD).
log "Switching to controller '$CONTROLLER_NAME'..."
juju switch "$CONTROLLER_NAME"

trap 'rm -f "$KUBECONFIG_FILE"' EXIT

# --- Build the rock ---------------------------------------------------------

log "Building the rock..."
cd "$REPO_ROOT"
rockcraft pack || die "rockcraft pack failed"
ROCK_FILE="$(ls -t *.rock 2>/dev/null | head -1)"
[[ -n "$ROCK_FILE" ]] || die "rockcraft pack did not produce a .rock file"
log "  Rock built: $ROCK_FILE"

# --- Push the image to the microk8s registry --------------------------------

log "Pushing OCI image to local registry ($ROCK_IMAGE)..."
skopeo copy "oci-archive:$ROCK_FILE" "docker://$ROCK_IMAGE" --dest-tls-verify=false
log "  Image pushed."

# --- Build the charm --------------------------------------------------------

log "Building the charm..."
cd "$CHARM_DIR"
charmcraft pack || die "charmcraft pack failed"
CHARM_FILE="$(ls -t *.charm 2>/dev/null | head -1)"
[[ -n "$CHARM_FILE" ]] || die "charmcraft pack did not produce a .charm file"
CHARM_PATH="$CHARM_DIR/$CHARM_FILE"
[[ -f "$CHARM_PATH" ]] || die "Cannot find packed .charm file: $CHARM_FILE"
log "  Charm built: $CHARM_PATH"

# --- Run integration tests --------------------------------------------------

export CHARM_PATH
export OCI_IMAGE="$ROCK_IMAGE"

log "Installing integration test dependencies..."
cd "$CHARM_DIR"
uv sync --group integration

log "Running integration tests..."
PYTEST_ARGS=("-v" "-s" "--tb=native" "--log-cli-level=INFO")
if [[ "${KEEP_MODEL:-0}" == "1" ]]; then
    PYTEST_ARGS+=("--no-juju-teardown")
fi
PYTHONPATH=src uv run python -m pytest tests/integration/ "${PYTEST_ARGS[@]}"

log "Integration tests passed!"
