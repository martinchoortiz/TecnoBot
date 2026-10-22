from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    kapso_api_key: str = ""
    kapso_phone_number_id: str = ""
    kapso_webhook_secret: str = ""
    kapso_base_url: str = "https://api.kapso.ai"
    # URL pública de este backend (ngrok o el dominio de prod), sin barra final.
    # Con ella se crea el workflow que muestra el banner y llama a /webhooks/kapso/resume.
    kapso_public_base_url: str = ""
    # Si está vacío y hay kapso_public_base_url, se usa el workflow slug tecnobot-handoff.
    kapso_handoff_workflow_id: str = ""
    # Usuario del proyecto Kapso. Si está vacío, no se asigna el hilo.
    kapso_handoff_user_id: str = ""

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_disable_thinking: bool = True

    database_url: str = "sqlite:///./tecnobot.db"
    echo_mode: bool = True
    labor_hourly_rate_ars: float = 0
    # Margen en % sobre el precio de catálogo de la batería. Precio final = catálogo * (1 + margen/100).
    battery_margin_pct: float = 30
    # Cargo de revisión técnica a domicilio de batería. Si no es la batería, se abona;
    # si se cambia, se descuenta del pago final. 0 = sin configurar.
    battery_revision_fee_ars: float = 0
    # Horas que se reutiliza una estimación mecánica para el mismo vehículo y trabajo. 0 = sin caché.
    estimate_cache_ttl_hours: float = 24


settings = Settings()
