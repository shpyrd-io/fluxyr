"""Isolated skill-builder jobs; workbench resumes with artifacts, never generated code tasks."""

import copy

from sqlalchemy import select

from .models import Effect, Event, Job, Session, Tool, ToolVersion
from .store import TERMINAL


class Builds:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db

    def prepare(self, args):
        skill = self.engine.skills.get(args["skill_id"])
        if not skill["instruction"].strip() or not skill["spec"].strip():
            raise ValueError(
                "Save the skill instruction and Markdown technical spec before building"
            )
        if args.get("action_id") and not any(
            t["id"] == args["action_id"] for t in skill["tools"]
        ):
            raise ValueError("Action does not belong to this skill")
        return args

    def enqueue(self, skill_id, parent=None, call_id=None, action_id=None):
        self.prepare({"skill_id": skill_id, "action_id": action_id})
        skill = self.engine.skills.get(skill_id)
        target = next((t for t in skill["tools"] if t["id"] == action_id), None)
        build = {
            "skill_id": skill_id,
            "skill": skill,
            "target": target,
            "created_versions": [],
            "plan": [],
            "parent_job_id": parent["id"] if parent else None,
        }
        prompt = f"Build Python actions for skill {skill['name']}.\nSkill ID: {skill_id}\n\nInstruction:\n{skill['instruction']}\n\nTechnical specification:\n{skill['spec']}"
        if target:
            prompt += f"\n\nRebuild ONLY action {target['name']} (ID {target['id']}). Keep this exact callable name. Description: {target['description']}"
        with self.db.transaction() as s:
            # Serializes competing builds for one skill, including full vs single-action builds.
            from .models import Skill

            s.get(Skill, skill_id, with_for_update=True)
            existing = s.scalar(
                select(Job)
                .where(
                    Job.input["builder"]["skill_id"].as_string() == skill_id,
                    Job.status.not_in(list(TERMINAL)),
                )
                .limit(1)
            )
            if existing:
                raise ValueError(
                    f"A build already exists for this skill: {existing.id}"
                )
            child = self.engine.store.enqueue(
                prompt,
                session_id=None,
                inputs={
                    "builder": build,
                    "parent_job_id": parent["id"] if parent else None,
                    "parent_call_id": call_id,
                },
                s=s,
            )
            s.get(Session, child["session_id"]).kind = "build"
            if parent:
                row = s.get(Job, parent["id"], with_for_update=True)
                row.input = {
                    **row.input,
                    "build_dependencies": {
                        **row.input.get("build_dependencies", {}),
                        call_id: child["id"],
                    },
                }
                s.add(
                    Event(
                        session_id=parent["session_id"],
                        job_id=parent["id"],
                        type="build_started",
                        payload={
                            "job_id": child["id"],
                            "session_id": child["session_id"],
                            "prompt": f"Build skill: {skill['name']}",
                            "status": "queued",
                        },
                    )
                )
        return child

    def plan(self, job_id, skill_id, actions):
        with self.db.transaction() as s:
            row = s.get(Job, job_id, with_for_update=True)
            build = dict(row.input["builder"])
            if skill_id != build["skill_id"] or not actions:
                raise ValueError(
                    "Plan must target this build's skill and contain actions"
                )
            names = [self.engine.skills.action_name(a["name"]) for a in actions]
            if len(names) != len(set(names)):
                raise ValueError("Plan contains duplicate action names")
            if build["target"] and names != [build["target"]["name"]]:
                raise ValueError(
                    "Single-action rebuild must keep the original action name"
                )
            if build["created_versions"]:
                raise ValueError("Submit the complete plan before creating code")
            build["plan"] = [
                {**a, "function_name": name} for a, name in zip(actions, names)
            ]
            row.input = {**row.input, "builder": build}
            return {"skill_id": skill_id, "actions": build["plan"]}

    def prepare_action(self, job_id, args):
        with self.db.transaction() as s:
            build = s.get(Job, job_id).input["builder"]
        if args["skill_id"] != build["skill_id"]:
            raise ValueError("Builder can only create actions for its specified skill")
        name = self.engine.skills.action_name(args["name"])
        if name not in [a["function_name"] for a in build["plan"]]:
            raise ValueError("Submit the action in the build plan before creating it")
        return self.engine.skills.prepare_create(args)

    def progress(self, job_id):
        """Compact, replayable view of a build's durable state, without Python source."""
        with self.db.transaction() as s:
            row = s.get(Job, job_id)
            if not row or not row.input.get("builder"):
                raise ValueError("Skill build not found")
            build = row.input["builder"]
            versions = dict(
                s.execute(
                    select(ToolVersion.id, Tool.name)
                    .join(Tool, Tool.id == ToolVersion.tool_id)
                    .where(ToolVersion.id.in_(build["created_versions"]))
                ).all()
            )
            created = {versions[v] for v in build["created_versions"] if v in versions}
            pending = {}
            # Read only lifecycle metadata, never source, tool output, or credentials.
            lifecycle = s.execute(
                select(
                    Event.type,
                    Event.payload["tool_call_id"].as_string(),
                    Event.payload["tool_name"].as_string(),
                    Event.payload["args"]["name"].as_string(),
                )
                .where(
                    Event.job_id == job_id, Event.type.in_(["tool_begin", "tool_end"])
                )
                .order_by(Event.id)
            )
            for kind, call_id, tool, name in lifecycle:
                if kind == "tool_begin":
                    pending[call_id] = (tool, name)
                else:
                    pending.pop(call_id, None)
            creating = set()
            for tool, name in pending.values():
                if tool == "create_action" and name:
                    try:
                        creating.add(self.engine.skills.action_name(name))
                    except ValueError:
                        pass  # A rejected action name must not break progress reads.
            researching = [
                tool
                for tool, _ in pending.values()
                if tool not in ("create_action", "submit_plan")
            ]
            active = row.status == "running" and not row.control
            actions = [
                {
                    "name": a["function_name"],
                    "description": a.get("description", ""),
                    "status": "creating"
                    if active and a["function_name"] in creating
                    else "created"
                    if a["function_name"] in created
                    else "planned",
                }
                for a in build["plan"]
            ]
            phase = row.status
            if active:
                phase = (
                    "creating"
                    if creating
                    else "researching"
                    if researching
                    else "planning"
                    if not actions
                    else "finalizing"
                    if all(a["name"] in created for a in actions)
                    else "generating"
                )
            return {
                "job_id": row.id,
                "session_id": row.session_id,
                "skill_name": build["skill"]["name"],
                "status": row.status,
                "control": row.control,
                "phase": phase,
                "research_tools": researching if active else [],
                "actions": actions,
                "created": sum(a["name"] in created for a in actions),
                "total": len(actions),
                "error": row.error,
            }

    def outcome(self, job):
        with self.db.transaction() as s:
            build = s.get(Job, job["id"]).input["builder"]
            versions = [s.get(ToolVersion, vid) for vid in build["created_versions"]]
            tools = {}
            for version in versions:
                tool = s.get(Tool, version.tool_id)
                tools[tool.name] = {
                    "action_id": tool.id,
                    "version_id": version.id,
                    "function_name": tool.name,
                    "description": tool.description,
                    "parameters": version.parameters,
                    "state": version.state,
                }
            expected = {a["function_name"] for a in build["plan"]}
            success = bool(expected) and set(tools) == expected
            return {
                "success": success,
                "build": {
                    "skill_id": build["skill_id"],
                    "tools": list(tools.values()),
                    "tested": False,
                },
                "failures": []
                if success
                else ["Builder did not produce every planned action"],
            }

    def resolve_dependencies(self):
        """Called by the supervisor, including after restart; never holds a worker waiting."""
        with self.db.transaction() as s:
            parents = list(
                s.scalars(
                    select(Job)
                    .where(Job.status.in_(["building", "waiting"]))
                    .with_for_update(skip_locked=True)
                )
            )
            for parent in parents:
                dependencies = parent.input.get("build_dependencies", {})
                if not dependencies:
                    continue
                state = copy.deepcopy(parent.brain)
                changed = False
                for entry in state.get("pending_tools", []):
                    child_id = dependencies.get(entry.get("call_id"))
                    if not child_id or entry.get("_status") != "parked":
                        continue
                    child = s.get(Job, child_id)
                    if not child or child.status not in TERMINAL:
                        continue
                    result = {
                        "job_id": child.id,
                        "session_id": child.session_id,
                        "status": child.status,
                        **(child.outcome or {}),
                    }
                    if child.status != "succeeded":
                        result["error"] = (
                            child.error
                            or "Skill build did not complete; inspect_execution contains its trace"
                        )
                    entry["_status"] = "completed"
                    entry["_result"] = result
                    entry["_reexec"] = {"result": result}
                    changed = True
                    for effect in s.scalars(
                        select(Effect).where(
                            Effect.job_id == parent.id, Effect.status == "parked"
                        )
                    ):
                        if (effect.result or {}).get("__await_job__") == child_id:
                            effect.status, effect.result = "done", result
                    s.add(
                        Event(
                            session_id=parent.session_id,
                            job_id=parent.id,
                            type="tool_end",
                            payload={
                                "tool_name": entry["name"],
                                "tool_call_id": entry["call_id"],
                                "result": result,
                                "mode": "continue",
                            },
                        )
                    )
                if changed:
                    parent.brain = state
                    ready = all(
                        t.get("_status") != "parked"
                        or t.get("_decision")
                        or t.get("_reexec")
                        for t in state.get("pending_tools", [])
                    )
                    if ready:
                        parent.status = "queued"
                    self.engine.store.sync_session(
                        s, s.get(Session, parent.session_id, with_for_update=True)
                    )
