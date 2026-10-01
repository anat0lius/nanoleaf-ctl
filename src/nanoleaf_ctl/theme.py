"""Palette sync (lock / preview) and PC session lifecycle."""

import colorsys
import os
import re
import sys
import time

from .api import api_request, set_power_state
from .controls import set_ct, set_scene
from .mirror import get_mirror_status, start_mirror, stop_mirror
from .state import (
    PREVIEW_PID_PATH,
    PREVIEW_STATE_PATH,
    SESSION_MARKER_PATH,
    cancel_preview,
    get_theme_sync_status,
    kill_preview_worker,
    load_config,
    load_power_intent,
    read_json,
    read_preview_pid,
    save_power_intent,
    save_theme_sync_status,
    spawn_worker,
    write_json,
)

# ============================================================================
# colors
# ============================================================================

def hex_to_hsb(hex_str: str) -> dict:
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return {
        "hue": round(h * 360) % 360,
        "saturation": max(15, min(100, round(s * 100))),
        "brightness": 100,
    }


# ============================================================================
# theme
# ============================================================================

PREVIEW_SECONDS = 2
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_colors(colors: list[str]) -> list[str]:
    """Validates hex colors and drops duplicates, keeping order."""
    out: list[str] = []
    for c in colors:
        m = _HEX.match(c.strip())
        if not m:
            raise ValueError(f"Invalid color '{c}' (expected hex like #f38d70)")
        hex_ = "#" + m.group(1).lower()
        if hex_ not in out:
            out.append(hex_)
    if not out:
        raise ValueError("At least one color is required")
    return out


def _theme_label(name: str) -> str:
    return f"Theme: {(name or 'palette').replace('-', ' ').title()}"


def apply_theme_palette(name: str, colors: list[str]) -> None:
    """Sends the dynamic palette flow payload to Nanoleaf."""
    palette = [hex_to_hsb(c) for c in normalize_colors(colors)]
    stop_mirror()
    payload = {
        "write": {
            "command": "display",
            "animName": _theme_label(name),
            "animType": "random",
            "colorType": "HSB",
            "palette": palette,
            "brightnessRange": {"minValue": 60, "maxValue": 100},
            "transTime": {"minValue": 30, "maxValue": 70},
            "delayTime": {"minValue": 15, "maxValue": 35},
            "loop": True,
        }
    }
    set_power_state(True)
    api_request("PUT", "effects", payload)


def sync_theme(name: str, colors: list[str]) -> None:
    """Locks theme sync ON for the given palette."""
    colors = normalize_colors(colors)
    save_power_intent(user_intent_off=False, mode="theme")
    cancel_preview()
    apply_theme_palette(name, colors)

    st = get_theme_sync_status()
    st.update(synced=True, theme=name, mode="dynamic", palette=colors)
    save_theme_sync_status(st)
    print(f"Nanoleaf locked to theme '{name}'")


def _previous_scene() -> str | None:
    """Last scene the user picked, else the device's first regular scene."""
    scene = load_power_intent().get("scene")
    if scene:
        return scene
    try:
        effects = api_request("GET", "effects/effectsList") or []
    except Exception:
        return None
    return next((e for e in effects if not e.startswith(("*", "Theme:"))), None)


def unsync_theme() -> None:
    """Unlocks theme sync and restores the previous scene."""
    st = get_theme_sync_status()
    st["synced"] = False
    save_theme_sync_status(st)
    scene = _previous_scene()
    if scene:
        set_scene(scene)
    print(f"Theme sync unlocked{f' (restored {scene})' if scene else ''}")


def _snapshot_state() -> None:
    """Captures the current device state so a preview can be reverted."""
    try:
        info = api_request("GET", "")
        state = info.get("state", {})
        mirror_st = get_mirror_status()
        write_json(PREVIEW_STATE_PATH, {
            "on": state.get("on", {}).get("value", True),
            "colorMode": state.get("colorMode", ""),
            "ct": state.get("ct", {}).get("value"),
            "scene": info.get("effects", {}).get("select", ""),
            "mirror_active": mirror_st.get("active", False),
            "mirror_display": mirror_st.get("display"),
        })
    except Exception as e:
        sys.stderr.write(f"Snapshot error: {e}\n")


def on_theme_change(name: str, colors: list[str]) -> None:
    """Handles a theme change: re-sync if locked, else preview briefly and restore."""
    colors = normalize_colors(colors)

    if get_theme_sync_status().get("synced"):
        # Locked: stay permanently synced with the new theme
        sync_theme(name, colors)
        return

    # Theme Sync is OFF: preview briefly, then revert to the exact previous state.
    if PREVIEW_PID_PATH.exists():
        # Preview already running; cancel old timer process, keep original snapshot
        kill_preview_worker(skip_self=False)
    else:
        _snapshot_state()

    apply_theme_palette(name, colors)

    spawn_worker("theme-preview-restore", "--delay", str(PREVIEW_SECONDS))
    print(f"Theme '{name}' previewing for {PREVIEW_SECONDS}s...")


def run_preview_restore(delay: int = PREVIEW_SECONDS) -> None:
    """Background worker that sleeps, then restores the pre-preview state."""

    my_pid = os.getpid()
    PREVIEW_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_PID_PATH.write_text(str(my_pid))

    time.sleep(delay)

    active_pid = read_preview_pid()
    if PREVIEW_PID_PATH.exists() and active_pid is not None and active_pid != my_pid:
        return  # A newer preview worker took over

    snapshot = read_json(PREVIEW_STATE_PATH)
    if snapshot:
        try:
            scene = snapshot.get("scene", "")
            if snapshot.get("mirror_active"):
                start_mirror(display=snapshot.get("mirror_display"))
            elif snapshot.get("colorMode") == "ct" and snapshot.get("ct"):
                set_ct(snapshot["ct"])
            elif scene and not scene.startswith("*"):
                set_scene(scene)

            if not snapshot.get("on", True):
                set_power_state(False)
        except Exception as e:
            sys.stderr.write(f"Restore error: {e}\n")

    cancel_preview()


# ============================================================================
# session
# ============================================================================

def _restore(st: dict, cfg: dict) -> None:
    mode = st.get("mode", "scene")
    if mode == "mirror":
        start_mirror(display=st.get("mirror_display"))
        print("Nanoleaf restored screen mirror on session start.")
    elif mode == "theme" and get_theme_sync_status().get("palette"):
        theme = get_theme_sync_status()
        sync_theme(theme.get("theme", ""), theme["palette"])
        print("Nanoleaf restored theme sync on session start.")
    elif mode == "ct" and st.get("ct"):
        kelvin = st["ct"]
        set_ct(kelvin)
        print(f"Nanoleaf restored white temperature {kelvin}K on session start.")
    elif mode == "scene" and st.get("scene"):
        scene = st["scene"]
        set_scene(scene)
        print(f"Nanoleaf restored scene '{scene}' on session start.")
    else:
        set_power_state(True, config=cfg)
        print("Nanoleaf powered ON on session start.")


def on_session_start(retries: int = 8, retry_interval: int = 2, once: bool = False) -> None:
    """Called on PC boot / session start.

    With ``once``, does nothing if the state was already restored during this login, so it is
    safe to call from something that can start more than once (e.g. a shell restart).
    """
    if once and SESSION_MARKER_PATH.exists():
        print("Session start: already restored during this login, skipping.")
        return

    cfg = load_config()
    if not cfg.get("ip"):
        print("Nanoleaf IP not configured, skipping.")
        return

    st = load_power_intent()
    if st.get("user_intent_off", False):
        print("Session start: last user state was OFF, skipping auto turn-on.")
        SESSION_MARKER_PATH.touch()
        return

    print(f"Session start: last user state was ON ({st.get('mode', 'scene')}), restoring Nanoleaf...")
    for attempt in range(retries):
        try:
            _restore(st, cfg)
            SESSION_MARKER_PATH.touch()
            return
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(retry_interval)
            else:
                sys.stderr.write(f"Session start failed after {retries} attempts: {e}\n")


def on_session_end() -> None:
    """Called on PC shutdown / logout / session end."""
    print("Session ending: turning off Nanoleaf (preserving user intent)...")
    stop_mirror(restore_state=False)
    cancel_preview()
    try:
        set_power_state(False, config=load_config())
        print("Nanoleaf turned off for session end.")
    except Exception as e:
        sys.stderr.write(f"Session end error: {e}\n")
