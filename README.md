# yt-pub-livesx

![YouTube Live Clips — Video Factory](assets/banner.jpg)

Automated pipeline to cut YouTube live streams into topic-based clips and publish them to another channel.

**Source channel** (live streams): [INEMA TDS](https://www.youtube.com/@inematdsx) (`UC2QbQDyPKuHk93dwo5iq3Sw`)
**Destination channel** (clips): [INEMA TIA](https://www.youtube.com/@InemaTIA) (`UCavuQHkxBSAZbzRoOm6Gq4g`)

## Flow

```
YouTube (live streams from source channel) → Transcription → AI Analysis → Cut (FFmpeg) → Thumbnail (AI) → Publish (destination channel)
```

1. **Syncs** live streams from the source channel via YouTube Data API
2. **Downloads** auto-generated transcription (YouTube subtitles)
3. **Analyzes topics** with AI (Piramyd/Claude/OpenRouter API)
4. **Cuts clips** with FFmpeg based on timestamps
5. **Generates thumbnails** with AI (LLM + image generator) or locally
6. **Publishes clips** to the destination channel with title, description, tags, and thumbnail

## Structure

```
yt-pub-livesx/
├── config/                    # Isolated project configuration
│   ├── .env                   # Environment variables (not committed to git)
│   ├── client_secret.json     # OAuth credentials (not committed to git)
│   ├── credentials.enc        # Encrypted tokens (not committed to git)
│   ├── .encryption_key        # AES-GCM key (not committed to git)
│   ├── prompt_cortes.txt      # AI prompt for topic analysis
│   ├── prompt_pub.txt         # AI prompt to refine title/description
│   └── prompt_thumb.txt       # AI prompt to generate thumbnails
├── data/
│   └── lives.db               # Local SQLite database (not committed to git)
├── dashboard/
│   ├── server.py              # Backend API (Python HTTP server)
│   └── index.html             # Frontend SPA (vanilla JS)
├── scripts/
│   ├── yt-auth                # Standalone OAuth authentication
│   ├── yt-clip                # Pipeline: transcription → analysis → cut
│   ├── yt-publish             # Upload video to YouTube
│   ├── yt-thumbnail           # Generate thumbnails with AI
│   ├── setup-db               # Create SQLite database (--import migrates from Sheets)
│   └── sync-instances         # Sync code to other instances
├── systemd/
│   ├── yt-dashboard.service   # systemd service (port 8091)
│   └── yt-scheduler.service   # systemd scheduler service
├── db.py                      # SQLite module (CONFIG, LIVES, PUBLICADOS)
├── scheduler.py               # Automatic scheduler
├── docker-compose.yml         # Docker (port 8091)
├── Dockerfile
├── requirements.txt
├── setup.sh
└── docs/
    └── SETUP-CANAL-DESTINO.md # Complete setup documentation
```

## Requirements

- Python 3.10+
- ffmpeg
- yt-dlp
- deno (JS runtime used by yt-dlp)
- curl
- Pillow (thumbnails)

## Architecture: master + channels

The system consists of **1 master-dashboard** + **N instances** (1 per channel).

```
~/projetos/
├── yt-pub-livesx/              ← TEMPLATE (this repo, no credentials)
│   ├── master-dashboard/       ← aggregates all instances
│   ├── scripts/setup-system    ← Part 1: starts master
│   └── scripts/setup-canal     ← Part 2: creates new instance
│
├── yt-pub-lives1/              ← Channel 1 (copy of template)
│   ├── config/.env             ← Channel 1 GCP credentials
│   ├── config/credentials.enc  ← Channel 1 OAuth tokens
│   ├── data/lives.db           ← Isolated SQLite
│   └── lives/                  ← Downloaded videos
│
├── yt-pub-livesx/              ← Channel 2 (same)
└── yt-pub-lives7/              ← Channel N
```

### What is shared vs isolated

| Resource | Master (port 8090) | Each channel (port 809N) |
|---|---|---|
| Code (Python/HTML) | own (template folder) | own copy |
| SQLite database | not used | own `data/lives.db` |
| GCP credentials | not used | own GCP project |
| Channel OAuth | no | own `config/credentials.enc` |
| systemd service | `yt-master-dashboard` | `yt-dashboard<N>` + `yt-scheduler<N>` |
| Code update | manual in template | via `sync-instances` (opt-in) |

### systemd services (final model)

```
yt-master-dashboard           → port 8090 (aggregates all)
yt-dashboard1 + yt-scheduler1 → port 8091 (channel 1)
yt-dashboard2 + yt-scheduler2 → port 8092 (channel 2)
...
yt-dashboardN + yt-schedulerN → port 809N (channel N)
```

Each `dashboard<N>` + `scheduler<N>` pair is **independent**: if one channel goes down, the others keep running. The master only consumes the HTTP APIs from each dashboard.

### Data flow (1 channel)

```
YouTube (source channel)
    ↓ YouTube Data API v3
scheduler<N>  ─→  downloads new live streams (yt-dlp)
    ↓
    transcription (YouTube subtitles)
    ↓
    AI analysis (Piramyd/Claude) → topics + timestamps
    ↓
    cut (FFmpeg)
    ↓
    thumbnail generation (AI or local)
    ↓
    upload via OAuth → YouTube (destination channel)
    ↓
data/lives.db  ←  local log + status
    ↑
dashboard<N>   ←  control UI (port 809N)
    ↑
master-dashboard ← aggregates all (port 8090)
```

### Why 1 channel = 1 instance?

- **YouTube OAuth is per user/channel** — you can't authenticate 2 channels under the same OAuth app
- **YouTube Data API quota is per GCP project** — separate projects = independent quotas
- **Failure isolation** — a bug or rate-limit on one channel doesn't affect the others
- **Optional code sync** — `scripts/sync-instances` propagates template updates; each instance opts in

## Installation

Installation is split into **two independent parts**:

- **Part 1 — `setup-system`** (once per machine): starts the master-dashboard
  on port 8090 and prepares dependencies.
- **Part 2 — `setup-canal`** (once per channel, including the first): creates
  a new instance by copying this template.

> This folder (`yt-pub-livesx`) is the **official template** — it must never
> contain `.env`, credentials, or data. Every new instance is a copy of it.

### Part 1 — System setup

```bash
git clone <repo> yt-pub-livesx
cd yt-pub-livesx
./setup.sh                    # equivalent to: ./scripts/setup-system
```

The script:
1. Verifies `python3`, `ffmpeg`, `curl`, `yt-dlp`, `deno`
2. Installs Python packages (`cryptography`, `anthropic`)
3. Starts the **master-dashboard** as a systemd user service (`yt-master-dashboard`)
4. Exits with an error if port 8090 is already in use

When done: `http://localhost:8090`

### Part 2 — Add a channel

```bash
./scripts/setup-canal
```

Before running, have the following ready:

| Question | Source | Default |
|---|---|---|
| Instance name | free (e.g. `yt-pub-lives7`) | — |
| Instance number (services) | extracted from name if it ends in a digit | next available |
| Dashboard port | free port on the machine | next available 8091+ |
| `YOUTUBE_CHANNEL_ID` (source) | UC... of the channel where live streams come from | INEMA TDS |
| Destination channel handle | documentation only | optional |
| `CLIENT_ID` / `CLIENT_SECRET` | GCP → OAuth Client ID (Desktop App) | — |
| `API_KEY` | GCP → API Key (YouTube Data API v3) | — |
| `GCP_PROJECT` | GCP project ID | — |
| `PIRAMYD_API_KEY` | Piramyd dashboard | — |
| Dashboard password | free | `Inema2026$$$` |
| Add to `sync-instances`? | y/N | N |

> **Press ENTER at any `[default]` prompt to accept the shown default.**

#### Prerequisites on Google Cloud (1 project per instance)

Each instance needs its own **Google Cloud project**:

1. Go to [Google Cloud Console](https://console.cloud.google.com) and create a project (e.g. `yt-pub-lives7`)
2. Enable the API: **YouTube Data API v3**
   - Menu: APIs & Services → Library → YouTube Data API v3 → Enable
3. Configure the **OAuth Consent Screen**:
   - Type: **External**, mode **Testing**
   - Scopes: `youtube`, `youtube.upload`
   - Test users: add the **email of the account that owns the destination channel**
4. Create **OAuth 2.0 → Desktop App** credentials:
   - Authorized redirect URIs: `http://localhost:8888`
   - For re-auth via master-dashboard: also add `http://localhost:8090/api/auth/callback`
   - Note `CLIENT_ID` and `CLIENT_SECRET`
5. Create an **API Key** — note the value
6. (Optional) Verify the channel's phone number at `youtube.com/verify`
   - Required to upload **custom thumbnails**

#### What `setup-canal` does

1. Asks the questions above (ENTER accepts default)
2. Shows a summary and asks for confirmation (`[S/n]`)
3. `cp -r` from this template to `~/projetos/<name>/`
4. Clears `data/`, `lives/`, `.git/` and sensitive files (`.env`, `credentials.enc`, `.encryption_key`)
5. Generates `config/.env` (chmod 600) with the provided answers
6. Patches service files (port, paths, dashboard/scheduler dependency)
7. Creates symlinks in `~/.config/systemd/user/yt-dashboard<N>.service` and `yt-scheduler<N>.service`
8. Starts the **dashboard** and **pauses** for you to run OAuth manually
9. After OAuth: starts the **scheduler**
10. (Optional) registers the instance in `scripts/sync-instances`

Final URL: `http://localhost:<port>` — and it already appears on the master at `http://localhost:8090`

### OAuth Authentication (manual step inside `setup-canal`)

When `setup-canal` pauses, open **another terminal** and run:

```bash
GWS_CONFIG_DIR=~/projetos/<name>/config python3 ~/projetos/<name>/scripts/yt-auth
```

`yt-auth`:
1. Generates a Google authentication link
2. Starts a local server at `http://localhost:8888` waiting for the callback
3. You open the link in the browser and authorize with the destination channel's account
4. The callback saves the encrypted tokens in `config/credentials.enc`

**OAuth Troubleshooting:**
- *"Access blocked"*: click **Advanced → Go to (app) (unsafe)** (normal in Testing mode)
- *"app has not completed verification"*: the account is not listed as a **test user** — add it in GCP → OAuth Consent Screen → Test users
- *"Unable to connect localhost:8888"*: the `yt-auth` script already exited — run it again and open the link **while it is running**
- Multiple accounts in the browser: use a **private/incognito tab** or append `&login_hint=email@gmail.com` to the link

**Re-authentication via Master Dashboard (port 8090):**

The master uses `redirect_uri=http://localhost:8090/api/auth/callback`. This URI must **also** be registered in GCP → Credentials → OAuth Client ID for the instance → Authorized redirect URIs. Without this, re-auth fails even after authorizing on Google.

### Database (local SQLite)

Created automatically when the scheduler or dashboard starts. To create manually:

```bash
python3 scripts/setup-db                # create empty DB
python3 scripts/setup-db --import       # create DB and import from Google Sheets (legacy)
```

Database at `data/lives.db` with tables **config**, **lives**, **publicados**.

### VPS Deployment (Ubuntu/Debian)

Step-by-step on a clean VPS from scratch.

#### 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip ffmpeg curl git unzip pipx
pipx install yt-dlp

# Deno (JS runtime used by yt-dlp)
curl -fsSL https://deno.land/install.sh | sh
echo 'export PATH="$HOME/.deno/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

#### 2. Enable lingering (services run without SSH login)

```bash
sudo loginctl enable-linger $USER
```

Without this, all `--user` services stop when you disconnect from SSH.

#### 3. Clone and run Part 1

```bash
mkdir -p ~/projetos && cd ~/projetos
git clone https://github.com/inematds/yt-pub-livesx.git
cd yt-pub-livesx
./setup.sh
```

#### 4. Firewall — optional but recommended

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8090/tcp        # master-dashboard
sudo ufw allow 8091:8099/tcp   # instance port range
sudo ufw enable
```

To avoid exposing ports publicly, keep the firewall closed and use an **SSH tunnel** from your local machine:

```bash
ssh -L 8090:localhost:8090 -L 8091:localhost:8091 user@vps
```

#### 5. Create the first channel

```bash
./scripts/setup-canal
```

**OAuth on a VPS without a browser:** when `setup-canal` pauses, open **another SSH terminal with a port 8888 tunnel**:

```bash
ssh -L 8888:localhost:8888 user@vps
# inside the VPS:
GWS_CONFIG_DIR=~/projetos/yt-pub-lives1/config python3 ~/projetos/yt-pub-lives1/scripts/yt-auth
```

Copy the generated link, open it in your **local machine's browser**, and authorize. The callback hits `localhost:8888` locally → travels through the SSH tunnel → lands on the VPS and saves the encrypted tokens.

#### 6. Verify

```bash
systemctl --user list-units --type=service --state=active | grep yt-
journalctl --user -u yt-dashboard1 -f
journalctl --user -u yt-scheduler1 -f
```

#### 7. Backup (essential)

Save regularly **outside the VPS**:

- `config/credentials.enc` — without this, you must redo OAuth
- `config/.encryption_key` — without this key, `credentials.enc` is useless
- `data/lives.db` — history of processed live streams

```bash
tar -czf backup-$(date +%F).tar.gz \
  ~/projetos/yt-pub-lives*/config/.env \
  ~/projetos/yt-pub-lives*/config/credentials.enc \
  ~/projetos/yt-pub-lives*/config/.encryption_key \
  ~/projetos/yt-pub-lives*/data/lives.db
```

#### Minimum VPS resources

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU (FFmpeg cuts video) |
| RAM | 2 GB | 4 GB |
| Disk | 20 GB | 50+ GB (raw videos stored in `lives/`) |
| Bandwidth | 1 TB/month | depends on number of channels |
| OS | Ubuntu 22.04+ / Debian 12+ | — |

> **Disk:** raw videos stay in `lives/` until the pipeline cuts and publishes them. Set up periodic cleanup or the disk will fill up:
> ```bash
> find ~/projetos/yt-pub-lives*/lives -mtime +7 -delete
> ```

### AI Prompts (optional)

Copy customized prompts to `config/`:
```bash
cp ~/path/to/prompt_cortes.txt config/
cp ~/path/to/prompt_pub.txt config/
cp ~/path/to/prompt_thumb.txt config/
```

Or edit them via the dashboard configuration tab.

## Usage

### Web Dashboard

```bash
python3 dashboard/server.py [port]    # default: 8091
```

Go to `http://localhost:8091` — the browser will ask for a password on first access.

#### Authentication

All dashboards (master `:8090` and each channel `:809N`) require a password.

- **Default password:** `Inema2026$$$`
- **Configured in:** `config/.env` → variable `DASHBOARD_PASSWORD`
- **Session cookie** (`ds`) lasts 30 days; expires if the service restarts

**Change password** (via curl or browser devtools):
```bash
curl -X POST http://localhost:8091/api/config/password \
  -b "ds=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"current":"Inema2026$$$","new":"NewPassword"}'
```

The `ds` token appears in browser cookies after login. Changing it invalidates all active sessions (forces re-login).

**Existing instances** (created before this version): manually add to `config/.env`:
```
DASHBOARD_PASSWORD=Inema2026$$$
```
Then restart the service: `systemctl --user restart yt-dashboard<N>`.

> **TODO (security — review before exposing publicly):**
> The current implementation is suitable for internal use on a VPS behind an SSH tunnel,
> but has limitations that should be evaluated before exposing to an open network:
> - No rate limiting on `/api/login` — susceptible to brute force
> - Password stored in plain text in `.env` (not hashed)
> - No TLS: cookie and password travel in plaintext (risk on untrusted networks)
> - Sessions in memory: service restart = forced logout for everyone
> - No inactivity expiration (only the 30-day Max-Age)
> - No 2FA
> While access is via local SSH tunnel, the risk is low. If exposing publicly,
> the minimum is to add a reverse proxy (nginx) with HTTPS.

Dashboard panels:
- Clickable stats (total live streams, cut, pending, clips waiting, published)
- Schedule configuration (24h visual picker)
- Live stream table with status filter
- Unified Clips tab: published + pending
- Clip controls: pause/resume individual publication
- Reprocess live streams with errors
- Privacy controls
- Thumbnail configuration
- Real-time scheduler status

### Docker

```bash
docker-compose up -d
```

Dashboard at `http://localhost:8091`.

### Systemd (user services)

```bash
# Create symlinks (example for lives5, port 8095)
ln -sf /home/nmaldaner/projetos/yt-pub-lives5/systemd/yt-scheduler.service ~/.config/systemd/user/yt-scheduler5.service
ln -sf /home/nmaldaner/projetos/yt-pub-lives5/systemd/yt-dashboard.service ~/.config/systemd/user/yt-dashboard5.service
systemctl --user daemon-reload
systemctl --user enable --now yt-scheduler5 yt-dashboard5
```

### Multi-instance

**Recommended convention:** GCP project name = destination channel name
(easier to audit — you can see in GCP Console which project serves which channel).

| Instance | Port | Scheduler | Dashboard | Destination Channel | GCP Project |
|----------|------|-----------|-----------|---------------------|-------------|
| lives1 | 8091 | yt-scheduler1 | yt-dashboard1 | INEMA TDS | inema-tds |
| lives2 | 8092 | yt-scheduler2 | yt-dashboard2 | INEMA TIA | inema-tia |
| lives3 | 8093 | yt-scheduler3 | yt-dashboard3 | INEMA TDS | inema-tds-2 |
| lives4 | 8094 | yt-scheduler4 | yt-dashboard4 | INEMA Tec | inema-tec |
| lives5 | 8095 | yt-scheduler5 | yt-dashboard5 | INEMA PROMPTS | inema-prompts |
| lives6 | 8096 | yt-scheduler6 | yt-dashboard6 | INEMA Robot | inema-robot |

**Code sync** (`yt-pub-livesx` is the source template):
```bash
./scripts/sync-instances    # Propagates code from the template to listed instances
```

**Restart all:**
```bash
systemctl --user restart yt-scheduler{1..6} yt-dashboard{1..6}
```

### Cut a Live Stream

```bash
yt-clip <video_id>                    # Manual mode (generates prompt)
yt-clip <video_id> --ai piramyd-api   # Automatic mode (Piramyd API)
yt-clip <video_id> --dry-run          # Show topics only
yt-clip <video_id> --publish          # Cut and publish
```

### Generate Thumbnail

```bash
yt-thumbnail --title "Clip title" --output thumb.jpg
```

### Publish a Video

```bash
yt-publish video.mp4 --title "Title" --description "Description"
yt-publish video.mp4 --title "Title" --description "Desc" --privacy unlisted --tags "ai,dev"
```

## Technologies

- **Backend**: Python 3 (stdlib HTTPServer, no frameworks)
- **Frontend**: Vanilla HTML/CSS/JS (single page, no build step)
- **Database**: Local SQLite (WAL mode, no external dependency)
- **APIs**: YouTube Data API v3
- **AI**: Piramyd API / Anthropic Claude API / OpenRouter (topic analysis + thumbnails)
- **Video**: FFmpeg (cutting), yt-dlp (downloading)
- **Auth**: OAuth 2.0 with refresh token (AES-GCM encrypted)

## License

Internal use — INEMA TDS (@inematdsx)
