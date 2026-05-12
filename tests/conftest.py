import os
import pytest

# Point db.py at a temp directory so tests never touch the real data/lives.db
@pytest.fixture(autouse=True)
def tmp_db_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('GWS_CONFIG_DIR', str(tmp_path / 'config'))
    os.makedirs(tmp_path / 'config', exist_ok=True)
    os.makedirs(tmp_path / 'data', exist_ok=True)
    import db
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'data' / 'lives.db'))
    monkeypatch.setattr(db, 'DB_DIR', str(tmp_path / 'data'))
    db.close_db()  # evict any cached connection from a prior test
    yield
    db.close_db()  # release file handle so tmp_path cleanup succeeds


@pytest.fixture
def tmp_lives_dir(tmp_path):
    lives = tmp_path / 'lives'
    lives.mkdir()
    return lives
