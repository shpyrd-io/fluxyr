"""Startup retention for disposable Python workdirs; never follow symlinks."""

import logging
import os
import shutil
import stat
import time

from sqlalchemy import or_, select

from ..models import Job
from ..store import TERMINAL

log = logging.getLogger(__name__)


def _old_tree(path, cutoff):
    """Keep the whole entry if anything inside changed recently or is mounted."""
    info = path.lstat()
    if info.st_mtime >= cutoff:
        return False
    if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
        return True
    if not stat.S_ISDIR(info.st_mode) or path.is_mount():
        return False
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if info.st_mtime >= cutoff:
                    return False
                if stat.S_ISDIR(info.st_mode):
                    if os.path.ismount(entry.path):
                        return False
                    pending.append(entry.path)
                elif not (stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    return False
    return True


def purge_workspace(settings, db, *, now=None):
    """Called by the owning worker before dispatch; DB history/data/envs stay intact.

    Age is based on the newest mtime in each tree AND the job finish timestamp.
    Pending jobs are protected even if their lease expired. Legacy action tests
    share workspace/tests, so keep them all while any job remains pending.
    """
    report = {"removed": 0, "kept": 0, "errors": 0}
    days = settings.workspace_retention_days
    root = settings.workspace
    if days == 0:
        return report
    if root.is_symlink():
        log.warning("Workspace cleanup skipped: workspace is a symlink")
        return {**report, "errors": 1}
    cutoff = (time.time() if now is None else now) - days * 86400
    # Read identifiers only, never materialize persisted agent contexts.
    with db.transaction() as session:
        protected = set(
            session.scalars(
                select(Job.id).where(
                    or_(
                        Job.status.not_in(TERMINAL),
                        Job.finished_at.is_(None),
                        Job.finished_at >= cutoff,
                    )
                )
            )
        )
        pending = session.scalar(
            select(Job.id).where(Job.status.not_in(TERMINAL)).limit(1)
        )

    def remove_old(path):
        try:
            if not _old_tree(path, cutoff):
                report["kept"] += 1
                return
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
            report["removed"] += 1
        except OSError:
            report["errors"] += 1
            log.warning("Could not clean workspace entry %s", path.name, exc_info=True)

    try:
        for entry in root.iterdir():
            if entry.name in protected or (entry.name == "tests" and pending):
                report["kept"] += 1
            elif entry.name == "tests" and entry.is_dir() and not entry.is_symlink():
                # Prune old test invocations individually; one recent test must not
                # retain every historical test in the shared container directory.
                if entry.is_mount():
                    report["kept"] += 1
                    continue
                for invocation in entry.iterdir():
                    remove_old(invocation)
            else:
                remove_old(entry)
    except OSError:
        report["errors"] += 1
        log.warning("Workspace cleanup could not finish", exc_info=True)
    log.info("Workspace retention (%s days): %s", days, report)
    return report
