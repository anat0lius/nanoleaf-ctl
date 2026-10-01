# nanoleaf-omarchy

Control Nanoleaf Blocks / Panels from the command line, and (on Omarchy) from a bar widget:

- power, brightness, scenes (music-reactive ones are marked) and white temperature
- screen mirroring on any detected display
- theme sync: lock the lights to your Omarchy theme palette, or preview a new theme briefly and then restore
- restore the last state at login

Pure Python, no dependencies. Works on any Linux with Python 3.10+; mirroring needs Hyprland and `grim`,
theme sync reads the current Omarchy theme.

## Quick start

```sh
git clone <this repo> && cd nanoleaf-omarchy
uv tool install --editable .      # provides `nanoleaf-ctl`
nanoleaf-ctl pair
nanoleaf-ctl status
```

`pair` finds the device over mDNS (needs `avahi-daemon`; or pass `--ip 192.168.x.x`), then asks you to
**hold the controller's power button for 5–7 seconds** until the LED flashes. The auth token is saved to
`~/.config/omarchy/nanoleaf.json`.

Without installing: `PYTHONPATH=src python3 -m nanoleaf_omarchy <command>`.

## Usage

```sh
nanoleaf-ctl status                 # power, brightness, current scene
nanoleaf-ctl on | off | toggle
nanoleaf-ctl brightness 60
nanoleaf-ctl scenes                 # list scenes (♪ = reacts to music)
nanoleaf-ctl scene "Northern Lights"
nanoleaf-ctl ct 2700                # white temperature in Kelvin
nanoleaf-ctl mirror start           # mirror the focused display
nanoleaf-ctl mirror start --display DP-2
nanoleaf-ctl mirror displays        # detected displays
nanoleaf-ctl mirror stop
nanoleaf-ctl theme-sync             # lock to the current Omarchy theme
nanoleaf-ctl theme-sync --toggle
nanoleaf-ctl theme-change <slug>    # call this when the theme changes
nanoleaf-ctl session-start --once   # restore last state (once per login)
nanoleaf-ctl session-end            # turn off
```

Every command that takes over the lights (scene, ct, mirror, theme sync) cancels the others, so only one
mode is active at a time.

### Theme sync

With theme sync **locked** the lights stay on the current theme's palette and follow every
`theme-change`. With it unlocked, `theme-change` previews the new palette for 2 seconds and then restores
whatever the lights were doing.

### Session start / end

`session-start --once` restores your last state (unless you turned the lights off) and does nothing if it
already ran during this login. `session-end` turns the lights off. To switch the lights off at logout, install
the optional user service (it runs `~/.local/bin/nanoleaf-ctl`, so install the CLI as in Quick start first):

```sh
mkdir -p ~/.config/systemd/user
cp contrib/systemd/nanoleaf-session.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nanoleaf-session.service
```

## Configuration

`~/.config/omarchy/nanoleaf.json` is written by `pair`. Optional keys:

| Key | Meaning |
| --- | --- |
| `ip` | Device address. If you set it before pairing (`{"ip": "192.168.1.50"}`), `pair` uses it instead of discovery |
| `friendly_name` | Name shown instead of the device's own name |
| `mirror_display` | Preferred display for mirroring (default: the focused display) |

## Status JSON

`nanoleaf-ctl status --json` is the interface other front-ends (such as the bar widget in `Panel.qml`)
build on. `tests/test_status_contract.py` guards its shape. When no device is paired it prints
`{"configured": false}`.

| Field | Type | Meaning |
| --- | --- | --- |
| `configured` | bool | A device is paired |
| `name`, `rawName`, `model`, `serialNo`, `firmware` | string | Device info (`name` honors `friendly_name`) |
| `on` | bool | Power |
| `brightness`, `hue`, `sat`, `ct` | number | Current state (brightness/sat in %, hue in degrees, `ct` in Kelvin) |
| `colorMode` | string | `effect`, `ct` or `hs` |
| `currentEffect` | string | Active scene (shown as `Theme: …` while theme sync is locked) |
| `effectsList` | string[] | All scenes |
| `musicScenes` | string[] | Scenes that react to music |
| `mirror` | object | `{active, display, pid, fps, trans_time}` |
| `displays` | string[] | Detected display names |
| `themeSync` | object | `{synced, theme, mode}` |
| `panelLayout` | object | Raw panel layout from the device |

## Troubleshooting

- **`pair` finds nothing:** make sure `avahi-daemon` is running, or give the address: pass `--ip`, or create
  `~/.config/omarchy/nanoleaf.json` containing `{"ip": "192.168.1.50"}` and pair again (this is also how to do
  it from the bar widget, which has no address field).
- **Pairing never completes:** hold the power button until the LED flashes, then release.
- **`status` fails after the router or device IP changed:** run `nanoleaf-ctl pair --ip <new ip>`.
- **Mirroring does nothing:** check that `grim` works and `nanoleaf-ctl mirror displays` lists your display.

## Repository layout

- `src/nanoleaf_omarchy/` — the Python package (`cli.py` is the entry point)
- `manifest.json`, `Panel.qml` — Omarchy bar widget; it runs the package from `src/` directly
- `contrib/` — optional extras (systemd user service)
- `tests/`

## Development

```sh
uv run pytest
uv run ruff check
```
