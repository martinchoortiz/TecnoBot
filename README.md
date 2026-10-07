# TecnoBot

Bot de WhatsApp de Tecnocar (presupuesto de batería ahora; neumáticos y mecánica después).

## Setup local

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

## RUN
En una terminal:
```powershell
uvicorn app.main:app --reload --port 8000
```
En otra terminal:
```powershell
ngrok http 8000
```

Comprobar: `GET http://127.0.0.1:8000/health`

Tests: `pytest`

Catálogo normalizado en tres CSV:

- `app/skills/data/batteries.csv`: productos y precios.
- `app/skills/data/vehicles.csv`: vehículos y variantes.
- `app/skills/data/vehicle_batteries.csv`: relación N-N vehículo↔batería.

Para regenerar vehículos y relaciones desde Heliar: `python scripts/build_heliar_catalog.py`.

Al levantar el server se crea `tecnobot.db` (SQLite). Cada mensaje queda en `conversation` / `message`. Errores internos (LLM 402, Kapso HTTP/red, mensaje WhatsApp fallido, tools) van a `internal_error`.

En Kapso, el webhook del número debe incluir `whatsapp.message.received` y `whatsapp.message.failed`.

## Webhook Kapso

`POST /webhooks/kapso`

Mientras `ECHO_MODE=true`, responde eco. Con `ECHO_MODE=false` y `LLM_API_KEY`, usa DeepSeek para batería / handoff / Coordinar Turno.

## Sobre el codigo

>C:\Users\MartínOrtiz\Documents\Martin\BotCar\botcar\app\prompts\system_prompt.md
 - Contiene las instrucciones al bot
