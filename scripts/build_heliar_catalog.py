"""Descarga fits Heliar y genera vehicles.csv y vehicle_batteries.csv."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "app" / "skills" / "data"
BATTERIES_PATH = DATA / "batteries.csv"
VEHICLES_PATH = DATA / "vehicles.csv"
RELATIONS_PATH = DATA / "vehicle_batteries.csv"
API = "https://www.heliar.com/api/fitment/product"
MOTOR_IN_MODEL = re.compile(r"^(.*?)\s*\((?:motor\s+)?(.+?)\)\s*$", re.I)


def sku_candidates(modelo: str) -> list[str]:
    raw = " ".join(modelo.split())
    out: list[str] = [raw, raw.replace(" ", "")]
    compact = raw.replace(" ", "")
    m = re.match(r"^(.+?)([A-Z])\s*/\s*E$", compact, re.I)
    if m:
        stem, last = m.group(1), m.group(2).upper()
        out.append(f"{stem}{last}")
        out.append(f"{stem}E")
    seen: set[str] = set()
    unique: list[str] = []
    for sku in out:
        key = sku.upper()
        if key and key not in seen:
            seen.add(key)
            unique.append(sku)
    return unique


def fetch_apps(partnum: str) -> list[dict]:
    q = urllib.parse.urlencode({"partnum": partnum, "sf_Culture": "es-ar"})
    req = urllib.request.Request(
        f"{API}?{q}",
        headers={"User-Agent": "TecnoBot/0.1 (catalogo local Tecnocar)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def split_model(model: str, engine: str | None) -> tuple[str, str]:
    motor = (engine or "").strip()
    modelo = " ".join((model or "").split())
    if not motor:
        m = MOTOR_IN_MODEL.match(modelo)
        if m:
            modelo, motor = m.group(1).strip(), m.group(2).strip()
    return modelo, motor


def consecutive_ranges(years: list[int]) -> list[tuple[int, int]]:
    ordered = sorted(set(years))
    start = prev = ordered[0]
    ranges: list[tuple[int, int]] = []
    for year in ordered[1:]:
        if year == prev + 1:
            prev = year
            continue
        ranges.append((start, prev))
        start = prev = year
    ranges.append((start, prev))
    return ranges


def vehicle_id(row: dict[str, str]) -> str:
    values = (
        row["brand"],
        row["model"],
        row["year_from"],
        row["year_to"],
        row["generation"],
        row["trim"],
        row["engine"],
        row["transmission"],
    )
    fingerprint = "|".join(value.casefold().strip() for value in values)
    return "VEH-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12].upper()


def normalize(
    apps: list[tuple[int, str, str, str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_year: dict[tuple[str, str, str, int], set[str]] = defaultdict(set)
    for year, make, model, motor, battery_id in apps:
        by_year[(make, model, motor, year)].add(battery_id)

    groups: dict[tuple[str, str, str, tuple[str, ...]], list[int]] = defaultdict(list)
    for (make, model, motor, year), battery_ids in by_year.items():
        groups[(make, model, motor, tuple(sorted(battery_ids)))].append(year)

    vehicles: list[dict[str, str]] = []
    relations: list[dict[str, str]] = []
    for (make, model, motor, battery_ids), years in groups.items():
        for low, high in consecutive_ranges(years):
            vehicle = {
                "id": "",
                "brand": make,
                "model": model,
                "year_from": str(low),
                "year_to": str(high),
                "generation": "",
                "trim": "",
                "engine": motor,
                "transmission": "",
            }
            vehicle["id"] = vehicle_id(vehicle)
            vehicles.append(vehicle)
            relations.extend(
                {"vehicle_id": vehicle["id"], "battery_id": battery_id}
                for battery_id in battery_ids
            )

    vehicles.sort(key=lambda row: (row["brand"], row["model"], row["year_from"], row["id"]))
    relations.sort(key=lambda row: (row["vehicle_id"], row["battery_id"]))
    return vehicles, relations


def main() -> None:
    with BATTERIES_PATH.open(encoding="utf-8-sig", newline="") as fh:
        batteries = list(csv.DictReader(fh))

    apps: list[tuple[int, str, str, str, str]] = []
    empty: list[str] = []
    for row in batteries:
        battery_id = (row.get("id") or "").strip()
        battery_model = (row.get("model") or "").strip()
        if not battery_id or not battery_model:
            continue
        found = False
        for sku in sku_candidates(battery_model):
            time.sleep(0.12)
            payload = fetch_apps(sku)
            if not payload:
                continue
            found = True
            for item in payload:
                year = item.get("year")
                make = (item.get("make") or "").strip()
                vehicle_model, motor = split_model(item.get("model") or "", item.get("engine"))
                if year is None or not make or not vehicle_model:
                    continue
                apps.append((int(year), make, vehicle_model, motor, battery_id))
        if not found:
            empty.append(battery_model)
        print(f"{battery_model}: {'ok' if found else 'sin aplicaciones'}")

    vehicles, relations = normalize(apps)
    vehicle_fields = [
        "id",
        "brand",
        "model",
        "year_from",
        "year_to",
        "generation",
        "trim",
        "engine",
        "transmission",
    ]
    with VEHICLES_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=vehicle_fields)
        writer.writeheader()
        writer.writerows(vehicles)
    with RELATIONS_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["vehicle_id", "battery_id"])
        writer.writeheader()
        writer.writerows(relations)
    print(f"Escribí {len(vehicles)} vehículos en {VEHICLES_PATH}")
    print(f"Escribí {len(relations)} relaciones en {RELATIONS_PATH}")
    if empty:
        print("Sin fits Heliar:", ", ".join(empty))


if __name__ == "__main__":
    main()
