import json
import os

from nanoleaf_ctl import state


def _mode(path):
    return os.stat(path).st_mode & 0o777


def test_save_config_is_private(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path / "nanoleaf")
    monkeypatch.setattr(state, "CONFIG_PATH", tmp_path / "nanoleaf/config.json")
    old = os.umask(0o022)
    try:
        state.save_config({"ip": "1.2.3.4", "token": "secret"})
    finally:
        os.umask(old)

    assert _mode(state.CONFIG_DIR) == 0o700
    assert _mode(state.CONFIG_PATH) == 0o600
    assert json.loads(state.CONFIG_PATH.read_text())["token"] == "secret"
    assert not state.CONFIG_PATH.with_name("config.json.tmp").exists()


def test_existing_world_readable_config_is_tightened_on_load_and_save(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "nanoleaf"
    cfg_dir.mkdir(mode=0o755)
    cfg_dir.chmod(0o755)
    cfg_path = cfg_dir / "config.json"
    cfg_path.write_text('{"ip": "1.2.3.4", "token": "t"}')
    cfg_path.chmod(0o644)
    monkeypatch.setattr(state, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(state, "CONFIG_PATH", cfg_path)

    assert state.load_config()["token"] == "t"
    assert _mode(cfg_dir) == 0o700
    assert _mode(cfg_path) == 0o600

    cfg_path.chmod(0o644)
    state.save_config({"token": "t2"})
    assert _mode(cfg_path) == 0o600


def test_spawn_worker_tightens_existing_state_dir_and_log(monkeypatch, tmp_path):
    state_dir = tmp_path / "nanoleaf"
    state_dir.mkdir()
    state_dir.chmod(0o755)
    log = state_dir / "worker.log"
    log.write_text("Request failed (http://1.2.3.4:16021/api/v1/secret/state): timed out\n")
    log.chmod(0o644)
    monkeypatch.setattr(state, "STATE_DIR", state_dir)
    monkeypatch.setattr(state, "WORKER_LOG_PATH", log)
    monkeypatch.setattr(state.subprocess, "Popen", lambda *a, **k: None)

    state.spawn_worker("mirror-run")

    assert _mode(state_dir) == 0o700
    assert _mode(log) == 0o600


def test_new_worker_log_is_private(monkeypatch, tmp_path):
    state_dir = tmp_path / "nanoleaf"
    monkeypatch.setattr(state, "STATE_DIR", state_dir)
    monkeypatch.setattr(state, "WORKER_LOG_PATH", state_dir / "worker.log")
    monkeypatch.setattr(state.subprocess, "Popen", lambda *a, **k: None)
    old = os.umask(0o022)
    try:
        state.spawn_worker("mirror-run")
    finally:
        os.umask(old)

    assert _mode(state_dir) == 0o700
    assert _mode(state_dir / "worker.log") == 0o600
