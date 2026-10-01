from nanoleaf_omarchy import theme


def test_session_start_once_skips_after_marker(monkeypatch, tmp_path, capsys):
    marker = tmp_path / "marker"
    monkeypatch.setattr(theme, "SESSION_MARKER_PATH", marker)
    monkeypatch.setattr(theme, "load_config", lambda: {"ip": "1.2.3.4"})
    monkeypatch.setattr(theme, "load_power_intent", lambda: {"mode": "scene", "scene": "X"})
    calls = []
    monkeypatch.setattr(theme, "_restore", lambda st, cfg: calls.append(st))

    theme.on_session_start(once=True)
    theme.on_session_start(once=True)

    assert len(calls) == 1
    assert marker.exists()


def test_session_start_unconfigured_leaves_no_marker(monkeypatch, tmp_path):
    marker = tmp_path / "marker"
    monkeypatch.setattr(theme, "SESSION_MARKER_PATH", marker)
    monkeypatch.setattr(theme, "load_config", lambda: {})

    theme.on_session_start(once=True)

    assert not marker.exists()
