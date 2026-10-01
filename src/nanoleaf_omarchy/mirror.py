"""Screen mirroring: grim capture -> per-panel average color -> UDP extControl."""

import json
import math
import os
import signal
import socket
import struct
import subprocess
import sys
import time

from .api import api_request, set_static_effect
from .state import (
    MIRROR_STATE_PATH,
    cancel_preview,
    is_pid_alive,
    load_config,
    read_json,
    remove,
    save_power_intent,
    unlock_theme_sync,
    write_json,
)

EXT_CONTROL_PORT = 60222
DEFAULT_FPS = 8
DEFAULT_TRANS_TIME = 4


# --- Display / layout geometry -------------------------------------------------

def get_hyprland_monitors() -> list[dict]:
    try:
        proc = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3)
        return json.loads(proc.stdout)
    except Exception:
        return []


def get_target_monitor(requested_name: str | None = None) -> dict | None:
    """Picks the requested display, else the configured `mirror_display`, else the focused one."""
    monitors = get_hyprland_monitors()
    if not monitors:
        return None
    preferred = requested_name or load_config().get("mirror_display")
    for wanted in (lambda m: preferred and m.get("name") == preferred,
                   lambda m: m.get("focused")):
        for m in monitors:
            if wanted(m):
                return m
    return monitors[0]


def project_panels(positions: list[dict], orientation: float, display_width: int, display_height: int) -> list[dict]:
    """Maps panel layout positions onto sample circles on a display of the given size."""
    if not positions:
        return []
    rad = math.radians(orientation)
    rotated = []
    for p in positions:
        x, y = p.get("x", 0), p.get("y", 0)
        rotated.append({
            "panelId": p["panelId"],
            "rx": round(x * math.cos(rad) + y * math.sin(rad)),
            "ry": round(-x * math.sin(rad) + y * math.cos(rad)),
        })

    min_x = min(p["rx"] for p in rotated)
    max_x = max(p["rx"] for p in rotated)
    min_y = min(p["ry"] for p in rotated)
    max_y = max(p["ry"] for p in rotated)
    width_span = max(1, max_x - min_x)
    height_span = max(1, max_y - min_y)

    inset_x = int(display_width * 0.06)
    inset_y = int(display_height * 0.06)
    usable_w = display_width - 2 * inset_x
    usable_h = display_height - 2 * inset_y
    radius = max(12, int(display_width * 0.035))

    return [
        {
            "panelId": p["panelId"],
            "cx": round(inset_x + (p["rx"] - min_x) / width_span * usable_w),
            "cy": round(inset_y + (1.0 - (p["ry"] - min_y) / height_span) * usable_h),
            "radius": radius,
        }
        for p in rotated
    ]


def compute_screen_panel_targets(display_width: int, display_height: int) -> list[dict]:
    info = api_request("GET", "", config=load_config())
    layout = info.get("panelLayout", {})
    orientation = layout.get("globalOrientation", {}).get("value", 0)
    positions = layout.get("layout", {}).get("positionData", [])
    return project_panels(positions, orientation, display_width, display_height)


def sample_region(pixels: bytes, width: int, height: int, cx: int, cy: int, radius: int, step: int = 3):
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


def build_packet(targets: list[dict], pixels: bytes, width: int, height: int, trans_time: int) -> bytes:
    packet = bytearray(struct.pack(">H", len(targets)))
    for t in targets:
        r, g, b = sample_region(pixels, width, height, t["cx"], t["cy"], t["radius"])
        packet += struct.pack(">HBBBBH", t["panelId"], r, g, b, 0, trans_time)  # 0 = white channel
    return bytes(packet)


# --- Process state ---------------------------------------------------------------

def get_mirror_status() -> dict:
    data = read_json(MIRROR_STATE_PATH)
    if data:
        pid = data.get("pid")
        if pid and is_pid_alive(pid):
            return data
        remove(MIRROR_STATE_PATH)
    return {"active": False, "display": None, "pid": None}


# --- Worker (runs in the detached `mirror-run` process) ----------------------------

def run_mirror_loop(display_name=None, fps=DEFAULT_FPS, trans_time=DEFAULT_TRANS_TIME) -> None:
    """Continuous frame capture and UDP streaming loop."""
    cfg = load_config()
    ip = cfg.get("ip")
    if not ip:
        sys.stderr.write("Nanoleaf IP not configured\n")
        return

    mon = get_target_monitor(display_name)
    if not mon:
        sys.stderr.write("No displays detected (is Hyprland running?)\n")
        return
    display = mon["name"]
    w, h = mon["width"], mon["height"]

    try:
        targets = compute_screen_panel_targets(w, h)
    except Exception as e:
        sys.stderr.write(f"Failed to compute panel targets: {e}\n")
        return
    if not targets:
        sys.stderr.write("No panel layout data available\n")
        return

    write_json(MIRROR_STATE_PATH, {
        "active": True,
        "pid": os.getpid(),
        "display": display,
        "fps": fps,
        "trans_time": trans_time,
    })

    try:
        api_request("PUT", "effects", {
            "write": {"command": "display", "animType": "extControl", "extControlVersion": "v2"}
        }, config=cfg)
    except Exception as e:
        sys.stderr.write(f"Failed to enable extControl: {e}\n")
        remove(MIRROR_STATE_PATH)
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    frame_interval = 1.0 / max(1, fps)
    running = True

    def stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while running:
            t_start = time.time()
            raw = subprocess.run(["grim", "-o", display, "-t", "ppm", "-"], capture_output=True).stdout
            idx = raw.find(b"\n255\n")
            if idx == -1:
                time.sleep(frame_interval)
                continue

            hdr = raw[:idx].split()
            if len(hdr) >= 3:
                try:
                    frame_w, frame_h = int(hdr[1]), int(hdr[2])
                    if (frame_w, frame_h) != (w, h):
                        w, h = frame_w, frame_h
                        targets = compute_screen_panel_targets(w, h)
                except Exception:
                    pass

            sock.sendto(build_packet(targets, raw[idx + 5:], w, h, trans_time), (ip, EXT_CONTROL_PORT))

            sleep_time = frame_interval - (time.time() - t_start)
            if sleep_time > 0:
                time.sleep(sleep_time)
    finally:
        sock.close()
        remove(MIRROR_STATE_PATH)
        try:
            set_static_effect(config=cfg)
        except Exception:
            pass


# --- Control (called from the CLI process) -----------------------------------------

def start_mirror(display=None, fps=DEFAULT_FPS, trans_time=DEFAULT_TRANS_TIME) -> bool:
    mon = get_target_monitor(display)
    if not mon:
        raise RuntimeError("No displays detected (is Hyprland running?)")
    target_display = mon["name"]
    save_power_intent(user_intent_off=False, mode="mirror", mirror_display=target_display)
    cancel_preview()
    unlock_theme_sync()
    st = get_mirror_status()
    if st.get("active"):
        if st.get("display") == target_display:
            print(f"Screen mirror is already running on {target_display} (PID: {st['pid']})")
            return True
        stop_mirror(restore_state=False)

    subprocess.Popen(
        [sys.executable, "-m", "nanoleaf_omarchy", "mirror-run",
         "--fps", str(fps), "--trans-time", str(trans_time), "--display", target_display],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )

    for _ in range(15):
        time.sleep(0.1)
        st = get_mirror_status()
        if st.get("active") and st.get("display") == target_display:
            print(f"Screen mirror started (PID: {st['pid']}, Display: {st['display']})")
            return True

    print(f"Screen mirror process started for {target_display}.")
    return True


def stop_mirror(restore_state: bool = True) -> bool:
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
        except OSError:
            pass

    remove(MIRROR_STATE_PATH)
    if restore_state:
        try:
            set_static_effect()
        except Exception:
            pass

    print("Screen mirror stopped.")
    return True


def toggle_mirror(display=None) -> bool:
    if get_mirror_status().get("active"):
        stop_mirror()
        return False
    start_mirror(display)
    return True
