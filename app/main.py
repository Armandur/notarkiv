from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import init_db
from app.logging_setup import setup_logging
from app.middleware import EnsureCSRFTokenMiddleware
from app.routes import auth as auth_routes
from app.routes import inventory as inventory_routes
from app.routes import loans as loans_routes
from app.routes import pages as pages_routes
from app.routes import people as people_routes
from app.routes import pieces as pieces_routes
from app.routes import scan as scan_routes
from app.routes import storage as storage_routes
from app.routes import tags as tags_routes
from app.routes.admin import settings as admin_settings_routes
from app.routes.admin import users as admin_users_routes
from app.tasks import close_pool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    logger.info("Startar notarkiv ({})", settings.app_env)
    init_db()
    yield
    await close_pool()
    logger.info("Stoppar notarkiv")


app = FastAPI(
    title="Notarkiv",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(EnsureCSRFTokenMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.app_env == "production",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/images", StaticFiles(directory=str(settings.images_path)), name="images")

app.include_router(pages_routes.router)
app.include_router(auth_routes.router)
app.include_router(storage_routes.router)
app.include_router(scan_routes.router)
app.include_router(pieces_routes.router)
app.include_router(people_routes.router)
app.include_router(inventory_routes.router)
app.include_router(loans_routes.router)
app.include_router(tags_routes.router)
app.include_router(admin_users_routes.router)
app.include_router(admin_settings_routes.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
