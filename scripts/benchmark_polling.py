"""Synthetic, disposable benchmark. Never reads DATABASE_URL or calls a model.

Run: python scripts/benchmark_polling.py [--serve PORT]
The optional local UI fixture includes historical lifecycle events to exercise
replay without triggering refresh storms. Stop with Ctrl-C when finished.
"""

import argparse
import json
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event

from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.database import MAIN_SESSION
from fluxyr.models import Event, Job, Message
from fluxyr.runtime.interruptible import InterruptibleAdapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="fluxyr-polling-benchmark-") as directory:
        app = create_app(
            Settings(
                root=Path(directory),
                database_url="sqlite:///" + directory + "/benchmark.sqlite",
                testing=True,
            )
        )
        engine = app.extensions["engine"]
        try:
            engine.start()
            time.sleep(4)  # Let the idle backoff settle.
            counts = Counter()

            def capture(conn, cursor, statement, *unused):
                if statement.startswith(("SELECT", "UPDATE", "DELETE", "INSERT")):
                    counts["queries"] += 1
                    if "FROM jobs" in statement:
                        counts["job_reads"] += 1
                        counts["context_reads"] += int("jobs.brain" in statement)

            event.listen(engine.db.engine, "before_cursor_execute", capture)
            start = time.monotonic()
            time.sleep(10)
            idle = {"seconds": time.monotonic() - start, **counts}
            engine.stop()
            # Control benchmark only; no background worker is restarted.
            engine.stopping.clear()
            with engine.db.transaction() as s:
                jobs = []
                for i in range(40):
                    job = Job(
                        session_id=MAIN_SESSION,
                        status="succeeded",
                        prompt="Synthetic " + "p" * 10000,
                        input={"source": "s" * 64000},
                        outcome={"output": "r" * 64000},
                    )
                    s.add(job)
                    jobs.append(job)
                s.flush()
                for job in jobs:
                    s.add(
                        Event(
                            session_id=MAIN_SESSION,
                            job_id=job.id,
                            type="succeeded",
                            payload={},
                        )
                    )
                s.add(
                    Message(
                        session_id=MAIN_SESSION,
                        role="user",
                        content="Synthetic traffic benchmark",
                    )
                )
                for i in range(1000):
                    s.add(
                        Event(
                            session_id=MAIN_SESSION,
                            job_id=jobs[i % 40].id,
                            type="usage",
                            payload={
                                "model_call_id": f"request-{i}",
                                "input_tokens": 100,
                                "output_tokens": 10,
                                "cost_usd": 0.001,
                                "available": True,
                            },
                        )
                    )
                jid = jobs[0].id
                jobs[0].owner = engine.owner
                jobs[0].brain = {"synthetic": "x" * 688000}
            counts.clear()

            class SyntheticAdapter:
                def execute_step(self, stream_callback):
                    for _ in range(200):
                        stream_callback({"text": "."})
                        time.sleep(0.01)

            adapter = InterruptibleAdapter(
                SyntheticAdapter(),
                lambda: engine._control(jid),
                threading.BoundedSemaphore(1),
            )
            start = time.monotonic()
            adapter.execute_step(stream_callback=lambda *_: None)
            stream = {"seconds": time.monotonic() - start, "chunks": 200, **counts}

            @app.post("/__benchmark/burst")
            def burst():
                for _ in range(200):
                    engine.store.emit(MAIN_SESSION, jid, "succeeded", {})
                return {"events": 200}

            client = app.test_client()
            original = client.get("/api/jobs")
            compact = client.get("/api/jobs?summary=1")
            full_usage = client.get("/api/usage")
            cursor, pages = None, []
            while True:
                response = client.get(
                    f"/api/usage?session_id={MAIN_SESSION}"
                    + (f"&after={cursor}" if cursor is not None else "")
                )
                pages.append(len(response.data))
                cursor = response.json["cursor"]
                if not response.json.get("has_more"):
                    break
            unchanged = client.get(
                f"/api/usage?session_id={MAIN_SESSION}&after={cursor}"
            )
            print(
                json.dumps(
                    {
                        "idle": idle,
                        "stream": stream,
                        "http_bytes": {
                            "jobs_original": len(original.data),
                            "jobs_summary": len(compact.data),
                            "usage_original": len(full_usage.data),
                            "usage_pages": pages,
                            "usage_unchanged": len(unchanged.data),
                        },
                    },
                    indent=2,
                ),
                flush=True,
            )
            if args.serve:
                from waitress import serve

                print(f"Fixture: http://127.0.0.1:{args.serve}", flush=True)
                serve(app, host="127.0.0.1", port=args.serve, threads=8)
        finally:
            engine.stop()
            engine.db.engine.dispose()


if __name__ == "__main__":
    main()
