"""Daily retention for instance data/tmp, outside the dispatch/heartbeat loop."""

import errno
import logging
import math
import os
import stat
import threading
import time

log = logging.getLogger(__name__)
INTERVAL = 86400
RETENTION = 30 * INTERVAL


def purge_tmp(settings, *, now=None, stopped=lambda: False):
    """Delete old regular files without following symlinks or other filesystems.

    Directory-relative operations retain open parent descriptors throughout the
    walk, so a renamed directory/symlink cannot redirect deletions outside tmp.
    Empty old directories are pruned too; data/tmp itself is always retained.
    """
    root = settings.data / "tmp"
    cutoff = (time.time() if now is None else now) - RETENTION
    report = {"files": 0, "directories": 0, "errors": 0}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(root, flags)
    except FileNotFoundError:
        return report
    except OSError:
        log.warning(
            "Temporary cleanup skipped: data/tmp is not an accessible directory"
        )
        report["errors"] += 1
        return report
    device = os.fstat(fd).st_dev

    def walk(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                if stopped():
                    return
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                        os.unlink(entry.name, dir_fd=directory)
                        report["files"] += 1
                    elif stat.S_ISDIR(info.st_mode) and info.st_dev == device:
                        child = os.open(entry.name, flags, dir_fd=directory)
                        try:
                            current = os.fstat(child)
                            if (
                                current.st_dev != device
                                or current.st_ino != info.st_ino
                            ):
                                continue
                            walk(child)
                        finally:
                            os.close(child)
                        if info.st_mtime < cutoff and not stopped():
                            try:
                                os.rmdir(entry.name, dir_fd=directory)
                                report["directories"] += 1
                            except OSError as exc:
                                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                                    raise
                except FileNotFoundError:
                    pass  # Browser/action removed it first.
                except OSError:
                    report["errors"] += 1

    try:
        walk(fd)
    finally:
        os.close(fd)
    log.info("Temporary file retention (30 days): %s", report)
    return report


class TmpMaintenance:
    """One cancellable maintenance thread owned/drained with the worker fence."""

    def __init__(self, settings, db=None):
        self.settings = settings
        self.db = db
        self.thread = None
        self.next_run = None
        self.marker = settings.runtime / "tmp-cleanup-at"

    def start(self, stopping):
        if self.thread and self.thread.is_alive():
            return
        if self.next_run is None:
            now = time.time()
            try:
                last = float(self.marker.read_text())
                if not math.isfinite(last) or last < 0:
                    raise ValueError("Invalid cleanup time")
                self.next_run = min(last + INTERVAL, now + INTERVAL)
            except (OSError, ValueError):
                self.next_run = now
        self.thread = threading.Thread(
            target=self._run,
            args=(stopping,),
            name="temporary-file-retention",
            daemon=True,
        )
        self.thread.start()

    def _run(self, stopping):
        while not stopping.wait(max(0, self.next_run - time.time())):
            try:
                purge_tmp(self.settings, stopped=stopping.is_set)
                if self.db is not None and not stopping.is_set():
                    from .workspace_cleanup import purge_workspace

                    purge_workspace(self.settings, self.db)
            except Exception:
                # Maintenance failure must not restart healthy execution workers.
                log.exception("Temporary cleanup could not finish")
            if stopping.is_set():
                return
            completed = time.time()
            self.next_run = completed + INTERVAL
            try:
                pending = self.marker.with_suffix(".pending")
                pending.write_text(str(completed))
                pending.replace(self.marker)
            except OSError:
                log.warning("Could not save temporary cleanup schedule", exc_info=True)

    def join(self):
        if self.thread:
            self.thread.join()
