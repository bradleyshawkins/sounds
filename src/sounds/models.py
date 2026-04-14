"""Shared data models used across the engine, library, and UI layers."""

from dataclasses import dataclass


@dataclass
class Section:
    """A labeled segment of a track, stored in input-sample space."""

    start_sample: int
    end_sample: int
    label: str
    color: str  # hex string, e.g. "#5B8DB8"
