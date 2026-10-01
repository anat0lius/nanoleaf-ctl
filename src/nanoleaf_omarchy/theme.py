"""Omarchy theme sync (palette lock / preview) and PC session lifecycle."""

import colorsys
import os
import subprocess
import sys
import time

from .api import api_request, set_power_state
from .controls import set_ct, set_scene
from .mirror import get_mirror_status, start_mirror, stop_mirror
from .state import (
    PREVIEW_PID_PATH,
    PREVIEW_STATE_PATH,
    cancel_preview,
    get_current_theme_slug,
    get_theme_colors,
    get_theme_sync_status,
    kill_preview_worker,
    load_config,
    load_power_intent,
    read_json,
    read_preview_pid,
    save_power_intent,
    save_theme_sync_status,
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

PALETTE_KEYS = ["accent", "blue", "cyan", "green", "yellow", "orange", "magenta", "red"]
PREVIEW_SECONDS = 2


def apply_theme_palette(theme_slug: str | None = None) -> bool:
    """Sends the dynamic palette flow payload to Nanoleaf."""
    stop_mirror()
    slug = theme_slug or get_current_theme_slug()
    colors = get_theme_colors(slug)
    if not colors:
        sys.stderr.write(f"No colors found for theme {slug}\n")
        return False

    palette_hexes = []
    for k in PALETTE_KEYS:
        if k in colors and colors[k] not in palette_hexes:
            palette_hexes.append(colors[k])
    if not palette_hexes:
        palette_hexes = [colors.get("accent", "#7daea3")]

    payload = {
        "write": {
            "command": "display",
            "animName": f"Theme: {(slug or 'omarchy').replace('-', ' ').title()}",
            "animType": "random",
            "colorType": "HSB",
            "palette": [hex_to_hsb(c) for c in palette_hexes],
            "brightnessRange": {"minValue": 60, "maxValue": 100},
            "transTime": {"minValue": 30, "maxValue": 70},
            "delayTime": {"minValue": 15, "maxValue": 35},
            "loop": True,
        }
    }
    set_power_state(True)
    api_request("PUT", "effects", payload)
    return True


def sync_theme(theme_slug: str | None = None) -> bool:
    """Locks Theme Sync ON for current/given theme."""
    save_power_intent(user_intent_off=False, mode="theme")
    cancel_preview()
    slug = theme_slug or get_current_theme_slug()
    if not apply_theme_palette(slug):
        return False

    st = get_theme_sync_status()
    st.update(synced=True, theme=slug, mode="dynamic")
    save_theme_sync_status(st)
    print(f"Nanoleaf locked to theme '{slug}'")
    return True


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


def toggle_theme_sync() -> bool:
    """Toggles theme sync lock on/off."""
    st = get_theme_sync_status()
    if st.get("synced"):
        st["synced"] = False
        save_theme_sync_status(st)
        scene = _previous_scene()
        if scene:
            set_scene(scene)
        print(f"Theme sync unlocked{f' (restored {scene})' if scene else ''}")
        return False
    sync_theme()
    return True


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


def on_theme_change(theme_slug: str | None) -> None:
    """Theme change handler called by the theme-set hook."""
    slug = theme_slug or get_current_theme_slug()

    if get_theme_sync_status().get("synced"):
        # User explicitly locked Theme Sync: stay permanently synced with the new theme
        sync_theme(slug)
        return

    # Theme Sync is OFF: preview briefly, then revert to the exact previous state.
    if PREVIEW_PID_PATH.exists():
        # Preview already running; cancel old timer process, keep original snapshot
        kill_preview_worker(skip_self=False)
    else:
        _snapshot_state()

    apply_theme_palette(slug)

    subprocess.Popen(
        [sys.executable, "-m", "nanoleaf_omarchy", "theme-preview-restore", "--delay", str(PREVIEW_SECONDS)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    print(f"Theme '{slug}' previewing for {PREVIEW_SECONDS}s...")


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
    elif mode == "theme":
        sync_theme()
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


def on_session_start(retries: int = 8, retry_interval: int = 2) -> None:
    """Called on PC boot / session start."""
    st = load_power_intent()
    if st.get("user_intent_off", False):
        print("Session start: last user state was OFF, skipping auto turn-on.")
        return

    print(f"Session start: last user state was ON ({st.get('mode', 'scene')}), restoring Nanoleaf...")
    cfg = load_config()
    if not cfg.get("ip"):
        print("Nanoleaf IP not configured, skipping.")
        return

    for attempt in range(retries):
        try:
            _restore(st, cfg)
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
