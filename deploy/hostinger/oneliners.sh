#!/usr/bin/env bash
# Prints the three copy-paste blocks of docs/DEPLOY_HOSTINGER_MR.md: each script as ONE line,
#   bash -c "$(echo <base64> | base64 -d)"
# (base64 so no quoting can break in the paste) with the script's SHA-256, to compare with
#   sha256sum deploy/hostinger/<script>.sh   on any checkout of the same commit.
# Usage: bash deploy/hostinger/oneliners.sh            (from the repo root)
set -euo pipefail
cd "$(dirname "$0")"
for s in bootstrap deploy rollback status; do
  printf '\n# %s.sh  sha256 %s\n' "$s" "$(sha256sum "$s.sh" | cut -d' ' -f1)"
  printf 'bash -c "$(echo %s | base64 -d)"\n' "$(base64 -w0 "$s.sh")"
done
