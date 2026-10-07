"""Disposable UI smoke-test server. No production mock mode or provider keys."""

import os
from pathlib import Path

from conftest import ScriptedAdapter
from sqlalchemy import create_engine, text

from fluxyr.app import create_app
from fluxyr.config import Settings

url = os.environ["TEST_DATABASE_URL"]
with create_engine(url).begin() as conn:
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS ui_preview"))
import tempfile

from sqlalchemy.engine import make_url

preview_url = make_url(url).update_query_dict({"options": "-csearch_path=ui_preview"})
settings = Settings(
    database_url=preview_url.render_as_string(hide_password=False),
    root=Path(tempfile.mkdtemp(prefix="fluxyr-ui-")),
    testing=True,
)
app = create_app(
    settings,
    lambda: ScriptedAdapter(
        ["The local engine is working. Build a skill or create a routine to continue."]
    ),
    start_worker=True,
)
from waitress import serve

try:
    serve(app, host="127.0.0.1", port=5059, threads=16)
finally:
    app.extensions["engine"].stop()
