#!/bin/sh
# M55 O3: regenerate backend/requirements.lock (every package pinned, with sha256 hashes) from backend/requirements.txt.
# The Docker image and CI install from the lock with `pip install --require-hashes`; requirements.txt stays the list of
# direct dependencies and their ranges (Dependabot's pip updates edit it - regenerate the lock in the same PR, CI fails
# while the two disagree).
#
#     sh scripts/lock_requirements.sh            # keep the current pins where requirements.txt still allows them
#     sh scripts/lock_requirements.sh --upgrade  # re-resolve every package to its newest allowed version
#
# Needs uv (`pip install uv==0.13.6`). One universal resolution for Python 3.11+ (CI runs 3.11, the image 3.13);
# the hashes cover every published file of each pinned version, so the lock installs on any Linux architecture.
set -eu
cd "$(dirname "$0")/.."
uv pip compile requirements.txt \
    --universal --python-version 3.11 \
    --generate-hashes \
    --custom-compile-command "sh scripts/lock_requirements.sh" \
    --output-file requirements.lock \
    "$@"
