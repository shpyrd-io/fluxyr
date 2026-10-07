"""Development reloader extends Werkzeug's Python watcher with skill files."""

from pathlib import Path


def skill_state(root):
    if not root:
        return {}
    return {
        str(p): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in Path(root).rglob("*.md")
        if p.is_file()
    }


def register_reloader(skill_root):
    from werkzeug._reloader import StatReloaderLoop, reloader_loops

    class FluxyrReloader(StatReloaderLoop):
        def __enter__(self):
            self.skills = skill_state(skill_root)
            return super().__enter__()

        def run_step(self):
            super().run_step()
            current = skill_state(skill_root)
            if current != self.skills:
                changed = next(
                    p
                    for p in current.keys() | self.skills.keys()
                    if current.get(p) != self.skills.get(p)
                )
                self.trigger_reload(changed)
            self.skills = current

    reloader_loops["fluxyr"] = FluxyrReloader
