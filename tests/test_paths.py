import importlib

from nanoleaf_ctl import state


def test_paths_follow_xdg_and_are_neutral(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    try:
        importlib.reload(state)
        assert state.CONFIG_PATH == tmp_path / "cfg/nanoleaf/config.json"
        assert state.MIRROR_STATE_PATH == tmp_path / "state/nanoleaf/mirror.json"
    finally:
        monkeypatch.undo()
        importlib.reload(state)
