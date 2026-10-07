"""Unauthenticated, single-instance web API and event replay."""

import json
import time
from pathlib import Path

from flask import (
    Blueprint,
    Flask,
    Response,
    jsonify,
    request,
    send_file,
    send_from_directory,
)
from jsonschema import ValidationError
from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from ._version import __version__
from .config import Settings
from .database import MAIN_SESSION, row_dict
from .engine import Engine
from .models import Configuration, Job, MemoryTask, Message, Session, VaultItem


def create_app(settings=None, adapter_factory=None, start_worker=False, *, app=None):
    settings = settings or Settings()
    engine = Engine(settings, adapter_factory)
    app = app or Flask(
        __name__, static_folder="static", static_url_path="/assets-static"
    )
    engine.app = app
    engine.native_tools = getattr(app, "_native_tools", {})
    app.config.update(
        MAX_CONTENT_LENGTH=settings.max_content_length,
        BRAIN_TOOL_BATCH_MAX_WORKERS=settings.tool_workers,
    )
    routes = Blueprint("fluxyr", __name__)
    app.extensions["engine"] = engine
    # Fail before serving if app tools or file skills collide with engine definitions.
    from .tools.registry import Registry
    from .core.brain import SyntheticBrain

    with engine.db.transaction() as s:
        snapshot = engine.store.snapshot(s)
    registry = Registry(
        engine,
        {
            "id": "registration",
            "session_id": MAIN_SESSION,
            "input": {},
            "snapshot": snapshot,
        },
        lambda *_: None,
        lambda: False,
    )
    names = [
        t["name"] for t in SyntheticBrain(None, tools=registry.definitions)._all_tools()
    ]
    if len(names) != len(set(names)):
        raise ValueError("Application tool conflicts with an engine or memory tool")

    @routes.get("/api/usage")
    def usage():
        from .usage import usage_report

        return jsonify(usage_report(engine.db))

    @app.before_request
    def local_origin():
        # No authentication by design. Reject browser cross-origin writes to the local service.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            from urllib.parse import urlsplit

            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="Cross-origin requests are not allowed"), 403
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="Cross-site requests are not allowed"), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    @app.errorhandler(ValidationError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(IntegrityError)
    def conflict(exc):
        return jsonify(
            error="A record with this name or identifier already exists"
        ), 409

    @app.errorhandler(FileNotFoundError)
    def missing(exc):
        return jsonify(error="File not found"), 404

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("API request failed")
        return jsonify(error="Request failed. Check the server log for details."), 500

    @routes.get("/api/health")
    def health():
        return jsonify(
            status="ok",
            version=__version__,
            main_session=MAIN_SESSION,
            worker=bool(engine.thread and engine.thread.is_alive()),
        )

    @routes.get("/api/sessions")
    def sessions():
        with engine.db.transaction() as s:
            return jsonify(
                [
                    {
                        k: v
                        for k, v in row_dict(x).items()
                        if k not in ("brain", "memories")
                    }
                    for x in s.scalars(
                        select(Session).order_by(Session.created_at.desc())
                    )
                ]
            )

    @routes.get("/api/sessions/<sid>")
    def session_detail(sid):
        with engine.db.transaction() as s:
            session = s.get(Session, sid)
            if not session:
                raise ValueError("Session not found")
            return jsonify(
                session={
                    k: v
                    for k, v in row_dict(session).items()
                    if k not in ("brain", "memories")
                },
                messages=[
                    row_dict(m)
                    for m in s.scalars(
                        select(Message)
                        .where(Message.session_id == sid)
                        .order_by(Message.created_at)
                    )
                ],
                jobs=[
                    dict(j, pending=j["pending"] or [])
                    for j in s.execute(
                        public_job_query()
                        .where(Job.session_id == sid)
                        .order_by(Job.created_at)
                    ).mappings()
                ],
            )

    @routes.post("/api/sessions/<sid>/clear")
    def clear_context(sid):
        return jsonify(engine.store.clear_context(sid))

    @routes.get("/api/sessions/<sid>/memory")
    def session_memory(sid):
        with engine.db.transaction() as s:
            session = s.get(Session, sid)
            if not session:
                raise ValueError("Session not found")
            running = s.scalar(
                select(Job)
                .where(
                    Job.session_id == sid,
                    Job.status.in_(["running", "paused", "waiting", "building"]),
                )
                .order_by(Job.created_at)
                .limit(1)
            )
            state = (
                session.memories
                or (running.brain if running and running.brain else session.brain)
                or {}
            )
            return jsonify(
                {
                    **{
                        key: state.get(key, {})
                        for key in ("semantic", "episodic", "implicit")
                    },
                    "_extraction": [
                        {"id": t.id, "status": t.status, "attempts": t.attempts}
                        for t in s.scalars(
                            select(MemoryTask)
                            .where(MemoryTask.session_id == sid)
                            .order_by(MemoryTask.created_at.desc())
                            .limit(5)
                        )
                    ],
                }
            )

    @routes.post("/api/sessions/<sid>/messages")
    def message(sid):
        content = request.json.get("content", "").strip()
        if not content or len(content) > 100000:
            raise ValueError("Message must contain 1–100000 characters")
        return jsonify(public_job(engine.store.enqueue(content, sid))), 202

    @routes.get("/api/jobs")
    def jobs():
        with engine.db.transaction() as s:
            return jsonify(
                [
                    dict(j, pending=j["pending"] or [])
                    for j in s.execute(
                        public_job_query().order_by(Job.created_at.desc()).limit(200)
                    ).mappings()
                ]
            )

    @routes.get("/api/jobs/<jid>")
    def job_detail(jid):
        with engine.db.transaction() as s:
            job = s.get(Job, jid)
            if not job:
                raise ValueError("Job not found")
            return jsonify(public_job(job))

    @routes.get("/api/jobs/<jid>/build")
    def build_progress(jid):
        return jsonify(engine.builds.progress(jid))

    @routes.post("/api/jobs/<jid>/<action>")
    def control(jid, action):
        return jsonify(public_job(engine.store.control(jid, action)))

    @routes.post("/api/jobs/<jid>/decisions/<call_id>")
    def decision(jid, call_id):
        value = request.json
        if value.get("decision") not in ("approve", "reject", "complete"):
            raise ValueError("Invalid decision")
        if value.get("result") is not None and not isinstance(value["result"], dict):
            raise ValueError("Result must be an object")
        return jsonify(engine.store.decide(jid, call_id, value))

    @routes.post("/api/jobs/<jid>/vault/<request_id>")
    def vault_decision(jid, request_id):
        from .interactions.vault import save_credential

        return jsonify(save_credential(engine, jid, request_id, request.json))

    @routes.get("/api/events")
    def events():
        sid = request.args.get("session_id")
        after = int(
            request.headers.get("Last-Event-ID") or request.args.get("after", 0)
        )

        def generate():
            cursor = after
            # Polls use short transactions; no DB connection is held by an idle browser.
            while True:
                records = engine.store.events(sid, cursor)
                for event in records:
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                if not records:
                    yield ": heartbeat\n\n"
                    time.sleep(1)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @routes.get("/api/tools")
    def tools_catalogue():
        from .tools.registry import Registry
        from .core.brain import SyntheticBrain

        with engine.db.transaction() as s:
            snapshot = engine.store.snapshot(s)
        registry = Registry(
            engine,
            {
                "id": "catalogue",
                "session_id": MAIN_SESSION,
                "input": {},
                "snapshot": snapshot,
            },
            lambda *_: None,
            lambda: False,
        )
        from types import SimpleNamespace

        config = engine.db.model_config()
        adapter = SimpleNamespace(
            get_provider_name=lambda: config["provider"],
            get_model_name=lambda: config["model"],
        )
        brain = SyntheticBrain(adapter, tools=registry.definitions)
        registry.brain = brain
        return jsonify(
            [
                {
                    **{k: t[k] for k in ("name", "description", "parameters")},
                    "source": "application"
                    if t["name"] in engine.native_tools
                    else "engine",
                }
                for t in brain._all_tools()
            ]
        )

    @routes.get("/api/skills")
    def skills():
        return jsonify(engine.skills.list())

    @routes.post("/api/skills")
    def build_skill():
        return jsonify(engine.skills.build(**request.json)), 201

    @routes.get("/api/skills/<path:skill_id>")
    def get_skill(skill_id):
        return jsonify(engine.skills.get(skill_id))

    @routes.patch("/api/skills/<path:skill_id>")
    def update_skill(skill_id):
        return jsonify(engine.skills.update(skill_id, **request.json))

    @routes.delete("/api/skills/<path:skill_id>")
    def delete_skill(skill_id):
        return jsonify(engine.skills.delete(skill_id))

    @routes.post("/api/skills/<path:skill_id>/build")
    def generate_skill(skill_id):
        return jsonify(
            public_job(
                engine.builds.enqueue(
                    skill_id, action_id=(request.json or {}).get("action_id")
                )
            )
        ), 202

    @routes.post("/api/actions")
    def create_action():
        return jsonify(engine.skills.create(**request.json)), 201

    @routes.post("/api/actions/<vid>/test")
    def test_action(vid):
        engine.skills.version(vid)
        job = engine.store.enqueue(
            "Test Python action " + vid[:8],
            None,
            inputs={
                "tool_test": {
                    "version_id": vid,
                    "params": request.json.get("params", {}),
                }
            },
        )
        return jsonify(public_job(job)), 202

    @routes.post("/api/actions/<vid>/activate")
    def activate(vid):
        return jsonify(engine.skills.activate(vid))

    @routes.get("/api/routines")
    def routines():
        return jsonify(engine.routines.list())

    @routes.post("/api/routines/preview")
    def routine_preview():
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from .routines import next_occurrence

        data = request.get_json() or {}
        expression, timezone = data.get("cron", ""), data.get("timezone", "UTC")
        if not isinstance(expression, str) or not isinstance(timezone, str):
            raise ValueError("Cron and timezone must be text")
        after, occurrences = time.time(), []
        for _ in range(3):
            after = next_occurrence(expression, timezone, after)
            occurrences.append(
                datetime.fromtimestamp(after, ZoneInfo(timezone)).isoformat()
            )
        return jsonify(occurrences=occurrences)

    @routes.post("/api/routines")
    def create_routine():
        return jsonify(engine.routines.put(request.json)), 201

    @routes.patch("/api/routines/<rid>")
    def update_routine(rid):
        return jsonify(engine.routines.put(request.json, rid))

    @routes.post("/api/routines/<rid>/run")
    def run(rid):
        return jsonify(public_job(engine.routines.run(rid))), 202

    @routes.delete("/api/routines/<rid>")
    def delete_routine(rid):
        return jsonify(engine.routines.delete(rid))

    @routes.get("/api/vault")
    def vault():
        return jsonify(engine.vault.list(include_configuration=True))

    @routes.post("/api/vault")
    def vault_put():
        return jsonify(engine.vault.put(**request.json))

    @routes.patch("/api/vault/<vid>")
    def vault_update(vid):
        return jsonify(engine.vault.update(vid, **request.json))

    @routes.delete("/api/vault/<vid>")
    def vault_delete(vid):
        with engine.db.transaction() as s:
            item = s.get(VaultItem, vid)
            if item:
                s.delete(item)
        return jsonify(ok=True)

    @routes.post("/api/vault/oauth/start")
    def oauth_start():
        redirect = (
            request.json.get("redirect_uri")
            or request.url_root.rstrip("/") + "/api/vault/oauth/callback"
        )
        return jsonify(url=engine.vault.start_oauth(request.json["name"], redirect))

    @routes.get("/api/vault/oauth/callback")
    def oauth_callback():
        if request.args.get("error"):
            raise ValueError("OAuth authorization was declined")
        name = engine.vault.finish_oauth(request.args["state"], request.args["code"])
        return Response(
            "Connected. You may close this window and return to Fluxyr Agent.",
            mimetype="text/plain",
        )

    @routes.get("/api/files")
    def files():
        return jsonify(engine.files.list(request.args.get("path", ".")))

    @routes.get("/api/files/content")
    def read_file():
        return jsonify(engine.files.read(request.args["path"]))

    @routes.post("/api/files/content")
    def write_file():
        return jsonify(engine.files.write(**request.json))

    @routes.post("/api/files/operation")
    def file_operation():
        return jsonify(engine.files.mutate(**request.json))

    @routes.post("/api/files/upload")
    def upload():
        file = request.files["file"]
        path = engine.files.path(request.form.get("path") or file.filename)
        if path.exists():
            raise ValueError("File already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        file.save(path)
        return jsonify(path=str(path.relative_to(settings.data)))

    @routes.get("/preview/<path:name>")
    def preview(name):
        path = engine.files.path(name)
        response = send_file(path, conditional=True)
        response.headers["Content-Security-Policy"] = (
            "sandbox allow-scripts; default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline' 'self'; script-src 'unsafe-inline' 'self'; media-src 'self' blob:; font-src 'self'; connect-src 'none'; form-action 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @routes.get("/api/settings")
    def model_settings():
        config = engine.db.model_config()
        import os

        config["configured_keys"] = {
            p: bool(os.getenv(p.upper() + "_API_KEY"))
            for p in ("anthropic", "openai", "openrouter", "custom")
        }
        config["agent_name"] = engine.settings.agent_name
        config["execution_mode"] = engine.settings.execution_mode
        config["managed_by"] = "environment"
        return jsonify(config)

    @routes.post("/api/settings")
    def save_settings():
        return jsonify(
            error="Settings are managed by environment variables. Update .env and restart the application."
        ), 405

    @routes.get("/")
    @routes.get("/<path:path>")
    def frontend(path=""):
        if path.startswith("api/"):
            return jsonify(error="Unknown API route"), 404
        root = Path(app.static_folder)
        if path and (root / path).is_file():
            return send_from_directory(root, path)
        if (root / "index.html").exists():
            return send_from_directory(root, "index.html")
        return Response(
            "Frontend not built. Run npm install && npm run build in frontend/.",
            status=503,
            mimetype="text/plain",
        )

    app.register_blueprint(routes)
    seen_routes = {}
    for rule in app.url_map.iter_rules():
        methods = set(rule.methods) - {"OPTIONS"}
        if seen_routes.get(rule.rule, set()) & methods:
            raise ValueError(
                f"Application route conflicts with an engine endpoint: {rule.rule}"
            )
        seen_routes.setdefault(rule.rule, set()).update(methods)
    if start_worker:
        engine.start()
    return app


def public_job_query():
    # Polling lists need metadata and pending human requests, never full context.
    return select(
        *(
            c
            for c in Job.__table__.columns
            if c.name not in ("brain", "snapshot", "owner", "lease_until")
        ),
        case(
            (Job.status == "waiting", Job.brain["pending_tools"]),
            else_=None,
        ).label("pending"),
    )


def public_job(job):
    row = job if isinstance(job, dict) else row_dict(job)
    data = {
        k: v
        for k, v in row.items()
        if k not in ("brain", "snapshot", "owner", "lease_until")
    }
    data["pending"] = (
        (row.get("brain") or {}).get("pending_tools", [])
        if row["status"] == "waiting"
        else []
    )
    return data
