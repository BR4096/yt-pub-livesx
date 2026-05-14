# CHANGELOG — yt-pub-livesx

All notable changes to this project will be documented here.
Format: [Semantic Versioning](https://semver.org/). Most recent first.

---

## [Unreleased]

## 2026-05-14

### Added
- **i18n**: Full codebase translation to English — all comments, docstrings, log messages, UI labels, echo statements across ~30 files. Variable names, function names, file paths, dict keys, and SQL schemas unchanged. Commits `82561f9`–`f410e8a`

## 2026-05-12

### Added
- **security**: Rate-limit `/api/login` — sliding window, 5 attempts / 5 min per IP, `Retry-After: 300` header on HTTP 429 — commits `341ae88`, `655f6d2`
- **reliability**: No-auth `GET /api/health/ping` for master-dashboard polling — returns `{ok, instance, scheduler_state, disk_free_gb}` — commit `5a8c0e0`
- **reliability**: Disk space guard in scheduler — `_check_disk_space(config)` blocks corte thread when `LIVES_DIR` free space is below `disk_min_gb` config key (default 5 GB) — commit `54ed259`
- **ops**: `setup-canal` credential validation — CLIENT_ID/SECRET format checks + live YouTube API_KEY test with ORIG channel ID validation — commits `39d880e`, `6b390a2`
- **test**: pytest scaffold with 10 tests covering all four new features — commits `003b918`, `98f4caf`
- **docs**: Translated all Portuguese documentation, comments, scripts, and UI to English — commit `01d0e8d`
- **docs**: `DEVLOG.md`, `BACKLOG.md`, `CHANGELOG.md`, `docs/session-notes/` dev doc set created
- `dev` working branch established from template commit `c7bb6b7`

---

## Prior History (from git log)

| Commit | Message |
|--------|---------|
| `c7bb6b7` | Fix formatting of README title |
| `977481a` | docs: documenta autenticacao do dashboard e riscos de seguranca |
| `e4223bc` | feat: protecao por senha em todos os dashboards web |
| `1165d2b` | fix(setup): corrige instalacao de deps em Ubuntu 22.04+ (PEP 668) |
| `839577e` | Limpa template: remove refs hardcoded a lives2, troca por placeholders |

_Full history: `git log --oneline`_
