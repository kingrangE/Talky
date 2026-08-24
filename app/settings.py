from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    LLM_PROVIDER: Literal["ollama", "openai"] = "ollama"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    MODEL_NAME: str = "skt/A.X-4.0-Light"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: Literal["cpu", "cuda"] = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    TTS_VOICE_EN: str = "en_US-amy-medium"
    TTS_VOICE_KO: str = "ko_KR-kss-medium"
    AUDIO_CACHE_DIR: str = "./.cache"

    # Speaking mock test
    MOCK_EXAM_DATA_DIR: str = "./.data/mock-exam"
    MOCK_EXAM_SET_DIR: str = "./app/mock_exam/data/sets"
    MOCK_EXAM_AUDIO_TTL_HOURS: int = 72
    MOCK_EXAM_RECOVERY_TTL_MINUTES: int = 10
    MOCK_EXAM_RESULT_TTL_HOURS: int = 72
    MOCK_EXAM_DAILY_LIMIT: int = 1
    MOCK_EXAM_GLOBAL_CONCURRENCY: int = 1
    MOCK_EXAM_SCORING_TIMEOUT_SECONDS: int = 90
    MOCK_EXAM_SIGNING_SECRET: str = ""
    MOCK_EXAM_FINGERPRINT_SALT: str = "talky-local-demo"
    MOCK_EXAM_SCORING_PROFILE: Literal["basic", "advanced", "auto"] = "auto"
    MOCK_EXAM_ADVANCED_MODEL: str = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"
    MOCK_EXAM_REVIEW_MODEL: str = "qwen2.5:7b"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "talky"
    POSTGRES_USER: str = "talky"
    POSTGRES_PASSWORD: str = "talky"

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "talky-dev-pw"

    APP_USER_ID: str = ""
    EVOLVE_BATCH: int = 5
    ROLLBACK_THRESHOLD: float = 0.5
    EMBED_MODEL: str = "BAAI/bge-m3"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def use_openai(self) -> bool:
        return self.LLM_PROVIDER == "openai" and bool(self.OPENAI_API_KEY)

    @property
    def mock_exam_data_path(self) -> Path:
        return Path(self.MOCK_EXAM_DATA_DIR)


@lru_cache
def get_settings() -> Settings:
    return Settings()
