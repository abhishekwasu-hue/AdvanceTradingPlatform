# Production host - Hostinger KVM 2 (मराठी runbook)

Host: **Hostinger KVM 2** - 2 vCPU / 8 GB RAM / 100 GB NVMe, Ubuntu 24.04, Mumbai, static IPv4. हा ATP चा production
host (PAPER पासून सुरुवात). **Trade repo चा DigitalOcean droplet वेगळा राहतो - त्याला या runbook मधलं काहीही
लागू नाही, त्याला स्पर्श नाही.**

तीन blocks, प्रत्येक **एका ओळीत** (`bash -c "$(echo <base64> | base64 -d)"`) - ओळी
`docs/DEPLOY_HOSTINGER_ONELINERS.txt` मध्ये (repo मधल्या scripts वरून तयार; CI test तपासते की त्या scripts शी जुळतात).
वाचायचं असेल तर मूळ scripts: `deploy/hostinger/bootstrap.sh`, `deploy.sh`, `rollback.sh`, `status.sh`.
PC वर हवं तर ओळी पुन्हा तयार करा: `bash deploy/hostinger/oneliners.sh`.

> **Secrets:** `.env` फक्त server वर (`/opt/atp/.env`, mode 600). Chat, GitHub, PR मध्ये कधीही नाही. Broker/AI keys
> फक्त Settings पानातून (encrypted). `SECRETS_ENCRYPTION_KEY` = PAPER PC वरचाच 44-अक्षरी key - **नवा कधीही नाही**.

## 0. आधी (एकदाच, तुम्ही)

| # | काय | कुठे |
|---|---|---|
| 0.1 | Server IP लिहून ठेवा (static IPv4) | Hostinger hPanel → VPS → Overview |
| 0.2 | तुमचा SSH public key VPS ला जोडा (password login नको) | hPanel → VPS → Settings → SSH keys |
| 0.3 | Domain असेल तर `A` record `atp.<domain>` → IP | registrar; नसेल तर तात्पुरतं IP वापरू (खाली) |
| 0.4 | Hostinger चा VPS firewall (असेल तर) 22/80/443 उघडे | hPanel → VPS → Firewall |
| 0.5 | Cloudflare R2 bucket `atp-backups` + bucket-scoped token (off-site backups) | Cloudflare → R2 (§5) |

## 1. Block 1 - bootstrap (root, एकदाच)

```
ssh root@<server-ip>
# docs/DEPLOY_HOSTINGER_ONELINERS.txt मधली "bootstrap.sh" ओळ paste करा
```

करतो: user `atp` + sudo (root चा SSH key), SSH key-only (password off, root login off), ufw (22/80/443 फक्त),
fail2ban (sshd), unattended security upgrades (auto-reboot **off** - market hours मध्ये reboot नको), Docker + compose,
swap 2 GB, timezone Asia/Kolkata, chrony. पुन्हा चालवला तरी काही बिघडत नाही.

**शेवटी:** नव्या terminal मध्ये `ssh atp@<server-ip>` चालतो का ते पाहा - **मगच** root ची window बंद करा.

## 2. Block 2 - deploy (`atp` user म्हणून; पहिल्यांदा आणि दर update ला)

```
ssh atp@<server-ip>
# "deploy.sh" ओळ paste करा
```

तो दोन ठिकाणी थांबतो (exit 10) - काम करा आणि तीच ओळ पुन्हा चालवा:

1. **Deploy key (read-only):** छापलेला `ssh-ed25519 ...` key → GitHub → AdvanceTradingPlatform → Settings → Deploy
   keys → Add (**Allow write access बंद**).
2. **`.env`:** `/opt/atp/.env` तयार होतो (`.env.example` वरून, `CHANGE_ME` placeholders, mode 600). `nano /opt/atp/.env`
   मध्ये भरा:
   - `POSTGRES_PASSWORD`, `JWT_SECRET_KEY`, `METRICS_TOKEN`, `BACKUP_ENCRYPTION_PASSPHRASE`:
     `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`
   - `SECRETS_ENCRYPTION_KEY`: PAPER PC वरचाच key (बदलू नका).
   - Domain असेल: `DOMAIN=atp.<domain>`, `ACME_EMAIL=<तुमचा email>`, `CADDYFILE=Caddyfile` (Let's Encrypt).
   - Domain नसेल: `DOMAIN=<server-ip>`, `ACME_EMAIL=ops@localhost`, `CADDYFILE=Caddyfile.ip` (Caddy चा internal
     certificate - browser एकदा इशारा देतो; refresh cookie Secure असल्याने साधं HTTP चालत नाही).

मग: build → migration guard (market चालू असताना schema बदल नाकारतो; 15:30 नंतर चालवा) → `alembic upgrade head` →
API → worker → frontend → Caddy/backup/offsite → health → `first_paper_day_check.py` (read-only).
Update साठी हीच ओळ (किंवा `bash /opt/atp/deploy/hostinger/deploy.sh`); दुसरा ref: `ATP_REF=<sha|tag> bash ...`.

अपेक्षित: `deployed <sha>`; `curl -sk https://<DOMAIN>/api/system/health` → `{"status":"ok",...}`.

## 3. Block 3 - rollback

```
bash /opt/atp/deploy/hostinger/rollback.sh            # मागचा deployed commit (.deploy/history)
bash /opt/atp/deploy/hostinger/rollback.sh <sha>      # ठराविक commit
```

Deploy सारखाच rolling मार्ग (API आधी, healthy झाल्यावर worker). Schema मागे जात नाही (सगळे migrations additive;
जुनी image नवा schema वाचते). शक्यतो market बंद असताना.

## 4. "काय चालू आहे" - status

```
bash /opt/atp/deploy/hostinger/status.sh
```

commit, containers, प्रत्येकाची memory, host RAM/disk/swap, API/deep health, worker heartbeat, migrations, शेवटचा
backup, off-site copy, public ports (फक्त 22/80/443 अपेक्षित), ufw, certificate, आणि **egress IP** (broker
whitelisting साठी - §6).

## 5. Backups (H-2: Cloudflare R2)

| काय | कसे | कुठे |
|---|---|---|
| रोज Postgres dump (encrypted, SHA-256) | compose `backup` service (24 h), retention 14 दिवस | volume `backups` |
| WAL (PITR, ≤5 मिनिटे RPO) | Postgres `archive_command` | volume `wal_archive` |
| Off-site copy (तासाने) | compose `offsite` (rclone): `copy` dumps, `sync` WAL | **Cloudflare R2** bucket `atp-backups` (`OFFSITE_REMOTE=offsite:atp-backups`) |

**R2 एकदाच तयार करा (तुम्ही, Cloudflare dashboard मध्ये; खर्च: 10 GB + egress मोफत, $5 मर्यादेत):**

1. Cloudflare → R2 → *Create bucket* `atp-backups` (location hint: Asia-Pacific). Public access **बंद**.
2. R2 → *Manage API tokens* → *Create API token*: permission **Object Read & Write**, फक्त bucket `atp-backups`,
   TTL नको. (Admin token नको - token ने bucket delete करता येऊ नये.)
3. मिळालेले तीन: Access Key ID, Secret Access Key, endpoint `https://<account id>.r2.cloudflarestorage.com` →
   **फक्त server वर** `nano /opt/atp/.env`: `OFFSITE_S3_ENDPOINT`, `OFFSITE_S3_ACCESS_KEY`, `OFFSITE_S3_SECRET_KEY`
   (deploy block तोपर्यंत `CHANGE_ME` म्हणून थांबतो). Chat/GitHub मध्ये कधीही नाही.
4. R2 → bucket → *Settings → Object lifecycle rules*: prefix `backups/`, 35 दिवसांनी delete (off-site copy कधी
   delete करत नाही - हा नियम 10 GB मोफत मर्यादेत ठेवतो). `wal_archive/` ला नियम नको (sync स्वतः छाटतो).
5. Block 2 पुन्हा चालवा. `status.sh` मध्ये "off-site" खाली `remote: offsite:atp-backups`, `synced`, शेवटचे dumps
   आणि एकूण size दिसले पाहिजे.

पर्याय (provider seam): Backblaze B2 - `OFFSITE_REMOTE=b2:<bucket>`, `OFFSITE_B2_KEY_ID`, `OFFSITE_B2_APPLICATION_KEY`
(bucket-scoped application key). दुसरा S3-compatible: `OFFSITE_S3_PROVIDER` + `OFFSITE_S3_ENDPOINT`. Provider नसलेला
box: `OFFSITE_REMOTE=/offsite_local` (host disk - खरा off-site नाही).

**Restore test - R2 वरून (आठवड्यातून एकदा, scratch database - production ला हात नाही):**

```
cd /opt/atp && C="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.hostinger.yml -p atp"
F="$($C exec -T offsite sh /scripts/offsite_fetch.sh /restore)" && $C run --rm backup sh /scripts/verify_backup.sh "$F"
```

पहिली ओळ R2 वरचा सर्वात नवा dump + `.sha256` `/var/backups/atp-restore-test` मध्ये आणते; दुसरी तो sha256 तपासून
scratch database मध्ये restore करते, तपासते आणि database drop करते. अपेक्षित: एका ओळीचा JSON, `"status": "verified"`.
Local copy वरून (R2 शिवाय): `$C run --rm backup sh /scripts/verify_backup.sh latest`. PITR drill: `docs/OPERATIONS.md` (Phase O5).

## 6. SEBI - static IP नोंदणी (प्रत्येक broker)

SEBI retail-algo framework: API orders फक्त broker कडे नोंदवलेल्या static IP वरून. Server चा IP = `status.sh` मधला
"egress IP" (hPanel मधला IPv4 सारखाच असावा).

| Broker | कुठे नोंदवायचा | नोंद |
|---|---|---|
| Upstox | developer.upstox.com → My Apps → app → Static IP | ATP चा स्वतंत्र app (Trade bots चा नाही) |
| Zerodha (Kite Connect) | developers.kite.trade → app → IP whitelist | |
| Fyers | myapi.fyers.in → app → whitelisted IPs | PAPER PC चा IP वेगळा - दोन्ही नोंदवायचे का ते broker नियमानुसार |
| Dhan / Angel One / Shoonya | त्या broker च्या API console मध्ये "static IP" | |

तपासणी (प्रत्येक broker): IP नोंदवला ☐ · बदलाची तारीख नोंदवली ☐ · backup IP (असेल तर) ☐ · Settings → Brokers →
Read-only check 9/9 ☐. **IP बदलायचा असेल** (Hostinger IP बदल / नवा server): आधी नव्या IP ची नोंद सर्व brokers कडे,
मगच switch - broker चे बदल-नियम (आठवड्यात किती वेळा) त्यांच्या पानावर पाहा. Shared IP (एकच IP दोन clients) ला
broker नकार देऊ शकतो - ATP आणि Trade droplet चे IP वेगळे आहेत. (Per-tenant IP model + Settings UI = भाग D4.)

## 7. 8 GB मध्ये काय कसं बसतं

`docs/OPERATIONS.md` → "Resource budget - 8 GB host". थोडक्यात: Postgres 2 GB, API 1.5 GB, worker 1.5 GB, Redis
0.5 GB, backup 0.5 GB, Caddy/frontend/offsite ≈ 0.6 GB → ≈ 6.6 GB; उरलेले ≈ 1.4 GB OS + page cache; 2 GB swap फक्त
सुरक्षितता. `status.sh` मधल्या memory रकान्यात limit जवळ आलेलं दिसलं तर `.env` मध्ये `MEM_*` बदला आणि block 2.

## 8. चुकलं तर

| दिसलं | करा |
|---|---|
| `ssh atp@...` चालत नाही | root window बंद करू नका; `cat /home/atp/.ssh/authorized_keys`; hPanel → Browser terminal |
| deploy: migration guard refused | market चालू आहे - 15:30 IST नंतर पुन्हा |
| `public https://... -> 000` | DNS अजून पोहोचला नाही / `CADDYFILE` आणि `DOMAIN` जुळत नाहीत; `docker compose ... logs caddy` |
| container `OOMKilled` | `status.sh` memory; `.env` मध्ये त्या service चा `MEM_*` वाढवा (एकूण ≤ ~7 GB) |
| first_paper_day_check ❌ | `docs/GO_LIVE_MR.md` §3 तक्ता |
