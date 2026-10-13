from app.skills.battery_catalog import lookup
from app.skills.battery_change import BatteryChangeSkill


def test_vento_needs_motor(catalog_fixture) -> None:
    result = lookup({"marca": "Volkswagen", "modelo": "Vento", "anio": "2014"})
    assert result["status"] == "need_slots"
    assert "motor" in result["missing"]
    assert "2.5" in result["options"]["motor"]


def test_vento_tsi_quotes(catalog_fixture) -> None:
    result = lookup({"marca": "Volkswagen", "modelo": "Vento", "anio": "2014", "motor": "2.0 TSI"})
    assert result["status"] == "ok"
    assert result["price"] == 214500


def test_corolla_unique(catalog_fixture) -> None:
    result = lookup({"marca": "Toyota", "modelo": "Corolla", "anio": "2016"})
    assert result["status"] == "ok"


def test_unknown_is_none(catalog_fixture) -> None:
    assert lookup({"marca": "Ferrari", "modelo": "F40", "anio": "1992"})["status"] == "none"


def test_all_batteries_apply_returns_every_option(catalog_fixture) -> None:
    result = lookup({"marca": "Chevrolet", "modelo": "Astra", "anio": "2014"})
    assert result["status"] == "ok"
    assert result["multiple"] is True
    assert [(o["bateria"], o["price"]) for o in result["opciones"]] == [
        ("12V 60Ah", 214500),
        ("12V 70Ah", 240500),
    ]


def test_multiple_options_quote_has_no_single_price(catalog_fixture) -> None:
    quote = BatteryChangeSkill().calculate_quote({"marca": "Chevrolet", "modelo": "Astra", "anio": "2014"})
    assert quote["status"] == "ok"
    assert quote["price"] is None
    assert len(quote["opciones"]) == 2


def test_customer_battery_narrows_to_single_price(catalog_fixture) -> None:
    result = lookup({"marca": "Chevrolet", "modelo": "Astra", "anio": "2014", "bateria": "12V 70Ah"})
    assert result["status"] == "ok"
    assert result["price"] == 240500
    assert "multiple" not in result


def test_skill_missing_then_quote(catalog_fixture) -> None:
    skill = BatteryChangeSkill()
    slots = {"marca": "Volkswagen", "modelo": "Vento", "anio": "2014"}
    assert skill.missing_slots(slots) == ["motor"]
    slots["motor"] = "2.0 TDI"
    assert skill.missing_slots(slots) == []
    quote = skill.calculate_quote(slots)
    assert quote["status"] == "ok"
    assert quote["price"] == 257400


def test_heliar_tiguan_uses_price_list() -> None:
    from app.skills.battery_catalog import load_catalog

    load_catalog.cache_clear()
    result = lookup({"marca": "Volkswagen", "modelo": "Tiguan", "anio": "2014"})
    assert result["status"] == "ok"
    assert result["bateria"] == "HFB60HD"
    assert result["price"] == 351000


def test_final_price_applies_margin(monkeypatch) -> None:
    from app.config import settings
    from app.skills.battery_catalog import final_price

    assert final_price(180000) == 234000
    assert final_price(275000) == 357500
    monkeypatch.setattr(settings, "battery_margin_pct", 0)
    assert final_price(180000) == 180000
    monkeypatch.setattr(settings, "battery_margin_pct", 12.5)
    assert final_price(100001) == 112501
