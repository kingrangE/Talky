from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    LLM_PROVIDER: Literal["ollama", "openai"] = "ollama"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    MODEL_NAME: str = "skt/A.X-4.0-Light"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"
    LANGFUSE_PROMPT_LABEL: str = "production"
    LANGFUSE_PROMPT_VERSIONS: dict[str, Annotated[int, Field(gt=0)]] = Field(default_factory=dict)
    LANGFUSE_RELEASE: str = ""
    LLM_TIMEOUT_SECONDS: Annotated[int, Field(ge=1, le=300)] = 60
    LLM_MAX_OUTPUT_TOKENS: Annotated[int, Field(ge=128, le=8192)] = 4096
    LLM_MAX_INPUT_BYTES: Annotated[int, Field(ge=1024, le=128000)] = 24000
    MAX_USER_TEXT_CHARS: Annotated[int, Field(ge=1, le=10000)] = 4000
    MAX_HISTORY_BYTES: Annotated[int, Field(ge=1024, le=32000)] = 12000
    MAX_AUDIO_BYTES: Annotated[int, Field(ge=1024, le=20000000)] = 5000000
    MAX_AUDIO_SECONDS: Annotated[int, Field(ge=1, le=120)] = 90
    INFERENCE_CAPACITY: Annotated[int, Field(ge=1, le=16)] = 1
    CONVERSATION_QUEUE_LIMIT: Annotated[int, Field(ge=1, le=10000)] = 32
    CONVERSATION_QUEUE_WAIT_SECONDS: Annotated[int, Field(ge=1, le=3600)] = 300

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
    MOCK_EXAM_JOB_TIMEOUT_SECONDS: Annotated[int, Field(ge=1, le=600)] = 180
    MOCK_EXAM_MAX_ATTEMPTS: Annotated[int, Field(ge=1, le=5)] = 3
    MOCK_EXAM_CLEANUP_INTERVAL_SECONDS: Annotated[int, Field(ge=1)] = 60
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
