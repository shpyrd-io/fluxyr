"""Real Python/OS tests; no LLM calls or simulated Landlock results.

Set FLUXYR_TEST_REQUIRE_LANDLOCK=1 in Linux CI to fail rather than skip if
Landlock cannot actually be enforced by the current kernel/container policy.
"""

import os
import threading
import zipfile

import pytest


from fluxyr.config import Settings
from fluxyr.runtime.landlock import LandlockUnavailable, validate_support
from fluxyr.runtime.python_runner import PythonRunner

pytestmark = pytest.mark.integration


def supported(tmp_path):
    try:
        return validate_support([tmp_path])
    except LandlockUnavailable as exc:
        if os.getenv("FLUXYR_TEST_REQUIRE_LANDLOCK") == "1":
            pytest.fail(str(exc))
        pytest.skip(str(exc))


@pytest.fixture(params=["local", "landlock"])
def runner(request, tmp_path):
    mode = request.param
    if mode == "landlock":
        supported(tmp_path)
    settings = Settings(
        root=tmp_path / "instance", database_url="sqlite://", execution_mode=mode
    )
    settings.prepare()
    return PythonRunner(settings, None)


@pytest.fixture
def confined(tmp_path):
    supported(tmp_path)
    settings = Settings(
        root=tmp_path / "instance", database_url="sqlite://", execution_mode="landlock"
    )
    settings.prepare()
    return PythonRunner(settings, None)


def run(runner, source, params=None, **kwargs):
    return runner.run(
        {"id": "protection-test", "source": source}, params or {}, "job", **kwargs
    )


def test_environment_data_temp_unicode_and_child_process(runner, monkeypatch):
    monkeypatch.setenv("FLUXYR_TEST_ENV", "configuração herdada")
    source = """from fluxyr import output, params, data_dir
import os, pathlib, tempfile, subprocess, sys
work = pathlib.Path.cwd()
with tempfile.NamedTemporaryFile() as file:
    assert pathlib.Path(file.name).parent == work
folder = data_dir / 'criação'
folder.mkdir()
p = folder / 'original'
p.write_text('olá')
p.rename(folder / 'renamed')
assert (folder / 'renamed').read_text() == 'olá'
(folder / 'renamed').unlink()
folder.rmdir()
subprocess.run([sys.executable, '-c', "from pathlib import Path; Path('child.txt').write_text('child')"], check=True, stdout=subprocess.DEVNULL)
output({'env': os.environ['FLUXYR_TEST_ENV'], 'params': params, 'child': pathlib.Path('child.txt').read_text()})
"""
    result = run(runner, source, {"x": "ação"})
    assert result["success"], result
    assert result["output"] == {
        "env": "configuração herdada",
        "params": {"x": "ação"},
        "child": "child",
    }


@pytest.mark.parametrize(
    "operation",
    [
        "target.write_text('changed')",
        "target.unlink()",
        "os.truncate(target, 0)",
        "os.close(os.open(target, os.O_RDONLY | os.O_TRUNC))",
        "target.rename(Path.cwd() / 'stolen')",
        "Path('new.txt').write_text('new'); Path('new.txt').replace(target)",
        "target.parent.joinpath('new').mkdir()",
        "shutil.rmtree(target.parent)",
        "Path('link').symlink_to(target); Path('link').write_text('changed')",
        "os.link(target, 'hardlink')",
        "subprocess.run([sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(target)) + ').unlink()'], check=True)",
    ],
)
def test_kernel_denies_mutations_outside_allowed_roots(confined, tmp_path, operation):
    protected = tmp_path / "protected"
    protected.mkdir()
    target = protected / "sentinel"
    target.write_text(
        "intact"
    )  # Same UID and writable: denial must come from Landlock.
    source = f"""from fluxyr import params, output
import os, shutil, subprocess, sys
from pathlib import Path
target = Path(params['target'])
{operation}
output('unexpected success')
"""
    result = run(confined, source, {"target": str(target)})
    assert result["done"] is True and result["success"] is False, result
    assert (
        "Permission denied" in result["error"]
        or "Invalid cross-device link" in result["error"]
    ), result
    assert target.read_text() == "intact"
    assert sorted(p.name for p in protected.iterdir()) == ["sentinel"]
    # The parent process is still unrestricted.
    target.write_text("parent can still write")


def test_local_mode_is_an_explicit_unrestricted_choice(tmp_path):
    settings = Settings(
        root=tmp_path / "instance", database_url="sqlite://", execution_mode="local"
    )
    settings.prepare()
    target = tmp_path / "outside"
    result = run(
        PythonRunner(settings, None),
        "from fluxyr import params, output\nfrom pathlib import Path\nPath(params['path']).write_text('allowed')\noutput(True)",
        {"path": str(target)},
    )
    assert result["success"], result
    assert target.read_text() == "allowed"


def test_sibling_workspace_and_cached_environment_are_protected(confined):
    python = confined.environment([], version_id="protection-test")
    sibling = confined.settings.workspace / "another-job"
    sibling.mkdir()
    for folder in [sibling, python.parent]:
        result = run(
            confined,
            "from fluxyr import params, output\nfrom pathlib import Path\nPath(params['path']).write_text('unexpected')\noutput(True)",
            {"path": str(folder / "must-not-exist")},
        )
        assert result["success"] is False, result
        assert not (folder / "must-not-exist").exists()


def test_dependency_setup_cannot_write_data_or_other_environments(confined):
    import sys

    environment = confined.settings.runtime / "envs" / "setup-check"
    environment.mkdir(parents=True)
    target = confined.settings.data / "outside-installation"
    with pytest.raises(ValueError, match="Permission denied"):
        confined._install(
            confined._command(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path("
                    + repr(str(target))
                    + ").write_text('forbidden')",
                ],
                [environment],
            ),
            10,
            lambda: False,
        )
    assert not target.exists()


def test_pip_installs_declared_dependency_with_confined_setup(
    runner, tmp_path, monkeypatch
):
    # Build a tiny offline wheel to test the actual pip subprocess reproducibly.
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    with zipfile.ZipFile(
        wheels / "fluxyr_probe_pkg-1.0-py3-none-any.whl", "w"
    ) as wheel:
        wheel.writestr("fluxyr_probe_pkg.py", "VALUE = 'installed by pip'\n")
        wheel.writestr(
            "fluxyr_probe_pkg-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: fluxyr-probe-pkg\nVersion: 1.0\n",
        )
        wheel.writestr(
            "fluxyr_probe_pkg-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr("fluxyr_probe_pkg-1.0.dist-info/RECORD", "")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheels))
    result = runner.run(
        {
            "id": "dependency-probe",
            "dependencies": ["fluxyr-probe-pkg==1.0"],
            "source": "from fluxyr import output\nfrom fluxyr_probe_pkg import VALUE\noutput(VALUE)",
        },
        {},
        "job",
    )
    assert result["success"] and result["output"] == "installed by pip", result
    assert not list(runner.settings.runtime.glob("envs/*/.setup-*"))


def test_human_wait_and_continuation_preserve_protocol(runner):
    source = "from fluxyr import request_input, output\nanswer = request_input('Seu nome?', key='name')\noutput(answer['user_input'])"
    first = run(runner, source)
    assert first["done"] is False and first["success"] is None, first
    response = {
        "request": first["waiting"]["request"],
        "response": {"decision": "complete", "user_input": "Patrick"},
    }
    second = run(runner, source, continuation={"responses": [response]})
    assert second["success"] and second["output"] == "Patrick", second


def test_streaming_pause_and_cancel_keep_process_group(runner):
    class Control:
        state = None
        cancelled = False

        def __call__(self):
            return self.cancelled

        def control(self):
            return self.state

        def paused(self, value):
            if value:
                paused.set()

    control = Control()
    streamed = threading.Event()
    paused = threading.Event()
    results = []

    def emit(line):
        if line == "ready":
            control.state = "pause"
            streamed.set()

    def execute():
        results.append(
            run(
                runner,
                "import time\nprint('ready', flush=True)\ntime.sleep(60)",
                stop=control,
                emit=emit,
            )
        )

    thread = threading.Thread(target=execute)
    thread.start()
    try:
        assert streamed.wait(20)
        assert paused.wait(5)
    finally:
        control.cancelled = True
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert results[0]["success"] is False and "cancelled" in results[0]["error"], (
        results
    )


def test_timeout_is_still_enforced(runner):
    runner.settings.tool_timeout = 1
    result = run(runner, "import time\ntime.sleep(60)")
    assert result["success"] is False and "timed out" in result["error"], result


def test_invalid_mode_never_falls_back(tmp_path):
    with pytest.raises(ValueError, match="EXECUTION_MODE"):
        Settings(root=tmp_path, execution_mode="typo").prepare()


def test_unsupported_platform_fails_before_action(monkeypatch, tmp_path):
    from fluxyr.runtime import landlock

    monkeypatch.setattr(landlock.sys, "platform", "darwin")
    with pytest.raises(LandlockUnavailable, match="requires Linux"):
        landlock.restrict_writes([tmp_path])


def test_startup_rejects_unavailable_landlock(tmp_path, monkeypatch):
    from fluxyr.runtime import landlock

    def unavailable(_):
        raise LandlockUnavailable("test kernel lacks Landlock")

    monkeypatch.setattr(landlock, "validate_support", unavailable)
    with pytest.raises(LandlockUnavailable, match="test kernel"):
        Settings(
            root=tmp_path, database_url="sqlite://", execution_mode="landlock"
        ).prepare()
