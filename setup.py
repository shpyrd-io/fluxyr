"""Keep distributable wheels self-contained; editable backend development is allowed."""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithUI(build_py):
    def run(self):
        if (
            not self.editable_mode
            and not Path("fluxyr/static/index.html").is_file()
        ):
            raise RuntimeError(
                "Build the bundled UI first: pnpm --dir frontend install --frozen-lockfile && pnpm --dir frontend build"
            )
        super().run()


setup(cmdclass={"build_py": BuildWithUI})
