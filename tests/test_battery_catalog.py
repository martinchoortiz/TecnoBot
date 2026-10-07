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
    assert result["price"] == 165000


def test_corolla_unique(catalog_fixture) -> None:
    result = lookup({"marca": "Toyota", "modelo": "Corolla", "anio": "2016"})
    assert result["status"] == "ok"


def test_unknown_is_none(catalog_fixture) -> None:
    assert lookup({"marca": "Ferrari", "modelo": "F40", "anio": "1992"})["status"] == "none"


def test_vehicle_can_have_multiple_batteries(catalog_fixture) -> None:
    result = lookup({"marca": "Chevrolet", "modelo": "Astra", "anio": "2014"})
    assert result["status"] == "need_slots"
    assert result["missing"] == ["bateria"]
    assert result["options"]["bateria"] == ["12V 60Ah", "12V 70Ah"]


def test_skill_missing_then_quote(catalog_fixture) -> None:
    skill = BatteryChangeSkill()
    slots = {"marca": "Volkswagen", "modelo": "Vento", "anio": "2014"}
    assert skill.missing_slots(slots) == ["motor"]
    slots["motor"] = "2.0 TDI"
    assert skill.missing_slots(slots) == []
    quote = skill.calculate_quote(slots)
    assert quote["status"] == "ok"
    assert quote["price"] == 198000


def test_heliar_tiguan_uses_price_list() -> None:
    from app.skills.battery_catalog import load_catalog

    load_catalog.cache_clear()
    result = lookup({"marca": "Volkswagen", "modelo": "Tiguan", "anio": "2014"})
    assert result["status"] == "ok"
    assert result["bateria"] == "HFB60HD"
    assert result["price"] == 270000
