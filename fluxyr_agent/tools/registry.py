"""Local tool catalogue bound to one durable job and its pinned action versions."""

import json
from urllib.parse import quote

from jsonschema import SchemaError, ValidationError, validate
from sqlalchemy import select

from ..database import row_dict
from ..interactions.envelope import build_approval_pua
from ..models import Event, Job, Message
from .contracts import ToolExecutionContext
from .web import WebBrowserToolProvider

S = {"type": "string"}
B = {"type": "boolean"}
O = {"type": "object"}
A = {"type": "array", "items": S}
PARAMETERS_SCHEMA = {
    "type": "object",
    "description": "JSON Schema for the action inputs. required and enum are JSON arrays, additionalProperties is a boolean. Use actual JSON types, never strings for booleans.",
    "properties": {
        "type": {"type": "string", "enum": ["object"]},
        "properties": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "type": {"type": ["string", "array"], "items": S},
                    "description": S,
                    "enum": {"type": "array", "items": {}},
                    "default": {},
                    "items": O,
                },
            },
        },
        "required": A,
        "additionalProperties": {"type": ["boolean", "object"]},
    },
    "required": ["type"],
}
DATA_PATH = {
    "type": "string",
    "description": "Relative to the data root. Use sales.csv, NOT data/sales.csv or ./data/sales.csv. Use '.' to list the root directory.",
}


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def json_payload(description):
    return {
        "type": "string",
        "description": description
        + " Encode valid JSON text; numbers/booleans/arrays inside it retain their JSON types.",
    }


def decode_payloads(args, fields, required=()):
    """Explicit JSON-text transport for open-ended payloads; never guess/coerce types."""
    args = dict(args)
    for field, expected in fields.items():
        encoded = field + "_json"
        if encoded in args:
            if field in args:
                raise ValueError(f"Supply only {field} or {encoded}, not both")
            args[field] = json.loads(args.pop(encoded))
        if field in required and field not in args:
            raise ValueError(f"Supply {field} or {encoded}")
        if field in args and expected and not isinstance(args[field], expected):
            raise ValueError(f"{field} must decode to {expected.__name__}")
    return args


def human(question, choices=None, preflight=False):
    return {
        "__pua__": build_approval_pua(
            "choices" if choices else "continue",
            question[:100],
            question,
            choices=choices or [],
            preflight=preflight,
        ),
        "question": question,
    }, "wait"


def normalize_human_args(args):
    """Accept the label-only option maps emitted by some models, unambiguously."""
    args = dict(args)
    if isinstance(args.get("choices"), list):
        args["choices"] = [
            next(iter(choice))
            if isinstance(choice, dict)
            and len(choice) == 1
            and next(iter(choice.values())) == ""
            else choice
            for choice in args["choices"]
        ]
    return args


class Registry:
    def __init__(self, engine, job, emit, stop):
        self.engine = engine
        self.job = job
        self.emit = emit
        self.stop = stop
        self.report = None
        self.snapshot = job["snapshot"]

    def definitions(self):
        e = self.engine
        definitions = []

        def add(
            name,
            description,
            properties,
            required,
            fn,
            parallel=False,
            effect=False,
            prepare=None,
            normalize=None,
        ):
            parameters = schema(properties, required)

            def invoke(**args):
                call_id = args.pop("__tool_call_id__", None)
                coord = args.pop("__tool_coord__", None)
                try:
                    if normalize:
                        args = normalize(args)
                    validate(args, parameters)
                    if prepare:
                        args = prepare(args)
                except (ValidationError, SchemaError, ValueError, SyntaxError) as exc:
                    return {
                        "error": (
                            f"At {'.'.join(map(str, exc.path)) or '<root>'}: {exc.message}"
                            if isinstance(exc, (ValidationError, SchemaError))
                            else str(exc)
                        ),
                        "type": "validation_error",
                        "executed": False,
                    }, "continue"
                if self.stop():
                    return {"error": "Execution stopped before dispatch"}, "continue"
                if effect and not name.startswith("action_"):
                    result = e.effects.run(
                        self.job["id"],
                        name,
                        coord[1] if coord else 0,
                        args,
                        lambda: fn(args, call_id, coord),
                    )
                else:
                    result = fn(args, call_id, coord)
                if isinstance(result, dict) and result.get("__await_job__"):
                    return result, "wait"
                return result if isinstance(result, tuple) else (result, "continue")

            definitions.append(
                {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                    "function": invoke,
                    "parallel_safe": parallel,
                    "side_effecting": effect,
                    "irreversible": effect,
                }
            )

        add(
            "list_tools",
            "Read the actual tools currently available to this agent, including descriptions and input schemas. Does not execute them.",
            {},
            [],
            lambda *_: [
                {k: t[k] for k in ("name", "description", "parameters")}
                for t in (
                    self.brain._all_tools()
                    if hasattr(self, "brain")
                    else self.definitions()
                )
            ],
            True,
        )
        web = WebBrowserToolProvider()
        for tool in web.get_tools("instance"):

            def browse(args, call, coord, name=tool.name):
                result = web.execute(
                    name,
                    args,
                    ToolExecutionContext(self.job["session_id"], self.job["id"]),
                )
                return (
                    {"output": result.output}
                    if result.success
                    else {"error": result.error}
                )

            add(
                tool.name,
                tool.description,
                tool.parameters["properties"],
                tool.parameters.get("required", []),
                browse,
                True,
            )
        add(
            "tech_doc",
            "Extract and distill an API documentation site to an integration reference.",
            {"url": S, "endpoints": A},
            ["url"],
            self.techdoc,
            True,
        )
        add(
            "ask_human",
            'Ask a question and wait durably for a human answer. Optional choices are plain strings, for example ["Rebuild", "Keep current version"]. Never proceed until the human answers.',
            {
                "question": {"type": "string", "minLength": 1},
                "choices": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "Optional answer labels as plain strings, not objects.",
                },
            },
            ["question"],
            lambda a, *_: human(**a),
            normalize=normalize_human_args,
        )
        add(
            "list_skills",
            "Inspect skills, action source, versions and test results.",
            {},
            [],
            lambda *_: e.skills.list(),
            True,
        )
        add(
            "set_skill_enabled",
            "Enable or disable a skill and all its actions. Disabling preserves code and history.",
            {"skill_id": S, "enabled": B},
            ["skill_id", "enabled"],
            self.set_skill_enabled,
            effect=True,
        )
        add(
            "delete_skill",
            "Delete a skill from the workbench and stop offering its actions; preserve execution history.",
            {"skill_id": S},
            ["skill_id"],
            lambda a, *_: e.skills.delete(**a),
            effect=True,
        )
        add(
            "create_skill",
            "Save skill metadata only. Returns the real UUID in id: use that exact id as skill_id in build_skill; never derive an ID from the name. Then build its Python actions, test and activate them.",
            {
                "name": S,
                "description": S,
                "instruction": S,
                "spec": {
                    "type": "string",
                    "description": "Markdown technical specification: one ## section per action with inputs/types/examples, outputs, API or algorithm, credentials and test cases. No Python.",
                },
            },
            ["name", "description", "instruction", "spec"],
            lambda a, *_: e.skills.build(**a),
            effect=True,
        )
        add(
            "get_skill",
            "Read complete skill instruction, technical spec, action descriptions, source and test evidence.",
            {"skill_id": S},
            ["skill_id"],
            lambda a, *_: e.skills.get(**a),
            True,
        )
        add(
            "update_skill",
            "Refine a skill's usage instructions, description or Markdown technical spec after inspecting execution evidence. Code changes require rebuild_action or build_skill.",
            {"skill_id": S, "name": S, "description": S, "instruction": S, "spec": S},
            ["skill_id"],
            lambda a, *_: e.skills.update(**a),
            effect=True,
        )
        add(
            "update_action_description",
            "Improve how an existing action is described using real execution evidence. Does not change its Python code.",
            {"action_id": S, "description": S},
            ["action_id", "description"],
            lambda a, *_: e.skills.describe_action(**a),
            effect=True,
        )
        add(
            "build_skill",
            "Build a saved skill in a SEPARATE builder context. Pass the real skill ID. This conversation suspends durably and resumes automatically with created action versions; no polling is needed. The workbench then inspects and tests the candidates.",
            {"skill_id": S},
            ["skill_id"],
            self.build,
            effect=True,
            prepare=e.builds.prepare,
        )
        add(
            "rebuild_action",
            "Ask the isolated builder to rebuild one action from this skill's updated technical spec, preserving other actions and active versions.",
            {"skill_id": S, "action_id": S},
            ["skill_id", "action_id"],
            self.build,
            effect=True,
            prepare=e.builds.prepare,
        )
        add(
            "submit_plan",
            "Builder only: persist the complete ordered action plan before generating code.",
            {
                "skill_id": S,
                "actions": {
                    "type": "array",
                    "items": schema(
                        {"name": S, "description": S}, ["name", "description"]
                    ),
                },
            },
            ["skill_id", "actions"],
            lambda a, *_: e.builds.plan(self.job["id"], **a),
            effect=True,
        )
        add(
            "create_action",
            "Save one immutable Python candidate. Names are normalized automatically; use the returned function_name after activation. Follow the Python development guide in the system prompt.",
            {
                "skill_id": {
                    "type": "string",
                    "description": "Exact UUID returned in id by create_skill or list_skills. Never use a name or slug.",
                },
                "name": {
                    "type": "string",
                    "description": "Display name such as Weather - Forecast, or a short identifier. The backend derives an action_ callable name (maximum 55 characters after the prefix).",
                },
                "description": S,
                "source": S,
                "parameters": PARAMETERS_SCHEMA,
                "parameters_json": json_payload(
                    'Preferred: complete action input JSON Schema. Example: {"type":"object","properties":{"count":{"type":"integer","default":1}},"required":["count"]}. Omit parameters when using this.'
                ),
                "dependencies": A,
                "requires_approval": B,
                "secrets": A,
            },
            ["skill_id", "name", "description", "source"],
            lambda a, *_: e.skills.create(**a, build_job_id=self.job["id"]),
            effect=True,
            prepare=lambda a: e.builds.prepare_action(
                self.job["id"], decode_payloads(a, {"parameters": dict}, ["parameters"])
            ),
        )
        add(
            "test_action",
            "Run a candidate locally with real inputs. Tests may have real side effects.",
            {
                "version_id": S,
                "params": O,
                "params_json": json_payload(
                    'Preferred: action input values, e.g. {"count":4,"sides":6,"drop_lowest":1}. Omit params when using this.'
                ),
            },
            ["version_id"],
            lambda a, c, k: e.skills.test(
                **a, emit=lambda x: self.progress(c, x), stop=self.stop
            ),
            effect=True,
            prepare=lambda a: e.skills.prepare_test(
                decode_payloads(a, {"params": dict}, ["params"])
            ),
        )
        add(
            "activate_action",
            "Activate a version with a passing test; preserve other running execution versions.",
            {"version_id": S},
            ["version_id"],
            self.activate,
            effect=True,
        )
        add(
            "vault_list",
            "List secret names and types without revealing their values.",
            {},
            [],
            lambda *_: e.vault.list(),
            True,
        )
        add(
            "list_files",
            "List a directory under ./data.",
            {"path": DATA_PATH},
            [],
            lambda a, *_: e.files.list(**a),
            True,
        )
        add(
            "read_file",
            "Read a UTF-8 file and its edit token.",
            {"path": DATA_PATH},
            ["path"],
            lambda a, *_: e.files.read(**a),
            True,
        )
        add(
            "write_file",
            "Write a UTF-8 file; supply the etag when editing an existing file.",
            {"path": DATA_PATH, "content": S, "etag": S},
            ["path", "content"],
            lambda a, *_: e.files.write(**a),
            effect=True,
        )
        add(
            "file_operation",
            "Create a directory, move a path or delete a file/empty directory.",
            {
                "operation": {"enum": ["mkdir", "move", "delete"]},
                "path": DATA_PATH,
                "destination": DATA_PATH,
            },
            ["operation", "path"],
            lambda a, *_: e.files.mutate(**a),
            effect=True,
        )
        add(
            "search_files",
            "Search text under ./data.",
            {"query": S, "path": S},
            ["query"],
            lambda a, *_: e.files.search(**a),
            True,
        )
        add(
            "render_preview",
            "Show a local file preview in the conversation.",
            {"path": S, "title": S},
            ["path"],
            self.preview,
        )
        add(
            "list_routines",
            "List saved routines and schedules.",
            {},
            [],
            lambda *_: e.routines.list(),
            True,
        )
        add(
            "create_routine",
            "Create a manual or scheduled prompt using implemented actions. Success criteria belong in the action spec and Python code, never a separate routine expectation. UTC cron defaults; overlap queue or skip.",
            {
                "name": S,
                "prompt": S,
                "cron": S,
                "timezone": S,
                "enabled": B,
                "overlap": {"enum": ["queue", "skip"]},
            },
            ["name", "prompt"],
            lambda a, *_: e.routines.put(a),
            effect=True,
            prepare=e.routines.prepare_create,
        )
        add(
            "update_routine",
            "Update a routine or disable its schedule.",
            {"routine_id": S, "values": O},
            ["routine_id", "values"],
            lambda a, *_: e.routines.put(a["values"], a["routine_id"]),
            effect=True,
        )
        add(
            "run_routine",
            "Enqueue a manual execution in its own conversation.",
            {"routine_id": S},
            ["routine_id"],
            lambda a, call, *_: e.routines.run(
                a["routine_id"], parent=self.job, call_id=call
            ),
            effect=True,
        )
        add(
            "list_executions",
            "List recent execution IDs and outcomes, including reports from scheduled routines.",
            {},
            [],
            self.executions,
            True,
        )
        add(
            "inspect_execution",
            "Read execution messages, outcome and tool event details.",
            {"job_id": S},
            ["job_id"],
            self.inspect,
            True,
        )
        add(
            "finish_execution",
            "Record a narrative summary and output for inspection only. This never determines success: executed Python action verdicts do.",
            {
                "output": {},
                "output_json": json_payload(
                    "Preferred: the actual result as JSON text, including a root array if that is the result. Omit output when using this."
                ),
                "evidence": S,
            },
            ["evidence"],
            self.finish,
            effect=True,
            prepare=lambda a: decode_payloads(a, {"output": None}, ["output"]),
        )
        for tool in self.snapshot["tools"]:
            if not e.skills.available(tool["skill_id"]):
                continue
            version = tool["version"]

            def execute(args, call, coord, t=tool):
                return self.action(t, args, call, coord)

            add(
                tool["name"],
                tool["description"],
                version["parameters"].get("properties", {}),
                version["parameters"].get("required", []),
                execute,
                effect=True,
            )
            definitions[-1]["parameters"] = version["parameters"]
            definitions[-1]["requires_approval"] = version["requires_approval"]
        if self.job["input"].get("builder"):
            allowed = {
                "submit_plan",
                "create_action",
                "get_skill",
                "list_skills",
                "vault_list",
                "list_files",
                "read_file",
                "search_files",
                "web_browse",
                "web_extract",
                "tech_doc",
                "ask_human",
                "inspect_execution",
            }
            return [d for d in definitions if d["name"] in allowed]
        return [
            d for d in definitions if d["name"] not in {"create_action", "submit_plan"}
        ]

    def set_skill_enabled(self, args, *_):
        result = self.engine.skills.update(**args)
        with self.engine.db.transaction() as s:
            fresh = self.engine.store.snapshot(s)
            existing = {t["id"]: t for t in self.snapshot["tools"]}
            self.snapshot = {
                "skills": fresh["skills"],
                "tools": [existing.get(t["id"], t) for t in fresh["tools"]],
            }
            s.get(Job, self.job["id"]).snapshot = self.snapshot
        return result

    def build(self, args, call_id, *_):
        child = self.engine.builds.enqueue(**args, parent=self.job, call_id=call_id)
        return {
            "__await_job__": child["id"],
            "waiting": {"kind": "skill_build"},
            "job_id": child["id"],
            "session_id": child["session_id"],
            "status": "building",
        }

    def progress(self, call_id, text):
        self.emit("progress", {"call_id": call_id, "text": str(text)})

    def techdoc(self, args, call_id, coord):
        from .techdoc import get_integration_docs
        from .techdoc.llm import adapter_factory, activity_emitter

        token = adapter_factory.set(self.engine.adapter)
        activity_token = activity_emitter.set(self.emit)
        try:
            self.progress(call_id, "Reading and distilling documentation…")
            return {"reference": get_integration_docs(**args)}
        finally:
            adapter_factory.reset(token)
            activity_emitter.reset(activity_token)

    def activate(self, args, *_):
        result = self.engine.skills.activate(args["version_id"])
        # This job explicitly activated the version; incorporate its own change immediately.
        with self.engine.db.transaction() as s:
            current = self.engine.store.snapshot(s)
        selected = next(
            (t for t in current["tools"] if t["name"] == result["name"]), None
        )
        self.snapshot = {
            **self.snapshot,
            "tools": [t for t in self.snapshot["tools"] if t["name"] != result["name"]]
            + ([selected] if selected else []),
        }
        with self.engine.db.transaction() as s:
            s.get(Job, self.job["id"]).snapshot = self.snapshot
        return result

    def preview(self, args, *_):
        path = self.engine.files.path(args["path"])
        if not path.is_file():
            raise ValueError("Preview file not found")
        result = {
            "path": args["path"],
            "url": "/preview/" + quote(args["path"]),
            "title": args.get("title", path.name),
        }
        self.emit("preview", result)
        return result

    def executions(self, *_):
        with self.engine.db.transaction() as s:
            return [
                {
                    "id": j.id,
                    "session_id": j.session_id,
                    "prompt": j.prompt,
                    "status": j.status,
                    "outcome": j.outcome,
                }
                for j in s.scalars(
                    select(Job).order_by(Job.created_at.desc()).limit(50)
                )
            ]

    def inspect(self, args, *_):
        with self.engine.db.transaction() as s:
            job = s.get(Job, args["job_id"])
            if not job:
                raise ValueError("Execution not found")
            return {
                "job": {
                    k: v
                    for k, v in row_dict(job).items()
                    if k not in ("brain", "snapshot")
                },
                "messages": [
                    row_dict(m)
                    for m in s.scalars(
                        select(Message)
                        .where(Message.session_id == job.session_id)
                        .order_by(Message.created_at)
                    )
                ],
                "events": [
                    row_dict(x)
                    for x in s.scalars(
                        select(Event)
                        .where(Event.job_id == job.id)
                        .order_by(Event.id)
                        .limit(500)
                    )
                ],
            }

    def finish(self, args, *_):
        self.report = args
        with self.engine.db.transaction() as s:
            job = s.get(Job, self.job["id"])
            job.input = {**job.input, "report": args}
        return {"recorded": True, "determines_success": False}

    def action(self, tool, args, call_id, coord, approved=False, answer=None):
        if not self.engine.skills.available(tool["skill_id"]):
            return {
                "error": "Skill is disabled or deleted",
                "executed": False,
            }, "continue"
        version = tool["version"]
        validate(args, version["parameters"])
        if version["requires_approval"] and not approved:
            return human(
                f"Execute {tool['name']} with {json.dumps(args, ensure_ascii=False)}?",
                preflight=True,
            )
        occurrence = coord[1] if coord else 0
        result = self.engine.effects.run(
            self.job["id"],
            tool["name"],
            occurrence,
            args,
            lambda: self.engine.runner.run(
                version,
                args,
                self.job["id"],
                lambda x: self.progress(call_id, x),
                self.stop,
                answer,
            ),
        )
        if result.get("waiting"):
            return human(result["waiting"]["question"])
        return result, "continue"
