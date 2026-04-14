"""本地开发入口：必须在 import app 之前完成 gevent monkey patch 与 psycopg 协作。

生产环境请使用 Gunicorn，例如 ``gunicorn -c gunicorn.conf.py wsgi:app``。
"""
from __future__ import annotations

from gevent import monkey

monkey.patch_all()

import psycogreen.gevent  # noqa: E402 — 须在 patch_all 之后

psycogreen.gevent.patch_psycopg()

from app import app  # noqa: E402
from config import settings  # noqa: E402


def main() -> None:
    debug = settings.flask_run_debug()
    log = app.logger
    log.info("Starting AI Assistant — http://%s:%s", settings.HOST, settings.PORT)
    from services.source_service import source_service

    log.info(
        "Sources config: %s — %d active",
        settings.SOURCES_CONFIG_PATH,
        source_service.count,
    )
    if not debug:
        log.warning("Running Flask dev server in production — use gunicorn instead")
    app.run(host=settings.HOST, port=settings.PORT, debug=debug)


if __name__ == '__main__':
    main()
