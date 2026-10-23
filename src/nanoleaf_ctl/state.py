"""Paths, JSON helpers, device config and persisted state (intent, theme lock, preview)."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ============================================================================
# paths
# ============================================================================

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "nanoleaf"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "nanoleaf"

CONFIG_PATH = CONFIG_DIR / "config.json"
MIRROR_STATE_PATH = STATE_DIR / "mirror.json"
MIRROR_LOCK_PATH = STATE_DIR / "mirror.lock"
THEME_STATE_PATH = STATE_DIR / "theme.json"
PREVIEW_STATE_PATH = STATE_DIR / "preview.json"
PREVIEW_PID_PATH = STATE_DIR / "preview.pid"
INTENT_STATE_PATH = STATE_DIR / "power-intent.json"
SCENE_TYPES_PATH = STATE_DIR / "scene-types.json"
WORKER_LOG_PATH = STATE_DIR / "worker.log"
# Lives in the runtime dir so it disappears at logout/reboot: "restored once per login".
SESSION_MARKER_PATH = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "nanoleaf-ctl-session-started"


# ============================================================================
# jsonio
# ============================================================================

def read_json(path: Path, default: Any = None) -> Any:
    """Return parsed JSON from ``path``, or ``default`` if missing/unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def acquire_lock(path: Path, wait: float = 0.0) -> int | None:
    """Takes an exclusive lock on ``path``, retrying for up to ``wait`` seconds.

    Returns the open file descriptor, which holds the lock until it is closed (the kernel also
    drops it if the process dies, so a crash can never leave a stale lock). None if still held.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.1)


def remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def spawn_worker(*args: str) -> None:
    """Runs `python -m nanoleaf_ctl <args>` detached.

    Puts this package's parent directory on PYTHONPATH so the worker also starts when the
    package is run straight from a checkout instead of being installed.
    """
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(WORKER_LOG_PATH, "a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, "-m", "nanoleaf_ctl", *args],
            env=env, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True,
        )


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_worker_pid(pid: int) -> bool:
    """True if ``pid`` is alive and is one of our `python -m nanoleaf_ctl` processes.

    Pid files outlive reboots and crashes, so a bare liveness check could match a recycled pid.
    """
    if not is_pid_alive(pid):
        return False
    try:
        return b"nanoleaf_ctl" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


# ============================================================================
# config
# ============================================================================

def _restrict(path: Path, mode: int) -> None:
    """Best-effort chmod, so a config written by an older version stops being world-readable."""
    try:
        if path.stat().st_mode & 0o777 != mode:
            path.chmod(mode)
    except OSError:
        pass


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    _restrict(CONFIG_DIR, 0o700)
    _restrict(CONFIG_PATH, 0o600)
    cfg = read_json(CONFIG_PATH)
    if cfg is None:
        sys.stderr.write(f"Error reading config: {CONFIG_PATH}\n")
        return {}
    return cfg


def save_config(cfg: dict) -> None:
    """Writes the config, which holds the device's auth token: private to the user (0700 / 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _restrict(CONFIG_DIR, 0o700)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ============================================================================
# intent
# ============================================================================


def load_power_intent() -> dict:
    return read_json(INTENT_STATE_PATH) or {
        "user_intent_off": False,
        "mode": "scene",
    }


def save_power_intent(**kwargs) -> None:
    st = load_power_intent()
    st.update(kwargs)
    st["updated_at"] = time.time()
    try:
        write_json(INTENT_STATE_PATH, st)
    except OSError as e:
        sys.stderr.write(f"Error saving power intent: {e}\n")


def remember_previous_mode(entering: str) -> None:
    """Notes the mode that mirror/theme sync is about to replace, so leaving them can resume it.

    Theme sync replacing mirror also keeps what mirror itself replaced (``before_mirror``), so
    resuming mirror doesn't turn it into the "previous" mode of whatever comes next.
    """
    st = load_power_intent()
    cur = st.get("mode", "scene")
    if cur == entering:
        return
    if cur == "mirror":
        save_power_intent(previous_mode="mirror", before_mirror=st.get("previous_mode"))
    else:
        save_power_intent(previous_mode=cur)


def set_user_power_intent(is_off: bool) -> None:
    """Records whether the user explicitly intended to leave the lights OFF."""
    save_power_intent(user_intent_off=bool(is_off))


def get_user_power_intent() -> bool:
    """Returns True if the user explicitly turned off the lights."""
    return bool(load_power_intent().get("user_intent_off", False))


# ============================================================================
# theme lock
# ============================================================================

def get_theme_sync_status() -> dict:
    return read_json(THEME_STATE_PATH) or {"synced": False, "theme": "", "mode": "dynamic"}


def save_theme_sync_status(st: dict) -> None:
    write_json(THEME_STATE_PATH, st)


def unlock_theme_sync() -> None:
    st = get_theme_sync_status()
    st["synced"] = False
    save_theme_sync_status(st)


# ============================================================================
# preview
# ============================================================================

def read_preview_pid() -> int | None:
    try:
        return int(PREVIEW_PID_PATH.read_text().strip())
    except (OSError, ValueError):
        return None


def kill_preview_worker(*, skip_self: bool = True) -> None:
    pid = read_preview_pid()
    if pid and not (skip_self and pid == os.getpid()) and is_worker_pid(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def cancel_preview() -> None:
    """Cancels any running preview restore timer and clears preview files."""
    if PREVIEW_PID_PATH.exists():
        kill_preview_worker()
        remove(PREVIEW_PID_PATH)
    remove(PREVIEW_STATE_PATH)
