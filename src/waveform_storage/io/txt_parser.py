"""Strict three-column waveform TXT parsing without unit conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


class TxtParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedWaveform:
    x: np.ndarray
    y: np.ndarray
    bunch_ids: np.ndarray


def parse_waveform_txt(path, *, expected_points=1024) -> ParsedWaveform:
    rows = []
    for line_number, raw in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw.strip():
            continue
        columns = raw.split()
        if len(columns) != 3:
            raise TxtParseError(
                f"line {line_number} must contain exactly three columns"
            )
        try:
            x_value, y_value, bunch_id = (int(value) for value in columns)
        except ValueError as exc:
            raise TxtParseError(
                f"line {line_number} contains a non-integer value"
            ) from exc
        if not -(2**63) <= x_value < 2**63:
            raise TxtParseError(f"line {line_number} X is outside int64")
        if not -(2**63) <= y_value < 2**63:
            raise TxtParseError(f"line {line_number} Y is outside int64")
        if not 0 <= bunch_id < 2**64:
            raise TxtParseError(f"line {line_number} bunchID is outside uint64")
        rows.append((x_value, y_value, bunch_id))
    if len(rows) != expected_points:
        raise TxtParseError(
            f"expected {expected_points} points, found {len(rows)}"
        )
    values = np.asarray(rows, dtype=object)
    return ParsedWaveform(
        x=np.asarray(values[:, 0], dtype=np.int64),
        y=np.asarray(values[:, 1], dtype=np.int64),
        bunch_ids=np.asarray(values[:, 2], dtype=np.uint64),
    )
