from __future__ import annotations

import os

import uvicorn

from app.config import ConfigStore
from app.logging_store import configure_logging
from app.web.app import create_app


def main() -> None:
    configure_logging()
    config_path = os.environ.get("XIAOAI_CONFIG", "config.json")
    store = ConfigStore(config_path)
    store.load()
    config = store.value
    app = create_app(store)
    uvicorn.run(
        app,
        host=config.web.host,
        port=config.web.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
