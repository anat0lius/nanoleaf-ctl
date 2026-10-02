"""Command-line interface."""

import argparse
import json
import sys

from . import api, controls, mirror, service, theme
from .api import DEFAULT_PORT
from .state import get_theme_sync_status, get_user_power_intent, set_user_power_intent


def _json_flag(p: argparse.ArgumentParser, help: str = "Output as JSON") -> None:
    p.add_argument("--json", action="store_true", help=help)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nanoleaf-ctl", description="Control Nanoleaf light panels")
    sub = parser.add_subparsers(dest="command")

    _json_flag(sub.add_parser("discover", help="Discover devices on local network"))

    p = sub.add_parser("pair", help="Pair with a Nanoleaf device")
    p.add_argument("--ip", help="IP address (defaults to auto-discovered)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    p.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")

    _json_flag(sub.add_parser("status", help="Get device status"), "Output raw JSON")

    p = sub.add_parser("rename", help="Set the display name (empty to use the device's own name)")
    p.add_argument("name", help="Display name")

    sub.add_parser("on", help="Turn device ON")
    sub.add_parser("off", help="Turn device OFF")
    sub.add_parser("toggle", help="Toggle device ON/OFF")

    p = sub.add_parser("brightness", help="Set brightness (0-100)")
    p.add_argument("level", type=int, help="Brightness percent (0-100)")

    p = sub.add_parser("scene", help="Select scene/effect")
    p.add_argument("name", help="Name of the scene/effect")

    _json_flag(sub.add_parser("scenes", help="List all available scenes"))

    p = sub.add_parser("ct", help="Set color temperature (Kelvin)")
    p.add_argument("kelvin", type=int, help="Color temperature in Kelvin")

    p_mirror = sub.add_parser("mirror", help="Manage screen mirroring")
    p_mirror.set_defaults(mirror_parser=p_mirror)
    m_sub = p_mirror.add_subparsers(dest="mirror_command")

    p = m_sub.add_parser("start", help="Start screen mirror")
    p.add_argument("--display", help="Target display (see `mirror displays`; default: configured or focused)")
    p.add_argument("--fps", type=int, default=mirror.DEFAULT_FPS, help="Capture FPS (default: 8)")
    p.add_argument("--trans-time", type=int, default=mirror.DEFAULT_TRANS_TIME,
                   help="Smooth transition time in deciseconds (default: 4 = 400ms)")
    m_sub.add_parser("stop", help="Stop screen mirror")
    p = m_sub.add_parser("toggle", help="Toggle screen mirror")
    p.add_argument("--display", help="Target display (see `mirror displays`; default: configured or focused)")
    _json_flag(m_sub.add_parser("status", help="Check screen mirror status"))
    _json_flag(m_sub.add_parser("displays", help="List available displays"))

    p = sub.add_parser("mirror-run", help=argparse.SUPPRESS)
    p.add_argument("--display")
    p.add_argument("--fps", type=int, default=mirror.DEFAULT_FPS)
    p.add_argument("--trans-time", type=int, default=mirror.DEFAULT_TRANS_TIME)

    p = sub.add_parser("theme-sync", help="Lock the lights to a color palette (e.g. from your desktop theme)")
    p.add_argument("--name", default="", help="Label for the palette (e.g. the theme name)")
    p.add_argument("--colors", nargs="+", metavar="HEX", help="Palette colors, e.g. #f38d70 #85dacc")
    p.add_argument("--off", action="store_true", help="Unlock theme sync and restore the previous scene")

    p = sub.add_parser("theme-change", help="Palette changed: re-sync if locked, else preview briefly then restore")
    p.add_argument("--name", default="", help="Label for the palette (e.g. the theme name)")
    p.add_argument("--colors", nargs="+", required=True, metavar="HEX", help="Palette colors")

    p = sub.add_parser("theme-preview-restore", help=argparse.SUPPRESS)
    p.add_argument("--delay", type=int, default=theme.PREVIEW_SECONDS, help="Delay in seconds before restoring")

    _json_flag(sub.add_parser("theme-status", help="Get theme sync status"))

    p = sub.add_parser("session-start", help="Auto turn-on on PC start if last user intent was ON")
    p.add_argument("--retries", type=int, default=8)
    p.add_argument("--interval", type=int, default=2)
    p.add_argument("--once", action="store_true", help="Skip if already restored during this login")
    p = sub.add_parser("session-end", help="Auto turn-off on PC shutdown/logout")
    p.add_argument("--if-enabled", action="store_true", help="Do nothing unless session-service is enabled")

    p = sub.add_parser("session-service", help="Restore the lights at login and turn them off at logout")
    p.add_argument("action", nargs="?", choices=["enable", "disable", "status"], default="status")
    _json_flag(p)

    p = sub.add_parser("power-intent", help="Check/set user power intent")
    p.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")

    return parser


# --- Command handlers -----------------------------------------------------------

def _cmd_discover(a):
    devs = api.discover_device()
    if a.json:
        print(json.dumps(devs, indent=2))
        return
    if not devs:
        print("No Nanoleaf devices discovered.")
    for d in devs:
        print(f"Device: {d['name']} | IP: {d['ip']}:{d['port']} | Model: {d['model']} | ID: {d['id']}")


def _cmd_pair(a):
    sys.exit(0 if api.pair(a.ip, a.port, a.timeout) else 1)


def _cmd_status(a):
    if a.json and not controls.is_configured():
        print(json.dumps({"configured": False}))
        return
    st = controls.get_status()
    if a.json:
        print(json.dumps(st, indent=2))
        return
    pwr = "ON" if st["on"] else "OFF"
    mirror_info = f" | Mirror: ACTIVE ({st['mirror']['display']})" if st["mirror"].get("active") else ""
    print(f"{st['name']} ({st['model']}) - Power: {pwr} | Brightness: {st['brightness']}% "
          f"| Scene: {st['currentEffect']}{mirror_info}")


def _cmd_rename(a):
    controls.set_friendly_name(a.name)
    print(f"Display name set to '{a.name.strip()}'" if a.name.strip() else "Display name reset")


def _cmd_on(a):
    controls.set_power(True)
    print("Nanoleaf turned ON")


def _cmd_off(a):
    controls.set_power(False)
    print("Nanoleaf turned OFF")


def _cmd_toggle(a):
    print(f"Nanoleaf toggled {'ON' if controls.toggle_power() else 'OFF'}")


def _cmd_brightness(a):
    controls.set_brightness(a.level)
    print(f"Brightness set to {a.level}%")


def _cmd_scene(a):
    controls.set_scene(a.name)
    print(f"Scene set to '{a.name}'")


def _cmd_scenes(a):
    st = controls.get_status()
    effects, current = st.get("effectsList", []), st.get("currentEffect", "")
    music = st.get("musicScenes", [])
    if a.json:
        print(json.dumps({"current": current, "scenes": effects, "music": music}, indent=2))
        return
    print(f"Current Scene: {current}")
    print("Available Scenes (♪ = reacts to music):")
    for eff in effects:
        print(f"{'* ' if eff == current else '  '}{eff}{' ♪' if eff in music else ''}")


def _cmd_ct(a):
    controls.set_ct(a.kelvin)
    print(f"Color temperature set to {a.kelvin}K")


def _mirror_status(a):
    st = mirror.get_mirror_status()
    if a.json:
        print(json.dumps(st, indent=2))
    elif st.get("active"):
        print(f"Screen mirror ACTIVE (PID: {st['pid']}, Display: {st['display']}, FPS: {st['fps']})")
    else:
        print("Screen mirror INACTIVE")


def _mirror_displays(a):
    mons = mirror.get_hyprland_monitors()
    if a.json:
        print(json.dumps(mons, indent=2))
        return
    for m in mons:
        foc = " [focused]" if m.get("focused") else ""
        print(f"{m.get('name')}: {m.get('width')}x{m.get('height')} @ {m.get('refreshRate', 60):.0f}Hz{foc}")


def _cmd_mirror(a):
    handlers = {
        "start": lambda: mirror.start_mirror(a.display, a.fps, a.trans_time),
        "stop": mirror.stop_mirror,
        "toggle": lambda: mirror.toggle_mirror(a.display),
        "status": lambda: _mirror_status(a),
        "displays": lambda: _mirror_displays(a),
    }
    handler = handlers.get(a.mirror_command)
    if handler:
        handler()
    else:
        a.mirror_parser.print_help()


def _cmd_mirror_run(a):
    mirror.run_mirror_loop(a.display, a.fps, a.trans_time)


def _cmd_theme_sync(a):
    if a.off:
        theme.unsync_theme()
    elif a.colors:
        theme.sync_theme(a.name, a.colors)
    else:
        raise ValueError("theme-sync needs --colors (or --off to unlock)")


def _cmd_theme_change(a):
    theme.on_theme_change(a.name, a.colors)


def _cmd_theme_restore(a):
    theme.run_preview_restore(a.delay)


def _cmd_theme_status(a):
    st = get_theme_sync_status()
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print(f"Theme: {st.get('theme')} | Mode: {st.get('mode')} | Synced: {st.get('synced')}")


def _cmd_session_start(a):
    theme.on_session_start(a.retries, a.interval, a.once)


def _cmd_session_end(a):
    if a.if_enabled and not service.is_enabled():
        return
    theme.on_session_end()


def _cmd_session_service(a):
    if a.action == "enable":
        service.enable()
    elif a.action == "disable":
        service.disable()
    enabled = service.is_enabled()
    if a.json:
        print(json.dumps({"available": service.available(), "enabled": enabled}))
    else:
        print(f"Session service: {'enabled' if enabled else 'disabled'}")


def _cmd_power_intent(a):
    if a.state == "status":
        print(f"User power intent: {'OFF' if get_user_power_intent() else 'ON'}")
    else:
        set_user_power_intent(a.state == "off")
        print(f"Power intent set to: {a.state.upper()}")


COMMANDS = {
    "discover": _cmd_discover,
    "pair": _cmd_pair,
    "status": _cmd_status,
    "rename": _cmd_rename,
    "on": _cmd_on,
    "off": _cmd_off,
    "toggle": _cmd_toggle,
    "brightness": _cmd_brightness,
    "scene": _cmd_scene,
    "scenes": _cmd_scenes,
    "ct": _cmd_ct,
    "mirror": _cmd_mirror,
    "mirror-run": _cmd_mirror_run,
    "theme-sync": _cmd_theme_sync,
    "theme-change": _cmd_theme_change,
    "theme-preview-restore": _cmd_theme_restore,
    "theme-status": _cmd_theme_status,
    "session-start": _cmd_session_start,
    "session-end": _cmd_session_end,
    "session-service": _cmd_session_service,
    "power-intent": _cmd_power_intent,
}


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    try:
        COMMANDS[args.command](args)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
