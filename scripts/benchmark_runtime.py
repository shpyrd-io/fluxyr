"""Repeatable local CPU/memory benchmark; synthetic data, no provider requests.

Requires TEST_DATABASE_URL pointing to a disposable PostgreSQL database. Creates
and drops only a unique benchmark schema. Never loads the instance .env.
"""

import argparse
import cProfile
import gc
import io
import json
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
import uuid

from sqlalchemy import create_engine, event, insert, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.database import MAIN_SESSION
from fluxyr.models import Event, Job
from fluxyr.activity import ActivityEmitter
from fluxyr.usage import usage_report


def rss():
    # ps reports current resident KiB on macOS/Linux (not max-ever RSS).
    return (
        int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())]))
        / 1024
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--idle-seconds", type=float, default=15)
    args = parser.parse_args()
    url = os.environ["TEST_DATABASE_URL"]
    assert url.startswith("postgresql"), "Use a disposable PostgreSQL database"
    schema = "bench_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    engine = None
    report = {
        "python": sys.version.split()[0],
        "schema": schema,
        "workload": {
            "jobs": 500,
            "context_bytes_per_job": 65536,
            "usage_events": 2000,
            "stream_events": 20000,
            "provider_calls": 0,
        },
    }
    try:
        with tempfile.TemporaryDirectory(prefix="fluxyr-bench-") as root:
            isolated_url = make_url(url).update_query_dict(
                {"options": f"-csearch_path={schema}"}
            )
            app = create_app(
                Settings(
                    database_url=isolated_url.render_as_string(hide_password=False),
                    root=Path(root),
                    testing=True,
                )
            )
            engine = app.extensions["engine"]
            client = app.test_client()
            queries = []

            def counted(*_):
                queries.append(1)

            event.listen(engine.db.engine, "before_cursor_execute", counted)
            engine.start()
            time.sleep(1)
            queries.clear()
            cpu, wall = time.process_time(), time.perf_counter()
            time.sleep(args.idle_seconds)
            elapsed = time.perf_counter() - wall
            report["idle"] = {
                "seconds": elapsed,
                "cpu_percent_one_core": 100 * (time.process_time() - cpu) / elapsed,
                "rss_mib": rss(),
                "sql_statements_per_second": len(queries) / elapsed,
            }
            engine.stop()
            event.remove(engine.db.engine, "before_cursor_execute", counted)
            ids = [str(uuid.uuid4()) for _ in range(500)]
            with engine.db.engine.begin() as c:
                c.execute(
                    insert(Job),
                    [
                        {
                            "id": jid,
                            "session_id": MAIN_SESSION,
                            "status": "succeeded",
                            "prompt": "Benchmark fixture",
                            "input": {"parent_job_id": ids[0]} if i else {},
                            "brain": {"fixture": "x" * 32768},
                            "snapshot": {"fixture": "y" * 32768},
                        }
                        for i, jid in enumerate(ids)
                    ],
                )
                c.execute(
                    insert(Event),
                    [
                        {
                            "session_id": MAIN_SESSION,
                            "job_id": ids[i % 500],
                            "type": "usage",
                            "payload": {
                                "model_call_id": str(i),
                                "request_id": str(i),
                                "provider": "fixture",
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "cost_usd": 0.001,
                                "available": True,
                            },
                        }
                        for i in range(2000)
                    ],
                )
                for batch in range(20):
                    c.execute(
                        insert(Event),
                        [
                            {
                                "session_id": MAIN_SESSION,
                                "job_id": ids[i % 500],
                                "type": "delta",
                                "payload": {"text": "x" * 100, "block_id": str(batch)},
                            }
                            for i in range(1000)
                        ],
                    )
            gc.collect()
            report["seeded_rss_mib"] = rss()

            def measure(name, fn):
                samples = []
                for _ in range(5):
                    cpu, wall = time.process_time(), time.perf_counter()
                    result = fn()
                    samples.append(
                        {
                            "wall_ms": 1000 * (time.perf_counter() - wall),
                            "cpu_ms": 1000 * (time.process_time() - cpu),
                        }
                    )
                    del result
                gc.collect()
                tracemalloc.start()
                result = fn()
                retained, peak = tracemalloc.get_traced_memory()
                del result
                del_result = tracemalloc.get_traced_memory()[0]
                rss_before_gc = rss()
                start = time.perf_counter()
                collected = gc.collect()
                gc_ms = 1000 * (time.perf_counter() - start)
                after_gc = tracemalloc.get_traced_memory()[0]
                tracemalloc.stop()
                report[name] = {
                    "median_wall_ms": statistics.median(s["wall_ms"] for s in samples),
                    "median_cpu_ms": statistics.median(s["cpu_ms"] for s in samples),
                    "python_peak_mib": peak / 2**20,
                    "python_result_mib": retained / 2**20,
                    "python_after_release_mib": del_result / 2**20,
                    "python_after_gc_mib": after_gc / 2**20,
                    "gc_objects": collected,
                    "gc_ms": gc_ms,
                    "rss_before_gc_mib": rss_before_gc,
                    "rss_after_gc_mib": rss(),
                }

            measure("usage", lambda: usage_report(engine.db))
            measure("jobs_http", lambda: client.get("/api/jobs").data)
            measure("event_replay", lambda: engine.store.events(MAIN_SESSION))
            job = {
                "id": ids[0],
                "session_id": MAIN_SESSION,
                "input": {},
                "prompt": "Benchmark stream",
            }
            emitter = ActivityEmitter(engine.store, job)
            cpu, wall = time.process_time(), time.perf_counter()
            for _ in range(500):
                emitter("delta", {"block_id": "benchmark", "text": "abc"})
            emitter.flush("main")
            report["stream_500_fragments"] = {
                "wall_ms": 1000 * (time.perf_counter() - wall),
                "cpu_ms": 1000 * (time.process_time() - cpu),
            }
            profile = cProfile.Profile()
            profile.runcall(usage_report, engine.db)
            out = io.StringIO()
            pstats.Stats(profile, stream=out).sort_stats("cumulative").print_stats(15)
            report["usage_profile"] = out.getvalue()
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k != "usage_profile"}, indent=2
                ),
                flush=True,
            )
    finally:
        if engine:
            engine.stop()
            engine.db.engine.dispose()
        with admin.begin() as c:
            c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


if __name__ == "__main__":
    main()
