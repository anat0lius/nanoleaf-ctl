"""Paths, JSON helpers, device config and persisted state (intent, theme lock, preview)."""

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
THEME_STATE_PATH = STATE_DIR / "theme.json"
PREVIEW_STATE_PATH = STATE_DIR / "preview.json"
PREVIEW_PID_PATH = STATE_DIR / "preview.pid"
INTENT_STATE_PATH = STATE_DIR / "power-intent.json"
SCENE_TYPES_PATH = STATE_DIR / "scene-types.json"
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
    subprocess.Popen(
        [sys.executable, "-m", "nanoleaf_ctl", *args],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ============================================================================
# config
# ============================================================================

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    cfg = read_json(CONFIG_PATH)
    if cfg is None:
        sys.stderr.write(f"Error reading config: {CONFIG_PATH}\n")
        return {}
    return cfg


def save_config(cfg: dict) -> None:
    write_json(CONFIG_PATH, cfg)


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
    if pid and not (skip_self and pid == os.getpid()) and is_pid_alive(pid):
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
