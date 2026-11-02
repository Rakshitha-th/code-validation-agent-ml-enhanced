from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "code-quality-service"
    host: str = "0.0.0.0"
    port: int = 8000
    analysis_timeout_seconds: int = 30
    max_files: int = 200
    max_file_size_bytes: int = 1_048_576
    default_tools: list[str] = ["ruff", "pylint", "radon", "mypy"]
    quality_gate_error_threshold: int = 0
    quality_gate_warning_threshold: int = 5
    quality_gate_complexity_threshold: int = 3
    quality_gate_min_score: int = 75

    class Config:
        env_prefix = "QUALITY_"


settings = Settings()
