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
