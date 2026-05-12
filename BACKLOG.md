# BACKLOG — yt-pub-livesx

## Security (High Priority)

- [x] Add rate limiting to `/api/login` (brute force protection) — sliding window, 5 attempts / 5 min per IP, `Retry-After` header on 429
- [ ] Hash dashboard passwords (bcrypt) instead of storing plaintext in `.env` — **DEFERRED** (acceptable behind SSH tunnel; implement before any public exposure)
- [ ] Add TLS / HTTPS support or document nginx reverse proxy setup
- [ ] Implement session inactivity timeout (currently only 30-day Max-Age)
- [ ] Add 2FA option for dashboard access
- [ ] Sessions currently in-memory — evaluate persistent session store to survive restarts

## Reliability

- [x] Add health check endpoint to each instance dashboard (for master polling) — no-auth `GET /api/health/ping` returns `{ok, instance, scheduler_state, disk_free_gb}`
- [ ] Add watchdog / auto-restart logic if scheduler dies silently
- [x] Disk space guard: alert or pause when `lives/` exceeds configurable threshold — `_check_disk_space(config)` in scheduler, `disk_min_gb` config key, default 5 GB
- [ ] Automated backup script with configurable remote destination (rclone/rsync)

## Operations

- [ ] Document `sync-instances` opt-in/opt-out workflow more clearly
- [ ] Add `--dry-run` flag to `sync-instances`
- [ ] Add per-instance log rotation for scheduler and dashboard logs
- [x] `setup-canal` script: validate GCP credentials before writing `.env` — CLIENT_ID/SECRET format checks + live YouTube API_KEY test

## Features

- [ ] Master dashboard: show per-instance disk usage
- [ ] Dashboard: dark mode
- [ ] `yt-clip`: support multiple AI backends (Claude, OpenRouter) via config flag
- [ ] Export processed clips list to CSV from dashboard

## Documentation

- [x] Translate `docs/import-worker-spec.md` to English
- [x] Translate `CLAUDE.md` to English
- [ ] Add architecture diagram (ASCII or image) for multi-instance setup
- [ ] Document `prompt_cortes.txt` / `prompt_pub.txt` / `prompt_thumb.txt` prompt format

## Done

- Rate-limit `/api/login` — 5 attempts / 5 min per IP (2026-05-12)
- No-auth `/api/health/ping` endpoint for master-dashboard polling (2026-05-12)
- Disk space guard in scheduler — `disk_min_gb` config key, default 5 GB (2026-05-12)
- `setup-canal` credential validation — CLIENT_ID/SECRET format + live API_KEY test (2026-05-12)
