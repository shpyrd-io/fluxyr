"""Local tool catalogue bound to one durable job and its pinned action versions."""

import json
from urllib.parse import quote

from jsonschema import SchemaError, ValidationError, validate

from ..inspection import execution_details, execution_list, skill_context
from ..interactions.human import action_wait, human
from ..models import Job
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


def normalize_human_args(args):
    """Accept the label-only option maps emitted by some models, unambiguously."""
    args = dict(args)
    if isinstance(args.get("choices"), list):
        args["choices"] = [
            next(iter(choice))
            if isinstance(choice, dict)
            and len(choice) == 1
            and next(iter(choice.values())) == ""
            else choice["value"]
            if isinstance(choice, dict)
            and set(choice) == {"key", "value"}
            and isinstance(choice["key"], str)
            and isinstance(choice["value"], str)
            else choice
            for choice in args["choices"]
        ]
    if args.get("choices") == []:
        args.pop("choices")
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
            parameter_schema=None,
        ):
            parameters = parameter_schema or schema(properties, required)

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
                if effect and not name.startswith("action_") and name != "test_action":
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
                    "minItems": 2,
                    "maxItems": 6,
                    "description": "2–6 answer labels for a single-choice interaction. Omit choices for free-text input; do not mix the two.",
                },
            },
            ["question"],
            lambda a, *_: human(**a),
            normalize=normalize_human_args,
        )
        add(
            "list_skills",
            "List skill/action metadata and version test summaries. Use get_skill for a specific spec or source.",
            {},
            [],
            lambda *_: skill_context(e),
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
            "Read one skill's current instructions, spec and version test summaries. Set include_source=true to inspect the latest candidate, or version_id for a specific version. Source is paginated via offset/source_next_offset.",
            {
                "skill_id": S,
                "include_source": B,
                "version_id": S,
                "offset": {"type": "integer", "minimum": 0},
            },
            ["skill_id"],
            lambda a, *_: skill_context(e, **a),
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
                "secrets": {
                    "type": "array",
                    "items": S,
                    "description": "Exact existing Vault item names returned by vault_list. Each secret(name) in Python MUST use one of these identical names. No logical aliases or UUIDs.",
                },
            },
            ["skill_id", "name", "description", "source"],
            lambda a, *_: {
                k: v
                for k, v in e.skills.create(**a, build_job_id=self.job["id"]).items()
                if k not in {"source", "environment"}
            },
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
            self.test_action,
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
            "List credential names/types and safe OAuth configuration (flow and linked certificate), never secret values. Use existing credentials; client_credentials needs no browser authorization.",
            {},
            [],
            lambda *_: e.vault.list(include_configuration=True),
            True,
        )
        add(
            "check_vault_credential",
            "Check an existing credential before building/testing an integration. OAuth may obtain or renew a token using configured mTLS. Returns readiness and safe diagnostics only, never secret values. Does not invoke the resource API or validate action code.",
            {"name": S},
            ["name"],
            lambda a, *_: e.vault.check(**a),
            True,
        )
        from ..interactions.vault import credential_request
        from ..vault import TYPES

        add(
            "manage_vault_credential",
            "Open an embedded private Vault form and wait for the user to save or cancel. Use create when a required credential is missing, edit for an existing ID from vault_list. Supply only metadata; never ask for, read or pass credential values in chat or ask_human. Returns only the saved Vault ID/name. Declare that name in action secrets and use secret(name) at runtime.",
            {
                "action": {"type": "string", "enum": ["create", "edit"]},
                "vault_item_type": {"type": "string", "enum": list(TYPES)},
                "suggested_name": {"type": "string", "maxLength": 200},
                "vault_item_id": S,
                "oauth_grant_type": {
                    "type": "string",
                    "enum": ["authorization_code", "client_credentials"],
                    "description": "Preselect the OAuth flow requested by the user. Client credentials uses ID + secret without browser authorization.",
                },
                "mtls_certificate_id": {
                    "type": "string",
                    "description": "Optional PEM/PFX Vault item ID from vault_list to preselect for OAuth mTLS.",
                },
            },
            ["action"],
            lambda a, *_: credential_request(e.vault, **a),
            parallel=True,
        )
        file_path = {
            "type": "string",
            "description": f"File path, absolute or relative to the working directory: {e.settings.data}. Supports ~.",
        }
        add(
            "read",
            "Read text or an image. Text is limited to 2000 lines or 50 KiB; use offset/limit to continue. Use read instead of cat/sed for inspecting files.",
            {
                "path": file_path,
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1},
            },
            ["path"],
            lambda a, *_: e.workspace_tools.file("read", a, self.job["id"], self.stop),
            parallel=True,
        )
        add(
            "write",
            "Create or overwrite a UTF-8 file, creating parent directories. Use only for new files or complete rewrites; use edit for targeted changes.",
            {"path": file_path, "content": S},
            ["path", "content"],
            lambda a, *_: e.workspace_tools.file("write", a, self.job["id"], self.stop),
            effect=True,
        )
        add(
            "edit",
            "Replace exact unique text in a file. Each edits[].oldText matches the ORIGINAL file. Replacements must not overlap; merge nearby changes. Returns a diff. Read the file first.",
            {
                "path": file_path,
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": schema(
                        {"oldText": S, "newText": S}, ["oldText", "newText"]
                    ),
                },
            },
            ["path", "edits"],
            lambda a, *_: e.workspace_tools.file("edit", a, self.job["id"], self.stop),
            effect=True,
        )
        add(
            "bash",
            f"Execute bash in {e.settings.data}. Use for ls, find, rg, mkdir and other commands. Each call starts a fresh shell; cd/export do not persist. Streams stdout/stderr; returns the last 2000 lines/50 KiB and a full-output file if truncated. Default/maximum timeout: {e.settings.tool_timeout}s.",
            {
                "command": S,
                "timeout": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": e.settings.tool_timeout,
                },
            },
            ["command"],
            lambda a, call, *_: e.workspace_tools.bash(
                **a,
                job_id=self.job["id"],
                emit=lambda chunk: self.progress(call, chunk),
                stop=self.stop,
            ),
            effect=True,
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
            "List recent execution IDs and concise status. Use next_before for older entries and inspect_execution for evidence.",
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "before": {"type": "number"},
            },
            [],
            self.executions,
            True,
        )
        add(
            "inspect_execution",
            "Read recent significant execution events, errors and this job's messages, without stream fragments. Page older events with next_before_event_id. Credential failures before Python require Vault configuration, not an action rebuild.",
            {
                "job_id": S,
                "before_event_id": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                "include_logs": B,
            },
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
                parallel=True,
                effect=True,
            )
            definitions[-1]["parameters"] = version["parameters"]
            definitions[-1]["requires_approval"] = version["requires_approval"]
            definitions[-1]["version_id"] = version["id"]
        for native in e.native_tools.values():
            if any(d["name"] == native.name for d in definitions):
                raise ValueError(
                    f"Application tool conflicts with engine tool: {native.name}"
                )
            add(
                native.name,
                native.description,
                {},
                [],
                lambda args, call, coord, n=native: n.invoke(args, e.app),
                parallel=native.parallel_safe,
                effect=native.side_effecting,
                parameter_schema=native.parameters,
            )
        if self.job["input"].get("builder"):
            allowed = {
                "submit_plan",
                "create_action",
                "get_skill",
                "list_skills",
                "vault_list",
                "manage_vault_credential",
                "read",
                "write",
                "edit",
                "bash",
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
        from .techdoc.llm import activity_emitter, adapter_factory

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
        relative = path.relative_to(self.engine.files.root).as_posix()
        result = {
            "path": relative,
            "url": "/preview/" + quote(relative),
            "title": args.get("title", path.name),
        }
        self.emit("preview", result)
        return result

    def executions(self, args, *_):
        return execution_list(self.engine, **args)

    def inspect(self, args, *_):
        return execution_details(self.engine, **args)

    def finish(self, args, *_):
        self.report = args
        with self.engine.db.transaction() as s:
            job = s.get(Job, self.job["id"])
            job.input = {**job.input, "report": args}
        return {"recorded": True, "determines_success": False}

    def test_action(self, args, call_id, coord, answer=None, continuation=None):
        args = self.engine.skills.prepare_test(
            decode_payloads(args, {"params": dict}, ["params"])
        )
        result = self.engine.effects.run(
            self.job["id"],
            "test_action",
            coord[1] if coord else 0,
            args,
            lambda: self.engine.skills.test(
                **args,
                emit=lambda x: self.progress(call_id, x),
                stop=self.stop,
                answer=answer,
                continuation=continuation,
            ),
        )
        if result.get("waiting"):
            return action_wait(result, self.engine.files), "wait"
        return result, "continue"

    def action(
        self, tool, args, call_id, coord, approved=False, answer=None, continuation=None
    ):
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
                continuation,
            ),
        )
        if result.get("waiting"):
            return action_wait(result, self.engine.files), "wait"
        return result, "continue"
