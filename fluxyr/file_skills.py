"""Read-only Markdown skills, independent of agent-authored database skills."""

from pathlib import Path

import yaml


def load_file_skills(settings):
    if not settings.skills_dir:
        return []
    root = Path(settings.skills_dir)
    root = (
        (settings.root / root).resolve() if not root.is_absolute() else root.resolve()
    )
    if not root.is_dir():
        raise ValueError(f"FLUXYR_SKILLS_DIR is not a directory: {root}")
    result = []
    names = set()
    for path in sorted(root.rglob("*.md")):
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Skill symlink escapes FLUXYR_SKILLS_DIR: {path.name}")
        raw = path.read_text(encoding="utf-8")
        if len(raw.encode()) > 256_000:
            raise ValueError(f"Skill exceeds 256 KB: {path.name}")
        metadata, body = {}, raw
        lines = raw.splitlines(keepends=True)
        if lines and lines[0].strip() == "---":
            end = next(
                (i for i in range(1, len(lines)) if lines[i].strip() == "---"), None
            )
            if end is None:
                raise ValueError(f"Unclosed YAML front matter: {path.name}")
            metadata = yaml.safe_load("".join(lines[1:end])) or {}
            if not isinstance(metadata, dict):
                raise ValueError(f"Skill metadata must be a mapping: {path.name}")
            body = "".join(lines[end + 1 :])
        relative = path.relative_to(root).as_posix()
        name = metadata.get("name", relative.removesuffix(".md"))
        description = metadata.get("description", "")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(description, str)
            or not body.strip()
        ):
            raise ValueError(f"Invalid or empty Markdown skill: {relative}")
        if name in names:
            raise ValueError(f"Duplicate file skill name: {name}")
        names.add(name)
        result.append(
            {
                "id": "file:" + relative,
                "name": name,
                "description": description,
                "instruction": body.strip(),
                "spec": "",
                "enabled": True,
                "tools": [],
                "source": "file",
                "source_path": relative,
                "readonly": True,
            }
        )
    return result
