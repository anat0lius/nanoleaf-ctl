"""Optional systemd user service: restore the lights at login, turn them off at logout."""

import shutil
import subprocess
import sys
from pathlib import Path

from .state import CONFIG_DIR

UNIT_NAME = "nanoleaf-session.service"
UNIT_PATH = CONFIG_DIR.parent / "systemd/user" / UNIT_NAME


def available() -> bool:
    return shutil.which("systemctl") is not None


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_unit() -> str:
    """Unit that runs this very copy of the package, wherever it is installed."""
    src = str(Path(__file__).resolve().parent.parent)
    run = f"{_quote(sys.executable)} -m nanoleaf_ctl"
    return f"""[Unit]
Description=Nanoleaf session lifecycle (restore on login, off on logout)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment={_quote("PYTHONPATH=" + src)}
ExecStart={run} session-start --once
ExecStop={run} session-end --if-enabled
TimeoutStopSec=6

[Install]
WantedBy=graphical-session.target
"""


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)


def is_enabled() -> bool:
    if not available():
        return False
    return _systemctl("is-enabled", UNIT_NAME, check=False).stdout.strip() == "enabled"


def enable() -> None:
    if not available():
        raise RuntimeError("systemctl not found: this needs a systemd user session")
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(render_unit())
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", UNIT_NAME)


def disable() -> None:
    """Disables without stopping: stopping would run session-end and switch the lights off now."""
    if not available():
        return
    _systemctl("disable", UNIT_NAME, check=False)
    UNIT_PATH.unlink(missing_ok=True)
    _systemctl("daemon-reload", check=False)
