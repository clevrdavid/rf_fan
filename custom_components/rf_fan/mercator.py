"""Frame helpers for direct-capture remotes: read captured frames, then
generate clean ones.

Written for the Mercator FRM87/FRM97. Per rtl_433 issue #2200 that remote is
OOK at 433.92 MHz with a base cell of ~333us; each data bit spans 3 cells
(symbol one = 110, symbol zero = 010). The button is a one-hot field, so light /
off / each speed are the *same* frame differing in a single bit. Rather than
reconstruct the exact bit map from the (ambiguous) published notation, we work
empirically from the user's own captured frames - which is robust to the
DIP-switch address and to Broadlink's timing - and that makes the approach
protocol-agnostic: any remote that repeats a fixed frame can be cleaned this way
(the Create Windcalm's 30-bit PWM frames work just the same).

A capture contains the frame repeated several times, separated by long gaps. We
split on those gaps, render each frame as a cell string (``H`` = high cell,
``l`` = low cell; a long pulse/gap = two cells), and take the most common frame
as the consensus - noise then shows up as low agreement.

The gaps matter too: receivers use the inter-frame gap to re-synchronise, and
its length is remote-specific (~1.8 ms on the Mercator, ~6.5 ms on the Create
Windcalm). A frame retransmitted with the wrong gap can go undecoded entirely
(the Windcalm ignores frames 1.8 ms apart), so the clean frame is returned with
the gap *measured* in the capture.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

_IDLE_US = 20000  # drop leading/trailing idle longer than this
_FRAME_GAP_US = 1300  # a gap longer than this separates repeated frames
_LONG_US = 600  # a pulse/gap at least this long counts as two cells

# Gap appended to a clean frame when the capture holds no inter-frame gap to
# measure (a single-frame capture). This is the Mercator FRM97's gap, the value
# every direct capture used before gaps were measured.
DEFAULT_REPEAT_GAP_US = -1800


def _split_frames_and_gaps(timings: list[int]) -> tuple[list[list[int]], list[int]]:
    """Trim idle, then split a capture into its repeated frames.

    Returns ``(frames, gaps)``. ``gaps`` holds the inter-frame gaps (negative
    microseconds) observed *between* consecutive frames, in capture order; a
    long gap before the first or after the last frame is not one.
    """
    ts = [int(t) for t in timings]
    while ts and abs(ts[0]) > _IDLE_US:
        ts.pop(0)
    while ts and abs(ts[-1]) > _IDLE_US:
        ts.pop()

    frames: list[list[int]] = []
    gaps: list[int] = []
    current: list[int] = []
    pending_gap: int | None = None
    for t in ts:
        if t < 0 and abs(t) > _FRAME_GAP_US:
            if current:
                frames.append(current)
                current = []
                pending_gap = t
            continue
        if not current and pending_gap is not None:
            gaps.append(pending_gap)
            pending_gap = None
        current.append(t)
    if current:
        frames.append(current)
    return frames, gaps


def split_frames(timings: list[int]) -> list[list[int]]:
    """Trim idle, then split a capture into its repeated frames."""
    return _split_frames_and_gaps(timings)[0]


def frame_cells(frame: list[int]) -> str:
    """Render one frame as a cell string (H = high cell, l = low cell)."""
    cells: list[str] = []
    for t in frame:
        count = 2 if abs(t) >= _LONG_US else 1
        cells.append(("H" if t > 0 else "l") * count)
    return "".join(cells)


def consensus(timings: list[int]) -> tuple[str, int, int]:
    """Return (most-common frame cell string, frames-agreeing, total-frames)."""
    signatures = [frame_cells(f) for f in split_frames(timings) if f]
    if not signatures:
        return "", 0, 0
    signature, count = Counter(signatures).most_common(1)[0]
    return signature, count, len(signatures)


def _median_gap(gaps: list[int], default: int) -> int:
    """Median of the observed gaps as a negative integer, or ``default``."""
    if not gaps:
        return default
    return -abs(round(median(gaps)))


def measured_frame_gap(timings: list[int], default: int = DEFAULT_REPEAT_GAP_US) -> int:
    """Return the inter-frame gap (negative us) a capture's remote actually uses.

    The median of the gaps between the captured frames - robust to a frame the
    receiver mangled. ``default`` is returned when the capture holds a single
    frame, so there is no gap to measure.
    """
    return _median_gap(_split_frames_and_gaps(timings)[1], default)


def clean_frame(timings: list[int], repeat_gap: int | None = None) -> list[int]:
    """Return one clean (consensus) frame's actual timings, with a trailing gap.

    A capture holds the frame repeated several times, some corrupted by the
    Broadlink receiver. We pick the most common (consensus) frame and return its
    real microsecond timings plus a trailing gap, so it can be retransmitted -
    repeated cleanly - without the noisy frames from the raw capture.

    ``repeat_gap`` defaults to the gap measured between the captured frames
    (see ``measured_frame_gap``), falling back to ``DEFAULT_REPEAT_GAP_US`` for
    a single-frame capture. Pass a value to override it.
    """
    frames, gaps = _split_frames_and_gaps(timings)
    frames = [f for f in frames if f]
    if not frames:
        return []
    signatures = [frame_cells(f) for f in frames]
    target = Counter(signatures).most_common(1)[0][0]
    frame = frames[signatures.index(target)]
    if repeat_gap is None:
        repeat_gap = _median_gap(gaps, DEFAULT_REPEAT_GAP_US)
    return [*frame, -abs(int(repeat_gap))]
