"""Central settings, loaded from environment / .env. Import `settings` everywhere."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# data.gov.in publishes this sample key in its own API docs; it is capped at 10 rows per call.
DATA_GOV_SAMPLE_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    groq_api_keys: str = ""
    groq_models: str = "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b"
    groq_stt_model: str = "whisper-large-v3"

    data_gov_in_api_key: str = ""
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    sarvam_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_default_chat_id: str = ""

    database_url: str = f"sqlite:///{(DATA_DIR / 'khetdrishti.db').as_posix()}"
    contact_ua: str = "KhetDrishti/1.0 (Forge26 hackathon)"
    monitor_hour_ist: int = 6

    @property
    def groq_keys(self) -> list[str]:
        return [k.strip() for k in self.groq_api_keys.split(",") if k.strip()]

    @property
    def groq_model_chain(self) -> list[str]:
        return [m.strip() for m in self.groq_models.split(",") if m.strip()]

    @property
    def data_gov_key(self) -> str:
        return self.data_gov_in_api_key or DATA_GOV_SAMPLE_KEY

    @property
    def using_sample_mandi_key(self) -> bool:
        return not self.data_gov_in_api_key


settings = Settings()
