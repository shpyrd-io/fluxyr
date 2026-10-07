"""Consumer smoke test: run with an installed wheel from outside the checkout."""

import tempfile
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from fluxyr import Fluxyr, __version__
from fluxyr.config import Settings
from fluxyr.runtime.python_runner import PythonRunner

assert __version__ == version("fluxyr")
assert files("fluxyr").joinpath("static/index.html").is_file()
with tempfile.TemporaryDirectory() as directory:
    settings = Settings(root=Path(directory), database_url="sqlite://", testing=True)
    app = Fluxyr(__name__, settings=settings)
    assert "engine" not in app.extensions

    @app.tool()
    def greet(name: str) -> dict:
        """Greet a visitor."""
        return {"message": f"Hello, {name}!"}

    assert greet("Fluxyr")["message"] == "Hello, Fluxyr!"
    # The action's local helper must still win over the installed framework.
    settings.prepare()
    result = PythonRunner(settings, None).run(
        {
            "id": "wheel-smoke",
            "source": "from fluxyr import output, params\noutput({'value': params['value']})",
        },
        {"value": 42},
        "smoke",
    )
    assert result["success"] and result["output"] == {"value": 42}, result
print(f"Installed Fluxyr {__version__}: import, UI, decorator and Python action OK")
