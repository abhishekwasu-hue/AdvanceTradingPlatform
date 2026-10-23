<!-- A3 safe deploys: every PR answers each line below. Delete nothing; write "n/a" where a line does not apply. -->

## What and why

<!-- What changes for the user or the operator, and which order / item it serves. -->

## Deploy

- **Migration:** no / yes (revision `________`, deploy after 15:30 IST; `MIGRATION_FORCE=1` only on Abhi's word)
- **Rollback-safe:** yes / no (every new migration sets `ROLLBACK_SAFE`; "no" means `atp.sh rollback --restore-db`)
- **Flags:** none / which flag, its default (new user-facing work sits behind `ui_v2` + the area's flag, OFF)
- **The one deploy line** (once merged and main's checks are green; `atp.sh deploy` refuses an untested commit):

  ```
  su - atp -c "/opt/atp/deploy/atp.sh deploy && /opt/atp/deploy/atp.sh health"
  ```

- **Anything else on the server:** none / ONE consolidated block for Abhi

## Checks

<!-- ruff + mypy on touched backend files, the pytest files run, npm test / build, the english-only and public-repo
     guards, shellcheck / actionlint for deploy or workflow changes. -->

## Self-review notes

<!-- docs/WORKING_RULES.md section 2: what you looked for in your own diff, and what you found. -->

## Screenshots (UI changes)

<!-- Before / after, desktop + phone (360 / 390 / 1440), light + dark. "n/a" when no UI changed. -->
