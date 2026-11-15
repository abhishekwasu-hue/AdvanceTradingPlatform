#!/usr/bin/env bash
# ATP runner installer (Runner S6, docs/RUNNER_INSTALL.md). Run as root on a fresh Ubuntu VPS:
#
#     curl -fsSL https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/releases/download/<runner-vX.Y.Z>/install.sh \
#         -o atp-runner-install.sh && sudo bash atp-runner-install.sh
#
#     sudo bash atp-runner-install.sh [install]    install (or re-install) and start the runner
#     sudo bash atp-runner-install.sh update       the new release's installer: verify + pull its image, restart
#     sudo bash atp-runner-install.sh status       container state, health, last log lines
#     sudo bash atp-runner-install.sh uninstall    stop and remove the runner; your folder (audit log, switches) is KEPT
#     options: --dry-run (print every action, change nothing)  --home <folder> (default /opt/atp-runner)
#              --broker angel_one (start with your broker once its details are in place)  --purge-data (uninstall only)
#
# What it does: installs Docker if missing (Ubuntu's docker.io package), checks the image's signature (cosign keyless,
# signed by this repository's release workflow for a `runner-v*` tag) and pulls it BY DIGEST, creates your folder with
# mode 0700, asks for the pairing code and your broker keys when the image supports them, starts the container with
# restart=unless-stopped and no inbound port, and prints the VPS public IP to register at your broker.
set -euo pipefail

# --- written by .github/workflows/runner-release.yml when it publishes a release (never edited by hand) -----------------
RUNNER_VERSION="@RUNNER_VERSION@"
RUNNER_DIGEST="@RUNNER_DIGEST@"
# ------------------------------------------------------------------------------------------------------------------------
RUNNER_IMAGE="ghcr.io/abhishekwasu-hue/atp-runner"
SIGNER_IDENTITY_REGEXP='^https://github\.com/abhishekwasu-hue/AdvanceTradingPlatform/\.github/workflows/runner-release\.yml@refs/tags/runner-v'
SIGNER_ISSUER="https://token.actions.githubusercontent.com"
COSIGN_VERSION="v2.4.1"
COSIGN_SHA256_AMD64="8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b"
CONTAINER="atp-runner"
RUNNER_UID=10001                     # the image's non-root user (deploy/runner/Dockerfile)
CONF="/etc/atp-runner.conf"
TOOLS="/usr/local/lib/atp-runner"

COMMAND="install"
DRY_RUN=0
HOME_DIR="/opt/atp-runner"
BROKER=""
PURGE=0

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Every action that changes the machine goes through run: in --dry-run it is printed, not done.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

# A question to the person at the keyboard (also when this script was piped into bash).
ask() {
  local prompt="$1" answer=""
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] would ask: %s\n' "$prompt" >&2
    return 0
  fi
  if [ -r /dev/tty ]; then
    read -r -p "$prompt" answer < /dev/tty || true
  fi
  printf '%s' "$answer"
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      install|update|status|uninstall) COMMAND="$1" ;;
      --dry-run) DRY_RUN=1 ;;
      --home) [ $# -ge 2 ] || die "--home needs a folder"; HOME_DIR="$2"; shift ;;
      --broker) [ $# -ge 2 ] || die "--broker needs a name"; BROKER="$2"; shift ;;
      --purge-data) PURGE=1 ;;
      -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
  done
  case "$BROKER" in ""|angel_one) ;; *) die "the runner's first broker is Angel One (--broker angel_one)" ;; esac
}

load_conf() {
  # The choices of the last install (folder, broker), kept so `update` starts the runner the same way.
  if [ -r "$CONF" ]; then
    local key value
    while IFS='=' read -r key value; do
      case "$key" in
        HOME_DIR) [ "$HOME_DIR" != "/opt/atp-runner" ] || HOME_DIR="$value" ;;
        BROKER) [ -n "$BROKER" ] || BROKER="$value" ;;
      esac
    done < "$CONF"
  fi
}

save_conf() {
  if [ "$DRY_RUN" = 1 ]; then
    say "[dry-run] write $CONF (HOME_DIR=$HOME_DIR BROKER=$BROKER VERSION=$RUNNER_VERSION DIGEST=$RUNNER_DIGEST)"
    return 0
  fi
  umask 077
  printf 'HOME_DIR=%s\nBROKER=%s\nVERSION=%s\nDIGEST=%s\n' "$HOME_DIR" "$BROKER" "$RUNNER_VERSION" "$RUNNER_DIGEST" > "$CONF"
}

preflight() {
  [ "$(uname -s)" = "Linux" ] || die "the runner installs on Linux (an Ubuntu VPS)"
  if [ "$DRY_RUN" != 1 ] && [ "$(id -u)" != 0 ]; then
    die "run it as root: sudo bash $0 $COMMAND"
  fi
  case "$(uname -m)" in
    x86_64|amd64) ;;
    *) die "this release is built for x86-64 (amd64) servers; choose an x86-64 VPS plan" ;;
  esac
}

image_ref() {
  case "$RUNNER_DIGEST" in
    sha256:*) printf '%s@%s' "$RUNNER_IMAGE" "$RUNNER_DIGEST" ;;
    *) if [ "$DRY_RUN" = 1 ]; then printf '%s@sha256:<digest of the release>' "$RUNNER_IMAGE"; else
         die "this copy of the installer has no image digest - download install.sh from a runner release page"; fi ;;
  esac
}

ensure_docker() {
  if command -v docker > /dev/null 2>&1; then
    say "Docker: already installed"
    return 0
  fi
  say "Docker: installing (Ubuntu package docker.io)"
  run apt-get update
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io ca-certificates curl
  run systemctl enable --now docker
}

ensure_cosign() {
  COSIGN="$TOOLS/cosign-$COSIGN_VERSION"
  if [ -x "$COSIGN" ]; then return 0; fi
  say "Signature checker: installing cosign $COSIGN_VERSION"
  run mkdir -p "$TOOLS"
  run curl -fsSL -o "$COSIGN.download" \
    "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-amd64"
  if [ "$DRY_RUN" = 1 ]; then
    say "[dry-run] check sha256 $COSIGN_SHA256_AMD64 of $COSIGN.download"
  else
    printf '%s  %s\n' "$COSIGN_SHA256_AMD64" "$COSIGN.download" | sha256sum -c --quiet - \
      || { rm -f "$COSIGN.download"; die "the downloaded cosign does not match its checksum"; }
  fi
  run chmod 0755 "$COSIGN.download"
  run mv "$COSIGN.download" "$COSIGN"
}

verify_and_pull() {
  local ref
  ref="$(image_ref)"
  say "Image: checking the signature of $ref"
  run "$COSIGN" verify --certificate-identity-regexp "$SIGNER_IDENTITY_REGEXP" \
    --certificate-oidc-issuer "$SIGNER_ISSUER" "$ref" \
    || die "the image signature did not verify - nothing was started"
  run docker pull "$ref"
}

make_folder() {
  say "Folder: $HOME_DIR (yours only, mode 0700)"
  run mkdir -p "$HOME_DIR"
  run chown "$RUNNER_UID:$RUNNER_UID" "$HOME_DIR"
  run chmod 0700 "$HOME_DIR"
}

# True when the image's command list has `name` (pairing arrives with S3/S4, the keystore with S5).
image_has_command() {
  local ref="$1" name="$2"
  [ "$DRY_RUN" = 1 ] && return 1
  docker run --rm "$ref" --help 2>/dev/null | grep -Eq "[{,]${name}[,}]"
}

runner_cli() {   # a one-off runner command on the folder, interactive
  local ref="$1"; shift
  run docker run --rm -it --user "$RUNNER_UID:$RUNNER_UID" -v "$HOME_DIR:/runner" "$ref" "$@" --home /runner
}

pair_and_keys() {
  local ref="$1" code=""
  if image_has_command "$ref" pair; then
    code="$(ask 'Pairing code from ATP (My runner page): ')"
    if [ -n "$code" ]; then runner_cli "$ref" pair "$code"; else say "Pairing: skipped (run update later to pair)"; fi
  else
    say "Pairing: not in this runner version yet - it arrives with a later update (docs/RUNNER_INSTALL.md)"
  fi
  if image_has_command "$ref" keystore; then
    say "Broker keys: your API key / secret / PIN / TOTP secret are asked by the runner and stored encrypted in your folder"
    runner_cli "$ref" keystore set
  else
    say "Broker keys: the keystore is not in this runner version yet - until then see 'Broker details' in docs/RUNNER_INSTALL.md"
  fi
}

start_container() {
  local ref="$1"
  local args=(run --home /runner)
  [ -z "$BROKER" ] || args+=(--broker "$BROKER")
  if [ "$DRY_RUN" = 1 ] || docker inspect "$CONTAINER" > /dev/null 2>&1; then
    run docker rm -f "$CONTAINER"
  fi
  # No -p: nothing listens for the internet. Read-only root, no Linux capabilities, no privilege gain.
  run docker run -d --name "$CONTAINER" --restart unless-stopped \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 \
    --user "$RUNNER_UID:$RUNNER_UID" -v "$HOME_DIR:/runner" \
    "$ref" "${args[@]}"
}

print_ip() {
  local ip=""
  if [ "$DRY_RUN" = 1 ]; then
    say "[dry-run] read this VPS's public IPv4 address"
    return 0
  fi
  ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
  if [ -n "$ip" ]; then
    say ""
    say "Your VPS public IP: $ip"
    say "Register this as your static IP at Angel One (SmartAPI app settings), then log in to your broker in ATP."
  else
    say "Could not read the public IP; your VPS provider's panel shows it - register that IP at Angel One."
  fi
}

do_install() {
  local ref
  preflight
  ensure_docker
  ensure_cosign
  verify_and_pull
  ref="$(image_ref)"
  make_folder
  pair_and_keys "$ref"
  start_container "$ref"
  save_conf
  say "Runner $RUNNER_VERSION started (PAPER unless you turn LIVE on yourself: docker exec $CONTAINER python -m app.runner live on)"
  print_ip
}

do_update() {
  local ref
  preflight
  load_conf
  ensure_docker
  ensure_cosign
  verify_and_pull
  ref="$(image_ref)"
  start_container "$ref"
  save_conf
  say "Runner updated to $RUNNER_VERSION; your folder, switches and audit log are unchanged"
}

do_status() {
  load_conf
  run docker ps -a --filter "name=^${CONTAINER}$" --format '{{.Names}}  {{.Status}}  {{.Image}}'
  run docker exec "$CONTAINER" python -m app.runner health --home /runner || true
  run docker logs --tail 20 "$CONTAINER" || true
}

do_uninstall() {
  local answer=""
  preflight
  load_conf
  run docker rm -f "$CONTAINER" || true
  local image
  if [ "$DRY_RUN" = 1 ]; then
    run docker image rm "<every $RUNNER_IMAGE image>"
  else
    for image in $(docker images --quiet --filter "reference=$RUNNER_IMAGE" 2>/dev/null | sort -u); do
      run docker image rm -f "$image" || true
    done
  fi
  run rm -f "$CONF"
  if [ "$PURGE" = 1 ]; then
    answer="$(ask "Delete $HOME_DIR with your audit log and switches? Type DELETE: ")"
    if [ "$answer" = "DELETE" ]; then run rm -rf "$HOME_DIR"; else say "Folder kept: $HOME_DIR"; fi
  else
    say "Runner removed. Your folder is kept: $HOME_DIR (add --purge-data to delete it)"
  fi
}

main() {
  parse_args "$@"
  [ "$DRY_RUN" = 0 ] || say "Dry run: nothing is changed on this machine"
  case "$COMMAND" in
    install) do_install ;;
    update) do_update ;;
    status) do_status ;;
    uninstall) do_uninstall ;;
  esac
}

main "$@"
