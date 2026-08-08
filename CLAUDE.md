# Claude Code Instructions — yt-pub-livesx

## Behavior Rules

### Before making code changes
**Always show the error found and the proposed solution BEFORE applying any change.**
Format:
- **Error:** clear description of what is wrong and where
- **Solution:** what will be changed and why
- Wait for user confirmation before editing files — unless the change is trivial and easily reversible (e.g. a one-line fix with an obvious rollback via git). Destructive or credential-adjacent changes (anything touching `.env`, `credentials.enc`, `config/`, `data/`, or the sync/deploy flow) always require confirmation regardless of size.

### Never overwrite .env
Never use Write to rewrite `.env` files. Use only Edit to change specific lines.
Reason: caused an outage on lives4 previously.

### Sync between instances
- `yt-pub-livesx` is the source code template
- `scripts/sync-instances` syncs to the instances listed in TARGETS
- **`sync-instances` runs on the server** (hardcoded `/home/nmaldaner/projetos/` paths) — not locally
- Deploy flow: `git push` → SSH to server → `git pull` in `yt-pub-lives2` → `bash scripts/sync-instances` → `systemctl --user restart yt-dashboard{N} yt-scheduler{N}`
- NEVER sync `config/`, `data/`, `credentials.enc`, `.env` between instances
- After sync, restart the affected services, then verify: `systemctl --user status yt-dashboard{N} yt-scheduler{N}` came up clean, and the dashboard URL responds — a clean restart exit code alone doesn't confirm the service is actually serving
- Full SOP: `brain2/operations/sops/sop-yt-pub-livesx-deploy.md`

### Version
Update the version (vMAJOR.FEATURES.BUGS) in `dashboard/index.html` with every functional change.
