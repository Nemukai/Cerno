from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from cerno import __version__
from cerno.api.auth import router as auth_router
from cerno.api.chat import router as chat_router
from cerno.api.dashboards import router as dashboards_router
from cerno.api.sessions import router as sessions_router
from cerno.config import get_settings
from cerno.log_config import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    settings.validate_runtime_safety()
    app = FastAPI(
        title="Cerno",
        version=__version__,
        lifespan=lifespan,
        root_path=settings.api_root_path,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.frontend_origin.startswith("https://"),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list({settings.frontend_dev_url, settings.frontend_origin}),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(auth_router)
    app.include_router(sessions_router)
    app.include_router(dashboards_router)
    app.include_router(chat_router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "cerno.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
