

569 204 030 95
es el numero de sandbox de kapso




# PASOS:
 1. Cargar saldo en DeepSeek y probar de punta a punta en WhatsApp el camino feliz (Vento 2014 → motor → precio → taller/domicilio → Coordinar Turno). ✅
 2. Completar el catálogo real de baterías (precios y fits de Tecnocar), no el JSON de ejemplo.✅
 3. Afinar el prompt con conversaciones reales (typos, “versión” vs motor, el cliente tira todo junto).
 4. Cerrar huecos de internal_error: parseo del webhook y fallos al arrancar.
 5. Handoff Kapso de verdad: que el empleado vea el hilo en el inbox, pueda escribir, y “Resume from Handoff” vuelva el bot (active).
 6. Notificar Coordinar Turno (qué ve el operario: quote, auto, taller vs domicilio).
 7. Aliases de ficha (vw → Volkswagen, 2.0 turbo → 2.0 TSI) para que el lookup no dependa del wording exacto.
 8. Apagar el eco en prod (ECHO_MODE=false) y documentar el .env que ya usás.
 9. Deploy: Railway/Fly + Postgres (Neon/Supabase) + webhook HTTPS fijo (sacar ngrok).
 10. Verificación de negocio Meta y número WhatsApp real (dejar el sandbox).
 11. Skill neumáticos (misma ficha de auto + Coordinar Turno).
 12. Skill mecánica (paquetes cotizables; el resto a humano + turno).
 13. AgendaPort: Coordinar Turno deja de ser solo handoff y pega a la API de turnos.
 14. Repo remoto / backups de tecnobot.db (o Postgres) y de internal_error.



En esa misma carpeta del repo:

.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
Debería aparecer algo como Uvicorn running on http://127.0.0.1:8000. Dejá esa ventana abierta.

Comprobá en el navegador: http://127.0.0.1:8000/health. Si ves ok, seguí con ngrok en otra terminal:


ngrok http 8000
