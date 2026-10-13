# TecnoBot

Bot de WhatsApp para Tecnocar. Recibe consultas, cotiza si aplica, o deriva a un humano. Trato formal y empresarial, de usted: sin voseo ni comentarios coloquiales; los avisos al cliente van como "Importante:".

## Alcance

1. Cambio de batería (ahora)
2. Neumáticos (futuro)
3. Atención mecánica: estimación con `cotizar_mecanica_ia` (el precio exacto y el turno los cierra el empleado)

Etapa común post-cotización: **Coordinar Turno** (hoy handoff al empleado; después `AgendaPort`).

## Arquitectura

Canal (Kapso) → `ChannelPort` → Orchestrator → LLM engine (DeepSeek, config por env) → Skills / Coordinar Turno / handoff genérico.

- El orchestrator no importa Kapso ni el proveedor de LLM.
- El precio de batería nunca lo inventa el modelo de la conversación: sale de `Skill.calculate_quote` cruzando `batteries.csv`, `vehicles.csv` y `vehicle_batteries.csv`. Precio final = precio de catálogo × (1 + `BATTERY_MARGIN_PCT`/100), margen 30 % por defecto; no hay costo de colocación.
- La estimación mecánica tampoco la arma ese modelo: `cotizar_mecanica_ia` busca en internet (vehículo + problema), abre otra sesión DeepSeek con esos resultados y el sistema suma repuestos + horas × valor hora. Si hay rango, el cliente ve de `total_min` a `total_max`. Ese monto es siempre más impuestos: no se indica el porcentaje ni el importe de impuestos. Para que el mismo vehículo + trabajo dé el mismo número: el estimador corre a temperatura 0, devuelve los repuestos por ítem (cantidad × precio unitario, el sistema suma) con un alcance estándar y rangos angostos, y la estimación se cachea por vehículo + trabajo (`ESTIMATE_CACHE_TTL_HOURS`, aplica siempre el valor hora vigente). Siempre es una estimación sujeta a verificación. Si la búsqueda o el estimador no alcanzan con los datos pedidos, la tool responde `need_slots` (con `motivo`) y el chat pide solo eso y vuelve a cotizar. Si el cliente quiere hacer el trabajo y coordinar turno, `coordinar_turno` para el precio exacto y la agenda.
- Cuándo estimar: trabajo concreto (pieza, service, síntoma con una única reparación habitual) → `cotizar_mecanica_ia`. Síntoma sin causa conocida o pedido de revisión → no se cotiza: se ofrece turno de revisión en taller (`coordinar_turno`). Si igual llega al estimador, este responde `requiere_revision` (sin precio, sin caché) y el chat ofrece la revisión.
- Tools del LLM: `cotizar_bateria`, `cotizar_mecanica_ia`, `coordinar_turno`, `derivar_a_humano`. El controlador llama `cotizar_mecanica_ia` cuando hay un problema del vehículo o piden cotizar un arreglo o recambio.
- `ECHO_MODE=true` eco; `false` + `LLM_API_KEY` = cerebro DeepSeek.
- Cortes bot↔Kapso se persisten en `internal_error` (`source=channel`): HTTP, red, config de envío, y webhook `whatsapp.message.failed`.
- Identificación de auto: lookup en catálogo; si el quote no es unívoco, pedir solo atributos del auto que realmente descartan baterías (`modelo`, `generacion`, `trim`, `motor`, `transmision`). Nunca se le pide al cliente que elija entre códigos de batería. Si tras filtrar siguen varias baterías que aplican al auto, `cotizar_bateria` devuelve `opciones` (una por batería, con su precio final) y `price` en `None`; el cliente elige.
- Zona de atención (batería **a domicilio**): solo Gran San Juan (San Juan Capital, Rivadavia, Rawson, Chimbas, Santa Lucía; `app/skills/service_area.py`). Al ofrecer domicilio el bot lo avisa y pide la dirección; `coordinar_turno` con `domicilio` exige `direccion` + `departamento` (`need_slots` si faltan) y responde `fuera_de_zona` (con `alternativa: taller`) sin handoff si el departamento no está en la lista; el bot avisa que no hace visitas técnicas en esa localidad y ofrece solo la atención en el taller. En taller (batería o mecánica) no se pide dirección ni se valida zona.
- Auto que no arranca o sospecha de batería: no se estima (no es `cotizar_mecanica_ia`); se ofrece **revisión de batería a domicilio** (misma zona de atención y mismas reglas de `need_slots` / `fuera_de_zona`, con alternativa taller) o en taller. Al ofrecer el domicilio, el bot informa siempre el cargo de revisión técnica (`BATTERY_REVISION_FEE_ARS`): si el problema no es la batería se abona la asistencia técnica; si se cambia la batería, ese importe se descuenta del pago final. En taller no hay ese cargo, ni se suma a una colocación ya cotizada. Se coordina con `coordinar_turno` y `tipo_turno=revision_bateria`, que además permite `domicilio` aunque la última cotización haya sido mecánica y deja "revisión de batería" en la nota del handoff.
- `lugar_colocacion`: `taller` | `domicilio`. Ambos van a Coordinar Turno. En mecánica (`cotizar_mecanica_ia`) siempre es `taller`: `coordinar_turno` lo fuerza si la última cotización fue mecánica.

## Stack

FastAPI, SQLModel, SQLite (local) / Postgres (prod), Kapso REST, DeepSeek vía SDK OpenAI.

## Cómo trabajar

Una fase verificable por vez. No hardcodear API keys. Tests de parseo/firma/skills/orchestrator sin red.

Estados de conversación: `active`, `handed_off`, `quoted`. Con `handed_off` el bot no llama `send_text`.
