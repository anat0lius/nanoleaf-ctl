"""Direct device controls: status, power, brightness, scenes, color."""

from .api import api_request, set_power_state
from .mirror import get_hyprland_monitors, get_mirror_status, grim_available, stop_mirror
from .state import (
    SCENE_TYPES_PATH,
    cancel_preview,
    get_theme_sync_status,
    load_config,
    read_json,
    save_config,
    save_power_intent,
    set_user_power_intent,
    unlock_theme_sync,
    write_json,
)


def get_music_scenes(names: list[str]) -> list[str]:
    """Scenes that react to music (the device marks them `pluginType: rhythm`).

    The device only reports this per scene, so results are cached by scene name.
    """
    cache = read_json(SCENE_TYPES_PATH) or {}
    types = {n: cache[n] for n in names if n in cache}
    for name in names:
        if name in types:
            continue
        try:
            details = api_request("PUT", "effects", {"write": {"command": "request", "animName": name}})
        except RuntimeError:
            continue  # unknown for now, retried on the next status call
        if isinstance(details, dict):
            types[name] = details.get("pluginType", "")
    if types != cache:
        write_json(SCENE_TYPES_PATH, types)
    return [n for n in names if types.get(n) == "rhythm"]


def _value(state: dict, key: str, default=0):
    return state.get(key, {}).get("value", default)


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("ip") and cfg.get("token"))


def set_friendly_name(name: str) -> None:
    """Sets the display name; an empty name goes back to the device's own name."""
    if not is_configured():
        raise RuntimeError("Not paired with a Nanoleaf device. Run: nanoleaf-ctl pair")
    cfg = load_config()
    name = name.strip()
    if name:
        cfg["friendly_name"] = name
    else:
        cfg.pop("friendly_name", None)
    save_config(cfg)


def get_status() -> dict:
    cfg = load_config()
    if not is_configured():
        raise RuntimeError("Not paired with a Nanoleaf device. Run: nanoleaf-ctl pair")
    info = api_request("GET", "", config=cfg)
    if not isinstance(info, dict):
        raise RuntimeError("Invalid response from device")

    state = info.get("state", {})
    effects = info.get("effects", {})
    theme_st = get_theme_sync_status()

    current_effect = effects.get("select", "")
    if (current_effect == "*Dynamic*" or current_effect.startswith("Theme:")) and theme_st.get("synced"):
        current_effect = f"Theme: {(theme_st.get('theme') or 'palette').replace('-', ' ').title()}"

    effects_list = effects.get("effectsList", [])
    displays = [m["name"] for m in get_hyprland_monitors()]
    return {
        "configured": True,
        "name": cfg.get("friendly_name") or info.get("name", "Nanoleaf"),
        "rawName": info.get("name", ""),
        "model": info.get("model", ""),
        "serialNo": info.get("serialNo", ""),
        "firmware": info.get("firmwareVersion", ""),
        "on": _value(state, "on", False),
        "brightness": _value(state, "brightness"),
        "hue": _value(state, "hue"),
        "sat": _value(state, "sat"),
        "ct": _value(state, "ct"),
        "colorMode": state.get("colorMode", ""),
        "currentEffect": current_effect,
        "effectsList": effects_list,
        "musicScenes": get_music_scenes(effects_list),
        "panelLayout": info.get("panelLayout", {}),
        "mirror": get_mirror_status(),
        "displays": displays,
        "capabilities": {"mirror": grim_available() and bool(displays)},
        "themeSync": theme_st,
    }


def _power(on: bool, cfg=None):
    set_user_power_intent(not on)
    if not on:
        stop_mirror()
        cancel_preview()
    return set_power_state(on, config=cfg)


def set_power(on: bool):
    return _power(on)


def toggle_power() -> bool:
    cfg = load_config()
    state = api_request("GET", "state/on", config=cfg)
    new_val = not (state.get("value", False) if isinstance(state, dict) else False)
    _power(new_val, cfg)
    return new_val


def set_brightness(level: int, duration: int = 0):
    level = max(0, min(100, int(level)))
    save_power_intent(brightness=level)
    return api_request("PUT", "state", {"brightness": {"value": level, "duration": duration}})


def _take_over(**intent) -> None:
    """Common prelude for manual controls: record intent, stop mirror/preview/theme lock."""
    save_power_intent(user_intent_off=False, **intent)
    stop_mirror()
    cancel_preview()
    unlock_theme_sync()


def set_scene(name: str):
    _take_over(mode="scene", scene=name)
    return api_request("PUT", "effects", {"select": name})


def set_ct(kelvin: int):
    _take_over(mode="ct", ct=kelvin)
    return api_request("PUT", "state", {"ct": {"value": int(kelvin)}})


def set_color(hue: int, sat: int):
    _take_over(mode="color", hue=hue, sat=sat)
    return api_request("PUT", "state", {"hue": {"value": int(hue)}, "sat": {"value": int(sat)}})
