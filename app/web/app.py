from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ConfigStore
from app.runtime import RuntimeManager
from app.web.routes import build_api_router


def create_app(config_store: ConfigStore, runtime: RuntimeManager | None = None) -> FastAPI:
    runtime = runtime or RuntimeManager(config_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = config_store.value
        if not config.web.password_hash:
            logging.getLogger("xiaoai.setup").warning(
                "首次初始化令牌 bootstrap_token=%s", config.web.bootstrap_token
            )
        await runtime.start()
        yield
        await runtime.stop()

    app = FastAPI(title="xiaoai-llm", version="0.1.0", lifespan=lifespan)
    app.state.config_store = config_store
    app.state.runtime = runtime

    public, protected = build_api_router(config_store, runtime)
    app.include_router(public)
    app.include_router(protected)

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
