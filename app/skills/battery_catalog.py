from __future__ import annotations

import csv
import unicodedata
from collections import defaultdict
from functools import cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
BATTERIES_PATH = DATA_DIR / "batteries.csv"
VEHICLES_PATH = DATA_DIR / "vehicles.csv"
VEHICLE_BATTERIES_PATH = DATA_DIR / "vehicle_batteries.csv"

SLOT_KEYS = ("marca", "modelo", "anio", "generacion", "trim", "motor", "transmision", "linea", "bateria")
DISCRIMINATORS = ("generacion", "trim", "motor", "transmision", "linea", "bateria")


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
    return " ".join(bits) + " + colocación"


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


def lookup(slots: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in load_catalog() if _matches_entry(row, slots)]
    if not rows:
        return {"status": "none"}

    prices = {row["price"] for row in rows}
    baterias = {row.get("bateria") for row in rows}
    if len(prices) == 1 and len(baterias) == 1:
        row = rows[0]
        return {
            "status": "ok",
            "price": row["price"],
            "detail": row.get("detail") or "",
            "bateria": row.get("bateria"),
            "linea": row.get("linea"),
        }

    option_keys = list(DISCRIMINATORS)
    if len({_norm(row.get("modelo")) for row in rows}) > 1:
        option_keys = ["modelo", *option_keys]

    missing: list[str] = []
    options: dict[str, list[str]] = {}
    for key in option_keys:
        if _norm(slots.get(key)):
            continue
        values = sorted({str(row[key]) for row in rows if _norm(row.get(key))})
        if len(values) > 1:
            missing.append(key)
            options[key] = values
            break
    return {"status": "need_slots", "missing": missing, "options": options}
