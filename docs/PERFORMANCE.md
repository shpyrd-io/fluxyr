# Runtime benchmark — 2026-10-07

Checkpoint before measurement: `793e075`. Measurements are local macOS / Python 3.11.6, with PostgreSQL. `scripts/benchmark_runtime.py` creates its own disposable schema and filesystem directory, then removes them. It never loads the instance environment or calls a model. This measures application overhead, not model quality, provider latency, or a real agent task.

```sh
TEST_DATABASE_URL='postgresql+psycopg2://fluxyr_test@127.0.0.1:55439/postgres' \
  .venv/bin/python scripts/benchmark_runtime.py \
  --output .runtime/benchmarks/result.json
```

The fixture contains 500 completed jobs with 64 KiB of context/snapshot per job, 2,000 usage events and 20,000 stream events. Request timings are medians of five runs without tracing; memory is measured separately with tracemalloc. CPU milliseconds measure this Python process. Idle CPU is relative to one core, sampled for 15 seconds. PostgreSQL and browser processes are excluded.

| Operation | Before wall ms | After wall ms | Before CPU ms | After CPU ms | Before peak Python MiB | After peak Python MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Usage aggregation | 81.28 | 19.00 | 71.56 | 13.71 | 36.75 | 1.96 |
| Jobs HTTP list | 35.81 | 2.36 | 30.10 | 1.88 | 13.11 | 0.75 |
| Replay 300 events | 2.67 | 4.35 | 2.12 | 2.10 | 0.74 | 0.74 |

Persisting 500 actual stream fragments took 171.9 ms / 100.1 ms CPU before and 187.3 ms / 103.5 ms CPU after. This path was not changed; these short samples show no improvement there. No fabricated provider stream was used to claim model behavior.

Empty-instance background polling fell from 14.20 to 8.73 SQL statements/second. Observed CPU was 1.67% before and 1.48% after. These are short, single samples on a shared machine, not a statistically established CPU reduction. Reducing the number of queries is the direct, repeatable change. Supervisor dispatch and build dependency checks retain their 350 ms cadence; cron checks run about once per second and background memory extraction polls once per second. All waits remain interruptible on shutdown.

## What changed

Usage aggregation now selects only the parent identifiers from jobs instead of deserializing every brain and snapshot. It iterates projected usage rows in batches of 256 instead of creating an ORM object list. Job lists select public metadata and only the pending-tools JSON fragment for waiting jobs. Pending human interactions retain their existing response contract. Totals, deduplication and child-to-parent attribution remain covered by tests.

The usage response still grows with the number of model calls. Large histories may eventually justify incremental aggregation, an index for event type/ID, and scoped or paginated usage responses. These were not added without a corresponding larger-history benchmark.

## Garbage collection and resident memory

After releasing the usage result in the baseline tracing run, approximately 0.69 MiB remained traced; a full collection reduced this to under 0.001 MiB. That collection took 22.7 ms and changed RSS only from 376.23 to 376.19 MiB. After optimization, collection took 17.5 ms and did not change RSS. Other measured paths behaved similarly. No forced collections or microsecond sleeps were added to request/stream paths. Avoiding unnecessary allocation was substantially more useful here.

The benchmark seed operation allocates large driver buffers, so its process RSS is not a clean service footprint measurement. RSS also includes native libraries and allocator retention; tracemalloc only tracks Python allocations. The short run cannot establish the absence of slow leaks.

Separately, the running local server with existing data and open UI tabs was sampled for 15 seconds: before restart, 11.38% CPU and 265.59→234.33 MiB RSS; after applying the changes, restarting, and warming jobs/usage endpoints, 2.66% CPU and 73.25→66.48 MiB RSS. This is an observational check, not a controlled RSS comparison: restart age, allocator history and browser traffic differ. It must not be presented as proof of a 75% total memory reduction. UI rendering and PostgreSQL memory were not profiled independently.

## Validation

80 backend tests passed against disposable PostgreSQL schemas, including projection tests that preserve pending human interactions and nested builder usage attribution while preventing context reads. 17 frontend tests passed. The updated local server reports a healthy worker, and the UI loads its saved routine unchanged after draft edits were cancelled.

Raw local reports: `.runtime/benchmarks/before.json`, `after.json`, and `live-after.json` (ignored by Git).
