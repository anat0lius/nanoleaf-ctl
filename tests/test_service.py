import subprocess

from nanoleaf_ctl import cli, service, theme


def test_unit_runs_this_package_and_guards_stop():
    unit = service.render_unit()
    assert "-m nanoleaf_ctl session-start --once" in unit
    assert "-m nanoleaf_ctl session-end --if-enabled" in unit
    assert 'Environment="PYTHONPATH=' in unit
    assert "WantedBy=graphical-session.target" in unit


def test_enable_writes_unit_and_disable_does_not_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "UNIT_PATH", tmp_path / "systemd/user/nanoleaf-session.service")
    monkeypatch.setattr(service, "available", lambda: True)
    calls = []

    def fake_systemctl(*a, check=True):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, "", "")

    monkeypatch.setattr(service, "_systemctl", fake_systemctl)

    service.enable()
    assert service.UNIT_PATH.exists()
    assert ("enable", "--now", service.UNIT_NAME) in calls

    calls.clear()
    service.disable()
    assert not service.UNIT_PATH.exists()
    assert ("disable", service.UNIT_NAME) in calls
    assert not any("--now" in c or c[0] == "stop" for c in calls)


def test_session_end_if_enabled_skips_when_disabled(monkeypatch):
    ended = []
    monkeypatch.setattr(theme, "on_session_end", lambda: ended.append(1))
    monkeypatch.setattr(service, "is_enabled", lambda: False)

    cli.main(["session-end", "--if-enabled"])
    assert not ended

    cli.main(["session-end"])
    assert ended == [1]
