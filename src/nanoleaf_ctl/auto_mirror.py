"""Auto mirror: mirror the screen while a game or video is fullscreen, then resume the lights."""

import json
import os
import select
import shutil
import signal
import socket
import sys
import time
from pathlib import Path

from .api import api_request
from .mirror import (
    SETUP_ATTEMPTS,
    SETUP_RETRY_INTERVAL,
    get_hyprland_monitors,
    get_mirror_status,
    grim_available,
    start_mirror,
    stop_mirror,
)
from .state import (
    AUTO_MIRROR_LOCK_PATH,
    AUTO_MIRROR_STATE_PATH,
    acquire_lock,
    get_theme_sync_status,
    get_user_power_intent,
    is_pid_alive,
    is_worker_pid,
    load_config,
    load_power_intent,
    read_json,
    remove,
    run_limited,
    save_config,
    save_power_intent,
    spawn_worker,
    write_json,
)

FULLSCREEN = 2  # bit of Hyprland's `fullscreen` value: 1 is only maximized
START_DELAY = 1  # games often flip modes while launching
STOP_DELAY = 3  # a quick alt-tab away and back shouldn't reset the lights
TICK_SECONDS = 0.5
MAX_PENDING_BYTES = 64 << 10  # events are short lines; a partial one never gets this long
AUDIO_CHECK_SECONDS = 2  # only while a fullscreen window is silent: it may start playing later
# What can change which window fills a display. Focus alone can't: the game stays visible.
HYPRLAND_EVENTS = {
    "fullscreen", "openwindow", "closewindow", "movewindow", "movewindowv2",
    "workspace", "workspacev2", "moveworkspace", "moveworkspacev2",
    "monitoradded", "monitoraddedv2", "monitorremoved", "monitorremovedv2",
}  # fmt: skip


# --- What's on screen -------------------------------------------------------------

def fullscreen_windows(monitors: list[dict], clients: list[dict]) -> list[dict]:
    """Fullscreen windows showing right now, whichever display has the focus."""
    found = []
    for mon in monitors:
        workspace = mon.get("activeWorkspace", {}).get("id")
        for c in clients:
            if (c.get("monitor") == mon.get("id")
                    and c.get("workspace", {}).get("id") == workspace
                    and c.get("fullscreen", 0) & FULLSCREEN
                    and not c.get("hidden")):
                found.append({"address": c.get("address"), "pid": c.get("pid"), "display": mon["name"]})
    return found


def process_chain(pid: int) -> list[int]:
    """The process and its ancestors: a game or browser may play audio from a child process."""
    chain = []
    while pid > 1 and pid not in chain:
        chain.append(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return chain


def playing_pids(pw_objects: list[dict]) -> set[int]:
    """Every process playing audio per a `pw-dump`, plus its ancestors."""
    pids = set()
    for obj in pw_objects:
        info = obj.get("info")
        props = info.get("props", {}) if isinstance(info, dict) else {}
        if props.get("media.class") == "Stream/Output/Audio" and info.get("state") == "running":
            try:
                pids.update(process_chain(int(props.get("application.process.id"))))
            except (TypeError, ValueError):
                pass
    return pids


def choose_window(windows: list[dict], playing: set[int], engaged: str | None, preferred: str | None) -> dict | None:
    """The window to mirror: the one already chosen, so pausing doesn't flip the lights, else one playing audio."""
    for w in windows:
        if w["address"] == engaged:
            return w
    candidates = [w for w in windows if w["pid"] in playing]
    for w in candidates:
        if w["display"] == preferred:
            return w
    return candidates[0] if candidates else None


class FullscreenDetector:
    """Works out which display, if any, is showing a fullscreen game or video."""

    def __init__(self) -> None:
        self.engaged = None  # the chosen window's address, until leaving fullscreen settles
        self.waiting = False  # something is fullscreen but silent, so it needs checking again

    def read(self, previous: str | None) -> str | None:
        monitors = get_hyprland_monitors()
        try:
            clients = json.loads(run_limited(["hyprctl", "clients", "-j"]))
        except Exception:
            return previous
        if not monitors:
            return previous
        windows = fullscreen_windows(monitors, clients)
        needs_audio = windows and not any(w["address"] == self.engaged for w in windows)
        window = choose_window(windows, _read_playing_pids() if needs_audio else set(), self.engaged,
                               load_config().get("mirror_display"))
        self.waiting = bool(windows) and window is None
        if window:
            self.engaged = window["address"]
        return window["display"] if window else None


def _read_playing_pids() -> set[int]:
    try:
        return playing_pids(json.loads(run_limited(["pw-dump"])))
    except Exception:
        return set()


# --- Lights ------------------------------------------------------------------------

def capture_lights() -> bool:
    """Records what the lights are doing, so it can be resumed even if the Nanoleaf app or the
    controller's buttons set it. False if they are off: a fullscreen window shouldn't turn them on.
    """
    if get_user_power_intent():
        return False
    info = api_request("GET", "")
    state = info.get("state", {})
    if not state.get("on", {}).get("value"):
        return False
    if get_theme_sync_status().get("synced"):
        return True
    color_mode = state.get("colorMode")
    scene = info.get("effects", {}).get("select", "")
    if color_mode == "ct":
        save_power_intent(mode="ct", ct=state.get("ct", {}).get("value"))
    elif color_mode == "hs":
        save_power_intent(mode="color", hue=state.get("hue", {}).get("value"), sat=state.get("sat", {}).get("value"))
    elif scene and not scene.startswith(("*", "Theme:")):
        save_power_intent(mode="scene", scene=scene)
    elif load_power_intent().get("mode") == "mirror":
        save_power_intent(mode="scene")  # no mirror is running, so it isn't what the lights are doing
    return True


class AutoMirror:
    """Moves the mirror towards the settled fullscreen display, without overriding the user."""

    def __init__(self) -> None:
        self.attempted = False  # once per fullscreen: if the user then stops it, it stays stopped

    def apply(self, display: str | None) -> None:
        st = get_mirror_status()
        ours = st.get("active") and st.get("auto")
        if display is None:
            self.attempted = False
            if ours:
                stop_mirror()
        elif ours:
            if st.get("display") != display:
                stop_mirror(restore_state=False)
                start_mirror(display, auto=True)
        elif not st.get("active") and not self.attempted:
            self.attempted = True
            if capture_lights():
                start_mirror(display, auto=True)


# --- Worker (runs in the detached `auto-mirror-run` process) -------------------------

def _event_socket_paths() -> list[Path]:
    signatures = [os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")]  # missing or stale in some services
    try:
        signatures += [i.get("instance") for i in json.loads(run_limited(["hyprctl", "instances", "-j"]))]
    except Exception:
        pass
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return [Path(runtime) / "hypr" / s / ".socket2.sock" for s in signatures if s]


def _connect_events() -> socket.socket | None:
    for attempt in range(SETUP_ATTEMPTS):
        if attempt:
            time.sleep(SETUP_RETRY_INTERVAL)
        for path in _event_socket_paths():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(path))
                return sock
            except OSError:
                sock.close()
    return None


def split_events(pending: bytes, data: bytes) -> tuple[list[bytes], bytes]:
    """Complete lines in what has arrived so far, and the unfinished rest, which is bounded."""
    *lines, rest = (pending + data).split(b"\n")
    return lines, rest if len(rest) <= MAX_PENDING_BYTES else b""  # no newline in sight: not Hyprland


def relevant_events(lines: list[bytes]) -> bool:
    return any(line.split(b">>", 1)[0].decode(errors="replace") in HYPRLAND_EVENTS for line in lines)


def run_watcher() -> None:
    """Follows Hyprland's events until told to stop or Hyprland exits. Only one runs at a time."""
    lock_fd = acquire_lock(AUTO_MIRROR_LOCK_PATH)
    if lock_fd is None:
        return
    try:
        sock = _connect_events()
        if sock is None:
            sys.stderr.write("Auto mirror: could not connect to Hyprland's event socket\n")
            return
        with sock:
            _watch(sock)
    finally:
        os.close(lock_fd)


def _watch(sock: socket.socket) -> None:
    running = True

    def stop(_sig, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    write_json(AUTO_MIRROR_STATE_PATH, {"pid": os.getpid()})

    auto = AutoMirror()
    detector = FullscreenDetector()
    wanted = detector.read(None)
    changed_at = checked_at = time.monotonic()
    pending = b""
    try:
        while running:
            stale = False
            if select.select([sock], [], [], TICK_SECONDS)[0]:
                data = sock.recv(65536)
                if not data:
                    # Hyprland is gone. Lights are left to session-end: resuming could turn them back on.
                    if get_mirror_status().get("auto"):
                        stop_mirror(restore_state=False)
                    return
                lines, pending = split_events(pending, data)
                stale = relevant_events(lines)
            if stale or (detector.waiting and time.monotonic() - checked_at >= AUDIO_CHECK_SECONDS):
                checked_at = time.monotonic()
                display = detector.read(wanted)
                if display != wanted:
                    wanted, changed_at = display, time.monotonic()
            if time.monotonic() - changed_at >= (START_DELAY if wanted else STOP_DELAY):
                if not wanted:
                    detector.engaged = None
                try:
                    auto.apply(wanted)
                except Exception as e:
                    sys.stderr.write(f"Auto mirror error: {e}\n")
    finally:
        remove(AUTO_MIRROR_STATE_PATH)


# --- Control (called from the CLI process) -----------------------------------------

def _worker_pid() -> int | None:
    pid = (read_json(AUTO_MIRROR_STATE_PATH) or {}).get("pid")
    return pid if pid and is_worker_pid(pid) else None


def get_status() -> dict:
    return {"enabled": bool(load_config().get("auto_mirror")), "running": _worker_pid() is not None}


def ensure_running() -> None:
    """Starts the watcher if it's enabled and not running yet; a duplicate exits on the lock."""
    if load_config().get("auto_mirror") and not _worker_pid():
        spawn_worker("auto-mirror-run")


def enable() -> None:
    cfg = load_config()
    if not cfg.get("ip"):
        raise RuntimeError("Not paired with a Nanoleaf device. Run: nanoleaf-ctl pair")
    if not grim_available() or not shutil.which("hyprctl") or not shutil.which("pw-dump"):
        raise RuntimeError("Auto mirror needs `grim`, Hyprland's `hyprctl` and PipeWire's `pw-dump` to be installed")
    cfg["auto_mirror"] = True
    save_config(cfg)
    st = get_mirror_status()
    if st.get("active") and not st.get("auto"):  # auto replaces an always-on mirror
        stop_mirror()
    ensure_running()


def disable() -> None:
    cfg = load_config()
    if "auto_mirror" in cfg:
        del cfg["auto_mirror"]
        save_config(cfg)
    pid = _worker_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(40):
                time.sleep(0.05)
                if not is_pid_alive(pid):
                    break
        except OSError:
            pass
    if get_mirror_status().get("auto"):
        stop_mirror()
