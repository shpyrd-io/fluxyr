"""Run a consumer application or the bundled Fluxyr workbench."""

import argparse
import logging
import os
import sys
from pathlib import Path

from ._version import __version__
from .framework import Fluxyr


def main():
    if sys.argv[1:2] == ["migrate-storage"]:
        from .storage import migration_cli

        migration_cli(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(
        description="Fluxyr Agent framework",
        epilog="Legacy storage: fluxyr migrate-storage --help",
    )
    parser.add_argument("--version", action="version", version=f"Fluxyr {__version__}")
    parser.add_argument(
        "--app",
        default=os.getenv("FLUXYR_APP"),
        help="Import target, e.g. app:app or app:create_app()",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize persistence without serving or starting workers",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Local development: restart on Python, skill Markdown and .env changes",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("FLUXYR_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.path.insert(0, str(Path.cwd()))
    if args.app:
        from flask.cli import ScriptInfo

        app = ScriptInfo(app_import_path=args.app).load_app()
        if not isinstance(app, Fluxyr):
            parser.error("--app must resolve to a Fluxyr application")
    else:
        app = Fluxyr("__main__", root_path=str(Path.cwd()))
    if args.init:
        try:
            app.initialize()
            print("Database and local directories initialized.")
        finally:
            app.close()
    elif args.reload:
        # Parent only imports/collects paths; only the reloader child starts the engine.
        from dotenv import dotenv_values
        from werkzeug._reloader import run_with_reloader

        project = Path.cwd()
        values = {**dotenv_values(project / ".env"), **os.environ}
        folder = values.get("FLUXYR_SKILLS_DIR")
        skill_root = project / Path(folder).expanduser() if folder else None
        extras = [str(project / ".env")]
        if skill_root:
            extras += [str(p) for p in skill_root.rglob("*.md")]
        from .development import register_reloader

        register_reloader(skill_root)
        try:
            run_with_reloader(app.run, extra_files=extras, reloader_type="fluxyr")
        finally:
            app.close()
    else:
        app.run()


if __name__ == "__main__":
    main()
