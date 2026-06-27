from __future__ import annotations

import os

import uvicorn

from .config import settings


def main() -> None:
    ssl_certfile = os.environ.get("HANDEX_SSL_CERTFILE") or None
    ssl_keyfile = os.environ.get("HANDEX_SSL_KEYFILE") or None
    kwargs = {
        "host": settings.host,
        "port": settings.port,
        "log_level": os.environ.get("HANDEX_LOG_LEVEL", "info"),
    }
    if ssl_certfile and ssl_keyfile:
        kwargs["ssl_certfile"] = ssl_certfile
        kwargs["ssl_keyfile"] = ssl_keyfile
    uvicorn.run("handex.app:app", **kwargs)


if __name__ == "__main__":
    main()
