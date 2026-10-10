"""Tests for the frame-cleaning helpers used by direct-capture replays.

``mercator.py`` is dependency-free, so it is loaded straight from its file:
importing it as ``custom_components.rf_fan.mercator`` would run the package
``__init__`` and need Home Assistant installed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import random

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "rf_fan"
    / "mercator.py"
)
_spec = importlib.util.spec_from_file_location("rf_fan_mercator", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
mercator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mercator)

# A Create Windcalm style remote: 30-bit PWM frames, 1.2 ms / 0.4 ms pulses,
# repeated frames separated by ~6.5 ms, ~49 ms of silence after the last one.
_BITS = "110010100001110100101100101010"
_WINDCALM_GAP = -6502
_MERCATOR_GAP = -1800


def _pwm_frame(bits: str, gap: int) -> list[int]:
    """One frame; the last bit's low period merges into the inter-frame gap."""
    frame: list[int] = []
    for bit in bits:
        frame += [1200, -400] if bit == "1" else [400, -1200]
    frame[-1] = gap
    return frame


def _capture(bits: str, gap: int, repeats: int, trailing: int = -49000) -> list[int]:
    timings: list[int] = []
    for _ in range(repeats):
        timings += _pwm_frame(bits, gap)
    timings[-1] = trailing
    return timings


def _windcalm_capture_with_noise() -> list[int]:
    """Five frames; the second one is mangled the way the Broadlink does it."""
    timings: list[int] = []
    for index in range(5):
        frame = _pwm_frame(_BITS, _WINDCALM_GAP)
        if index == 1:
            # The receiver missed a space: mark-space-mark merged into one mark.
            frame[10:13] = [frame[10] + abs(frame[11]) + frame[12]]
        if index == 3:
            frame[-1] = _WINDCALM_GAP + 32  # one tick of jitter on a gap
        timings += frame
    timings[-1] = -49000
    return timings


def test_split_frames_keeps_frame_count_and_returns_inter_frame_gaps() -> None:
    capture = _capture(_BITS, _WINDCALM_GAP, repeats=5)
    frames, gaps = mercator._split_frames_and_gaps(capture)
    assert len(frames) == 5
    assert all(len(frame) == 59 for frame in frames)  # 60 pulses, last low merged
    assert gaps == [_WINDCALM_GAP] * 4  # gaps between frames only, not the idle


def test_measured_gap_is_the_median_of_observed_gaps() -> None:
    capture = _windcalm_capture_with_noise()
    assert mercator.measured_frame_gap(capture) == _WINDCALM_GAP


def test_measured_gap_falls_back_for_single_frame_capture() -> None:
    single = _pwm_frame(_BITS, -49000)
    assert mercator.measured_frame_gap(single) == mercator.DEFAULT_REPEAT_GAP_US
    assert mercator.measured_frame_gap(single, default=-2500) == -2500


def test_measured_gap_ignores_leading_gap_before_first_frame() -> None:
    capture = [-15000, *_capture(_BITS, _WINDCALM_GAP, repeats=3)]
    assert mercator.measured_frame_gap(capture) == _WINDCALM_GAP


def test_clean_frame_uses_consensus_frame_and_measured_gap() -> None:
    capture = _windcalm_capture_with_noise()
    cleaned = mercator.clean_frame(capture)
    expected = _pwm_frame(_BITS, _WINDCALM_GAP)
    assert len(cleaned) == 60
    assert cleaned == expected
    assert cleaned[-1] == _WINDCALM_GAP


def test_clean_frame_explicit_gap_overrides_measurement() -> None:
    capture = _windcalm_capture_with_noise()
    assert mercator.clean_frame(capture, repeat_gap=-1800)[-1] == -1800
    # A positive value is still treated as a space.
    assert mercator.clean_frame(capture, repeat_gap=2000)[-1] == -2000


def test_clean_frame_mercator_style_capture_keeps_its_own_gap() -> None:
    # Manchester-ish 333 us cells, repeated with the FRM97's 1.8 ms gap.
    rng = random.Random(1)
    cells = [rng.choice([333, 666]) * (1 if i % 2 == 0 else -1) for i in range(40)]
    frame = [*cells[:-1]]
    capture = [*frame, _MERCATOR_GAP] * 4
    capture[-1] = -30000
    cleaned = mercator.clean_frame(capture)
    assert cleaned == [*frame, _MERCATOR_GAP]
    # Previous behaviour (hardcoded -1800) is reproduced for the Mercator gap.
    assert cleaned[-1] == mercator.DEFAULT_REPEAT_GAP_US


def test_clean_frame_handles_empty_and_idle_only_captures() -> None:
    assert mercator.clean_frame([]) == []
    assert mercator.clean_frame([-50000, 60000]) == []


def test_consensus_reports_agreement() -> None:
    signature, agreeing, total = mercator.consensus(_windcalm_capture_with_noise())
    assert total == 5
    assert agreeing == 4
    assert signature == mercator.frame_cells(_pwm_frame(_BITS, _WINDCALM_GAP)[:-1])
