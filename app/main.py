from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import init_db
from app.logging_setup import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    logger.info("Startar notarkiv ({})", settings.app_env)
    init_db()
    yield
    logger.info("Stoppar notarkiv")


app = FastAPI(
    title="Notarkiv",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.app_env == "production",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return """
    <!doctype html>
    <html lang="sv">
    <head><meta charset="utf-8"><title>Notarkiv</title></head>
    <body>
      <h1>Notarkiv</h1>
      <p>Skelettet kör. UI byggs i nästa steg.</p>
      <p><a href="/healthz">/healthz</a></p>
    </body>
    </html>
    """
