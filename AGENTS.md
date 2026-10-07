# TecnoBot

Bot de WhatsApp para Tecnocar. Recibe consultas, cotiza si aplica, o deriva a un humano.

## Alcance

1. Cambio de batería (ahora)
2. Neumáticos (futuro)
3. Atención mecánica (futuro)

Etapa común post-cotización: **Coordinar Turno** (hoy handoff al empleado; después `AgendaPort`).

## Arquitectura

Canal (Kapso) → `ChannelPort` → Orchestrator → LLM engine (DeepSeek, config por env) → Skills / Coordinar Turno / handoff genérico.

- El orchestrator no importa Kapso ni el proveedor de LLM.
- El precio nunca lo inventa el modelo: sale de `Skill.calculate_quote` cruzando `batteries.csv`, `vehicles.csv` y `vehicle_batteries.csv`.
- Tools del LLM: `cotizar_bateria`, `coordinar_turno`, `derivar_a_humano`.
- `ECHO_MODE=true` eco; `false` + `LLM_API_KEY` = cerebro DeepSeek.
- Cortes bot↔Kapso se persisten en `internal_error` (`source=channel`): HTTP, red, config de envío, y webhook `whatsapp.message.failed`.
- Identificación de auto: lookup en catálogo; si el quote no es unívoco, pedir solo slots que discriminan (`marca`, `modelo`, `anio`, `generacion`, `trim`, `motor`, `transmision`).
- `lugar_colocacion`: `taller` | `domicilio`. Ambos van a Coordinar Turno.

## Stack

FastAPI, SQLModel, SQLite (local) / Postgres (prod), Kapso REST, DeepSeek vía SDK OpenAI.

## Cómo trabajar

Una fase verificable por vez. No hardcodear API keys. Tests de parseo/firma/skills/orchestrator sin red.

Estados de conversación: `active`, `handed_off`, `quoted`. Con `handed_off` el bot no llama `send_text`.
