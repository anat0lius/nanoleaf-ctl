from nanoleaf_omarchy.theme import hex_to_hsb


def test_red():
    assert hex_to_hsb("#ff0000") == {"hue": 0, "saturation": 100, "brightness": 100}


def test_short_hex_and_saturation_floor():
    assert hex_to_hsb("fff") == {"hue": 0, "saturation": 15, "brightness": 100}
