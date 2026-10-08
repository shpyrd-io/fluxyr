"""Retention only touches old temporary artifacts and sleeps between daily runs."""

import base64
import os
import re
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from fluxyr.runtime import tmp_cleanup
from fluxyr.runtime.tmp_cleanup import INTERVAL, RETENTION, TmpMaintenance, purge_tmp


def old_file(path, modified):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("temporary")
    os.utime(path, (modified, modified))
    return path


def test_retention_is_recursive_and_preserves_recent_and_persistent_files(make_app):
    _, e, _ = make_app()
    now = time.time()
    root = e.settings.data / "tmp"
    old = old_file(root / "browser/download/session/old.webp", now - RETENTION - 1)
    fresh = old_file(old.parent / "recent.png", now)
    boundary = old_file(root / "boundary.txt", now - RETENTION)
    other = old_file(root / "action/old.txt", now - RETENTION - 1)
    os.utime(other.parent, (now - RETENTION - 1,) * 2)
    permanent = old_file(e.settings.data / "keep.txt", now - RETENTION - 1)
    legacy = old_file(e.settings.data / "browser/old.webp", now - RETENTION - 1)
    result = purge_tmp(e.settings, now=now)
    assert result == {"files": 2, "directories": 1, "errors": 0}
    assert not old.exists() and not other.parent.exists()
    assert all(p.exists() for p in (root, fresh, boundary, permanent, legacy))


def test_retention_never_follows_symlinks_or_removes_special_files(make_app, tmp_path):
    _, e, _ = make_app()
    now = time.time()
    root = e.settings.data / "tmp"
    root.mkdir()
    external = old_file(tmp_path / "outside/important.txt", now - RETENTION - 1)
    (root / "linked-dir").symlink_to(external.parent, target_is_directory=True)
    (root / "linked-file").symlink_to(external)
    fifo = root / "pipe"
    os.mkfifo(fifo)
    result = purge_tmp(e.settings, now=now)
    assert result["files"] == 0 and external.exists() and fifo.exists()
    (root / "linked-dir").unlink()
    (root / "linked-file").unlink()
    fifo.unlink()
    root.rmdir()
    root.symlink_to(external.parent, target_is_directory=True)
    assert purge_tmp(e.settings, now=now)["errors"] == 1
    assert external.exists()


def test_cleanup_continues_after_permission_error_and_can_stop(make_app, monkeypatch):
    _, e, _ = make_app()
    now = time.time()
    root = e.settings.data / "tmp"
    a = old_file(root / "blocked.txt", now - RETENTION - 1)
    b = old_file(root / "remove.txt", now - RETENTION - 1)
    unlink = os.unlink

    def guarded(name, **kwargs):
        if name == "blocked.txt":
            raise PermissionError("Denied")
        return unlink(name, **kwargs)

    monkeypatch.setattr(os, "unlink", guarded)
    assert purge_tmp(e.settings, now=now, stopped=lambda: True)["files"] == 0
    assert a.exists() and b.exists()
    result = purge_tmp(e.settings, now=now)
    assert result["files"] == result["errors"] == 1
    assert a.exists() and not b.exists()


def test_daily_schedule_survives_restart_without_polling_database(
    make_app, monkeypatch
):
    _, e, _ = make_app()
    clock = [time.time()]
    calls = []
    monkeypatch.setattr(tmp_cleanup.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        tmp_cleanup, "purge_tmp", lambda *_a, **_k: calls.append(clock[0])
    )
    maintenance = TmpMaintenance(e.settings)
    maintenance.next_run = clock[0]

    class TwoDays:
        def __init__(self):
            self.delays = []

        def wait(self, delay):
            self.delays.append(delay)
            if len(self.delays) > 2:
                return True
            clock[0] += delay
            return False

        def is_set(self):
            return False

    stop = TwoDays()
    maintenance._run(stop)
    assert stop.delays == [0, INTERVAL, INTERVAL]
    assert calls[1] - calls[0] == INTERVAL
    assert float(maintenance.marker.read_text()) == calls[1]
    # A fresh worker reads the checkpoint and sleeps, rather than purging again.
    restarted = TmpMaintenance(e.settings)
    stopping = threading.Event()
    restarted.start(stopping)
    assert restarted.next_run == calls[1] + INTERVAL
    stopping.set()
    restarted.join()
    assert len(calls) == 2


def test_worker_starts_and_drains_maintenance(make_app, monkeypatch):
    _, e, _ = make_app()
    performed = threading.Event()
    monkeypatch.setattr(tmp_cleanup, "purge_tmp", lambda *_a, **_k: performed.set())
    e.start()
    try:
        assert performed.wait(3)
        thread = e.tmp_maintenance.thread
        e.tmp_maintenance.start(e.stopping)
        assert e.tmp_maintenance.thread is thread
    finally:
        e.stop()
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "mime,extension",
    [("image/png", "png"), ("image/webp", "webp"), ("image/jpeg", "jpg")],
)
def test_browser_artifacts_use_base62_uuid_and_keep_previews(
    make_app, monkeypatch, mime, extension
):
    from fluxyr import browser as module

    app, e, _ = make_app()
    identifier = uuid.UUID("abcdefab-cdef-4abc-9def-abcdefabcdef")
    monkeypatch.setattr(module.uuid, "uuid4", lambda: identifier)
    from io import BytesIO

    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (2, 2)).save(
        image, format={"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[extension]
    )
    encoded = base64.b64encode(image.getvalue()).decode()
    driver = SimpleNamespace(
        call=lambda *_a, **_k: {
            "content": [{"type": "image", "mimeType": mime, "data": encoded}]
        }
    )
    monkeypatch.setattr(e.browsers, "_get", lambda *_: (driver, None, "generation"))
    result = e.browsers.call("session", "capture_image")
    block = result["content"][0]
    assert re.fullmatch(r"tmp/browser/[0-9A-Za-z]{22}\." + extension, block["path"])
    name = block["path"].split("/")[-1].split(".")[0]
    value = 0
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    for char in name:
        value = value * 62 + alphabet.index(char)
    assert value == identifier.int
    assert block["url"] == "/preview/" + block["path"]
    response = app.test_client().get(block["url"])
    assert response.status_code == 200 and response.mimetype == mime
    assert e.files.path(block["path"]).read_bytes() == image.getvalue()


def test_download_directories_are_temporary_base62(make_app, monkeypatch):
    from fluxyr import browser as module

    _, e, _ = make_app()
    e.browsers.enabled = True
    destinations = []

    def driver(runtime, profile, downloads):
        destinations.append(downloads)
        raise module.BrowserError("Test stops before starting Chrome")

    monkeypatch.setattr(module, "Driver", driver)
    with pytest.raises(module.BrowserError, match="Test stops"):
        e.browsers._get("session")
    assert destinations[0].parent == e.settings.data / "tmp/browser"
    assert re.fullmatch(r"[0-9A-Za-z]{22}", destinations[0].name)


def test_daily_maintenance_cleans_workspace_but_keeps_pending_jobs(make_app):
    from test_workspace_cleanup import work

    _, engine, _ = make_app()
    orphan = work(engine.settings.workspace, "old-orphan")
    queued = engine.store.enqueue("Preserve this workspace")
    protected = work(engine.settings.workspace, queued["id"])

    class Once:
        calls = 0

        def wait(self, seconds):
            self.calls += 1
            return self.calls > 1

        def is_set(self):
            return False

    engine.tmp_maintenance.next_run = time.time()
    engine.tmp_maintenance._run(Once())
    assert not orphan.exists()
    assert protected.exists()
