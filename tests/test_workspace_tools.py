"""Actual file/shell processes, including optional real Linux Landlock checks."""

import base64
import copy
import io
import os
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from fluxyr.config import Settings
from fluxyr.core.adapters.anthropic import AnthropicAdapter
from fluxyr.core.adapters.openai_message_formatter import format_messages
from fluxyr.core.memory.conversation_cleaner import strip_file_attachments
from fluxyr.core.tools.tool_response import ToolResponse
from fluxyr.runtime.landlock import LandlockUnavailable, validate_support
from fluxyr.runtime.python_runner import PythonRunner, _locks
from fluxyr.tools.registry import Registry
from fluxyr.tools.workspace import WorkspaceTools


@pytest.fixture(params=["local", "landlock"])
def workspace(request, tmp_path):
    if request.param == "landlock":
        try:
            validate_support([tmp_path])
        except LandlockUnavailable as exc:
            if os.getenv("FLUXYR_TEST_REQUIRE_LANDLOCK") == "1":
                pytest.fail(str(exc))
            pytest.skip(str(exc))
    settings = Settings(
        root=tmp_path / "instance",
        database_url="sqlite://",
        execution_mode=request.param,
        tool_timeout=5,
    )
    settings.prepare()
    return WorkspaceTools(settings)


def file(workspace, operation, **args):
    return workspace.file(operation, args, "test-job")


def bash(workspace, command, **kwargs):
    return workspace.bash(command, "test-job", **kwargs)


def py(source):
    return shlex.quote(sys.executable) + " -c " + shlex.quote(source)


def test_paths_write_read_edit_and_action_helper_share_data(workspace):
    result = file(workspace, "write", path="nested/ação.txt", content="Olá\nOriginal\n")
    assert "error" not in result and "Original" not in result["content"]
    assert (
        file(workspace, "read", path="nested/ação.txt")["content"] == "Olá\nOriginal\n"
    )
    result = file(
        workspace,
        "edit",
        path="nested/ação.txt",
        edits=[{"oldText": "Original", "newText": "Alterado"}],
    )
    assert "error" not in result and "+Alterado" in result["diff"]
    assert bash(workspace, "cat nested/ação.txt")["content"] == "Olá\nAlterado\n"
    assert (
        file(workspace, "read", path=str(workspace.cwd / "nested/ação.txt"))["content"]
        == "Olá\nAlterado\n"
    )
    action = PythonRunner(workspace.settings, None).run(
        {
            "id": "shared-files",
            "source": "from fluxyr import data_dir, output\np=data_dir/'nested/ação.txt'\np.write_text(p.read_text()+'Action\\n')\noutput(str(p))",
        },
        {},
        "action-job",
    )
    assert action["success"], action
    assert file(workspace, "read", path="nested/ação.txt")["content"].endswith(
        "Action\n"
    )


def test_read_bounded_pagination_and_long_lines(workspace):
    path = workspace.cwd / "large.txt"
    path.write_text("".join(f"{n}: ação\n" for n in range(5000)))
    first = file(workspace, "read", path="large.txt")
    assert (
        first["truncated"]
        and first["next_offset"] == 2001
        and first["total_lines"] == 5000
    )
    assert file(workspace, "read", path="large.txt", offset=2001, limit=2)[
        "content"
    ].startswith("2000: ação\n2001: ação\n")
    assert "error" in file(workspace, "read", path="large.txt", offset=9000)
    path.write_text("ç" * 30000 + "\nnext\n")
    assert "exceeds 50 KiB" in file(workspace, "read", path="large.txt")["content"]
    assert file(workspace, "read", path="large.txt", offset=2)["content"] == "next\n"
    path.write_bytes(b"")
    assert file(workspace, "read", path="large.txt")["content"] == ""


def test_edit_atomic_multiple_fuzzy_bom_crlf_and_failures(workspace):
    path = workspace.cwd / "edit.txt"
    original = "\ufeffunchanged café  \r\nname = “Fluxyr”  \r\ncount = 1\r\n"
    path.write_bytes(original.encode())
    result = file(
        workspace,
        "edit",
        path="edit.txt",
        edits=[
            {"oldText": 'name = "Fluxyr"', "newText": 'name = "Agent"'},
            {"oldText": "count = 1", "newText": "count = 2"},
        ],
    )
    assert "error" not in result, result
    expected = '\ufeffunchanged café  \r\nname = "Agent"\r\ncount = 2\r\n'
    assert path.read_bytes() == expected.encode()
    for edits in [
        [
            {"oldText": "count = 2", "newText": "count = 3"},
            {"oldText": "missing", "newText": "bad"},
        ],
        [
            {"oldText": "name", "newText": "a"},
            {"oldText": 'name = "Agent"', "newText": "b"},
        ],
        [{"oldText": "count = 2", "newText": "count = 2"}],
        [{"oldText": "", "newText": "bad"}],
    ]:
        assert "error" in file(workspace, "edit", path="edit.txt", edits=edits)
        assert path.read_bytes() == expected.encode()
    path.write_text("same\nsame\n")
    assert (
        "occurrences"
        in file(
            workspace,
            "edit",
            path="edit.txt",
            edits=[{"oldText": "same", "newText": "different"}],
        )["error"]
    )
    assert path.read_text() == "same\nsame\n"


def test_bash_files_environment_and_fresh_shell(workspace, monkeypatch):
    monkeypatch.setenv("TOOL_TEST_ENV", "configuração")
    result = bash(
        workspace,
        'mkdir -p a/b; printf "$TOOL_TEST_ENV" > a/b/input; find a -type f; mv a/b/input a/renamed; cat a/renamed; rm a/renamed',
    )
    assert (
        result["exit_code"] == 0
        and "a/b/input" in result["content"]
        and "configuração" in result["content"]
    )
    assert bash(workspace, "cd a; pwd")["content"].strip() == str(workspace.cwd / "a")
    assert bash(workspace, "pwd")["content"].strip() == str(workspace.cwd)
    result = bash(workspace, "echo error >&2; exit 7")
    assert result["exit_code"] == 7 and result["error"] and "error" in result["content"]


def test_stream_without_newline_tail_spool_and_quota(workspace):
    chunks = []
    result = bash(
        workspace,
        py(
            "import time; print('first',end='',flush=True); time.sleep(.2); print('second',flush=True)"
        ),
        emit=chunks.append,
    )
    assert chunks[0] == "first" and result["content"] == "firstsecond\n"
    result = bash(workspace, py("for i in range(4000): print('line',i)"))
    assert (
        result["truncated"]
        and "line 3999" in result["content"]
        and "line 0\n" not in result["content"]
    )
    assert file(workspace, "read", path=result["full_output_path"], limit=1)[
        "content"
    ].startswith("line 0\n")
    workspace.settings.bash_max_output_bytes = 65536
    result = bash(workspace, py("print('x'*1000000)"))
    assert "quota" in result["error"] and result["exit_code"] != 0
    assert os.stat(result["full_output_path"]).st_size <= 65536


def test_timeout_cancel_and_closed_stdout(workspace):
    result = bash(workspace, py("import time; time.sleep(20)"), timeout=0.2)
    assert "timed out" in result["error"] and result["wall_time_seconds"] < 3
    stop = threading.Event()
    stop.set()
    assert (
        "before dispatch"
        in bash(workspace, "echo wrong > cancelled.txt", stop=stop.is_set)["error"]
    )
    assert not (workspace.cwd / "cancelled.txt").exists()
    stop.clear()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    try:
        result = bash(workspace, py("import time; time.sleep(20)"), stop=stop.is_set)
        assert "aborted" in result["error"] and result["wall_time_seconds"] < 3
    finally:
        timer.cancel()
    # Child closes stdout but keeps running: continue waiting and enforce timeout.
    result = bash(workspace, "exec 1>&- 2>&-; sleep 20", timeout=0.2)
    assert "timed out" in result["error"]
    # A background process keeping the pipe open cannot leave the tool hanging.
    start = time.monotonic()
    assert bash(workspace, "sleep 20 & echo done")["exit_code"] == 0
    assert time.monotonic() - start < 3


def test_pause_resume_freezes_child(workspace):
    paused, entered = threading.Event(), threading.Event()
    paused.set()

    def stop():
        return False

    stop.control = lambda: "pause" if paused.is_set() else None
    stop.paused = lambda value: entered.set() if value else None
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            bash,
            workspace,
            py("import time; time.sleep(.4); print('done')"),
            stop=stop,
            timeout=1,
        )
        assert entered.wait(2)
        time.sleep(1.1)
        assert not future.done()
        paused.clear()
        result = future.result(3)
    assert result["exit_code"] == 0 and result["content"] == "done\n"


def test_image_real_file_and_both_provider_payloads(workspace):
    path = workspace.cwd / "picture.png"
    Image.new("RGB", (2400, 1200), "#00FF00").save(path)
    result = file(workspace, "read", path="picture.png")
    assert "error" not in result, result
    image = result["image"]
    assert Image.open(io.BytesIO(base64.b64decode(image["image_data"]))).size == (
        2000,
        1000,
    )
    responses = [
        ToolResponse("read", "a", result).to_message(),
        ToolResponse("read", "b", {"content": "text"}).to_message(),
    ]
    assert image["image_data"] not in responses[0]["content"]
    messages = [
        {
            "role": "assistant",
            "function_calls": [
                {"call_id": "a", "name": "read", "arguments": {"path": "picture.png"}},
                {"call_id": "b", "name": "read", "arguments": {"path": "text.txt"}},
            ],
        },
        *responses,
        {"role": "assistant", "content": "answer"},
    ]
    wire = format_messages(messages)
    assert [m["role"] for m in wire] == [
        "assistant",
        "tool",
        "tool",
        "user",
        "assistant",
    ]
    assert wire[3]["content"][1]["type"] == "image_url"
    adapter = AnthropicAdapter(api_key="unit-test-key")
    wire = adapter.format_messages(messages)
    assert wire[1]["content"][0]["content"][1]["source"]["data"] == image["image_data"]
    assert len(wire[1]["content"]) == 2
    assert "attachments" not in strip_file_attachments(copy.deepcopy(responses))[0]


def test_local_vs_landlock_outside_writes(workspace, tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("original")
    assert file(workspace, "read", path=str(target))["content"] == "original"
    for operation, args in [
        ("write", {"path": str(target), "content": "changed"}),
        (
            "edit",
            {
                "path": str(target),
                "edits": [{"oldText": "original", "newText": "changed"}],
            },
        ),
        ("bash", {"command": f"echo changed > {shlex.quote(str(target))}"}),
    ]:
        result = (
            bash(workspace, **args)
            if operation == "bash"
            else file(workspace, operation, **args)
        )
        if workspace.settings.execution_mode == "landlock":
            assert "error" in result, result
            assert target.read_text() == "original"
        else:
            assert "error" not in result, result
            assert target.read_text().strip() == "changed"
            target.write_text("original")
    (workspace.cwd / "escape").symlink_to(target)
    result = file(workspace, "write", path="escape", content="changed")
    assert ("error" in result) == (workspace.settings.execution_mode == "landlock")


def test_same_file_mutations_serialize_and_lock_entries_expire(workspace):
    file(workspace, "write", path="count.txt", content="A B")
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda token: file(
                    workspace,
                    "edit",
                    path="count.txt",
                    edits=[{"oldText": token, "newText": token.lower()}],
                ),
                ["A", "B"],
            )
        )
    assert all("error" not in result for result in results), results
    assert file(workspace, "read", path="count.txt")["content"] == "a b"
    assert str(workspace.cwd / "count.txt") not in _locks


def test_registry_has_four_operations_for_workbench_and_builder_and_api_paths(make_app):
    app, engine, _ = make_app()
    engine.store.enqueue("Inspect files")
    job = engine.store.claim(engine.owner)
    registry = Registry(engine, job, lambda *_: None, lambda: False)
    for builder in (False, True):
        job["input"]["builder"] = builder
        names = {tool["name"] for tool in registry.definitions()}
        assert {"read", "write", "edit", "bash"} <= names
        assert not names & {
            "read_file",
            "write_file",
            "list_files",
            "search_files",
            "file_operation",
            "mutate_file",
        }
    result = engine.workspace_tools.file(
        "write", {"path": "view.html", "content": "<h1>ação</h1>"}, job["id"]
    )
    assert "error" not in result
    client = app.test_client()
    assert (
        client.get("/api/files/content?path=view.html").json["content"]
        == "<h1>ação</h1>"
    )
    preview = registry.preview({"path": str(engine.settings.data / "view.html")})
    assert preview["url"] == "/preview/view.html"
    assert client.get(preview["url"]).status_code == 200


def test_image_survives_brain_execution_and_checkpoint(make_app):
    from conftest import execute_next

    _, engine, adapter = make_app(
        [[("read", {"path": "sample.png"})], "Image received"]
    )
    Image.new("RGB", (10, 10), "red").save(engine.settings.data / "sample.png")
    engine.store.enqueue("Inspect image")
    job = execute_next(engine)
    assert job["status"] == "succeeded", job["error"]
    tools = [m for m in adapter.calls[-1] if m["role"] == "tool"]
    assert tools[-1]["attachments"][0]["image_data"]
    persisted = job["brain"]["short_term"]["items"]
    assert any(m.get("attachments") for m in persisted)
    public = next(
        ev["payload"]["result"]
        for ev in engine.store.events(job["session_id"])
        if ev["type"] == "tool_end"
    )
    assert public["image"]["attached_to_model"] and "image_data" not in public["image"]


def test_image_context_budget_drops_oldest_without_destroying_tool_identity(
    monkeypatch,
):
    from fluxyr.core.memory import short_term

    monkeypatch.setattr(short_term, "MAX_TOOL_IMAGE_BYTES", 10)
    memory = short_term.ShortTermMemory()
    for identifier in ("old", "new"):
        memory.add(
            ToolResponse(
                "read",
                identifier,
                {
                    "content": identifier,
                    "image": {
                        "content_type": "image",
                        "image_data": "123456",
                        "mime_type": "image/jpeg",
                    },
                },
            ).to_message()
        )
    assert "attachments" not in memory.items[0]
    assert (
        memory.items[0]["tool_call_id"] == "old"
        and "Use read" in memory.items[0]["content"]
    )
    assert memory.items[1]["attachments"]
