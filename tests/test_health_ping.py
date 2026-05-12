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
    """GET /api/health/ping returns 200 with expected fields."""
    with patch.dict('os.environ', {'INSTANCE_NAME': 'yt-pub-lives7', 'LIVES_DIR': str(tmp_path)}):
        import dashboard.server as srv
        importlib.reload(srv)
        h = make_handler(srv)
        h._handle_health_ping()

        h.send_response.assert_called_with(200)
        h.wfile.seek(0)
        payload = json.loads(h.wfile.read())
        assert payload['ok'] is True
        assert payload['instance'] == 'yt-pub-lives7'
        assert 'scheduler_state' in payload
        assert 'disk_free_gb' in payload

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

def test_health_ping_reads_scheduler_status(tmp_path):
    """Returns scheduler state from scheduler_status.json when present."""
    status_file = tmp_path / 'scheduler_status.json'
    status_file.write_text('{"state": "cortando"}')
    with patch.dict('os.environ', {'LIVES_DIR': str(tmp_path), 'INSTANCE_NAME': 'test'}):
        import dashboard.server as srv
        importlib.reload(srv)
        # Redirect __file__ so dirname(__file__) resolves to tmp_path
        with patch.object(srv, '__file__', str(tmp_path / 'server.py')):
            h = make_handler(srv)
            h._handle_health_ping()
            h.wfile.seek(0)
            payload = json.loads(h.wfile.read())
            assert payload['scheduler_state'] == 'cortando'
