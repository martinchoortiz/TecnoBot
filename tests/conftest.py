from pathlib import Path

import pytest

from app.config import settings
from app.skills import battery_catalog

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def battery_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "battery_margin_pct", 30)


@pytest.fixture
def catalog_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(battery_catalog, "BATTERIES_PATH", FIXTURES / "batteries.csv")
    monkeypatch.setattr(battery_catalog, "VEHICLES_PATH", FIXTURES / "vehicles.csv")
    monkeypatch.setattr(
        battery_catalog,
        "VEHICLE_BATTERIES_PATH",
        FIXTURES / "vehicle_batteries.csv",
    )
    battery_catalog.load_catalog.cache_clear()
    yield
    battery_catalog.load_catalog.cache_clear()
