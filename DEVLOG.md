# DEVLOG — yt-pub-livesx

## Current Objectives

1. Establish English working branch (`dev`) from template commit `c7bb6b7`
2. Review architecture for multi-instance scalability
3. Address security TODOs noted in README (rate limiting, password hashing, TLS)

---

## Known Risks / Blockers

- No rate limiting on `/api/login` — brute force risk if exposed publicly
- Dashboard password stored in plaintext in `.env`
- No TLS: credentials travel in plaintext over HTTP
- Session tokens in memory: service restart = forced logout for all users
- OAuth re-auth requires `http://localhost:8090/api/auth/callback` to be registered in each instance's GCP OAuth Client — easy to miss when adding new instances

---

## WIP

_Nothing in progress yet — branch just created._

---

## Session Log

### 2026-05-12 — Branch init
- Cloned from `BR4096/yt-pub-livesx` at commit `c7bb6b7`
- Created `dev` working branch
- Translated `README.md` to English (all paths/filenames preserved)
- Created dev doc set: DEVLOG, BACKLOG, CHANGELOG, session-notes

---
