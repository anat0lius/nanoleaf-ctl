import json
import os
import subprocess
import sys
import time

import pytest

from nanoleaf_ctl import auto_mirror, mirror, theme
from nanoleaf_ctl.auto_mirror import (
    AutoMirror,
    FullscreenDetector,
    choose_window,
    fullscreen_windows,
    playing_pids,
    process_chain,
    relevant_events,
    split_events,
)

MONITORS = [
    {"id": 0, "name": "DP-1", "focused": False, "activeWorkspace": {"id": 1}},
    {"id": 1, "name": "DP-2", "focused": True, "activeWorkspace": {"id": 2}},
]


def client(monitor=0, workspace=1, fullscreen=2, pid=100, address="0xa", **extra):
    return {"monitor": monitor, "workspace": {"id": workspace}, "fullscreen": fullscreen, "pid": pid,
            "address": address, **extra}


@pytest.mark.parametrize("c, display", [
    (client(fullscreen=0), None),
    (client(fullscreen=1), None),  # only maximized
    (client(fullscreen=2), "DP-1"),  # not the focused display, still showing
    (client(monitor=1, workspace=2), "DP-2"),
    (client(workspace=5), None),  # on a workspace that isn't showing
    (client(hidden=True), None),
])  # fmt: skip
def test_fullscreen_windows(c, display):
    found = fullscreen_windows(MONITORS, [c])
    assert [w["display"] for w in found] == ([display] if display else [])


def window(display="DP-1", pid=100, address="0xa"):
    return {"display": display, "pid": pid, "address": address}


def test_choose_window_needs_audio_from_the_windows_own_process():
    assert choose_window([window(pid=100)], {100}, None, None) == window(pid=100)
    assert choose_window([window(pid=100)], {999}, None, None) is None  # e.g. music from another app
    assert choose_window([window(pid=100)], set(), None, None) is None


def test_choose_window_stays_on_the_chosen_one_when_it_goes_silent():
    assert choose_window([window()], set(), "0xa", None) == window()
    assert choose_window([window()], set(), "0xother", None) is None


def test_choose_window_prefers_configured_display_when_both_play():
    both = [window("DP-1", 100, "0xa"), window("DP-2", 200, "0xb")]
    assert choose_window(both, {100, 200}, None, None)["display"] == "DP-1"
    assert choose_window(both, {100, 200}, None, "DP-2")["display"] == "DP-2"
    assert choose_window(both, {100}, None, "DP-2")["display"] == "DP-1"  # a silent preferred display doesn't count


def stream(pid, state="running", media_class="Stream/Output/Audio"):
    return {"info": {"state": state, "props": {"media.class": media_class, "application.process.id": pid}}}


def test_playing_pids_counts_only_running_output_streams(monkeypatch):
    monkeypatch.setattr(auto_mirror, "process_chain", lambda pid: [pid])
    assert playing_pids([
        stream(1),
        stream(2, state="idle"),
        stream(3, state="suspended"),
        stream(4, media_class="Stream/Input/Audio"),
        stream("not a pid"),
        {"type": "PipeWire:Interface:Core", "info": {"props": {}}},
        {"info": None},
    ]) == {1}


def test_playing_pids_includes_ancestors_for_audio_from_a_child_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert os.getpid() in process_chain(child.pid)
        assert os.getpid() in playing_pids([stream(child.pid)])
    finally:
        child.kill()
        child.wait()


def test_detector_waits_on_a_silent_window_then_sticks_once_it_plays(monkeypatch):
    monkeypatch.setattr(auto_mirror, "get_hyprland_monitors", lambda: MONITORS)
    monkeypatch.setattr(auto_mirror, "load_config", lambda: {})
    monkeypatch.setattr(auto_mirror, "run_limited", lambda *a, **k: json.dumps([client()]))
    playing = set()
    monkeypatch.setattr(auto_mirror, "_read_playing_pids", lambda: playing)
    d = FullscreenDetector()

    assert d.read(None) is None
    assert d.waiting is True  # silent, e.g. a static page

    playing.add(100)
    assert d.read(None) == "DP-1"
    assert d.waiting is False

    playing.clear()  # paused
    assert d.read("DP-1") == "DP-1"

    monkeypatch.setattr(auto_mirror, "run_limited", lambda *a, **k: "[]")
    assert d.read("DP-1") is None
    assert d.waiting is False  # nothing is fullscreen: no need to keep checking


def test_split_events_keeps_a_partial_line_for_next_time():
    assert split_events(b"", b"fullscreen>>1\nworkspace>>") == ([b"fullscreen>>1"], b"workspace>>")
    assert split_events(b"workspace>>", b"2\n") == ([b"workspace>>2"], b"")


def test_split_events_drops_a_partial_line_that_never_ends():
    lines, rest = split_events(b"", b"x" * (auto_mirror.MAX_PENDING_BYTES + 1))
    assert (lines, rest) == ([], b"")


def test_relevant_events():
    assert relevant_events([b"fullscreen>>1"])
    assert relevant_events([b"windowtitle>>abc", b"workspacev2>>2,2"])
    assert not relevant_events([b"windowtitle>>fullscreen", b"activewindow>>x,y"])


@pytest.fixture
def driver(monkeypatch):
    """AutoMirror with the mirror faked; `st` is the mirror status, `calls` what was done to it."""
    d = AutoMirror()
    d.st = {"active": False}
    d.calls = []
    d.lights_on = True

    def start(display, auto=False):
        d.calls.append(("start", display, auto))
        d.st = {"active": True, "auto": auto, "display": display}

    def stop(restore_state=True):
        d.calls.append(("stop", restore_state))
        d.st = {"active": False}

    monkeypatch.setattr(auto_mirror, "get_mirror_status", lambda: d.st)
    monkeypatch.setattr(auto_mirror, "start_mirror", start)
    monkeypatch.setattr(auto_mirror, "stop_mirror", stop)
    monkeypatch.setattr(auto_mirror, "capture_lights", lambda: d.lights_on)
    return d


def test_starts_on_fullscreen_and_resumes_after(driver):
    driver.apply("DP-1")
    driver.apply("DP-1")
    driver.apply(None)
    driver.apply(None)
    assert driver.calls == [("start", "DP-1", True), ("stop", True)]


def test_follows_fullscreen_to_the_other_display_without_resuming_between(driver):
    driver.apply("DP-1")
    driver.apply("DP-2")
    assert driver.calls == [("start", "DP-1", True), ("stop", False), ("start", "DP-2", True)]


def test_leaves_a_manual_mirror_alone(driver):
    driver.st = {"active": True, "auto": False, "display": "DP-2"}
    driver.apply("DP-1")
    driver.apply(None)
    assert driver.calls == []


def test_user_taking_over_mid_game_sticks_until_fullscreen_ends(driver):
    driver.apply("DP-1")
    driver.st = {"active": False}  # e.g. picked a scene, which stops the mirror
    driver.apply("DP-1")
    driver.apply(None)
    assert driver.calls == [("start", "DP-1", True)]  # no restart, and no resume over the scene

    driver.apply("DP-1")  # the next fullscreen is a new chance
    assert driver.calls[-1] == ("start", "DP-1", True)
    assert len(driver.calls) == 2


def test_does_not_start_when_lights_are_off(driver):
    driver.lights_on = False
    driver.apply("DP-1")
    assert driver.calls == []


def device(monkeypatch, **state):
    effect = state.pop("effect", "*Static*")
    info = {"state": {"on": {"value": True}, **state}, "effects": {"select": effect}}
    monkeypatch.setattr(auto_mirror, "api_request", lambda *a, **k: info)
    monkeypatch.setattr(auto_mirror, "get_user_power_intent", lambda: False)
    monkeypatch.setattr(auto_mirror, "get_theme_sync_status", lambda: {"synced": False})
    monkeypatch.setattr(auto_mirror, "load_power_intent", lambda: {"mode": "scene"})
    saved = {}
    monkeypatch.setattr(auto_mirror, "save_power_intent", lambda **kw: saved.update(kw))
    return saved


def test_capture_records_a_scene_set_outside_this_tool(monkeypatch):
    saved = device(monkeypatch, colorMode="effect", effect="Northern Lights")
    assert auto_mirror.capture_lights() is True
    assert saved == {"mode": "scene", "scene": "Northern Lights"}


def test_capture_records_white_and_color(monkeypatch):
    saved = device(monkeypatch, colorMode="ct", ct={"value": 2700})
    assert auto_mirror.capture_lights() is True
    assert saved == {"mode": "ct", "ct": 2700}

    saved = device(monkeypatch, colorMode="hs", hue={"value": 10}, sat={"value": 20})
    assert auto_mirror.capture_lights() is True
    assert saved == {"mode": "color", "hue": 10, "sat": 20}


def test_capture_keeps_a_synced_theme(monkeypatch):
    saved = device(monkeypatch, colorMode="effect", effect="*Dynamic*")
    monkeypatch.setattr(auto_mirror, "get_theme_sync_status", lambda: {"synced": True})
    assert auto_mirror.capture_lights() is True
    assert saved == {}


def test_capture_refuses_when_device_is_off(monkeypatch):
    saved = device(monkeypatch, colorMode="effect", effect="X")
    monkeypatch.setattr(auto_mirror, "api_request", lambda *a, **k: {"state": {"on": {"value": False}}})
    assert auto_mirror.capture_lights() is False
    assert saved == {}


@pytest.fixture
def mirror_process(monkeypatch):
    calls = {"saved": [], "spawned": [], "unlocked": 0}
    monkeypatch.setattr(mirror, "grim_available", lambda: True)
    monkeypatch.setattr(mirror, "get_target_monitor", lambda d: {"name": d})
    monkeypatch.setattr(mirror, "cancel_preview", lambda: None)
    monkeypatch.setattr(mirror, "remember_previous_mode", lambda m: calls["saved"].append(m))
    monkeypatch.setattr(mirror, "save_power_intent", lambda **kw: calls["saved"].append(kw))
    monkeypatch.setattr(mirror, "spawn_worker", lambda *a: calls["spawned"].append(a))
    monkeypatch.setattr(mirror.time, "sleep", lambda s: None)

    def unlock():
        calls["unlocked"] += 1

    monkeypatch.setattr(mirror, "unlock_theme_sync", unlock)
    return calls


def test_auto_start_leaves_saved_mode_and_theme_lock_alone(monkeypatch, mirror_process):
    monkeypatch.setattr(mirror, "get_mirror_status", lambda: {"active": False})
    mirror.start_mirror("DP-1", auto=True)
    assert mirror_process["saved"] == []
    assert mirror_process["unlocked"] == 0
    assert "--auto" in mirror_process["spawned"][0]


def test_manual_start_is_saved_and_not_auto(monkeypatch, mirror_process):
    monkeypatch.setattr(mirror, "get_mirror_status", lambda: {"active": False})
    mirror.start_mirror("DP-1")
    assert mirror_process["saved"]
    assert "--auto" not in mirror_process["spawned"][0]


def test_manual_start_claims_a_running_auto_mirror(monkeypatch, mirror_process):
    running = {"active": True, "auto": True, "display": "DP-1", "pid": 1}
    monkeypatch.setattr(mirror, "get_mirror_status", lambda: running)
    written = []
    monkeypatch.setattr(mirror, "write_json", lambda path, data: written.append(data))
    mirror.start_mirror("DP-1")
    assert written[0]["auto"] is False
    assert mirror_process["spawned"] == []


def test_enabling_auto_replaces_an_always_on_mirror(monkeypatch):
    monkeypatch.setattr(auto_mirror, "grim_available", lambda: True)
    monkeypatch.setattr(auto_mirror.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(auto_mirror, "load_config", lambda: {"ip": "1.2.3.4"})
    monkeypatch.setattr(auto_mirror, "save_config", lambda cfg: None)
    monkeypatch.setattr(auto_mirror, "ensure_running", lambda: None)
    stopped = []
    monkeypatch.setattr(auto_mirror, "stop_mirror", lambda: stopped.append(True))

    monkeypatch.setattr(auto_mirror, "get_mirror_status", lambda: {"active": True, "auto": True})
    auto_mirror.enable()
    assert stopped == []  # already its own

    monkeypatch.setattr(auto_mirror, "get_mirror_status", lambda: {"active": True, "auto": False})
    auto_mirror.enable()
    assert stopped == [True]


@pytest.mark.parametrize("args, expected", [
    (["mirror", "start", "--display", "DP-1"], ["start", "disable"]),  # started first: it claims a running auto mirror
    (["mirror", "auto"], ["enable"]),
    (["mirror", "stop"], ["disable", "stop"]),
    (["auto-mirror-run", "--ensure"], ["ensure"]),
])  # fmt: skip
def test_cli_mirror_modes(monkeypatch, args, expected):
    from nanoleaf_ctl import cli

    calls = []
    monkeypatch.setattr(cli.mirror, "start_mirror", lambda *a: calls.append("start"))
    monkeypatch.setattr(cli.mirror, "stop_mirror", lambda: calls.append("stop"))
    monkeypatch.setattr(cli.auto_mirror, "enable", lambda: calls.append("enable"))
    monkeypatch.setattr(cli.auto_mirror, "disable", lambda: calls.append("disable"))
    monkeypatch.setattr(cli.auto_mirror, "ensure_running", lambda: calls.append("ensure"))
    cli.main(args)
    assert calls == expected


@pytest.mark.parametrize("mirror_st, enabled, running, expected", [
    ({"active": False}, False, False, ""),
    ({"active": False}, True, True, " | Mirror: AUTO"),
    ({"active": False}, True, False, " | Mirror: AUTO, watcher not running"),
    ({"active": True, "auto": True, "display": "DP-2"}, True, True, " | Mirror: AUTO, active (DP-2)"),
    ({"active": True, "auto": False, "display": "DP-2"}, False, False, " | Mirror: ON (DP-2)"),
    ({"active": True, "auto": False, "display": "DP-2"}, True, True, " | Mirror: ON (DP-2)"),
])  # fmt: skip
def test_cli_status_reports_mirror_mode(monkeypatch, capsys, mirror_st, enabled, running, expected):
    from nanoleaf_ctl import cli

    status = {"name": "N", "model": "M", "on": True, "brightness": 50, "currentEffect": "S", "mirror": mirror_st}
    monkeypatch.setattr(cli.controls, "get_status", lambda: status)
    monkeypatch.setattr(cli.auto_mirror, "get_status", lambda: {"enabled": enabled, "running": running})
    cli.main(["status"])
    assert capsys.readouterr().out == f"N (M) - Power: ON | Brightness: 50% | Scene: S{expected}\n"


def test_disable_leaves_a_config_without_auto_untouched(monkeypatch):
    monkeypatch.setattr(auto_mirror, "load_config", lambda: {"ip": "1.2.3.4"})
    saved = []
    monkeypatch.setattr(auto_mirror, "save_config", saved.append)
    monkeypatch.setattr(auto_mirror, "_worker_pid", lambda: None)
    monkeypatch.setattr(auto_mirror, "get_mirror_status", lambda: {"active": False})
    auto_mirror.disable()
    assert saved == []


@pytest.mark.parametrize("auto, expected", [(True, "intent"), (False, "previous")])
def test_stopping_resumes_the_right_mode(monkeypatch, auto, expected):
    from nanoleaf_ctl import controls

    monkeypatch.setattr(mirror, "get_mirror_status", lambda: {"active": True, "auto": auto, "pid": None})
    monkeypatch.setattr(mirror, "remove", lambda p: None)
    restored = []
    monkeypatch.setattr(theme, "restore_intent", lambda: restored.append("intent"))
    monkeypatch.setattr(controls, "restore_previous", lambda: restored.append("previous"))
    mirror.stop_mirror()
    assert restored == [expected]


def test_stop_does_not_wait_for_a_worker_that_exited_as_a_zombie(monkeypatch):
    # The watcher is the mirror worker's parent, so the worker stays a zombie until reaped.
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "nanoleaf_ctl"])
    try:
        time.sleep(0.1)
        monkeypatch.setattr(mirror, "get_mirror_status", lambda: {"active": True, "pid": worker.pid})
        monkeypatch.setattr(mirror, "remove", lambda p: None)
        started = time.monotonic()
        mirror.stop_mirror(restore_state=False)
        assert time.monotonic() - started < 0.5
    finally:
        worker.kill()
        worker.wait()


def test_restore_intent_handles_color(monkeypatch):
    monkeypatch.setattr(theme, "load_power_intent", lambda: {"mode": "color", "hue": 10, "sat": 20})
    monkeypatch.setattr(theme, "load_config", lambda: {})
    done = []
    monkeypatch.setattr(theme, "set_color", lambda h, s: done.append((h, s)))
    theme.restore_intent()
    assert done == [(10, 20)]
