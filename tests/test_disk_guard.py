import sys
import os
from unittest.mock import patch, MagicMock


def _load_scheduler(tmp_path):
    env_patch = {
        'GWS_CONFIG_DIR': str(tmp_path / 'config'),
        'LIVES_DIR': str(tmp_path / 'lives'),
    }
    (tmp_path / 'config').mkdir(exist_ok=True)
    (tmp_path / 'lives').mkdir(exist_ok=True)
    with patch.dict(os.environ, env_patch):
        for mod in ('scheduler', 'db'):
            sys.modules.pop(mod, None)
        import scheduler as sched
        return sched


def test_disk_guard_blocks_when_low(tmp_path):
    """_check_disk_space returns (False, free_gb) when disk is below threshold."""
    sched = _load_scheduler(tmp_path)
    config = {'disk_min_gb': '100000'}  # absurdly high → always triggers

    with patch('shutil.disk_usage') as mock_usage:
        mock_usage.return_value = MagicMock(free=2 * 1024 ** 3)  # 2 GB
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
    """_check_disk_space returns (True, -1.0) when shutil.disk_usage raises."""
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
        mock_usage.return_value = MagicMock(free=4 * 1024 ** 3)  # 4 GB — below 5 GB default
        ok, _ = sched._check_disk_space(config)

    assert ok is False
