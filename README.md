# nanoleaf-omarchy

Control Nanoleaf Blocks / Panels from Omarchy: power, scenes, color temperature,
screen mirroring (grim → UDP extControl) and Omarchy theme sync.

## Install

```sh
uv tool install --editable .   # provides `nanoleaf-ctl` in ~/.local/bin
nanoleaf-ctl pair              # hold the controller's power button when prompted
```

Runtime dependencies are system tools only: `avahi-browse` (discovery), `grim` and
`hyprctl` (mirroring).

## Layout

- `src/nanoleaf_omarchy/` — the Python package (`cli.py` is the entry point)
- `plugin/` — Quickshell bar widget (`Panel.qml`, `manifest.json`); calls `nanoleaf-ctl`
- `hooks/` — Omarchy hooks (`post-boot.d`, `theme-set.d`)
- `systemd/` — user service for session start/end

Link `plugin/` to `~/.config/omarchy/plugins/nanoleaf` and the hooks into
`~/.config/omarchy/hooks/`.

## Development

```sh
uv run pytest
uv run ruff check
```
