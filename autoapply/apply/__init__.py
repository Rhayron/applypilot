"""Registry de appliers automáticos por fonte."""
from __future__ import annotations

from .base import Applier, ApplyResult, ScreeningAnswerer
from .greenhouse import GreenhouseApplier
from .lever import LeverApplier

APPLIERS: dict[str, type[Applier]] = {
    "greenhouse": GreenhouseApplier,
    "lever": LeverApplier,
    # ashby/gupy/linkedin: sem automação — vão para alerta/review
}


def get_applier(source: str, answerer: ScreeningAnswerer, headless: bool = True) -> Applier | None:
    cls = APPLIERS.get(source)
    return cls(answerer, headless=headless) if cls else None


__all__ = ["Applier", "ApplyResult", "ScreeningAnswerer", "get_applier", "APPLIERS"]
