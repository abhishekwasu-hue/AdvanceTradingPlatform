# Open questions

Questions met while working without the owner. Each has a provisional decision (taken so work continues) and where it
applies. When answered: mark **answered**, note the answer, and fix the code if it changes anything.

| # | Question | Provisional decision + reason | Applies to | Status |
|---|----------|-------------------------------|------------|--------|
| H-1 | Redis on the 8 GB host: evict at the cap (`volatile-lru`) or refuse writes (`noeviction`)? | `volatile-lru` with a 384 MB cap: only keys with a TTL (caches, rate windows, quote cache) can be evicted; keys without a TTL (locks are set with TTLs, so they are evictable last-used-first - a lock evicted early is re-taken fail-closed by the S2 lock code). `noeviction` would turn a full cache into failed API calls. Changeable without a code change: `.env` `REDIS_MAXMEMORY`, the policy in `docker-compose.hostinger.yml`. | `docker-compose.hostinger.yml`, Hostinger PR | open |
| H-2 | Off-site backups before an S3-compatible provider is chosen? | A local directory (`/var/backups/atp-offsite`) through the same rclone `offsite` service, so the switch later is one `.env` line (`OFFSITE_REMOTE=spaces:atp-backups` + `OFFSITE_S3_*`). It is **not** a real off-site copy (same disk): the runbook asks for a weekly `scp` to the PC until a provider is picked. Choosing a provider (cost, possibly > $5/month) is the owner's decision. | `docs/DEPLOY_HOSTINGER_MR.md` §5, Hostinger PR | open |
