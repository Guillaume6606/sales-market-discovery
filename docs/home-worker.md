# Home ingestion worker: LeBonCoin from a residential connection

**Status: not measured.** LeBonCoin access from a residential IP is unverified until step 0 below
has been run. Baseline (production, 2026-10-03, rolling 24 h, see
`production-readiness-2026-10-03.md`): from the VPS datacenter IP, LeBonCoin had 74 successes,
15 empty results and 43 errors out of 132 runs; its API answers HTTP 403 even through the
residential proxy.

## How it works

| | VPS (primary worker, `WORKER_ROLE=primary`) | Home (remote worker, `WORKER_ROLE=remote`) |
|---|---|---|
| Crons | global jobs (alert delivery, stale marking, computation, enrichment, scoring, system health) + providers **not** in `REMOTE_PROVIDERS` | only `scheduled_<provider>_ingestion` for `REMOTE_PROVIDERS` |
| ARQ queue | `arq:queue` | `arq:queue:remote` |
| Heartbeat key | `worker:heartbeat` | `worker:heartbeat:remote` |

- `REMOTE_PROVIDERS` (comma-separated, default empty) must hold the same value on the VPS and in
  `.env.home`. Empty means one worker ingests everything, exactly as before.
- Manual triggers from the API/UI are still enqueued on `arq:queue`; the primary forwards the
  remote-provider part to `arq:queue:remote` (a multi-provider trigger is split). The remote worker
  never forwards back; it skips providers it does not serve.
- The per-product Postgres advisory lock (`ingest:<product_id>`) and the Redis cooldown keys
  (`ingestion:cooldown:<provider>`) live on the VPS and are shared, so they stay correct with two
  workers. Each provider is ingested by exactly one worker.
- The home worker runs the post-ingestion pipeline for its runs (details, PMN, vision, scoring,
  alert evaluation and first delivery attempt). Hence `.env.home` starts from the VPS `.env`.
- **Home node off:** the remote heartbeat expires after 31 s. `/health/workers` shows
  `online: false`; in `/health/overview` and `/health/ingestion` the LeBonCoin entry carries
  `worker: remote, worker_online: false`, its card turns gray ("Unknown") and the system banner
  turns yellow ("degraded"). eBay/Vinted/CashConverters keep their own status. The 2-hourly system
  health Telegram alert lists LeBonCoin with `remote worker heartbeat missing`. `/health/ready`
  only checks the VPS worker, so deploys are not blocked by the home node.

## 0. Probe first (no VPS change, one request)

On the home machine (Mac: `colima start` first):

```bash
git switch feature/home-worker          # same commit as the VPS
cp .env.home.example .env.home && chmod 600 .env.home
make home-probe                         # optional: KEYWORD="sony a7"
```

`home-probe` builds the ingestion image and runs **one** LeBonCoin search (5 ads, one page, no
retry, no loop). Expected: `leboncoin_probe ok listings=N`.

- `N > 0`: the residential IP is accepted for this search. Continue.
- `RuntimeError: LeBonCoin search failed: HTTP 403`: the residential IP is blocked as well. Stop
  here; this setup will not help.
- Redis is not reachable yet at this step; the cooldown lookup times out after 2 s and is skipped.

## 1. VPS changes (once, nothing is exposed publicly)

1. **Tailscale ACL**: allow only the home node to reach the VPS on `tcp:5432` and `tcp:6379`.
2. **Boot ordering**: let Docker publish on the Tailscale IP even before `tailscaled` has set it:
   ```bash
   echo 'net.ipv4.ip_nonlocal_bind=1' | sudo tee /etc/sysctl.d/99-tailnet-bind.conf
   sudo sysctl --system
   ```
3. **Postgres password**: `POSTGRES_PASSWORD` in `/opt/market-discovery/.env` must not be the
   default `app`. If it is, rotate it first
   (`$DC exec db psql -U app -d app -c "ALTER USER app PASSWORD '<new>'"`, then update `.env`).
4. **Deploy this branch unchanged** (no env change yet; behaviour is identical to today):
   `make deploy` from the operator machine, then check `curl -fsS localhost:8000/health/ready`
   on the VPS.
5. **Enable the topology** in `/opt/market-discovery/.env`:
   ```bash
   REDIS_PASSWORD=<openssl rand -hex 32>
   REDIS_URL=redis://:<same password>@redis:6379/0
   REMOTE_PROVIDERS=leboncoin
   TAILNET_BIND_IP=100.99.53.92
   COMPOSE_PROFILES=tailnet
   ```
6. **Apply** (Redis restarts with a password; in-flight ARQ jobs may be lost and are re-enqueued by
   the next 5-minute cron):
   ```bash
   cd /opt/market-discovery
   DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
   $DC up -d --force-recreate --no-deps redis backend ingestion ui
   $DC up -d tailnet-gateway
   ```
   `tailnet-gateway` (HAProxy, `infra/tailnet/haproxy.cfg`) refuses to start without
   `REDIS_PASSWORD`. It is the only service publishing 5432/6379, and only on `TAILNET_BIND_IP`.
   Do **not** add `ufw allow` rules for 5432/6379 and never bind them to `0.0.0.0`.
7. **Verify on the VPS**:
   ```bash
   curl -fsS localhost:8000/health/ready
   curl -s localhost:8000/health/workers      # primary online:true, remote online:false
   sudo ss -ltnp | grep -E ':(5432|6379)\b'    # only 100.99.53.92 listed
   ```
   From a machine **outside** the tailnet: `nc -vz -w 3 <VPS public IP> 5432` and `6379` must fail.

From step 6 until the home node starts, LeBonCoin is not ingested at all and the Health page
shows it as such (gray card, yellow banner).

## 2. Home node (once)

1. Tailscale logged into the same tailnet; check `nc -vz 100.99.53.92 5432` and `6379`.
2. Build `.env.home` from the VPS `.env` (LLM, vision, Telegram, `WORKING_CAPITAL_EUR` and alert
   settings must match), then apply the overrides of `.env.home.example`:
   `REMOTE_PROVIDERS=leboncoin`, `POSTGRES_HOST=100.99.53.92`, `POSTGRES_PASSWORD`,
   `REDIS_URL=redis://:<password>@100.99.53.92:6379/0` and an **empty**
   `SCRAPING_PROXY_URL=` (the connection is already residential; no paid proxy traffic).
   `chmod 600 .env.home`.
3. `make home-probe` (now with Redis reachable), then `make home-up` and `make home-logs`: expect
   the ARQ worker start line and `scheduled_leboncoin_ingestion` firing at minutes 1, 6, 11, …
4. Keep it running: Mac `sudo pmset -a sleep 0` (or a mini-PC), `brew services start colima`. The
   container has `restart: unless-stopped`.
5. Check `curl -s -u <user>:<password> https://<DOMAIN>/health/workers`: both workers
   `online: true`.

## Success criterion

T0 = time of `make home-up`. Read at **T0 + 7 days** (earlier, the 7-day window still contains
VPS-era runs): Health page, LeBonCoin card, **"Fetch Success 7d" ≥ 90 %** (`success_rate_7d` in
`/health/ingestion`). Denominator: completed runs (success + empty + error) in the window; empty
results count as non-success. Also report `completed_runs_7d`: if it is far below the expected
schedule, the node was offline (an uptime problem, not an access problem).

Early read (runs since T0 only):

```bash
curl -s -u <user>:<password> "https://<DOMAIN>/ingestion/runs?source=leboncoin&date_from=<T0 ISO>&page_size=100" \
  | jq '[.runs[].status] | group_by(.) | map({(.[0]): length}) | add'
```

## Rollback

1. Home: `make home-down`.
2. VPS `.env`: set `REMOTE_PROVIDERS=` (empty), remove `COMPOSE_PROFILES` and `TAILNET_BIND_IP`
   (`REDIS_PASSWORD`/`REDIS_URL` can stay). Then:
   ```bash
   $DC rm -sf tailnet-gateway
   $DC up -d --force-recreate --no-deps backend ingestion ui
   $DC exec redis sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning DEL arq:queue:remote'
   ```
   LeBonCoin goes back to the VPS worker (and its proxy). No code rollback is needed: an empty
   `REMOTE_PROVIDERS` is the previous behaviour.

## Operating notes

- **Deploys**: `make home-down` before `make deploy` (migrations run while the VPS workers are
  stopped), then switch the home checkout to the deployed commit and `make home-up`.
- **Lost home node mid-run**: its Postgres session (and the product advisory lock) is cut by the
  gateway after 30 min idle; meanwhile other-provider runs of that product are skipped and retried
  by the next cron.
- **Still on the VPS IP**: `audit_ingestion_sample`, `run_on_demand_audit` and listing screenshots
  fetch listing URLs from the VPS. `/ingestion/queue/status` counts `arq:queue` only.
