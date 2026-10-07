"""Check version tags and the actual archives we publish (no runtime imports)."""

import argparse
import ast
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def version():
    tree = ast.parse((ROOT / "fluxyr/_version.py").read_text())
    return next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
    )


def check_names(names):
    for name in names:
        parts = Path(name).parts
        assert not {
            ".git",
            ".venv",
            "node_modules",
            "workspace",
            ".runtime",
            "data",
        }.intersection(parts), name
        assert not any(p.startswith(".env") and p != ".env.example" for p in parts), (
            name
        )
        assert not name.endswith((".pyc", ".pem", ".pfx", ".key", ".log")), name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    current = version()
    if args.tag and args.tag != f"v{current}":
        parser.error(f"Tag {args.tag!r} does not match v{current}")
    if args.artifacts:
        wheels = list(args.artifacts.glob("*.whl"))
        sources = list(args.artifacts.glob("*.tar.gz"))
        assert len(wheels) == len(sources) == 1, (
            "Expected exactly one wheel and one sdist"
        )
        with zipfile.ZipFile(wheels[0]) as wheel:
            names = wheel.namelist()
            check_names(names)
            assert "fluxyr/static/index.html" in names
            assert "fluxyr/prompts/vault.md" in names
            assert any(
                n.startswith("fluxyr/static/assets/") and n.endswith(".js")
                for n in names
            )
            metadata = BytesParser().parsebytes(
                wheel.read(next(n for n in names if n.endswith(".dist-info/METADATA")))
            )
            assert metadata["Name"] == "fluxyr"
            assert metadata["Version"] == current
            assert metadata["License-Expression"] == "MPL-2.0"
            assert any(n.endswith("/licenses/LICENSE") for n in names)
        with tarfile.open(sources[0]) as source:
            names = source.getnames()
            check_names(names)
            prefix = f"fluxyr-{current}/"
            for path in (
                "setup.py",
                "pyproject.toml",
                "fluxyr/_version.py",
                "fluxyr/static/index.html",
                "frontend/pnpm-lock.yaml",
                "frontend/pnpm-workspace.yaml",
                "LICENSE",
                "NOTICE",
            ):
                assert prefix + path in names, path
    print(f"Fluxyr {current}: release metadata OK")


if __name__ == "__main__":
    main()
