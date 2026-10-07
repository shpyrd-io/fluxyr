"""Opt-in wheel consumer validation in a separate venv/project, real MiniMax.

Requires an already-installed wheel in --python. Creates an isolated PostgreSQL
schema and a temporary app project; only exported evidence remains in the repo.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--python", required=True)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to authorize provider requests")
    repo = Path(__file__).resolve().parents[1]
    load_dotenv(repo / ".env")
    run = "framework_" + uuid.uuid4().hex[:12]
    root = Path(tempfile.mkdtemp(prefix=run + "_"))
    evidence = repo / ".runtime/live-validation" / run
    evidence.mkdir(parents=True)
    admin = create_engine(os.environ["DATABASE_URL"])
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{run}"'))
    url = make_url(os.environ["DATABASE_URL"]).update_query_dict(
        {"options": f"-csearch_path={run}"}
    )
    shutil.copyfile(repo / "examples/minimal/app.py", root / "app.py")
    shutil.copytree(repo / "examples/minimal/skills", root / "skills")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FLUXYR_")
        and k not in ("PYTHONPATH", "DATABASE_URL", "WERKZEUG_RUN_MAIN")
    }
    env.update(
        DATABASE_URL=url.render_as_string(hide_password=False),
        FLUXYR_PROVIDER="openrouter",
        FLUXYR_MODEL="minimax/minimax-m3",
        FLUXYR_SKILLS_DIR="skills",
        PORT="5059",
    )
    origin = subprocess.check_output(
        [args.python, "-c", "import fluxyr; print(fluxyr.__file__)"],
        cwd=root,
        env=env,
        text=True,
    ).strip()
    assert str(repo) not in origin and "site-packages" in origin, origin
    log = (evidence / "server.log").open("w")
    proc = subprocess.Popen(
        [args.python, "-m", "fluxyr", "--app", "app:app"],
        cwd=root,
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    print(json.dumps({"run": run, "project": str(root), "module": origin}), flush=True)

    def request(path, data=None):
        req = urllib.request.Request(
            "http://127.0.0.1:5059" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    try:
        for _ in range(100):
            try:
                if request("/api/health")["worker"]:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Consumer failed to start")
        endpoint = request("/api/example/time")
        skills = request("/api/skills")
        assert skills[0]["id"] == "file:server-time.md" and skills[0]["readonly"]
        config = request("/api/settings")
        assert config["managed_by"] == "environment"
        with urllib.request.urlopen("http://127.0.0.1:5059/") as response:
            assert b"<html" in response.read()
        job = request(
            "/api/sessions/00000000-0000-0000-0000-000000000001/messages",
            {
                "content": "Qual é o horário atual exato do servidor do banco de dados? Consulte e me informe."
            },
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            jobs = request("/api/jobs")
            current = next(j for j in jobs if j["id"] == job["id"])
            if current["status"] in ("succeeded", "failed", "waiting", "cancelled"):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Consumer model request timed out")
        with admin.connect() as db:
            events = [
                dict(r)
                for r in db.execute(
                    text(
                        f'SELECT id,type,payload,created_at FROM "{run}".events WHERE job_id=:id ORDER BY id'
                    ),
                    {"id": job["id"]},
                ).mappings()
            ]
            messages = [
                dict(r)
                for r in db.execute(
                    text(f'SELECT role,content FROM "{run}".messages WHERE job_id=:id'),
                    {"id": job["id"]},
                ).mappings()
            ]
        native = [
            ev
            for ev in events
            if ev["type"] == "tool_end"
            and ev["payload"].get("tool_name") == "server_time"
        ]
        report = dict(
            mock_model=False,
            module=origin,
            project=str(root),
            job=current,
            endpoint=endpoint,
            skills=skills,
            events=events,
            messages=messages,
        )
        (evidence / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str)
        )
        assert (
            current["status"] == "succeeded"
            and native
            and native[-1]["payload"]["result"]["success"]
        ), current
        print(
            json.dumps(
                {
                    "status": "passed",
                    "job_id": job["id"],
                    "native_result": native[-1]["payload"]["result"],
                    "report": str(evidence / "report.json"),
                }
            ),
            flush=True,
        )
        if args.serve:
            print("UI on 5059; touch evidence/stop to exit", flush=True)
            while not (evidence / "stop").exists():
                time.sleep(1)
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        log.close()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{run}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
