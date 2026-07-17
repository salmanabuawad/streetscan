from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Buqata StreetScan"
    api_prefix: str = "/api"
    database_url: str
    upload_dir: str = "./uploads"
    cors_origins: str = "http://localhost:5173"

    # Auth
    jwt_secret: str = "dev-secret-change-in-production"
    token_ttl_s: int = 12 * 3600

    # AI worker
    model_path: str = "yolo11n.pt"          # swap for custom municipal-asset weights
    detection_confidence: float = 0.35
    frame_stride_s: float = 1.0             # analyse one frame per second
    worker_poll_s: int = 10
    ocr_frame_stride_s: float = 1.5         # OCR one video frame per 1.5s
    ocr_blur_min: float = 90.0             # skip frames blurrier than this (Laplacian var)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

settings = Settings()
