"""Paths, JSON helpers, device config and persisted state (intent, theme lock, preview)."""

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

# ============================================================================
# paths
# ============================================================================

HOME = Path.home()

CONFIG_PATH = HOME / ".config/omarchy/nanoleaf.json"

STATE_DIR = HOME / ".local/state/omarchy"
MIRROR_STATE_PATH = STATE_DIR / "nanoleaf-mirror.json"
THEME_STATE_PATH = STATE_DIR / "nanoleaf-theme.json"
PREVIEW_STATE_PATH = STATE_DIR / "nanoleaf-preview.json"
PREVIEW_PID_PATH = STATE_DIR / "nanoleaf-preview.pid"
INTENT_STATE_PATH = STATE_DIR / "nanoleaf-power-intent.json"

OMARCHY_CURRENT_THEME_NAME = STATE_DIR / "current/theme.name"
OMARCHY_CURRENT_THEME_COLORS = STATE_DIR / "current/theme/colors.toml"
USER_THEMES_DIR = HOME / ".config/omarchy/themes"
SYSTEM_THEMES_DIR = Path("/usr/share/omarchy/themes")


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

DEFAULT_SCENE = "Golden Hour"


def load_power_intent() -> dict:
    return read_json(INTENT_STATE_PATH) or {
        "user_intent_off": False,
        "mode": "scene",
        "scene": DEFAULT_SCENE,
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
# theme_state
# ============================================================================

DEFAULT_THEME = "gruvbox"


def get_current_theme_slug() -> str:
    try:
        return OMARCHY_CURRENT_THEME_NAME.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_THEME


def get_theme_colors(theme_slug: str | None = None) -> dict:
    if theme_slug:
        user_p = USER_THEMES_DIR / theme_slug / "colors.toml"
        theme_file: Path = user_p if user_p.exists() else SYSTEM_THEMES_DIR / theme_slug / "colors.toml"
    else:
        theme_file = OMARCHY_CURRENT_THEME_COLORS

    colors = {}
    try:
        for line in theme_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                colors[k.strip()] = v.strip().strip("\"'")
    except FileNotFoundError:
        pass
    except OSError as e:
        sys.stderr.write(f"Error reading theme colors: {e}\n")
    return colors


def get_theme_sync_status() -> dict:
    return read_json(THEME_STATE_PATH) or {
        "synced": False,
        "theme": get_current_theme_slug(),
        "mode": "dynamic",
    }


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
