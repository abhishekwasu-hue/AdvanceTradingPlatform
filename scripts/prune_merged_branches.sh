#!/usr/bin/env bash
# AUD-V7 (owner audit 28 Oct): delete remote claude/* branches that are fully merged into origin/main and have no open
# pull request. Dry run by default (prints what it would delete); pass --apply to delete. Never touches main or tags.
set -euo pipefail
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
REPO="${REPO:-abhishekwasu-hue/AdvanceTradingPlatform}"
git fetch -q --prune origin "+refs/heads/*:refs/remotes/origin/*"
open_heads=$(gh api "repos/$REPO/pulls?state=open&per_page=100" --paginate --jq '.[].head.ref' | sort -u)
count=0
for ref in $(git for-each-ref --format='%(refname:strip=3)' 'refs/remotes/origin/claude/'); do
  if echo "$open_heads" | grep -qx "$ref"; then continue; fi
  if git merge-base --is-ancestor "origin/$ref" origin/main; then
    count=$((count + 1))
    if [ "$APPLY" = 1 ]; then git push -q origin --delete "$ref" && echo "deleted $ref"; else echo "would delete $ref"; fi
  fi
done
echo "$count merged claude/* branch(es) $( [ "$APPLY" = 1 ] && echo deleted || echo 'to delete (dry run; --apply to delete)')"
