# Claude Code Instructions — yt-pub-livesx

## Behavior Rules

### Before making code changes
**Always show the error found and the proposed solution BEFORE applying any change.**
Format:
- **Error:** clear description of what is wrong and where
- **Solution:** what will be changed and why
- Wait for user confirmation before editing files

### Never overwrite .env
Never use Write to rewrite `.env` files. Use only Edit to change specific lines.
Reason: caused an outage on lives4 previously.

### Sync between instances
- `yt-pub-livesx` is the source code template
- `scripts/sync-instances` syncs to the instances listed in TARGETS
- **`sync-instances` runs on the server** (hardcoded `/home/nmaldaner/projetos/` paths) — not locally
- Deploy flow: `git push` → SSH to server → `git pull` in `yt-pub-lives2` → `bash scripts/sync-instances` → `systemctl --user restart yt-dashboard{N} yt-scheduler{N}`
- NEVER sync `config/`, `data/`, `credentials.enc`, `.env` between instances
- After sync, restart the affected services
- Full SOP: `brain2/operations/sops/sop-yt-pub-livesx-deploy.md`

### Version
Update the version (vMAJOR.FEATURES.BUGS) in `dashboard/index.html` with every functional change.
