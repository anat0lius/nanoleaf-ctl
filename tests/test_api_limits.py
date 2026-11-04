import io

import pytest

from nanoleaf_ctl import api


def test_read_limited_returns_small_bodies():
    assert api.read_limited(io.BytesIO(b'{"a": 1}'), limit=16) == '{"a": 1}'


def test_read_limited_accepts_body_exactly_at_limit():
    assert api.read_limited(io.BytesIO(b"x" * 16), limit=16) == "x" * 16


def test_read_limited_rejects_oversized_body_without_reading_it_all():
    body = io.BytesIO(b"x" * 1000)
    with pytest.raises(RuntimeError, match="larger than 16 bytes"):
        api.read_limited(body, limit=16)
    assert body.tell() == 17  # stopped one byte past the limit


def test_run_limited_returns_small_output():
    from nanoleaf_ctl.state import run_limited

    assert run_limited(["echo", "hi"]).strip() == "hi"


def test_run_limited_refuses_oversized_output():
    from nanoleaf_ctl.state import run_limited

    with pytest.raises(RuntimeError, match="larger than"):
        run_limited(["head", "-c", "5000", "/dev/zero"], limit=1000)


def test_run_limited_kills_a_command_that_hangs():
    import time

    from nanoleaf_ctl.state import run_limited

    start = time.monotonic()
    assert run_limited(["sleep", "30"], timeout=0.3) == ""
    assert time.monotonic() - start < 5
