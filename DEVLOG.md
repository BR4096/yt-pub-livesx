# DEVLOG — yt-pub-livesx

## Current Objectives

1. ~~Establish English working branch (`dev`)~~ — **DONE** 2026-05-12
2. ~~Address security TODOs (rate limiting, health ping, disk guard, setup-canal)~~ — **DONE** 2026-05-12
3. ~~Full codebase i18n to English~~ — **DONE** 2026-05-14
4. Review architecture for multi-instance scalability
5. Merge `dev` → `main` or deploy to instances

---

## Known Risks / Blockers

- ~~No rate limiting on `/api/login`~~ — **RESOLVED** (2026-05-12, commit `341ae88`)
- Dashboard password stored in plaintext in `.env` — **DEFERRED** (acceptable behind SSH tunnel; see BACKLOG item 2)
- No TLS: credentials travel in plaintext over HTTP
- Session tokens in memory: service restart = forced logout for all users
- OAuth re-auth requires `http://localhost:8090/api/auth/callback` registered in each GCP OAuth Client — easy to miss when adding instances

---

## WIP

_Branch `dev` fully up-to-date and pushed. Ready for deployment or merge._

---

## Session Log

### 2026-05-14 — Full i18n translation
- Deployed 6 parallel agents covering ~30 files (scheduler, dashboard/server, master-dashboard, Python modules, scripts/, infra/config)
- All Portuguese comments, docstrings, log/echo messages, UI labels translated to English
- Variable names, function names, SQL schemas, API keys, dict keys unchanged
- 10/10 tests passing post-translation; branch pushed

### 2026-05-12 — Security & reliability hardening + docs
- Rate-limited `/api/login`: 5 attempts / 5 min / IP, `Retry-After` header
- Added no-auth `/api/health/ping` for master-dashboard polling
- Added `_check_disk_space()` in scheduler blocking cortes when disk < `disk_min_gb`
- Added credential validation to `setup-canal` (CLIENT_ID/SECRET format + live YouTube API test)
- Created pytest scaffold from scratch; 10 tests, all passing
- Two-stage review (spec + quality) caught: stale local `import shutil`, test writing to real source dir, missing `Retry-After` header, `exit 0` → `exit 1` on cancel
- Translated all docs, CLAUDE.md, import-worker-spec.md; created DEVLOG/BACKLOG/CHANGELOG/session-notes

### 2026-05-12 — Branch init
- Cloned from `BR4096/yt-pub-livesx` at commit `c7bb6b7`
- Created `dev` working branch

---
