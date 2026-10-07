from app.skills.battery_change import BatteryChangeSkill


def test_missing_base_slots() -> None:
    skill = BatteryChangeSkill()
    assert skill.missing_slots({}) == ["marca", "modelo", "anio"]
    assert skill.missing_slots({"marca": "VW", "modelo": "Vento", "anio": "2014"}) == []


def test_catalog_discriminators() -> None:
    skill = BatteryChangeSkill()
    slots = {"marca": "VW", "modelo": "Vento", "anio": "2014"}
    hint = {"discriminators": ["motor"]}
    assert skill.missing_slots(slots, hint) == ["motor"]
    slots["motor"] = "2.0 TSI"
    assert skill.missing_slots(slots, hint) == []
