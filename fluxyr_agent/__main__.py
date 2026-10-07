"""Run one application process with an embedded execution supervisor."""

import argparse
import logging
import signal

from dotenv import load_dotenv

from .app import create_app
from .config import Settings


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Fluxyr Agent local engine")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize database and local directories, then exit",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = Settings()
    app = create_app(settings, start_worker=not args.init)
    if args.init:
        print("Database and local directories initialized.")
        return
    engine = app.extensions["engine"]

    def stop(*_):
        engine.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        from waitress import serve

        serve(app, host=settings.host, port=settings.port, threads=24)
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
