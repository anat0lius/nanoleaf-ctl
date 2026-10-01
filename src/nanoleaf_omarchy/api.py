"""Nanoleaf OpenAPI client, mDNS discovery and pairing."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .state import CONFIG_PATH, load_config, save_config

# ============================================================================
# api
# ============================================================================

DEFAULT_PORT = 16021
REQUEST_TIMEOUT = 4


def api_request(method: str, endpoint: str, data=None, config=None):
    """Sends an HTTP request to the Nanoleaf OpenAPI."""
    if not config:
        config = load_config()
    ip = config.get("ip")
    port = config.get("port", DEFAULT_PORT)
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
    body = json.dumps(data).encode("utf-8") if data is not None else None

    try:
        with urllib.request.urlopen(req, data=body, timeout=REQUEST_TIMEOUT) as resp:
            content = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Request failed ({url}): {e}") from e

    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def set_power_state(on: bool, config=None):
    return api_request("PUT", "state", {"on": {"value": bool(on)}}, config=config)


def set_static_effect(config=None):
    """Leave external-control (mirror) mode."""
    return api_request(
        "PUT", "effects",
        {"write": {"command": "display", "animType": "static"}},
        config=config,
    )


# ============================================================================
# discovery
# ============================================================================

def discover_device(timeout: int = 5) -> list[dict]:
    """Discovers Nanoleaf devices via avahi-browse."""

    try:
        proc = subprocess.run(
            ["avahi-browse", "-rpt", "_nanoleafapi._tcp"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:
        sys.stderr.write(f"Discovery error: {e}\n")
        return []

    devices = []
    for line in proc.stdout.splitlines():
        parts = line.split(";")
        if len(parts) < 9 or parts[0] != "=" or parts[2] != "IPv4":
            continue
        dev_id = model = ""
        txt = parts[9] if len(parts) > 9 else ""
        for item in txt.split('" "'):
            clean = item.strip('"')
            if clean.startswith("id="):
                dev_id = clean[3:]
            elif clean.startswith("md="):
                model = clean[3:]
        devices.append({
            "name": parts[3].replace("\\032", " "),
            "host": parts[6],
            "ip": parts[7],
            "port": int(parts[8]),
            "id": dev_id,
            "model": model,
        })
    return devices


def pair(ip=None, port=DEFAULT_PORT, timeout=45) -> bool:
    """Attempts to pair with the Nanoleaf device (requires power button hold)."""
    cfg = load_config()
    ip = ip or cfg.get("ip")
    if not ip:
        print("Discovering Nanoleaf on local network...")
        devs = discover_device(timeout=4)
        if not devs:
            print(f"No Nanoleaf device found via mDNS. Pass --ip, or set \"ip\" in {CONFIG_PATH} and try again.")
            return False
        ip, port = devs[0]["ip"], devs[0]["port"]
        cfg.update(devs[0])
        print(f"Found {devs[0]['name']} at {ip}:{port}")

    print(f"Pairing with {ip}:{port}...")
    print("👉 Please hold the power button on the Nanoleaf Blocks controller for 5-7 seconds")
    print("   until the LED begins flashing/cycling, then release it.")

    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"http://{ip}:{port}/api/v1/new", method="POST")
            with urllib.request.urlopen(req, timeout=3) as resp:
                token = json.loads(resp.read().decode("utf-8")).get("auth_token")
            if token:
                cfg.update(ip=ip, port=port, token=token)
                save_config(cfg)
                print(f"🎉 Successfully paired! Auth token saved to {CONFIG_PATH}")
                info = api_request("GET", "", config=cfg)
                if isinstance(info, dict):
                    cfg["name"] = info.get("name", cfg.get("name", "Nanoleaf"))
                    cfg["model"] = info.get("model", cfg.get("model", ""))
                    cfg["serialNo"] = info.get("serialNo", "")
                    save_config(cfg)
                    print(f"Device name: {cfg['name']} (Model: {cfg['model']})")
                return True
        except urllib.error.HTTPError as e:
            if e.code == 403:
                sys.stdout.write(".")
                sys.stdout.flush()
            else:
                sys.stderr.write(f"\nHTTP {e.code}: {e.reason}\n")
        except Exception:
            sys.stdout.write("x")
            sys.stdout.flush()
        time.sleep(2)

    print("\nPairing timed out. Please try again.")
    return False
