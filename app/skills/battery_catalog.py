from __future__ import annotations

import csv
import unicodedata
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

from app.config import settings

DATA_DIR = Path(__file__).resolve().parent / "data"
BATTERIES_PATH = DATA_DIR / "batteries.csv"
VEHICLES_PATH = DATA_DIR / "vehicles.csv"
VEHICLE_BATTERIES_PATH = DATA_DIR / "vehicle_batteries.csv"

SLOT_KEYS = ("marca", "modelo", "anio", "generacion", "trim", "motor", "transmision", "linea", "bateria")
# Atributos del auto que se le piden al cliente. `linea` y `bateria` no: si todas las
# baterías aplican, se cotizan todas en vez de pedirle que elija.
DISCRIMINATORS = ("generacion", "trim", "motor", "transmision")


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(without_marks.lower().split())


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return _fold(str(value))


def _year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip()[:4])
    except ValueError:
        return None


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


def load_batteries() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in _csv_rows(BATTERIES_PATH):
        battery_id = row.get("id") or ""
        price = row.get("price") or ""
        if not battery_id or not price:
            continue
        index[battery_id] = {
            "id": battery_id,
            "category": row.get("category") or "",
            "brand": row.get("brand") or "",
            "code": row.get("code") or "",
            "model": row.get("model") or "",
            "dimensions": row.get("dimensions") or "",
            "price": int(str(price).replace(".", "").replace(" ", "")),
        }
    return index


def _detail(battery: dict[str, Any]) -> str:
    brand = str(battery.get("brand") or "Heliar")
    category = str(battery.get("category") or "")
    title = category if _norm(category).startswith(_norm(brand)) else f"{brand} {category}".strip()
    bits = [title]
    if battery.get("model"):
        bits.append(str(battery["model"]))
    return " ".join(bits)


@cache
def load_catalog() -> list[dict[str, Any]]:
    batteries = load_batteries()
    vehicles = {row.get("id"): row for row in _csv_rows(VEHICLES_PATH) if row.get("id")}
    relations: dict[str, set[str]] = defaultdict(set)
    for row in _csv_rows(VEHICLE_BATTERIES_PATH):
        vehicle_id = row.get("vehicle_id") or ""
        battery_id = row.get("battery_id") or ""
        if vehicle_id and battery_id:
            relations[vehicle_id].add(battery_id)

    rows: list[dict[str, Any]] = []
    for vehicle_id, battery_ids in relations.items():
        vehicle = vehicles.get(vehicle_id)
        if not vehicle:
            continue
        for battery_id in battery_ids:
            battery = batteries.get(battery_id)
            if not battery:
                continue
            year_from = vehicle.get("year_from") or ""
            year_to = vehicle.get("year_to") or ""
            rows.append(
                {
                    "marca": vehicle.get("brand") or "",
                    "modelo": vehicle.get("model") or "",
                    "anio_desde": int(year_from) if year_from else None,
                    "anio_hasta": int(year_to) if year_to else None,
                    "generacion": vehicle.get("generation") or "",
                    "trim": vehicle.get("trim") or "",
                    "motor": vehicle.get("engine") or "",
                    "transmision": vehicle.get("transmission") or "",
                    "linea": battery["category"],
                    "bateria": battery["model"],
                    "price": battery["price"],
                    "detail": _detail(battery),
                }
            )
    return rows


def _matches_entry(entry: dict[str, Any], slots: dict[str, Any]) -> bool:
    for key in ("marca", "modelo", "generacion", "trim", "motor", "transmision", "linea", "bateria"):
        asked = _norm(slots.get(key))
        if not asked:
            continue
        got = _norm(entry.get(key))
        if not got:
            continue
        if asked not in got and got not in asked:
            return False
    year = _year(slots.get("anio"))
    if year is not None:
        low = entry.get("anio_desde")
        high = entry.get("anio_hasta")
        if low is not None and year < int(low):
            return False
        if high is not None and year > int(high):
            return False
    return True


def final_price(catalog_price: int | float) -> int:
    """Precio al cliente: precio de catálogo * (1 + margen). Sin costo de colocación."""
    margin = Decimal(str(settings.battery_margin_pct)) / 100
    return int((Decimal(str(catalog_price)) * (1 + margin)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def lookup(slots: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {**row, "price": final_price(row["price"])}
        for row in load_catalog()
        if _matches_entry(row, slots)
    ]
    if not rows:
        return {"status": "none"}

    choices = _distinct_batteries(rows)
    if len(choices) > 1:
        # Primero: ¿algún atributo del auto separa las baterías (no aplican todas)?
        option_keys = list(DISCRIMINATORS)
        if len({_norm(row.get("modelo")) for row in rows}) > 1:
            option_keys = ["modelo", *option_keys]
        for key in option_keys:
            if _norm(slots.get(key)):
                continue
            values = sorted({str(row[key]) for row in rows if _norm(row.get(key))})
            if len(values) > 1 and _splits_batteries(rows, key, values):
                return {"status": "need_slots", "missing": [key], "options": {key: values}}

    # Todas las baterías restantes aplican al auto: se informa el total de cada una.
    if len(choices) == 1:
        row = choices[0]
        return {
            "status": "ok",
            "price": row["price"],
            "detail": row.get("detail") or "",
            "bateria": row.get("bateria"),
            "linea": row.get("linea"),
        }
    opciones = [
        {
            "bateria": row.get("bateria"),
            "linea": row.get("linea"),
            "price": row["price"],
            "detail": row.get("detail") or "",
        }
        for row in choices
    ]
    return {
        "status": "ok",
        "price": None,
        "multiple": True,
        "opciones": opciones,
        "detail": " / ".join(f"{o['detail']}: ${o['price']:,}".replace(",", ".") for o in opciones),
    }


def _distinct_batteries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        seen.setdefault((str(row.get("linea")), str(row.get("bateria")), int(row["price"])), row)
    return sorted(seen.values(), key=lambda r: (r["price"], str(r.get("bateria"))))


def _splits_batteries(rows: list[dict[str, Any]], key: str, values: list[str]) -> bool:
    """True si elegir un valor de `key` deja un conjunto de baterías distinto que otro valor."""
    sets = set()
    for value in values:
        subset = [r for r in rows if _norm(r.get(key)) in ("", _norm(value))]
        sets.add(frozenset((r.get("linea"), r.get("bateria"), r["price"]) for r in subset))
    return len(sets) > 1
