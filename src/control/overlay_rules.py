"""Shared overlay/dashboard rules. Source of truth is /think pick."""
from __future__ import annotations

from typing import Any


def stand_aside(think: dict[str, Any] | None) -> bool:
    """Stand aside only when there is no pick.

    Fade / waiting / sit_reason must not wipe a pick on one surface while
    the other still shows it. `suggested === "no trade"` counts as no pick.
    """
    if not think:
        return True
    pick = think.get("pick")
    if pick and pick != "no trade":
        return False
    suggested = think.get("suggested")
    if suggested and suggested != "no trade":
        return False
    return True
