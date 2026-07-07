from __future__ import annotations

from .base import ATSAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter

ADAPTERS: tuple[ATSAdapter, ...] = (GreenhouseAdapter(), LeverAdapter())


def find_adapter(url: str) -> ATSAdapter | None:
    for adapter in ADAPTERS:
        if adapter.detect(url):
            return adapter
    return None
