# Auto-deploy (owner request, 1 Nov - DECISIONS 2026-11-01)

A systemd timer on the ATP host runs this script every 10 minutes as the `atp` user. It deploys the newest commit on
`main` once `atp.sh deploy` accepts it (atp.sh keeps all its own guards: the CI + staging gate, the encrypted sealed
pre-migration backup, the refusal during an open market session / open positions, its lock). A refused or failed
commit is not retried; the next commit on main is.

- Log: `~atp/atp-autodeploy.log`
- Pause: `touch ~atp/atp-autodeploy.off` (resume: delete the file)
- Stop: `systemctl disable --now atp-autodeploy.timer`
- Status: `systemctl list-timers atp-autodeploy.timer`

Tested locally with a fake atp.sh: CI pending (logged once, retried), market refusal (waits), deploy + health,
same commit not deployed twice, a failed commit skipped and the next one deployed, pause file honoured.

```bash
#!/bin/bash
# ATP auto-deploy: every run deploys the newest main commit once atp.sh's own CI gate says its checks passed.
# Pause: touch ~/atp-autodeploy.off   Resume: rm ~/atp-autodeploy.off   Log: ~/atp-autodeploy.log
set -u
REPO_URL=https://github.com/abhishekwasu-hue/AdvanceTradingPlatform.git
ATP=/opt/atp/deploy/atp.sh
STATE="$HOME/.atp-autodeploy"; LOG="$HOME/atp-autodeploy.log"
mkdir -p "$STATE"
exec 9>"$STATE/lock"; flock -n 9 || exit 0
[ -e "$HOME/atp-autodeploy.off" ] && exit 0
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
once() { [ "$(cat "$STATE/note" 2>/dev/null)" = "$1" ] || { log "$2"; echo "$1" > "$STATE/note"; }; }
sha="$(git ls-remote "$REPO_URL" refs/heads/main 2>/dev/null | cut -f1)"
[ -n "$sha" ] || { once "nogit" "could not read main from GitHub - trying again in 10 minutes"; exit 0; }
[ "$sha" = "$(cat "$STATE/done" 2>/dev/null)" ] && exit 0
[ "$sha" = "$(cat "$STATE/failed" 2>/dev/null)" ] && exit 0
out="$("$ATP" deploy "$sha" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then
  echo "$sha" > "$STATE/done"
  log "=== DEPLOYED ${sha:0:12} ==="
  printf '%s\n' "$out" | grep -E "pre-migration backup|seal|schema at head|migrat" >> "$LOG"
  h="$("$ATP" health 2>&1)"; hrc=$?
  printf '%s\n' "$h" | tail -n 3 >> "$LOG"; log "=== HEALTH EXIT $hrc (0 = all green) ==="
  exit 0
fi
if printf '%s' "$out" | grep -q -e "Run the same command again" -e "Try again in a few minutes" -e "another atp.sh run holds"; then
  once "wait-$sha" "waiting: CI still running for ${sha:0:12} (or GitHub / another atp.sh run busy) - checking every 10 minutes"; exit 0
fi
if [ "$rc" -eq 3 ]; then
  once "market-$sha" "${sha:0:12} waits: atp.sh refused during an open market session / open positions - it deploys after the close"; exit 0
fi
echo "$sha" > "$STATE/failed"
log "=== NOT DEPLOYED ${sha:0:12} (exit $rc) - nothing more is tried for this commit; the next commit on main will be ==="
printf '%s\n' "$out" | tail -n 8 >> "$LOG"
```
