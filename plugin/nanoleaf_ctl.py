#!/usr/bin/env python3
"""
Nanoleaf Controller CLI & Library for Omarchy
Handles discovery, pairing, state queries, device controls, real-time screen mirroring,
and intelligent theme synchronization (dynamic palette lock or 4s preview & restore)
for Nanoleaf Blocks / Panels.
"""

import sys
import os
import json
import time
import math
import struct
import socket
import signal
import colorsys
import argparse
import urllib.request
import urllib.error
import subprocess

CONFIG_PATH = os.path.expanduser("~/.config/omarchy/nanoleaf.json")
MIRROR_STATE_PATH = os.path.expanduser("~/.local/state/omarchy/nanoleaf-mirror.json")
THEME_STATE_PATH = os.path.expanduser("~/.local/state/omarchy/nanoleaf-theme.json")
PREVIEW_STATE_PATH = os.path.expanduser("~/.local/state/omarchy/nanoleaf-preview.json")
PREVIEW_PID_PATH = os.path.expanduser("~/.local/state/omarchy/nanoleaf-preview.pid")
INTENT_STATE_PATH = os.path.expanduser("~/.local/state/omarchy/nanoleaf-power-intent.json")

def load_power_intent():
    if os.path.exists(INTENT_STATE_PATH):
        try:
            with open(INTENT_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"user_intent_off": False, "mode": "scene", "scene": "Golden Hour"}

def save_power_intent(**kwargs):
    st = load_power_intent()
    st.update(kwargs)
    st["updated_at"] = time.time()
    try:
        os.makedirs(os.path.dirname(INTENT_STATE_PATH), exist_ok=True)
        with open(INTENT_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    except Exception as e:
        sys.stderr.write(f"Error saving power intent: {e}\n")

def set_user_power_intent(is_off: bool):
    """Records whether the user explicitly intended to leave the lights OFF."""
    save_power_intent(user_intent_off=bool(is_off))

def get_user_power_intent():
    """Returns True if the user explicitly turned off the lights."""
    st = load_power_intent()
    return bool(st.get("user_intent_off", False))

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            sys.stderr.write(f"Error reading config: {e}\n")
    return {}

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def discover_device(timeout=5):
    """Discovers Nanoleaf devices via avahi-browse."""
    try:
        proc = subprocess.run(
            ["avahi-browse", "-rpt", "_nanoleafapi._tcp"],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        devices = []
        for line in proc.stdout.splitlines():
            parts = line.split(";")
            if len(parts) >= 9 and parts[0] == "=" and parts[2] == "IPv4":
                name = parts[3].replace("\\032", " ")
                host = parts[6]
                ip = parts[7]
                port = int(parts[8])
                txt = parts[9] if len(parts) > 9 else ""
                dev_id = ""
                model = ""
                for item in txt.split('" "'):
                    clean = item.strip('"')
                    if clean.startswith("id="):
                        dev_id = clean[3:]
                    elif clean.startswith("md="):
                        model = clean[3:]
                devices.append({
                    "name": name,
                    "host": host,
                    "ip": ip,
                    "port": port,
                    "id": dev_id,
                    "model": model
                })
        return devices
    except Exception as e:
        sys.stderr.write(f"Discovery error: {e}\n")
        return []

def api_request(method, endpoint, data=None, config=None):
    """Sends an HTTP request to the Nanoleaf OpenAPI."""
    if not config:
        config = load_config()
    ip = config.get("ip")
    port = config.get("port", 16021)
    token = config.get("token")

    if not ip:
        raise ValueError("Nanoleaf IP address not configured")
    if not token and endpoint != "new":
        raise ValueError("Nanoleaf auth_token not configured. Run pairing first.")

    if endpoint == "new":
        url = f"http://{ip}:{port}/api/v1/new"
    else:
        url = f"http://{ip}:{port}/api/v1/{token}/{endpoint}".rstrip("/")

    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")

    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")

    try:
        with urllib.request.urlopen(req, data=body_bytes, timeout=4) as resp:
            content = resp.read().decode("utf-8")
            if content:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return content
            return None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Request failed ({url}): {e}")

def pair(ip=None, port=16021, timeout=45):
    """Attempts to pair with the Nanoleaf device (requires power button hold)."""
    cfg = load_config()
    if not ip:
        ip = cfg.get("ip")
    if not ip:
        print("Discovering Nanoleaf on local network...")
        devs = discover_device(timeout=4)
        if not devs:
            print("No Nanoleaf device found via mDNS. Please specify --ip.")
            return False
        ip = devs[0]["ip"]
        port = devs[0]["port"]
        cfg.update(devs[0])
        print(f"Found {devs[0]['name']} at {ip}:{port}")

    print(f"Pairing with {ip}:{port}...")
    print("👉 Please hold the power button on the Nanoleaf Blocks controller for 5-7 seconds")
    print("   until the LED begins flashing/cycling, then release it.")

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(f"http://{ip}:{port}/api/v1/new", method="POST")
            with urllib.request.urlopen(req, timeout=3) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                token = res.get("auth_token")
                if token:
                    cfg["ip"] = ip
                    cfg["port"] = port
                    cfg["token"] = token
                    save_config(cfg)
                    print(f"🎉 Successfully paired! Auth token saved to {CONFIG_PATH}")
                    info = api_request("GET", "", config=cfg)
                    if isinstance(info, dict):
                        cfg["name"] = info.get("name", cfg.get("name", "Nanoleaf Blocks"))
                        cfg["model"] = info.get("model", cfg.get("model", ""))
                        cfg["serialNo"] = info.get("serialNo", "")
                        save_config(cfg)
                        print(f"Device name: {cfg['name']} (Model: {cfg['model']})")
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 403:
                sys.stdout.write(".")
                sys.stdout.flush()
                time.sleep(2)
            else:
                sys.stderr.write(f"\nHTTP {e.code}: {e.reason}\n")
                time.sleep(2)
        except Exception as e:
            sys.stdout.write("x")
            sys.stdout.flush()
            time.sleep(2)

    print("\nPairing timed out. Please try again.")
    return False

def get_status():
    cfg = load_config()
    info = api_request("GET", "", config=cfg)
    if not isinstance(info, dict):
        raise RuntimeError("Invalid response from device")

    state = info.get("state", {})
    effects = info.get("effects", {})
    mirror_st = get_mirror_status()
    theme_st = get_theme_sync_status()

    current_effect = effects.get("select", "")
    if (current_effect == "*Dynamic*" or current_effect.startswith("Theme:")) and theme_st.get("synced"):
        current_effect = f"Theme: {theme_st.get('theme', 'Omarchy').replace('-', ' ').title()}"

    status = {
        "name": cfg.get("friendly_name") or info.get("name", "Nanoleaf Blocks"),
        "rawName": info.get("name", ""),
        "model": info.get("model", ""),
        "serialNo": info.get("serialNo", ""),
        "firmware": info.get("firmwareVersion", ""),
        "on": state.get("on", {}).get("value", False),
        "brightness": state.get("brightness", {}).get("value", 0),
        "hue": state.get("hue", {}).get("value", 0),
        "sat": state.get("sat", {}).get("value", 0),
        "ct": state.get("ct", {}).get("value", 0),
        "colorMode": state.get("colorMode", ""),
        "currentEffect": current_effect,
        "effectsList": effects.get("effectsList", []),
        "panelLayout": info.get("panelLayout", {}),
        "mirror": mirror_st,
        "themeSync": theme_st
    }
    return status

def set_power(on: bool):
    set_user_power_intent(not on)
    if not on:
        stop_mirror()
        cancel_preview()
    return api_request("PUT", "state", {"on": {"value": bool(on)}})

def toggle_power():
    cfg = load_config()
    state = api_request("GET", "state/on", config=cfg)
    current_val = state.get("value", False) if isinstance(state, dict) else False
    new_val = not current_val
    set_user_power_intent(not new_val)
    if not new_val:
        stop_mirror()
        cancel_preview()
    api_request("PUT", "state", {"on": {"value": new_val}}, config=cfg)
    return new_val

def set_brightness(level: int, duration: int = 0):
    level = max(0, min(100, int(level)))
    save_power_intent(brightness=level)
    return api_request("PUT", "state", {"brightness": {"value": level, "duration": duration}})

def set_scene(name: str):
    save_power_intent(user_intent_off=False, mode="scene", scene=name)
    stop_mirror()
    cancel_preview()
    st = get_theme_sync_status()
    st["synced"] = False
    save_theme_sync_status(st)
    return api_request("PUT", "effects", {"select": name})

def set_ct(kelvin: int):
    save_power_intent(user_intent_off=False, mode="ct", ct=kelvin)
    stop_mirror()
    cancel_preview()
    st = get_theme_sync_status()
    st["synced"] = False
    save_theme_sync_status(st)
    return api_request("PUT", "state", {"ct": {"value": int(kelvin)}})

def set_color(hue: int, sat: int):
    save_power_intent(user_intent_off=False, mode="color", hue=hue, sat=sat)
    stop_mirror()
    cancel_preview()
    st = get_theme_sync_status()
    st["synced"] = False
    save_theme_sync_status(st)
    return api_request("PUT", "state", {"hue": {"value": int(hue)}, "sat": {"value": int(sat)}})

# ==============================================================================
# Color Conversion Helpers
# ==============================================================================

def hex_to_hsb(hex_str):
    hex_str = hex_str.strip().lstrip('#')
    if len(hex_str) == 3:
        hex_str = ''.join([c*2 for c in hex_str])
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return {
        "hue": int(round(h * 360)) % 360,
        "saturation": max(15, min(100, int(round(s * 100)))),
        "brightness": 100
    }

# ==============================================================================
# Intelligent Theme Sync Engine
# ==============================================================================

def get_current_theme_slug():
    name_path = os.path.expanduser("~/.local/state/omarchy/current/theme.name")
    if os.path.exists(name_path):
        try:
            with open(name_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "gruvbox"

def get_theme_colors(theme_slug=None):
    if not theme_slug:
        theme_file = os.path.expanduser("~/.local/state/omarchy/current/theme/colors.toml")
    else:
        user_p = os.path.expanduser(f"~/.config/omarchy/themes/{theme_slug}/colors.toml")
        sys_p = f"/usr/share/omarchy/themes/{theme_slug}/colors.toml"
        theme_file = user_p if os.path.exists(user_p) else sys_p

    colors = {}
    if os.path.exists(theme_file):
        try:
            with open(theme_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        colors[k.strip()] = v.strip().strip('"\'')
        except Exception as e:
            sys.stderr.write(f"Error reading theme colors: {e}\n")
    return colors

def get_theme_sync_status():
    if os.path.exists(THEME_STATE_PATH):
        try:
            with open(THEME_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"synced": False, "theme": get_current_theme_slug(), "mode": "dynamic"}

def save_theme_sync_status(st):
    os.makedirs(os.path.dirname(THEME_STATE_PATH), exist_ok=True)
    with open(THEME_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)

def cancel_preview():
    """Cancels any running preview restore timer and clears preview files."""
    if os.path.exists(PREVIEW_PID_PATH):
        try:
            with open(PREVIEW_PID_PATH, "r") as f:
                pid = int(f.read().strip())
            if pid and pid != os.getpid() and is_pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            os.remove(PREVIEW_PID_PATH)
        except Exception:
            pass
    if os.path.exists(PREVIEW_STATE_PATH):
        try:
            os.remove(PREVIEW_STATE_PATH)
        except Exception:
            pass

def apply_theme_palette(theme_slug=None):
    """Sends the dynamic palette flow payload to Nanoleaf."""
    stop_mirror()
    slug = theme_slug or get_current_theme_slug()
    colors = get_theme_colors(slug)
    if not colors:
        sys.stderr.write(f"No colors found for theme {slug}\n")
        return False

    palette_keys = ["accent", "blue", "cyan", "green", "yellow", "orange", "magenta", "red"]
    palette_hexes = []
    for k in palette_keys:
        if k in colors and colors[k] not in palette_hexes:
            palette_hexes.append(colors[k])
    if not palette_hexes:
        palette_hexes = [colors.get("accent", "#7daea3")]

    palette = [hex_to_hsb(c) for c in palette_hexes]
    anim_name = f"Theme: {slug.replace('-', ' ').title()}"
    payload = {
        "write": {
            "command": "display",
            "animName": anim_name,
            "animType": "random",
            "colorType": "HSB",
            "palette": palette,
            "brightnessRange": {"minValue": 60, "maxValue": 100},
            "transTime": {"minValue": 30, "maxValue": 70},
            "delayTime": {"minValue": 15, "maxValue": 35},
            "loop": True
        }
    }
    api_request("PUT", "state", {"on": {"value": True}})
    api_request("PUT", "effects", payload)
    return True

def sync_theme(theme_slug=None):
    """Locks Theme Sync ON for current/given theme."""
    save_power_intent(user_intent_off=False, mode="theme")
    cancel_preview()
    slug = theme_slug or get_current_theme_slug()
    if not apply_theme_palette(slug):
        return False

    st = get_theme_sync_status()
    st["synced"] = True
    st["theme"] = slug
    st["mode"] = "dynamic"
    save_theme_sync_status(st)
    print(f"Nanoleaf locked to theme '{slug}'")
    return True

def toggle_theme_sync():
    """Toggles theme sync lock on/off."""
    st = get_theme_sync_status()
    if st.get("synced"):
        st["synced"] = False
        save_theme_sync_status(st)
        set_scene("Golden Hour")
        print("Theme sync unlocked (restored Golden Hour)")
        return False
    else:
        sync_theme()
        return True

def on_theme_change(theme_slug):
    """Intelligent theme change handler called by theme-set hook."""
    slug = theme_slug or get_current_theme_slug()
    st = get_theme_sync_status()

    if st.get("synced"):
        # User explicitly locked Theme Sync: stay permanently synced with the new theme
        sync_theme(slug)
        return

    # Theme Sync is OFF: preview for 4 seconds, then revert to exact previous state!
    if os.path.exists(PREVIEW_PID_PATH):
        # Preview already running; cancel old timer process, keep original snapshot
        try:
            with open(PREVIEW_PID_PATH, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid and is_pid_alive(old_pid):
                os.kill(old_pid, signal.SIGTERM)
        except Exception:
            pass
    else:
        # Capture fresh snapshot of current state before preview
        try:
            info = api_request("GET", "")
            state = info.get("state", {})
            effects = info.get("effects", {})
            mirror_st = get_mirror_status()

            snapshot = {
                "on": state.get("on", {}).get("value", True),
                "colorMode": state.get("colorMode", ""),
                "ct": state.get("ct", {}).get("value", 6500),
                "scene": effects.get("select", ""),
                "mirror_active": mirror_st.get("active", False),
                "mirror_display": mirror_st.get("display", "DP-1")
            }
            os.makedirs(os.path.dirname(PREVIEW_STATE_PATH), exist_ok=True)
            with open(PREVIEW_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception as e:
            sys.stderr.write(f"Snapshot error: {e}\n")

    # Display preview of new theme
    apply_theme_palette(slug)

    # Spawn background restore worker with 2s delay
    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "theme-preview-restore",
        "--delay", "2"
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    print(f"Theme '{slug}' previewing for 2s...")

def run_preview_restore(delay=2):
    """Background worker that sleeps 2s and restores the original state."""
    my_pid = os.getpid()
    os.makedirs(os.path.dirname(PREVIEW_PID_PATH), exist_ok=True)
    with open(PREVIEW_PID_PATH, "w") as f:
        f.write(str(my_pid))

    time.sleep(delay)

    # Verify we are still the active preview worker
    if os.path.exists(PREVIEW_PID_PATH):
        try:
            with open(PREVIEW_PID_PATH, "r") as f:
                active_pid = int(f.read().strip())
            if active_pid != my_pid:
                return  # A newer preview worker took over
        except Exception:
            pass

    # Read snapshot and restore
    if os.path.exists(PREVIEW_STATE_PATH):
        try:
            with open(PREVIEW_STATE_PATH, "r", encoding="utf-8") as f:
                snapshot = json.load(f)

            if snapshot.get("mirror_active"):
                start_mirror(display=snapshot.get("mirror_display", "DP-1"))
            elif snapshot.get("colorMode") == "ct":
                set_ct(snapshot.get("ct", 6500))
            elif snapshot.get("scene") and not snapshot.get("scene", "").startswith("*"):
                set_scene(snapshot.get("scene"))

            if not snapshot.get("on", True):
                api_request("PUT", "state", {"on": {"value": False}})
        except Exception as e:
            sys.stderr.write(f"Restore error: {e}\n")

    cancel_preview()

# ==============================================================================
# PC Session Lifecycle (Boot / Shutdown)
# ==============================================================================

def on_session_start(retries=8, retry_interval=2):
    """Called on PC boot / session start."""
    st = load_power_intent()
    if st.get("user_intent_off", False):
        print("Session start: last user state was OFF, skipping auto turn-on.")
        return

    mode = st.get("mode", "scene")
    print(f"Session start: last user state was ON ({mode}), restoring Nanoleaf...")
    cfg = load_config()
    ip = cfg.get("ip")
    if not ip:
        print("Nanoleaf IP not configured, skipping.")
        return

    for attempt in range(retries):
        try:
            if mode == "mirror":
                disp = st.get("mirror_display", "DP-1")
                start_mirror(display=disp)
                print(f"Nanoleaf restored screen mirror on {disp} on session start.")
            elif mode == "theme":
                sync_theme()
                print("Nanoleaf restored theme sync on session start.")
            elif mode == "ct":
                kelvin = st.get("ct", 6500)
                set_ct(kelvin)
                print(f"Nanoleaf restored white temperature {kelvin}K on session start.")
            elif mode == "scene":
                sc = st.get("scene", "Golden Hour")
                set_scene(sc)
                print(f"Nanoleaf restored scene '{sc}' on session start.")
            else:
                api_request("PUT", "state", {"on": {"value": True}}, config=cfg)
                print("Nanoleaf powered ON on session start.")
            return
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(retry_interval)
            else:
                sys.stderr.write(f"Session start failed after {retries} attempts: {e}\n")

def on_session_end():
    """Called on PC shutdown / logout / session end."""
    print("Session ending: turning off Nanoleaf (preserving user intent)...")
    stop_mirror(restore_state=False)
    cancel_preview()
    cfg = load_config()
    try:
        api_request("PUT", "state", {"on": {"value": False}}, config=cfg)
        print("Nanoleaf turned off for session end.")
    except Exception as e:
        sys.stderr.write(f"Session end error: {e}\n")

# ==============================================================================
# Screen Mirroring Engine
# ==============================================================================

def get_hyprland_monitors():
    try:
        proc = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3)
        return json.loads(proc.stdout)
    except Exception:
        return []

def get_target_monitor(requested_name=None):
    monitors = get_hyprland_monitors()
    if not monitors:
        return {"name": "DP-1", "width": 2560, "height": 1440}
    if requested_name:
        for m in monitors:
            if m.get("name") == requested_name:
                return m
    for m in monitors:
        if m.get("name") == "DP-1":
            return m
    for m in monitors:
        if m.get("focused"):
            return m
    return monitors[0]

def compute_screen_panel_targets(display_width, display_height):
    cfg = load_config()
    info = api_request("GET", "", config=cfg)
    layout = info.get("panelLayout", {})
    orientation = layout.get("globalOrientation", {}).get("value", 0)
    rad = math.radians(orientation)
    positions = layout.get("layout", {}).get("positionData", [])

    if not positions:
        return []

    rotated = []
    min_x = float('inf')
    max_x = float('-inf')
    min_y = float('inf')
    max_y = float('-inf')

    for p in positions:
        x = p.get("x", 0)
        y = p.get("y", 0)
        rx = int(round(x * math.cos(rad) + y * math.sin(rad)))
        ry = int(round(-x * math.sin(rad) + y * math.cos(rad)))
        rotated.append({"panelId": p["panelId"], "rx": rx, "ry": ry})
        if rx < min_x: min_x = rx
        if rx > max_x: max_x = rx
        if ry < min_y: min_y = ry
        if ry > max_y: max_y = ry

    width_span = max(1, max_x - min_x)
    height_span = max(1, max_y - min_y)

    inset_x = int(display_width * 0.06)
    usable_w = display_width - 2 * inset_x
    inset_y = int(display_height * 0.06)
    usable_h = display_height - 2 * inset_y
    radius = max(12, int(display_width * 0.035))

    targets = []
    for p in rotated:
        norm_x = (p["rx"] - min_x) / width_span
        norm_y = 1.0 - ((p["ry"] - min_y) / height_span)
        cx = int(round(inset_x + norm_x * usable_w))
        cy = int(round(inset_y + norm_y * usable_h))
        targets.append({
            "panelId": p["panelId"],
            "cx": cx,
            "cy": cy,
            "radius": radius
        })

    return targets

def sample_region(pixels, width, height, cx, cy, radius, step=3):
    total_r = total_g = total_b = count = 0
    min_x = max(0, cx - radius)
    max_x = min(width - 1, cx + radius)
    min_y = max(0, cy - radius)
    max_y = min(height - 1, cy + radius)
    for y in range(min_y, max_y + 1, step):
        row = y * width * 3
        for x in range(min_x, max_x + 1, step):
            o = row + x * 3
            total_r += pixels[o]
            total_g += pixels[o + 1]
            total_b += pixels[o + 2]
            count += 1
    if not count:
        return 0, 0, 0
    return total_r // count, total_g // count, total_b // count

def is_pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def cleanup_mirror_state():
    if os.path.exists(MIRROR_STATE_PATH):
        try:
            os.remove(MIRROR_STATE_PATH)
        except Exception:
            pass

def get_mirror_status():
    if os.path.exists(MIRROR_STATE_PATH):
        try:
            with open(MIRROR_STATE_PATH, "r") as f:
                data = json.load(f)
            pid = data.get("pid")
            if pid and is_pid_alive(pid):
                return data
            else:
                cleanup_mirror_state()
        except Exception:
            pass
    return {"active": False, "display": None, "pid": None}

def run_mirror_loop(display_name=None, fps=8, trans_time=4):
    """Continuous frame capture and UDP streaming loop."""
    cfg = load_config()
    ip = cfg.get("ip")
    if not ip:
        sys.stderr.write("Nanoleaf IP not configured\n")
        return

    mon = get_target_monitor(display_name)
    display = mon.get("name", "DP-1")
    w = mon.get("width", 2560)
    h = mon.get("height", 1440)

    try:
        targets = compute_screen_panel_targets(w, h)
    except Exception as e:
        sys.stderr.write(f"Failed to compute panel targets: {e}\n")
        return

    if not targets:
        sys.stderr.write("No panel layout data available\n")
        return

    os.makedirs(os.path.dirname(MIRROR_STATE_PATH), exist_ok=True)
    with open(MIRROR_STATE_PATH, "w") as f:
        json.dump({
            "active": True,
            "pid": os.getpid(),
            "display": display,
            "fps": fps,
            "trans_time": trans_time
        }, f, indent=2)

    try:
        api_request("PUT", "effects", {
            "write": {
                "command": "display",
                "animType": "extControl",
                "extControlVersion": "v2"
            }
        }, config=cfg)
    except Exception as e:
        sys.stderr.write(f"Failed to enable extControl: {e}\n")
        cleanup_mirror_state()
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    frame_interval = 1.0 / max(1, fps)
    running = True

    def sig_handler(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    try:
        while running:
            t_start = time.time()
            proc = subprocess.run(["grim", "-o", display, "-t", "ppm", "-"], capture_output=True)
            raw = proc.stdout
            idx = raw.find(b'\n255\n')
            if idx == -1:
                time.sleep(frame_interval)
                continue

            hdr = raw[:idx].split()
            if len(hdr) >= 3:
                try:
                    frame_w = int(hdr[1])
                    frame_h = int(hdr[2])
                    if frame_w != w or frame_h != h:
                        w, h = frame_w, frame_h
                        targets = compute_screen_panel_targets(w, h)
                except Exception:
                    pass

            pixels = raw[idx + 5:]

            packet = bytearray()
            packet.extend(struct.pack(">H", len(targets)))

            for t in targets:
                r, g, b = sample_region(pixels, w, h, t["cx"], t["cy"], t["radius"])
                packet.extend(struct.pack(">H", t["panelId"]))
                packet.append(r)
                packet.append(g)
                packet.append(b)
                packet.append(0)  # white channel
                packet.extend(struct.pack(">H", trans_time))

            sock.sendto(packet, (ip, 60222))

            elapsed = time.time() - t_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    finally:
        sock.close()
        cleanup_mirror_state()
        try:
            api_request("PUT", "effects", {"write": {"command": "display", "animType": "static"}}, config=cfg)
        except Exception:
            pass

def start_mirror(display=None, fps=8, trans_time=4):
    target_display = display or get_target_monitor().get("name", "DP-1")
    save_power_intent(user_intent_off=False, mode="mirror", mirror_display=target_display)
    cancel_preview()
    st = get_mirror_status()
    if st.get("active"):
        if st.get("display") == target_display:
            print(f"Screen mirror is already running on {target_display} (PID: {st['pid']})")
            return True
        stop_mirror(restore_state=False)

    cmd = [
        sys.executable,
        os.path.abspath(__file__),
        "mirror-run",
        "--fps", str(fps),
        "--trans-time", str(trans_time),
        "--display", target_display
    ]

    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    for _ in range(15):
        time.sleep(0.1)
        st = get_mirror_status()
        if st.get("active") and st.get("display") == target_display:
            print(f"Screen mirror started (PID: {st['pid']}, Display: {st['display']})")
            return True

    print(f"Screen mirror process started for {target_display}.")
    return True

def stop_mirror(restore_state=True):
    st = get_mirror_status()
    if not st.get("active"):
        return True

    pid = st.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.05)
                if not is_pid_alive(pid):
                    break
        except Exception:
            pass

    cleanup_mirror_state()
    if restore_state:
        cfg = load_config()
        try:
            api_request("PUT", "effects", {"write": {"command": "display", "animType": "static"}}, config=cfg)
        except Exception:
            pass

    print("Screen mirror stopped.")
    return True

def toggle_mirror(display=None):
    st = get_mirror_status()
    if st.get("active"):
        stop_mirror()
        return False
    else:
        start_mirror(display)
        return True

# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Nanoleaf Blocks & Panels CLI for Omarchy")
    sub = parser.add_subparsers(dest="command")

    p_discover = sub.add_parser("discover", help="Discover devices on local network")
    p_discover.add_argument("--json", action="store_true", help="Output as JSON")

    p_pair = sub.add_parser("pair", help="Pair with a Nanoleaf device")
    p_pair.add_argument("--ip", help="IP address (defaults to auto-discovered)")
    p_pair.add_argument("--port", type=int, default=16021, help="Port (default: 16021)")
    p_pair.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")

    p_status = sub.add_parser("status", help="Get device status")
    p_status.add_argument("--json", action="store_true", help="Output raw JSON")

    p_on = sub.add_parser("on", help="Turn device ON")
    p_off = sub.add_parser("off", help="Turn device OFF")
    p_toggle = sub.add_parser("toggle", help="Toggle device ON/OFF")

    p_bri = sub.add_parser("brightness", help="Set brightness (0-100)")
    p_bri.add_argument("level", type=int, help="Brightness percent (0-100)")

    p_scene = sub.add_parser("scene", help="Select scene/effect")
    p_scene.add_argument("name", help="Name of the scene/effect")

    p_scenes = sub.add_parser("scenes", help="List all available scenes")
    p_scenes.add_argument("--json", action="store_true", help="Output as JSON")

    p_ct = sub.add_parser("ct", help="Set color temperature (Kelvin, e.g. 2700-6500)")
    p_ct.add_argument("kelvin", type=int, help="Color temperature in Kelvin")

    # Mirror subcommands
    p_mirror = sub.add_parser("mirror", help="Manage screen mirroring")
    m_sub = p_mirror.add_subparsers(dest="mirror_command")

    m_start = m_sub.add_parser("start", help="Start screen mirror")
    m_start.add_argument("--display", help="Target display (e.g. DP-2, DP-1)")
    m_start.add_argument("--fps", type=int, default=8, help="Capture FPS (default: 8)")
    m_start.add_argument("--trans-time", type=int, default=4, help="Smooth transition time in deciseconds (default: 4 = 400ms)")

    m_stop = m_sub.add_parser("stop", help="Stop screen mirror")

    m_toggle = m_sub.add_parser("toggle", help="Toggle screen mirror")
    m_toggle.add_argument("--display", help="Target display (e.g. DP-2, DP-1)")

    m_status = m_sub.add_parser("status", help="Check screen mirror status")
    m_status.add_argument("--json", action="store_true", help="Output as JSON")

    m_displays = m_sub.add_parser("displays", help="List available displays")
    m_displays.add_argument("--json", action="store_true", help="Output as JSON")

    p_run = sub.add_parser("mirror-run", help=argparse.SUPPRESS)
    p_run.add_argument("--display", help="Display name")
    p_run.add_argument("--fps", type=int, default=8)
    p_run.add_argument("--trans-time", type=int, default=4)

    # Theme sync subcommands
    p_theme = sub.add_parser("theme-sync", help="Lock/toggle Nanoleaf with Omarchy theme")
    p_theme.add_argument("--theme", help="Specific theme slug to sync")
    p_theme.add_argument("--toggle", action="store_true", help="Toggle theme sync lock on/off")

    p_theme_change = sub.add_parser("theme-change", help="Intelligent theme change handler (hook)")
    p_theme_change.add_argument("theme", nargs="?", help="New theme slug")

    p_theme_restore = sub.add_parser("theme-preview-restore", help=argparse.SUPPRESS)
    p_theme_restore.add_argument("--delay", type=int, default=2, help="Delay in seconds before restoring")

    p_theme_st = sub.add_parser("theme-status", help="Get theme sync status")
    p_theme_st.add_argument("--json", action="store_true", help="Output as JSON")

    # Session lifecycle subcommands
    p_session_start = sub.add_parser("session-start", help="Auto turn-on on PC start if last user intent was ON")
    p_session_start.add_argument("--retries", type=int, default=8)
    p_session_start.add_argument("--interval", type=int, default=2)

    p_session_end = sub.add_parser("session-end", help="Auto turn-off on PC shutdown/logout")

    p_intent = sub.add_parser("power-intent", help="Check/set user power intent")
    p_intent.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "discover":
            devs = discover_device()
            if args.json:
                print(json.dumps(devs, indent=2))
            else:
                if not devs:
                    print("No Nanoleaf devices discovered.")
                for d in devs:
                    print(f"Device: {d['name']} | IP: {d['ip']}:{d['port']} | Model: {d['model']} | ID: {d['id']}")

        elif args.command == "pair":
            ok = pair(args.ip, args.port, args.timeout)
            sys.exit(0 if ok else 1)

        elif args.command == "status":
            st = get_status()
            if args.json:
                print(json.dumps(st, indent=2))
            else:
                pwr = "ON" if st["on"] else "OFF"
                mirror_info = f" | Mirror: ACTIVE ({st['mirror']['display']})" if st["mirror"].get("active") else ""
                print(f"{st['name']} ({st['model']}) - Power: {pwr} | Brightness: {st['brightness']}% | Scene: {st['currentEffect']}{mirror_info}")

        elif args.command == "on":
            set_power(True)
            print("Nanoleaf turned ON")

        elif args.command == "off":
            set_power(False)
            print("Nanoleaf turned OFF")

        elif args.command == "toggle":
            new_st = toggle_power()
            print(f"Nanoleaf toggled {'ON' if new_st else 'OFF'}")

        elif args.command == "brightness":
            set_brightness(args.level)
            print(f"Brightness set to {args.level}%")

        elif args.command == "scene":
            set_scene(args.name)
            print(f"Scene set to '{args.name}'")

        elif args.command == "scenes":
            st = get_status()
            effects = st.get("effectsList", [])
            current = st.get("currentEffect", "")
            if args.json:
                print(json.dumps({"current": current, "scenes": effects}, indent=2))
            else:
                print(f"Current Scene: {current}")
                print("Available Scenes:")
                for eff in effects:
                    marker = "* " if eff == current else "  "
                    print(f"{marker}{eff}")

        elif args.command == "ct":
            set_ct(args.kelvin)
            print(f"Color temperature set to {args.kelvin}K")

        elif args.command == "mirror":
            if args.mirror_command == "start":
                start_mirror(args.display, args.fps, args.trans_time)
            elif args.mirror_command == "stop":
                stop_mirror()
            elif args.mirror_command == "toggle":
                toggle_mirror(args.display)
            elif args.mirror_command == "status":
                st = get_mirror_status()
                if args.json:
                    print(json.dumps(st, indent=2))
                else:
                    if st.get("active"):
                        print(f"Screen mirror ACTIVE (PID: {st['pid']}, Display: {st['display']}, FPS: {st['fps']})")
                    else:
                        print("Screen mirror INACTIVE")
            elif args.mirror_command == "displays":
                mons = get_hyprland_monitors()
                if args.json:
                    print(json.dumps(mons, indent=2))
                else:
                    for m in mons:
                        foc = " [focused]" if m.get("focused") else ""
                        print(f"{m.get('name')}: {m.get('width')}x{m.get('height')} @ {m.get('refreshRate', 60):.0f}Hz{foc}")
            else:
                p_mirror.print_help()

        elif args.command == "mirror-run":
            run_mirror_loop(args.display, args.fps, args.trans_time)

        elif args.command == "theme-sync":
            if args.toggle:
                toggle_theme_sync()
            else:
                sync_theme(args.theme)

        elif args.command == "theme-change":
            on_theme_change(args.theme)

        elif args.command == "theme-preview-restore":
            run_preview_restore(args.delay)

        elif args.command == "theme-status":
            st = get_theme_sync_status()
            if args.json:
                print(json.dumps(st, indent=2))
            else:
                print(f"Theme: {st.get('theme')} | Mode: {st.get('mode')} | Synced: {st.get('synced')}")

        elif args.command == "session-start":
            on_session_start(args.retries, args.interval)

        elif args.command == "session-end":
            on_session_end()

        elif args.command == "power-intent":
            if args.state == "on":
                set_user_power_intent(False)
                print("Power intent set to: ON")
            elif args.state == "off":
                set_user_power_intent(True)
                print("Power intent set to: OFF")
            else:
                intent_off = get_user_power_intent()
                print(f"User power intent: {'OFF' if intent_off else 'ON'}")

    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
