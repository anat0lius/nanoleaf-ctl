"""The bar widget (plugin/Panel.qml) parses `nanoleaf-ctl status --json`; keep its shape stable."""

from nanoleaf_ctl import controls

DEVICE_INFO = {
    "name": "Blocks 1A2B",
    "model": "NL81",
    "serialNo": "S123",
    "firmwareVersion": "1.0",
    "state": {
        "on": {"value": True},
        "brightness": {"value": 60},
        "hue": {"value": 10},
        "sat": {"value": 20},
        "ct": {"value": 4000},
        "colorMode": "effect",
    },
    "effects": {"select": "Golden Hour", "effectsList": ["Golden Hour", "Energetic Beat"]},
    "panelLayout": {},
}


def test_status_json_contract(monkeypatch):
    monkeypatch.setattr(controls, "load_config", lambda: {"ip": "1.2.3.4", "token": "t"})
    monkeypatch.setattr(controls, "api_request", lambda *a, **k: DEVICE_INFO)
    monkeypatch.setattr(controls, "grim_available", lambda: True)
    monkeypatch.setattr(controls, "get_music_scenes", lambda names: ["Energetic Beat"])
    monkeypatch.setattr(controls, "get_mirror_status", lambda: {"active": False, "display": None, "pid": None})
    monkeypatch.setattr(controls, "get_hyprland_monitors", lambda: [{"name": "DP-1"}, {"name": "DP-2"}])
    monkeypatch.setattr(controls, "get_theme_sync_status", lambda: {"synced": False, "theme": "x", "mode": "dynamic"})

    st = controls.get_status()

    assert set(st) == {
        "configured", "name", "rawName", "model", "serialNo", "firmware",
        "on", "brightness", "hue", "sat", "ct", "colorMode",
        "currentEffect", "effectsList", "musicScenes",
        "panelLayout", "mirror", "displays", "capabilities", "themeSync",
    }  # fmt: skip
    assert st["on"] is True
    assert st["brightness"] == 60
    assert st["currentEffect"] == "Golden Hour"
    assert st["musicScenes"] == ["Energetic Beat"]
    assert st["displays"] == ["DP-1", "DP-2"]
    assert st["capabilities"] == {"mirror": True}
    assert set(st["mirror"]) >= {"active", "display"}
    assert set(st["themeSync"]) >= {"synced", "theme"}


def test_status_json_when_not_paired(monkeypatch, capsys):
    from nanoleaf_ctl import cli

    monkeypatch.setattr(controls, "load_config", lambda: {})
    cli.main(["status", "--json"])
    assert capsys.readouterr().out.strip() == '{"configured": false}'


def test_mirror_capability_requires_grim_and_a_display(monkeypatch):
    monkeypatch.setattr(controls, "load_config", lambda: {"ip": "1.2.3.4", "token": "t"})
    monkeypatch.setattr(controls, "api_request", lambda *a, **k: DEVICE_INFO)
    monkeypatch.setattr(controls, "get_music_scenes", lambda names: [])
    monkeypatch.setattr(controls, "get_mirror_status", lambda: {"active": False})
    monkeypatch.setattr(controls, "get_theme_sync_status", lambda: {"synced": False})
    monkeypatch.setattr(controls, "get_hyprland_monitors", lambda: [{"name": "DP-1"}])

    monkeypatch.setattr(controls, "grim_available", lambda: False)
    assert controls.get_status()["capabilities"] == {"mirror": False}

    monkeypatch.setattr(controls, "grim_available", lambda: True)
    monkeypatch.setattr(controls, "get_hyprland_monitors", lambda: [])
    assert controls.get_status()["capabilities"] == {"mirror": False}
