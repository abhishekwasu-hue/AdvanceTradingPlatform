# Install the ATP runner on your own VPS

The runner is ATP's trading engine running on YOUR server, with YOUR broker key and YOUR static IP. ATP never
holds your broker keys. It starts in PAPER mode. Only you can turn LIVE on, and only you can use the kill switch.
Exits always run.

## What you need

- A small VPS: Ubuntu 22.04 or 24.04, x86-64 (amd64), 1 vCPU, 1 GB RAM, about 10 GB disk. Any provider works.
  The VPS's public IP is your static IP.
- An Angel One account with your own SmartAPI app (smartapi.angelbroking.com: create an app, copy its API key).
- 10 minutes.

## Steps

1. Log in to your VPS as root, or as a user that can use `sudo`. Your provider's web console or `ssh` both work.
2. Paste the one install line from the latest runner release (on the GitHub release page called "ATP runner"):

   ```
   curl -fsSL https://github.com/abhishekwasu-hue/AdvanceTradingPlatform/releases/download/<runner-vX.Y.Z>/install.sh -o atp-runner-install.sh && sudo bash atp-runner-install.sh
   ```

   Want to see what it will do first? Add `--dry-run` at the end. It prints every step and changes nothing.
3. The installer then:
   - installs Docker if it is missing;
   - checks the runner image's signature. If the check fails it stops, and nothing is started;
   - downloads the exact signed image (pinned by its digest);
   - creates your folder `/opt/atp-runner`, readable only by the runner;
   - asks for your pairing code and broker keys, if this runner version supports them (see "Not in this version yet");
   - starts the runner. It restarts by itself after a reboot, and it opens no port to the internet;
   - prints your VPS public IP.
4. Register that IP as your static IP in your Angel One SmartAPI app. Angel One accepts orders only from that IP.
5. Check that the runner is healthy: `sudo bash atp-runner-install.sh status` (it should say "Runner running").

## Everyday commands (on the VPS)

| What | Command |
|---|---|
| Status, health, last log lines | `sudo bash atp-runner-install.sh status` |
| Kill switch ON (no new entries; exits continue) | `sudo docker exec atp-runner python -m app.runner kill` |
| Kill switch OFF | `sudo docker exec atp-runner python -m app.runner resume` |
| Your LIVE switch (default OFF) | `sudo docker exec atp-runner python -m app.runner live on` / `live off` / `live status` |
| Check the audit log's chain | `sudo docker exec atp-runner python -m app.runner audit-verify` |
| Update to a new release | download that release's `install.sh`, then `sudo bash atp-runner-install.sh update` |
| Remove the runner (your folder is kept) | `sudo bash atp-runner-install.sh uninstall` |
| Remove it and delete your folder | `sudo bash atp-runner-install.sh uninstall --purge-data` (asks you to type DELETE) |

Update after market hours. An update restarts the runner. Open positions stay in your folder and the runner manages
them again as soon as it starts.

## Not in this version yet

- **Pairing with ATP** (the runner showing on ATP's "My runner" page) comes in a later runner update. Until then the
  installer says "Pairing: not in this runner version yet" and goes on.
- **Encrypted broker keystore** comes in a later runner update. Until then the installer says so, and the runner starts
  in PAPER with no broker. To connect Angel One now (the owner's pilot), put your broker details in a file
  only the runner can read, then start the runner again with your broker:

  ```
  sudo install -m 600 -o 10001 -g 10001 /dev/null /opt/atp-runner/broker.json
  sudo nano /opt/atp-runner/broker.json
  #   {"api_key": "...", "client_id": "...", "pin": "...", "totp_secret": "..."}
  sudo bash atp-runner-install.sh install --broker angel_one
  ```

  The runner refuses this file unless only its owner can read it (mode 600). It never logs the file and never writes it
  to the audit log. The keystore update will replace this file.

## Your folder (`/opt/atp-runner`)

`runner.db` (your local data), `audit.jsonl` (every order intent and result, hash-chained), `LIVE` and `KILL` (your
switches), `runner.json` (optional: cycle length, history to read), `runner.lock`. Back it up if you want to keep your
history. Uninstalling never deletes it unless you ask with `--purge-data`.
