import pytest

from nanoleaf_ctl import theme
from nanoleaf_ctl.theme import hex_to_hsb


def test_red():
    assert hex_to_hsb("#ff0000") == {"hue": 0, "saturation": 100, "brightness": 100}


def test_short_hex_and_saturation_floor():
    assert hex_to_hsb("fff") == {"hue": 0, "saturation": 15, "brightness": 100}


def test_normalize_colors_validates_and_dedupes():
    assert theme.normalize_colors(["#F38D70", "f38d70", "#abc"]) == ["#f38d70", "#abc"]


@pytest.mark.parametrize("bad", ["", "#12", "zzzzzz", "#12345"])
def test_normalize_colors_rejects_bad_hex(bad):
    with pytest.raises(ValueError):
        theme.normalize_colors([bad])


def test_normalize_colors_requires_one():
    with pytest.raises(ValueError):
        theme.normalize_colors([])


def test_sync_theme_saves_palette_for_session_restore(monkeypatch):
    saved = {}
    monkeypatch.setattr(theme, "save_power_intent", lambda **k: None)
    monkeypatch.setattr(theme, "cancel_preview", lambda: None)
    monkeypatch.setattr(theme, "apply_theme_palette", lambda name, colors: None)
    monkeypatch.setattr(theme, "get_theme_sync_status", lambda: {})
    monkeypatch.setattr(theme, "save_theme_sync_status", saved.update)

    theme.sync_theme("ristretto", ["#F38D70", "#85dacc"])

    assert saved == {"synced": True, "theme": "ristretto", "mode": "dynamic", "palette": ["#f38d70", "#85dacc"]}


def test_mirror_then_theme_off_resumes_mirror_not_what_preceded_it(monkeypatch, tmp_path):
    from nanoleaf_ctl import controls, state

    monkeypatch.setattr(state, "INTENT_STATE_PATH", tmp_path / "intent.json")
    started = []
    monkeypatch.setattr(controls, "start_mirror", lambda display=None: started.append(display))
    state.save_power_intent(mode="scene", scene="Aurora")

    # scene -> mirror -> theme sync
    state.remember_previous_mode("mirror")
    state.save_power_intent(mode="mirror", mirror_display="DP-1")
    state.remember_previous_mode("theme")
    state.save_power_intent(mode="theme")

    assert controls.restore_previous() == "mirror"  # theme sync off
    assert started == ["DP-1"]
    # stopping the resumed mirror goes back to the scene, not into theme sync
    assert state.load_power_intent()["previous_mode"] == "scene"
