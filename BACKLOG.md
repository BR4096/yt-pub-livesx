# BACKLOG — yt-pub-livesx

## Security (High Priority)

- [ ] Add rate limiting to `/api/login` (brute force protection)
- [ ] Hash dashboard passwords (bcrypt) instead of storing plaintext in `.env`
- [ ] Add TLS / HTTPS support or document nginx reverse proxy setup
- [ ] Implement session inactivity timeout (currently only 30-day Max-Age)
- [ ] Add 2FA option for dashboard access
- [ ] Sessions currently in-memory — evaluate persistent session store to survive restarts

## Reliability

- [ ] Add health check endpoint to each instance dashboard (for master polling)
- [ ] Add watchdog / auto-restart logic if scheduler dies silently
- [ ] Disk space guard: alert or pause when `lives/` exceeds configurable threshold
- [ ] Automated backup script with configurable remote destination (rclone/rsync)

## Operations

- [ ] Document `sync-instances` opt-in/opt-out workflow more clearly
- [ ] Add `--dry-run` flag to `sync-instances`
- [ ] Add per-instance log rotation for scheduler and dashboard logs
- [ ] `setup-canal` script: validate GCP credentials before writing `.env`

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

_Nothing marked done yet — backlog seeded at branch init._
