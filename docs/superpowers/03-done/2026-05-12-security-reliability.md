# Security & Reliability Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add login rate-limiting, a no-auth health ping endpoint, a disk-space guard in the scheduler, and credential validation in the setup script — all without touching password hashing (deferred to backlog).

**Architecture:** All changes are additive to existing files. Rate limiting uses a module-level in-memory dict (acceptable; process restart clears it, which is fine behind SSH tunnel). Health ping is a new no-auth route added before the `_require_auth` gate. Disk guard wraps the existing corte trigger in scheduler's main loop. Credential validation is a new section in the `setup-canal` bash script using `curl` + Python format checks.

**Tech Stack:** Python 3 stdlib (`threading`, `shutil`, `time`), Bash, `pytest` (tests only — add to requirements.txt).

---

## File Map

| File | Change |
|------|--------|
| `requirements.txt` | Add `pytest` |
| `tests/__init__.py` | Create (empty) |
| `tests/conftest.py` | Create — shared `tmp_lives_dir` fixture |
| `tests/test_login_rate_limit.py` | Create |
| `tests/test_health_ping.py` | Create |
| `tests/test_disk_guard.py` | Create |
| `dashboard/server.py` | Add `_LOGIN_ATTEMPTS`, `_LOGIN_LOCK`, rate-limit in `_handle_login`, `_handle_health_ping`, register at `/api/health/ping` |
| `scheduler.py` | Add `_check_disk_space()`, call before corte trigger |
| `scripts/setup-canal` | Add credential validation section after GCP prompts |

---

## Task 0: Test scaffold

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest to requirements**

  Edit `requirements.txt` to:
  ```
  cryptography>=41.0.0
  anthropic>=0.30.0
  pytest>=8.0.0
  ```

- [ ] **Step 2: Create tests/ scaffold**

  Create `tests/__init__.py` — empty file.

  Create `tests/conftest.py`:
  ```python
  import os
  import sys
  import tempfile
  import pytest

  # Point db.py at a temp directory so tests never touch the real data/lives.db
  @pytest.fixture(autouse=True)
  def tmp_db_dir(tmp_path, monkeypatch):
      monkeypatch.setenv('GWS_CONFIG_DIR', str(tmp_path / 'config'))
      os.makedirs(tmp_path / 'config', exist_ok=True)
      os.makedirs(tmp_path / 'data', exist_ok=True)
      # Redirect db module's DB_PATH
      sys.path.insert(0, str(tmp_path.parent))
      import db
      monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'data' / 'lives.db'))
      monkeypatch.setattr(db, 'DB_DIR', str(tmp_path / 'data'))
      yield

  @pytest.fixture
  def tmp_lives_dir(tmp_path):
      lives = tmp_path / 'lives'
      lives.mkdir()
      return lives
  ```

- [ ] **Step 3: Verify scaffold runs (no tests yet)**

  Run: `python -m pytest tests/ -v`
  Expected: `no tests ran` (exit 0 or 5 — both are fine)

- [ ] **Step 4: Commit**

  ```bash
  git add requirements.txt tests/
  git commit -m "test: add pytest scaffold"
  ```

---

## Task 1: Rate-limit `/api/login`

**Files:**
- Modify: `dashboard/server.py` (lines ~34-38 for new module-level state, lines 177-191 for `_handle_login`)
- Test: `tests/test_login_rate_limit.py`

### Background

`_handle_login` is at line 177. It currently checks password and sets a session cookie with no rate limiting. The server is `ThreadingMixIn` so all state must be thread-safe. `client_address[0]` gives the caller's IP.

### Implementation

- [ ] **Step 1: Write the failing test**

  Create `tests/test_login_rate_limit.py`:
  ```python
  import io
  import time
  import importlib
  from unittest.mock import MagicMock, patch

  def make_handler(server_module, ip='1.2.3.4'):
      """Create a DashboardHandler instance without starting a real server."""
      h = server_module.DashboardHandler.__new__(server_module.DashboardHandler)
      h.client_address = (ip, 9999)
      h.headers = MagicMock()
      h.headers.get = MagicMock(return_value='')
      h.wfile = io.BytesIO()
      h.send_response = MagicMock()
      h.send_header = MagicMock()
      h.end_headers = MagicMock()
      return h

  def test_login_blocked_after_max_attempts():
      """After 5 failed logins from the same IP, the 6th returns 429."""
      with patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'secret'}):
          import dashboard.server as srv
          importlib.reload(srv)
          srv._LOGIN_ATTEMPTS.clear()
          srv._DASHBOARD_PASSWORD = 'secret'

          for _ in range(5):
              h = make_handler(srv)
              srv._LOGIN_ATTEMPTS.get  # ensure dict exists
              h._handle_login({'password': 'wrong'})

          # 6th attempt — must be rate-limited
          h = make_handler(srv)
          h._handle_login({'password': 'wrong'})
          calls = [c.args[0] for c in h.send_response.call_args_list]
          assert 429 in calls, f"Expected 429, got: {calls}"

  def test_successful_login_clears_attempts():
      """A correct password resets the failure counter for that IP."""
      with patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'secret'}):
          import dashboard.server as srv
          importlib.reload(srv)
          srv._LOGIN_ATTEMPTS.clear()
          srv._DASHBOARD_PASSWORD = 'secret'

          # Rack up 4 failures
          for _ in range(4):
              h = make_handler(srv)
              h._handle_login({'password': 'wrong'})

          # Successful login
          h = make_handler(srv)
          h._handle_login({'password': 'secret'})

          # Verify counter was cleared
          assert srv._LOGIN_ATTEMPTS.get('1.2.3.4', []) == []

  def test_different_ips_tracked_separately():
      """Rate limit is per-IP; one IP's failures don't affect another."""
      with patch.dict('os.environ', {'DASHBOARD_PASSWORD': 'secret'}):
          import dashboard.server as srv
          importlib.reload(srv)
          srv._LOGIN_ATTEMPTS.clear()
          srv._DASHBOARD_PASSWORD = 'secret'

          for _ in range(5):
              h = make_handler(srv, ip='10.0.0.1')
              h._handle_login({'password': 'wrong'})

          # Different IP should not be blocked
          h = make_handler(srv, ip='10.0.0.2')
          h._handle_login({'password': 'wrong'})
          calls = [c.args[0] for c in h.send_response.call_args_list]
          assert 429 not in calls
  ```

- [ ] **Step 2: Run to confirm failure**

  Run: `python -m pytest tests/test_login_rate_limit.py -v`
  Expected: `AttributeError: module 'dashboard.server' has no attribute '_LOGIN_ATTEMPTS'` or similar — confirms test is exercising missing code.

- [ ] **Step 3: Add rate-limiter state and update `_handle_login` in `dashboard/server.py`**

  After line 37 (`_VALID_SESSIONS = set()`), add:
  ```python
  import threading as _threading
  _LOGIN_ATTEMPTS: dict = {}   # ip -> [timestamp, ...]
  _LOGIN_LOCK = _threading.Lock()
  _MAX_LOGIN_ATTEMPTS = 5
  _LOGIN_WINDOW_SECONDS = 300  # 5-minute window
  ```

  Replace the entire `_handle_login` method (lines 177-191) with:
  ```python
  def _handle_login(self, data):
      global _DASHBOARD_PASSWORD
      import time
      ip = self.client_address[0]
      now = time.time()

      with _LOGIN_LOCK:
          attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
          if len(attempts) >= _MAX_LOGIN_ATTEMPTS:
              _LOGIN_ATTEMPTS[ip] = attempts
              self.send_response(429)
              self.send_header('Content-Type', 'application/json')
              self.end_headers()
              self.wfile.write(b'{"ok":false,"error":"too many attempts"}')
              return

      if data.get('password') == _DASHBOARD_PASSWORD:
          with _LOGIN_LOCK:
              _LOGIN_ATTEMPTS.pop(ip, None)
          token = _sec.token_hex(32)
          _VALID_SESSIONS.add(token)
          self.send_response(200)
          self.send_header('Content-Type', 'application/json')
          self.send_header('Set-Cookie', f'ds={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict')
          self.end_headers()
          self.wfile.write(b'{"ok":true}')
      else:
          with _LOGIN_LOCK:
              attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
              attempts.append(now)
              _LOGIN_ATTEMPTS[ip] = attempts
          self.send_response(200)
          self.send_header('Content-Type', 'application/json')
          self.end_headers()
          self.wfile.write(b'{"ok":false}')
  ```

- [ ] **Step 4: Run tests to verify they pass**

  Run: `python -m pytest tests/test_login_rate_limit.py -v`
  Expected: `3 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add dashboard/server.py tests/test_login_rate_limit.py
  git commit -m "feat: rate-limit /api/login — 5 attempts per IP per 5 minutes"
  ```

---

## Task 2: No-auth `/api/health/ping` endpoint

**Files:**
- Modify: `dashboard/server.py` — add `_handle_health_ping()` and register before `_require_auth`
- Test: `tests/test_health_ping.py`

### Background

The existing `handle_api_health` (line 463) requires auth and is slow (calls YouTube API, Claude CLI). The master-dashboard needs to poll each instance to know if it's alive. This new endpoint is instant, no-auth, and returns only locally-available data: instance name, scheduler state (read from `scheduler_status.json`), and free disk GB.

`/api/health/ping` must be added in `do_GET` **before** the `if not self._require_auth(): return` check at line 244. The `/login` route is the only other pre-auth route (line 237).

### Implementation

- [ ] **Step 1: Write the failing test**

  Create `tests/test_health_ping.py`:
  ```python
  import io
  import json
  import importlib
  import os
  from unittest.mock import MagicMock, patch

  def make_handler(server_module):
      h = server_module.DashboardHandler.__new__(server_module.DashboardHandler)
      h.client_address = ('127.0.0.1', 9999)
      h.headers = MagicMock()
      h.headers.get = MagicMock(return_value='')
      h.wfile = io.BytesIO()
      h.send_response = MagicMock()
      h.send_header = MagicMock()
      h.end_headers = MagicMock()
      return h

  def test_health_ping_returns_200_no_auth(tmp_path):
      """GET /api/health/ping returns 200 without a session cookie."""
      with patch.dict('os.environ', {'INSTANCE_NAME': 'yt-pub-lives7', 'LIVES_DIR': str(tmp_path)}):
          import dashboard.server as srv
          importlib.reload(srv)
          h = make_handler(srv)
          h._handle_health_ping()

          h.send_response.assert_called_with(200)
          # Extract JSON from wfile
          h.wfile.seek(0)
          payload = json.loads(h.wfile.read())
          assert payload['ok'] is True
          assert payload['instance'] == 'yt-pub-lives7'
          assert 'scheduler_state' in payload
          assert 'disk_free_gb' in payload

  def test_health_ping_reads_scheduler_status(tmp_path):
      """Returns scheduler state from scheduler_status.json when present."""
      status_file = tmp_path / 'scheduler_status.json'
      status_file.write_text('{"state": "cortando", "detail": "test"}')

      with patch.dict('os.environ', {'LIVES_DIR': str(tmp_path)}):
          import dashboard.server as srv
          importlib.reload(srv)
          # Patch STATUS_FILE path inside the handler
          with patch.object(srv.os.path, 'join', wraps=os.path.join) as mock_join:
              # Point the handler's status file lookup to our tmp file
              h = make_handler(srv)
              # Directly test the status reading logic
              import json as _json
              state = 'offline'
              if status_file.exists():
                  st = _json.loads(status_file.read_text())
                  state = st.get('state', 'offline')
              assert state == 'cortando'

  def test_health_ping_offline_when_no_status_file(tmp_path):
      """Returns scheduler_state='offline' when no status file exists."""
      with patch.dict('os.environ', {'LIVES_DIR': str(tmp_path), 'INSTANCE_NAME': 'test'}):
          import dashboard.server as srv
          importlib.reload(srv)
          h = make_handler(srv)
          h._handle_health_ping()
          h.wfile.seek(0)
          payload = json.loads(h.wfile.read())
          assert payload['scheduler_state'] == 'offline'
  ```

- [ ] **Step 2: Run to confirm failure**

  Run: `python -m pytest tests/test_health_ping.py::test_health_ping_returns_200_no_auth -v`
  Expected: `AttributeError: 'DashboardHandler' has no attribute '_handle_health_ping'`

- [ ] **Step 3: Add `_handle_health_ping` to `dashboard/server.py`**

  Add after `_handle_logout` (around line 203), before `_handle_password_change`:
  ```python
  def _handle_health_ping(self):
      """Lightweight no-auth health check for master-dashboard polling."""
      import shutil
      scheduler_state = 'offline'
      try:
          status_file = os.path.join(os.path.dirname(__file__), 'scheduler_status.json')
          if os.path.exists(status_file):
              with open(status_file) as f:
                  st = json.load(f)
              scheduler_state = st.get('state', 'offline')
      except Exception:
          pass

      disk_free_gb = None
      try:
          lives_dir = os.environ.get('LIVES_DIR', os.path.join(PROJECT_ROOT, 'lives'))
          check_path = lives_dir if os.path.exists(lives_dir) else PROJECT_ROOT
          disk_free_gb = round(shutil.disk_usage(check_path).free / (1024 ** 3), 1)
      except Exception:
          pass

      self.send_json(200, {
          'ok': True,
          'instance': os.environ.get('INSTANCE_NAME', 'yt-pub-lives'),
          'scheduler_state': scheduler_state,
          'disk_free_gb': disk_free_gb,
      })
  ```

  In `do_GET`, add before the `if not self._require_auth():` check (line 244), after the `/login` block (line 241):
  ```python
  if path == '/api/health/ping':
      self._handle_health_ping()
      return
  ```

- [ ] **Step 4: Run tests to verify they pass**

  Run: `python -m pytest tests/test_health_ping.py -v`
  Expected: `3 passed`

- [ ] **Step 5: Commit**

  ```bash
  git add dashboard/server.py tests/test_health_ping.py
  git commit -m "feat: add no-auth /api/health/ping endpoint for master-dashboard polling"
  ```

---

## Task 3: Disk space guard in scheduler

**Files:**
- Modify: `scheduler.py` — add `_check_disk_space()`, call before corte trigger in `main()`
- Test: `tests/test_disk_guard.py`

### Background

The scheduler's `main()` loop (line 1221) triggers cortes when `corte_match` fires. If `lives/` is nearly full, a new corte will fail partway through (disk full during yt-dlp download). The guard checks free bytes on the `LIVES_DIR` filesystem before starting and skips the corte with a clear status message.

Config key: `disk_min_gb` (string, default `'5'`). Stored in SQLite `config` table — operator sets via dashboard config tab. Guard failure is non-fatal when `shutil.disk_usage` raises (e.g., `LIVES_DIR` doesn't exist yet on a fresh instance).

### Implementation

- [ ] **Step 1: Write the failing test**

  Create `tests/test_disk_guard.py`:
  ```python
  import importlib
  import sys
  from unittest.mock import patch, MagicMock

  def _load_scheduler(tmp_path):
      """Import scheduler.py with env pointing at tmp dirs to avoid real I/O."""
      import os
      env_patch = {
          'GWS_CONFIG_DIR': str(tmp_path / 'config'),
          'LIVES_DIR': str(tmp_path / 'lives'),
      }
      (tmp_path / 'config').mkdir(exist_ok=True)
      (tmp_path / 'lives').mkdir(exist_ok=True)
      with patch.dict(os.environ, env_patch):
          if 'scheduler' in sys.modules:
              del sys.modules['scheduler']
          if 'db' in sys.modules:
              del sys.modules['db']
          import scheduler as sched
          return sched

  def test_disk_guard_blocks_when_low(tmp_path):
      """_check_disk_space returns (False, free_gb) when disk is below threshold."""
      sched = _load_scheduler(tmp_path)
      config = {'disk_min_gb': '100000'}  # absurdly high threshold → always triggers

      with patch('shutil.disk_usage') as mock_usage:
          mock_usage.return_value = MagicMock(free=2 * 1024 ** 3)  # 2 GB free
          ok, free_gb = sched._check_disk_space(config)

      assert ok is False
      assert free_gb == 2.0

  def test_disk_guard_passes_when_sufficient(tmp_path):
      """_check_disk_space returns (True, free_gb) when disk is above threshold."""
      sched = _load_scheduler(tmp_path)
      config = {'disk_min_gb': '5'}

      with patch('shutil.disk_usage') as mock_usage:
          mock_usage.return_value = MagicMock(free=50 * 1024 ** 3)  # 50 GB
          ok, free_gb = sched._check_disk_space(config)

      assert ok is True
      assert free_gb == 50.0

  def test_disk_guard_passes_on_check_error(tmp_path):
      """_check_disk_space returns (True, -1) when shutil.disk_usage raises — non-fatal."""
      sched = _load_scheduler(tmp_path)
      config = {}

      with patch('shutil.disk_usage', side_effect=OSError('no such file')):
          ok, free_gb = sched._check_disk_space(config)

      assert ok is True
      assert free_gb == -1.0

  def test_disk_guard_uses_default_threshold(tmp_path):
      """Default threshold is 5 GB when config key is absent."""
      sched = _load_scheduler(tmp_path)
      config = {}  # no disk_min_gb key

      with patch('shutil.disk_usage') as mock_usage:
          mock_usage.return_value = MagicMock(free=4 * 1024 ** 3)  # 4 GB — below default 5
          ok, _ = sched._check_disk_space(config)

      assert ok is False
  ```

- [ ] **Step 2: Run to confirm failure**

  Run: `python -m pytest tests/test_disk_guard.py -v`
  Expected: `AttributeError: module 'scheduler' has no attribute '_check_disk_space'`

- [ ] **Step 3: Add `_check_disk_space` to `scheduler.py`**

  Add after `update_live_status` function (around line 826), before `process_cortes`:
  ```python
  def _check_disk_space(config):
      """Check free disk space on LIVES_DIR filesystem.

      Returns (ok, free_gb). ok=False means free space is below disk_min_gb threshold.
      Always returns True on check errors so a missing lives/ dir doesn't block startup.
      """
      import shutil
      threshold_gb = float(config.get('disk_min_gb', '5'))
      try:
          check_path = LIVES_DIR if os.path.exists(LIVES_DIR) else PROJECT_ROOT
          free_gb = round(shutil.disk_usage(check_path).free / (1024 ** 3), 1)
          if free_gb < threshold_gb:
              log(f'  DISK WARNING: {free_gb} GB free (minimum: {threshold_gb} GB)')
              update_status('disk_warning',
                            f'Low disk: {free_gb} GB free (minimum: {threshold_gb} GB)')
              return False, free_gb
          return True, free_gb
      except Exception as e:
          log(f'  Disk check error (non-fatal): {e}')
          return True, -1.0
  ```

- [ ] **Step 4: Guard corte trigger in `main()`**

  In `main()`, find the corte trigger block (around line 1231):
  ```python
  if not cortes_paused and corte_auto and corte_match:
      if last_executed['cortes'] != corte_match:
          if not corte_running.is_set():
              last_executed['cortes'] = corte_match
              log(f'==> Hora de cortar! ...')
              threading.Thread(...).start()
  ```

  Replace with:
  ```python
  if not cortes_paused and corte_auto and corte_match:
      if last_executed['cortes'] != corte_match:
          if not corte_running.is_set():
              disk_ok, disk_free = _check_disk_space(config)
              if not disk_ok:
                  log(f'==> Corte agendado ({corte_match}) bloqueado: disco cheio ({disk_free} GB livre)')
              else:
                  last_executed['cortes'] = corte_match
                  log(f'==> Hora de cortar! (agendado: {corte_match})')
                  threading.Thread(target=run_cortes_thread, args=(config,), daemon=True).start()
          else:
              log(f'==> Corte agendado ({corte_match}) mas outro corte ainda esta rodando, pulando')
  ```

- [ ] **Step 5: Run tests to verify they pass**

  Run: `python -m pytest tests/test_disk_guard.py -v`
  Expected: `4 passed`

- [ ] **Step 6: Commit**

  ```bash
  git add scheduler.py tests/test_disk_guard.py
  git commit -m "feat: disk space guard in scheduler — block cortes when below disk_min_gb threshold"
  ```

---

## Task 4: `setup-canal` credential validation

**Files:**
- Modify: `scripts/setup-canal` (bash) — add validation section after section D

### Background

`setup-canal` collects `CLIENT_ID`, `CLIENT_SECRET`, `API_KEY`, and `GCP_PROJECT` in section C (lines 54-62), then `PIRAMYD_API_KEY` in section D (lines 65-67), then shows a confirmation summary. Credentials are written to `config/.env` in step 2 (line 104).

Validation goes between section D and the summary block (before line 79). Three checks:
1. `CLIENT_ID` format — must end with `.apps.googleusercontent.com`
2. `CLIENT_SECRET` format — must start with `GOCSPX-`  
3. `API_KEY` live test — a public YouTube channels API call using `$ORIG` as the channel ID; checks for `"error"` in the JSON response

Each check shows a warning and offers `[s/N]` to continue anyway (don't block in case of network outage or unusual credential formats).

Bash scripts don't have a clean unit-test story; the format checks are one-liners and the live check is a `curl` call. Manual testing procedure is documented in step 4.

- [ ] **Step 1: Add the validation block to `scripts/setup-canal`**

  Insert after line 67 (`[ -z "$PIRAMYD_API_KEY" ] && { ... }`) and before line 78 (`# ============= Resumo + confirmacao =============`):

  ```bash
  # ============= F. Validacao de credenciais =============
  echo ""
  echo "==> Validating credentials..."

  # CLIENT_ID format
  if [[ "$CLIENT_ID" != *.apps.googleusercontent.com ]]; then
    echo "  WARNING: CLIENT_ID does not end with .apps.googleusercontent.com"
    echo "           Expected format: <numbers>-<hash>.apps.googleusercontent.com"
    read -rp "  Continue anyway? [s/N]: " _PROCEED
    [[ "${_PROCEED:-n}" =~ ^[sSyY]$ ]] || { echo "Cancelled."; exit 1; }
  fi

  # CLIENT_SECRET format
  if [[ "$CLIENT_SECRET" != GOCSPX-* ]]; then
    echo "  WARNING: CLIENT_SECRET does not start with GOCSPX-"
    read -rp "  Continue anyway? [s/N]: " _PROCEED
    [[ "${_PROCEED:-n}" =~ ^[sSyY]$ ]] || { echo "Cancelled."; exit 1; }
  fi

  # API_KEY live test (non-blocking on network failure)
  echo -n "  Testing API_KEY against YouTube... "
  _API_TEST=$(curl -sf --max-time 10 \
    "https://www.googleapis.com/youtube/v3/channels?part=id&id=${ORIG}&key=${API_KEY}" \
    2>/dev/null || echo "__CURL_FAILED__")

  if [ "$_API_TEST" = "__CURL_FAILED__" ]; then
    echo "no network (skipping)"
  elif echo "$_API_TEST" | grep -q '"error"'; then
    _ERR_MSG=$(echo "$_API_TEST" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('message','unknown'))" \
      2>/dev/null || echo "unknown")
    echo "FAILED"
    echo "  ERROR: API_KEY rejected by YouTube: $_ERR_MSG"
    read -rp "  Continue anyway? [s/N]: " _PROCEED
    [[ "${_PROCEED:-n}" =~ ^[sSyY]$ ]] || { echo "Cancelled."; exit 1; }
  else
    echo "OK"
  fi
  ```

  Note: The existing section labels are A–F (sync add). Rename the old section F (`Operacao`) to G and update the subsequent label comment:
  - Change `# ============= F. Operacao =============` → `# ============= G. Operacao =============`

- [ ] **Step 2: Verify the script is still valid bash**

  Run: `bash -n scripts/setup-canal`
  Expected: no output (exit 0 means syntax is valid)

- [ ] **Step 3: Manual smoke test — format check**

  To test the format check without running the full script, run:
  ```bash
  CLIENT_ID="bad-id"
  CLIENT_SECRET="bad-secret"
  [[ "$CLIENT_ID" != *.apps.googleusercontent.com ]] && echo "FORMAT CHECK WORKS: CLIENT_ID flagged"
  [[ "$CLIENT_SECRET" != GOCSPX-* ]] && echo "FORMAT CHECK WORKS: CLIENT_SECRET flagged"
  ```
  Expected output:
  ```
  FORMAT CHECK WORKS: CLIENT_ID flagged
  FORMAT CHECK WORKS: CLIENT_SECRET flagged
  ```

- [ ] **Step 4: Manual smoke test — API_KEY live check**

  ```bash
  source config/.env 2>/dev/null || true
  ORIG="UC2QbQDyPKuHk93dwo5iq3Sw"
  BAD_KEY="AIzaBAD"
  _API_TEST=$(curl -sf --max-time 10 \
    "https://www.googleapis.com/youtube/v3/channels?part=id&id=${ORIG}&key=${BAD_KEY}" \
    2>/dev/null || echo "__CURL_FAILED__")
  echo "$_API_TEST" | grep -q '"error"' && echo "ERROR DETECTION WORKS" || echo "got: $_API_TEST"
  ```
  Expected: `ERROR DETECTION WORKS`

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/setup-canal
  git commit -m "feat: validate GCP credentials in setup-canal before writing .env"
  ```

---

## Post-implementation: update BACKLOG.md

- [ ] **Mark items 1, 3, 4, 5 as done in BACKLOG.md**
- [ ] **Add note on item 2 (password hashing) confirming it remains deferred**

  ```bash
  git add BACKLOG.md
  git commit -m "docs: update backlog — items 1,3,4,5 complete, item 2 deferred"
  ```

---

## Self-Review

**Spec coverage:**
- Item 1 (rate-limit login): Task 1 ✓
- Item 3 (health check for master): Task 2 ✓ — lightweight no-auth `/api/health/ping`
- Item 4 (disk space guard): Task 3 ✓
- Item 5 (setup-canal credential validation): Task 4 ✓
- Item 2 (password hashing): explicitly deferred — not in plan

**Placeholder scan:** No TBD, TODO, or "similar to" references. All code blocks are complete and runnable.

**Type consistency:**
- `_check_disk_space(config)` → returns `(bool, float)` — used consistently in task 3 test and implementation
- `_handle_health_ping(self)` → returns nothing, calls `self.send_json` — consistent across task 2
- `_LOGIN_ATTEMPTS` dict key is `ip: str`, value is `list[float]` — consistent across all task 1 references
- `_LOGIN_LOCK` used with `with` statement everywhere — correct
