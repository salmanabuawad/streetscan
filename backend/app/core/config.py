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

    # Survey area (Buqata). A phone occasionally reports a self-consistent GPS
    # fix from an old almanac hundreds of km away; such points must never draw a
    # track line or geolocate an asset. Matches the frontend map maxBounds.
    survey_lat_min: float = 33.18
    survey_lat_max: float = 33.22
    survey_lng_min: float = 35.76
    survey_lng_max: float = 35.80

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def in_survey_area(self, lat: float | None, lng: float | None) -> bool:
        return (lat is not None and lng is not None
                and self.survey_lat_min <= lat <= self.survey_lat_max
                and self.survey_lng_min <= lng <= self.survey_lng_max)

settings = Settings()
