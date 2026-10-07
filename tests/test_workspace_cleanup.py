"""Exercise retention on real directories, including pending jobs and symlinks."""

import os
import time

import pytest

from fluxyr.models import Job
from fluxyr.runtime.workspace_cleanup import purge_workspace

NOW = time.time()
OLD = NOW - 45 * 86400


def age(path, timestamp=OLD):
    if path.is_dir() and not path.is_symlink():
        for item in path.iterdir():
            age(item, timestamp)
    os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    return path


def work(root, name):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "action.py").write_text('print("temporary")')
    return age(directory)


def test_prunes_old_orphans_files_and_individual_tests_only(make_app):
    _, engine, _ = make_app()
    root = engine.settings.workspace
    old = work(root, "orphan-job")
    old_test = work(root, "tests/old-invocation")
    recent_test = work(root, "tests/recent-invocation")
    age(recent_test, NOW)
    recent_child = work(root, "old-parent/nested")
    # A parent's timestamp alone does not tell us whether its contents are old.
    (recent_child / "action.py").write_text("changed recently")
    age(root / "old-parent", OLD)
    os.utime(recent_child / "action.py", (NOW, NOW))
    loose = root / "old-output.txt"
    loose.write_text("temporary")
    age(loose)
    data = engine.settings.data / "keep.txt"
    data.write_text("persistent")
    age(data)
    cache = work(engine.settings.runtime, "envs/keep")
    result = purge_workspace(engine.settings, engine.db, now=NOW)
    assert result["removed"] == 3
    assert not old.exists() and not old_test.exists() and not loose.exists()
    assert recent_test.exists() and recent_child.exists()
    assert data.read_text() == "persistent" and cache.exists()


@pytest.mark.parametrize(
    "status", ["queued", "running", "waiting", "paused", "building"]
)
def test_pending_jobs_and_legacy_tests_are_protected(make_app, status):
    _, engine, _ = make_app()
    job = engine.store.enqueue("Keep my workspace")
    with engine.db.transaction() as db:
        db.get(Job, job["id"]).status = status
    directory = work(engine.settings.workspace, job["id"])
    old_test = work(engine.settings.workspace, "tests/legacy")
    result = purge_workspace(engine.settings, engine.db, now=NOW)
    assert result["removed"] == 0
    assert directory.exists() and old_test.exists()


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "interrupted"])
def test_terminal_jobs_require_old_finish_time_as_well_as_old_files(make_app, status):
    _, engine, _ = make_app()
    old = engine.store.enqueue("Finished long ago")
    new = engine.store.enqueue("Finished today")
    with engine.db.transaction() as db:
        for job, finished in [(old, OLD), (new, NOW)]:
            row = db.get(Job, job["id"])
            row.status = status
            row.finished_at = finished
    old_dir = work(engine.settings.workspace, old["id"])
    new_dir = work(engine.settings.workspace, new["id"])
    purge_workspace(engine.settings, engine.db, now=NOW)
    assert not old_dir.exists() and new_dir.exists()
    # Only temporary files were purged, not execution history.
    with engine.db.transaction() as db:
        assert db.get(Job, old["id"]).status == status


def test_never_follows_symlinks(make_app, tmp_path):
    _, engine, _ = make_app()
    outside = work(tmp_path, "outside")
    link = engine.settings.workspace / "old-link"
    link.symlink_to(outside, target_is_directory=True)
    age(link)
    nested = work(engine.settings.workspace, "nested-link")
    (nested / "external").symlink_to(outside, target_is_directory=True)
    age(nested)
    purge_workspace(engine.settings, engine.db, now=NOW)
    assert not link.is_symlink() and not nested.exists()
    assert (outside / "action.py").exists()


def test_symlink_workspace_and_disabled_cleanup_do_not_delete(make_app, tmp_path):
    _, engine, _ = make_app(workspace_retention_days=0)
    root = engine.settings.workspace
    old = work(root, "old")
    assert purge_workspace(engine.settings, engine.db, now=NOW)["removed"] == 0
    assert old.exists()
    old.rename(tmp_path / "outside")
    root.rmdir()
    root.symlink_to(tmp_path / "outside", target_is_directory=True)
    engine.settings.workspace_retention_days = 30
    assert purge_workspace(engine.settings, engine.db, now=NOW)["errors"] == 1
    assert (tmp_path / "outside/action.py").exists()


def test_read_error_keeps_entry_and_continues(make_app, monkeypatch):
    from fluxyr.runtime import workspace_cleanup

    _, engine, _ = make_app()
    blocked = work(engine.settings.workspace, "blocked")
    removable = work(engine.settings.workspace, "old")
    original = workspace_cleanup._old_tree

    def inspect(path, cutoff):
        if path.name == "blocked":
            raise PermissionError("Denied")
        return original(path, cutoff)

    monkeypatch.setattr(workspace_cleanup, "_old_tree", inspect)
    result = purge_workspace(engine.settings, engine.db, now=NOW)
    assert result["errors"] == 1 and blocked.exists() and not removable.exists()


def test_startup_cleans_before_dispatch_and_only_once(make_app, monkeypatch):
    _, engine, _ = make_app()
    old = work(engine.settings.workspace, "old")
    seen = []
    monkeypatch.setattr(engine, "_supervise", lambda: seen.append(old.exists()))
    monkeypatch.setattr(engine.memory_queue, "start", lambda: None)
    engine.start()
    engine.thread.join(timeout=2)
    engine.start()
    assert seen == [False]
