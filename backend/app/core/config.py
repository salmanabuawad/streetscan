from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Buqata StreetScan"
    api_prefix: str = "/api"
    database_url: str
    upload_dir: str = "./uploads"
    cors_origins: str = "http://localhost:5173"

    # AI worker
    model_path: str = "yolo11n.pt"          # swap for custom municipal-asset weights
    detection_confidence: float = 0.35
    frame_stride_s: float = 1.0             # analyse one frame per second
    worker_poll_s: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

settings = Settings()
