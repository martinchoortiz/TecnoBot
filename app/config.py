from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    kapso_api_key: str = ""
    kapso_phone_number_id: str = ""
    kapso_webhook_secret: str = ""
    kapso_base_url: str = "https://api.kapso.ai"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_disable_thinking: bool = True

    database_url: str = "sqlite:///./tecnobot.db"
    echo_mode: bool = True
    labor_hourly_rate_ars: float = 0
    # Horas que se reutiliza una estimación mecánica para el mismo vehículo y trabajo. 0 = sin caché.
    estimate_cache_ttl_hours: float = 24


settings = Settings()
