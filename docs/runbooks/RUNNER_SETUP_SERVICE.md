# "We set up your VPS" - the owner's checklist for a customer's runner (paid service)

For the owner (Abhi), when a customer pays for the runner setup. The customer owns everything: the VPS account, the
broker account, the SmartAPI app and its key. The owner works on HIS server, in a session with him, and never keeps any
of his secrets. Pricing is a subscription only, with no per-trade fee and no profit share (R-4). Do not promise returns
and do not call it an "AI bot" (R-5).

## Before the session (customer does, owner guides)
- [ ] He buys a VPS in his own name: Ubuntu 22.04 / 24.04, x86-64, 1 vCPU, 1 GB RAM, 10 GB disk.
- [ ] He has an Angel One account and creates his own SmartAPI app (his own API key, never ATP's or the owner's).
- [ ] He has his ATP account (USER group) and knows his Angel One client code, PIN and TOTP set-up.
- [ ] Agree on a time after market hours.

## In the session (screen share; he types his secrets himself)
- [ ] He logs in to his VPS. You never take a copy of his root password or SSH key.
- [ ] Download `install.sh` from the latest `runner-v*` release, check it against `install.sh.sha256` from the same
      page, then run it with `--dry-run` once and read the plan together.
- [ ] Run it for real: `sudo bash atp-runner-install.sh`. The signature check must pass. If it fails, stop, and report it
      to the owner's queue. Never work around it.
- [ ] Pairing code and broker keys: he types them himself when the installer asks. On a version without the keystore,
      he fills `broker.json` himself (docs/RUNNER_INSTALL.md, "Not in this version yet"). The owner does not see it.
- [ ] Note the public IP the installer prints. He registers it as the primary static IP in his SmartAPI app.
      (Angel One allows a change of the primary IP once a week.)
- [ ] `status` shows "Runner running". `audit-verify` shows "chain intact".
- [ ] Show him the kill switch (`kill` / `resume`) and say plainly: only he turns it on or off. Exits always run.
- [ ] Leave LIVE OFF. Show him `live status`. Turning LIVE on is his own action, on his own runner, after his PAPER
      check. The owner never turns it on for him.
- [ ] Show him `update` and `uninstall`. His folder is kept unless he asks for `--purge-data`.

## After the session
- [ ] Nothing of his is stored with the owner: no keys, PIN, TOTP, passwords or IP in notes, chat or the repo.
- [ ] He has docs/RUNNER_INSTALL.md (the everyday commands).
- [ ] Before LIVE for any user other than the owner: the lawyer items in docs/design/USER_RUNNER.md section 6 (RUN-Q1)
      and Angel One's data terms are cleared.
