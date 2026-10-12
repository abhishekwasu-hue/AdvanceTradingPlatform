#!/usr/bin/env bash
# Z11 - first install, step 2 of 2 (step 1 is bootstrap.sh as root). Run ONCE as the non-root user, as one pasted line
# from docs/DEPLOY_HOSTINGER_ONELINERS.txt ("install.sh"), because the repository is not on the server yet:
#
#   ssh atp@<SERVER_IP>
#   bash -c "$(echo <base64> | base64 -d)"
#
# It only gets the code: a read-only deploy key (printed; you add it on GitHub, then paste the line again - exit 10),
# then a clone in /opt/atp. Then it hands over to the one owner command, /opt/atp/deploy/atp.sh install
# (bootstrap checks -> deploy -> health -> first backup + restore check). It never creates or edits .env and never
# prints a secret; atp.sh stops (exit 10) and names the keys to fill if .env is missing or incomplete.
# The install is a Phase 0 deployment (ATP_DEPLOYMENT_PHASE=phase0, the default of atp.sh and docker-compose.hostinger.yml):
# the first boot stores live_trading OFF and phase0_paper_only ON, so there is no flag for the owner to set.
#
#   ATP_DIR   install dir (default /opt/atp)
#   ATP_REPO  git@github.com:abhishekwasu-hue/AdvanceTradingPlatform.git
set -euo pipefail

ATP_DIR="${ATP_DIR:-/opt/atp}"
ATP_REPO="${ATP_REPO:-git@github.com:abhishekwasu-hue/AdvanceTradingPlatform.git}"
KEY="$HOME/.ssh/atp_deploy_ed25519"
log() { printf '\n==> %s\n' "$*"; }
[ "$(id -u)" != "0" ] || { echo "run as the non-root user from the bootstrap block (atp), not root" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker missing - run the bootstrap block as root first" >&2; exit 10; }
if [ ! -d "$ATP_DIR" ] || [ ! -w "$ATP_DIR" ]; then echo "$ATP_DIR missing or not writable - run the bootstrap block as root first" >&2; exit 10; fi

log "deploy key (read-only)"
mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "atp-deploy@$(hostname)" -f "$KEY"
grep -q "Host github.com" "$HOME/.ssh/config" 2>/dev/null || printf 'Host github.com\n  IdentityFile %s\n  IdentitiesOnly yes\n' "$KEY" >> "$HOME/.ssh/config"
chmod 600 "$HOME/.ssh/config"
# GitHub's host key is pinned: the scanned key is accepted only when its fingerprint is GitHub's published ed25519
# fingerprint (docs.github.com "GitHub's SSH key fingerprints") - a man in the middle cannot slip its own key in here.
GITHUB_ED25519_FP="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
if ! ssh-keygen -F github.com -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1; then
  scanned="$(ssh-keyscan -t ed25519 github.com 2>/dev/null | grep -E '^github\.com ssh-ed25519 ' | head -n 1 || true)"
  if [ -z "$scanned" ]; then
    echo "github.com did not answer on port 22 (ssh-keyscan returned nothing) - check the server's outbound network / DNS" >&2
    echo "(e.g. 'ssh -T git@github.com', 'getent hosts github.com'), then paste the line again" >&2
    exit 1
  fi
  fp="$(printf '%s\n' "$scanned" | ssh-keygen -lf - 2>/dev/null | awk '{print $2}')"
  if [ -z "$scanned" ] || [ "$fp" != "$GITHUB_ED25519_FP" ]; then
    echo "github.com answered with host key ${fp:-none}, not GitHub's published $GITHUB_ED25519_FP - refusing (network intercepted?)" >&2
    exit 1
  fi
  printf '%s\n' "$scanned" >> "$HOME/.ssh/known_hosts"
fi
if ! git ls-remote "$ATP_REPO" HEAD >/dev/null 2>&1; then
  echo
  echo "Add this PUBLIC key at GitHub > AdvanceTradingPlatform > Settings > Deploy keys > Add deploy key"
  echo "(title: hostinger-$(hostname), 'Allow write access' OFF), then paste the same line again:"
  echo
  cat "$KEY.pub"
  exit 10
fi

log "repository -> $ATP_DIR"
if [ ! -d "$ATP_DIR/.git" ]; then
  git clone -q "$ATP_REPO" "$ATP_DIR"
else
  git -C "$ATP_DIR" fetch -q origin
fi
[ -x "$ATP_DIR/deploy/atp.sh" ] || git -C "$ATP_DIR" checkout -q --detach origin/main
log "handing over to $ATP_DIR/deploy/atp.sh install"
exec bash "$ATP_DIR/deploy/atp.sh" install "$@"
