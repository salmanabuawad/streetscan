from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import router
from app.api.hazards_routes import router as hazards_router
from app.db.session import Base, engine
from app.models import hazards as _hazards  # noqa: F401 — register hazard tables
from app import migrate as _migrate

Base.metadata.create_all(bind=engine)   # create any missing tables
_migrate.run()                          # then apply ordered column migrations

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_prefix)
app.include_router(hazards_router, prefix=settings.api_prefix)
