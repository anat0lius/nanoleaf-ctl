import struct

from nanoleaf_ctl.mirror import build_packet, project_panels, sample_region


def test_project_panels_maps_corners_inside_display():
    positions = [{"panelId": 1, "x": 0, "y": 0}, {"panelId": 2, "x": 100, "y": 100}]
    t = {p["panelId"]: p for p in project_panels(positions, 0, 1000, 500)}
    assert (t[1]["cx"], t[1]["cy"]) == (60, 470)  # bottom-left (y is flipped)
    assert (t[2]["cx"], t[2]["cy"]) == (940, 30)  # top-right


def test_project_panels_empty():
    assert project_panels([], 0, 100, 100) == []


def test_sample_region_averages_solid_color():
    pixels = bytes([10, 20, 30]) * (8 * 8)
    assert sample_region(pixels, 8, 8, 4, 4, 3) == (10, 20, 30)


def test_build_packet_layout():
    pixels = bytes([1, 2, 3]) * (4 * 4)
    targets = [{"panelId": 7, "cx": 2, "cy": 2, "radius": 1}]
    assert build_packet(targets, pixels, 4, 4, 4) == struct.pack(">HHBBBBH", 1, 7, 1, 2, 3, 0, 4)
