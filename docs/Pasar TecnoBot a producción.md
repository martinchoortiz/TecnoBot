# Pasar TecnoBot a producción

Las recomendaciones de abajo salen de lo que hay hoy en el repo. Los precios son aproximados y de memoria, así que confirmalos en cada sitio antes de decidir.

## Lo que falta en el repo

- **No hay `Dockerfile`, `Procfile` ni config de deploy.** Hay que agregar uno. En Railway, Render o Fly alcanza con un Dockerfile mínimo o un comando de arranque.
- **Postgres no funciona todavía.** `pyproject.toml` no incluye ningún driver (`psycopg[binary]`). Además, `DATABASE_URL` tiene que usar el esquema `postgresql+psycopg://...`. Los proveedores suelen entregar `postgres://...`, que SQLAlchemy no acepta.
- **No hay migraciones.** `app/db/session.py` usa `SQLModel.metadata.create_all`, que crea tablas pero no las modifica. Si cambiás un modelo en producción, no se actualiza solo. A mediano plazo conviene Alembic.
- **La firma del webhook es opcional.** En `app/main.py`, si `KAPSO_WEBHOOK_SECRET` está vacío, no se verifica nada y cualquiera puede mandar mensajes falsos al bot. En producción tiene que estar seteado.
- **SQLite sirve solo con disco persistente.** En Railway, Render o Fly, el filesystem se borra en cada deploy, así que `tecnobot.db` se perdería. Con Postgres no hay problema.

## Paso a paso

### 1. Número de WhatsApp real
1. **Meta Business:** creá o usá el Business Portfolio de Tecnocar y hacé la **verificación del negocio**. Es gratis. Piden CUIT, constancia de AFIP y datos del sitio o dominio, y tarda de días a un par de semanas.
2. **Número:** necesitás uno que **no esté registrado en WhatsApp ni en WhatsApp Business app**, y que pueda recibir un SMS o llamada de verificación. Una línea prepaga nueva sirve. Si es el número de siempre del taller, hay que migrarlo y se pierde el chat en la app.
3. **En Kapso:** conectá la cuenta de WhatsApp Business (Embedded Signup) y cargá el número. Definí el **display name**, que Meta aprueba. Después copiá `KAPSO_PHONE_NUMBER_ID` y la API key.
4. **Webhook:** configurá `https://TU-DOMINIO/webhooks/kapso` con los eventos `whatsapp.message.received` y `whatsapp.message.failed`. Copiá el secret a `KAPSO_WEBHOOK_SECRET`.
5. El sandbox (569 204 030 95) lo dejás solo para desarrollo.

### 2. Base de datos
- Creá una instancia de Postgres (opciones abajo).
- Agregá `psycopg[binary]` a las dependencias.
- Seteá `DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/tecnobot`.
- Al primer arranque se crean las tablas (`conversation`, `message`, `internal_error`, etc.).
- Activá backups automáticos. Es el paso 14 de tu `Ayudas.md`.

### 3. Hosting de la app
- Armá un contenedor o servicio que corra `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Dejá **una sola réplica** al principio. No sé si el estado de la conversación o el caché de estimaciones viven en memoria o en DB, y con varias réplicas se podrían desincronizar.
- Usá `/health` como health check.
- Pedí un dominio con HTTPS, que es la URL fija para el webhook. Con esto sacás ngrok.

### 4. Variables de entorno (en el panel del host, no en un `.env` subido al repo)

| Variable | Valor en prod |
|---|---|
| `ECHO_MODE` | `false` |
| `LLM_API_KEY` | key de DeepSeek (con saldo cargado) |
| `KAPSO_API_KEY`, `KAPSO_PHONE_NUMBER_ID`, `KAPSO_WEBHOOK_SECRET` | los del número real |
| `DATABASE_URL` | Postgres |
| `LABOR_HOURLY_RATE_ARS` | **mayor a 0**, el valor hora real del taller |
| `ESTIMATE_CACHE_TTL_HOURS` | 24 (o lo que quieras) |

### 5. Verificación antes de abrir al público
1. Probá de punta a punta con el número real: batería, mecánica, handoff, y que `handed_off` deje de responder.
2. Confirmá que un POST sin firma válida al webhook devuelva 401.
3. Revisá `internal_error` después de las pruebas.
4. Confirmá que el empleado ve el hilo en el inbox de Kapso y puede devolver el bot a `active`. Son los pasos 5 y 6 de tu lista.
5. Probá un redeploy y comprobá que los datos siguen ahí.

### 6. Operación
- Backups de la DB.
- Alertas simples (uptime sobre `/health`, revisión periódica de `internal_error`).
- Alerta de saldo de DeepSeek: si se agota, el bot deja de responder.
- Cuando cambie el catálogo CSV, redeploy.

## Costos aproximados (USD/mes)

### WhatsApp (Meta + Kapso)
- **Verificación Meta:** gratis.
- **Mensajes:** Meta cobra por mensaje de plantilla, no por conversación. Si el bot **solo responde** a clientes que escribieron dentro de las 24 horas, lo normal es que el costo sea **cercano a 0**. Cobra si empezás vos la conversación (plantillas de marketing, o utility fuera de la ventana), por ejemplo para recordatorios de turno. Mirá la tarifa de Argentina en la lista de precios de Meta.
- **Kapso:** tiene un plan gratis/sandbox y planes pagos por uso o por número. Revisá su página de precios, porque no tengo el valor actual. Contá con **USD 0 a ~25-50**.
- **Línea telefónica:** una SIM prepaga, ~USD 2-5 de carga ocasional.

### LLM y búsqueda web
- **DeepSeek:** muy barato, del orden de centavos de dólar por millón de tokens de entrada. Una conversación típica cuesta fracciones de centavo, y la estimación mecánica abre una segunda sesión que suma algo. Con unos pocos cientos de conversaciones al mes, esperá **USD 1-10**. Cargá saldo con alerta.
- **Búsqueda web:** `ddgs` es gratis, pero scrapea DuckDuckGo y puede dar rate limits o fallar. Si la estimación mecánica se vuelve crítica, evaluá una API paga (Tavily, Brave, Serper, etc., desde ~USD 0-30).

### Hosting + base de datos (tres combinaciones)

| Stack | App | DB | Total aprox. |
|---|---|---|---|
| **A. Mínimo / barato** | Fly.io, 1 máquina chica (~USD 2-5) | Neon free (0,5 GB, se suspende y despierta con una latencia extra) | **~USD 2-5** |
| **B. Simple y cómodo (recomendado para empezar)** | Railway (Hobby ~USD 5 más uso) o Render Starter (~USD 7) | Postgres del mismo proveedor (~USD 1-7) o Neon | **~USD 8-15** |
| **C. VPS propio** | Hetzner o DigitalOcean (~USD 5-6) con Docker Compose (app + Postgres + Caddy para HTTPS) | incluido en el VPS | **~USD 5-6**, pero vos hacés backups, parches y monitoreo |

Más notas:
- **Supabase free** pausa proyectos inactivos y su plan Pro cuesta ~USD 25. Para un bot que tiene que estar siempre disponible, Neon o el Postgres del proveedor son mejores.
- **Dominio propio:** ~USD 10-15 al año. En Railway, Render o Fly también podés usar el subdominio gratis que te dan.

### Total estimado
| Escenario | USD/mes |
|---|---|
| Mínimo viable | ~10-20 |
| Cómodo | ~25-60 |

Esto sin contar mensajes de plantilla de Meta ni una API de búsqueda paga. Casi todo el costo fijo está en Kapso y el hosting. El LLM casi no pesa.

## Orden sugerido
1. Iniciá ya la verificación de negocio en Meta, porque es lo que más tarda.
2. En paralelo, agregá el Dockerfile y el driver de Postgres.
3. Desplegá en staging con el sandbox y Postgres.
4. Pasá al número real.
5. Activá backups.

Si querés que lo implemente (Dockerfile, driver, ajuste del `DATABASE_URL`, forzar el secret del webhook), pasate a Agent mode y lo hago.