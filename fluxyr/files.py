"""Local ./data storage with explicit containment and optimistic edits."""

import hashlib
import os
import tempfile


class Files:
    def __init__(self, root):
        self.root = root.resolve()

    def path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Path must stay inside ./data")
        return path

    def list(self, path="."):
        directory = self.path(path)
        if not directory.is_dir():
            raise ValueError("Not a directory")
        result = []
        for p in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if not p.resolve().is_relative_to(self.root):
                continue
            result.append(
                {
                    "name": p.name,
                    "path": str(p.relative_to(self.root)),
                    "directory": p.is_dir(),
                    "size": p.stat().st_size,
                }
            )
        return result

    def read(self, path):
        p = self.path(path)
        if p.stat().st_size > 2_000_000:
            raise ValueError("Use a preview/download for files larger than 2 MB")
        data = p.read_bytes()
        return {
            "path": path,
            "content": data.decode("utf-8"),
            "etag": hashlib.sha256(data).hexdigest(),
        }

    def write(self, path, content, etag=None):
        from .runtime.python_runner import lock_for

        p = self.path(path)
        if p == self.root:
            raise ValueError("File path required")
        with lock_for(str(p)):
            if etag is not None and (
                not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != etag
            ):
                raise ValueError(
                    "File changed since it was read; read it again before editing"
                )
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=p.parent)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(content)
                os.replace(name, p)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return self.read(path)

    def mutate(self, operation, path, destination=None):
        p = self.path(path)
        if p == self.root:
            raise ValueError("Cannot modify the data root")
        if operation == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
        elif operation == "delete":
            if p.is_dir():
                p.rmdir()  # only empty directories; no accidental recursive deletion
            else:
                p.unlink()
        elif operation == "move":
            dest = self.path(destination)
            if dest.exists():
                raise ValueError("Destination already exists")
            dest.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dest)
        else:
            raise ValueError("Unknown file operation")
        return {"ok": True}
