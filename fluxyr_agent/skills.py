"""Immutable action versions and explicit test/activation lifecycle."""

import ast
import re
import time
import unicodedata

from jsonschema.validators import validator_for
from sqlalchemy import select

from .database import row_dict
from .models import Job, Skill, Tool, ToolVersion


class Skills:
    def __init__(self, db, runner):
        self.db = db
        self.runner = runner

    def list(self):
        with self.db.transaction() as s:
            return [
                {
                    **row_dict(skill),
                    "tools": [
                        {
                            **row_dict(tool),
                            "versions": [
                                row_dict(v)
                                for v in s.scalars(
                                    select(ToolVersion)
                                    .where(ToolVersion.tool_id == tool.id)
                                    .order_by(ToolVersion.created_at.desc())
                                )
                            ],
                        }
                        for tool in s.scalars(
                            select(Tool).where(Tool.skill_id == skill.id)
                        )
                    ],
                }
                for skill in s.scalars(
                    select(Skill).where(Skill.deleted_at.is_(None)).order_by(Skill.name)
                )
            ]

    def get(self, skill_id):
        return (
            next((s for s in self.list() if s["id"] == skill_id), None)
            or self._missing_skill()
        )

    @staticmethod
    def _missing_skill():
        raise ValueError("Skill not found; use the exact returned UUID")

    def update(self, skill_id, **values):
        with self.db.transaction() as s:
            skill = s.get(Skill, skill_id, with_for_update=True)
            if not skill or skill.deleted_at:
                self._missing_skill()
            for key in ("name", "description", "instruction", "spec", "enabled"):
                if key in values:
                    setattr(skill, key, values[key])
            s.flush()
            return row_dict(skill)

    def available(self, skill_id):
        with self.db.transaction() as s:
            skill = s.get(Skill, skill_id)
            return bool(skill and skill.enabled and not skill.deleted_at)

    def delete(self, skill_id):
        with self.db.transaction() as s:
            skill = s.get(Skill, skill_id, with_for_update=True)
            if not skill:
                self._missing_skill()
            skill.enabled = False
            skill.deleted_at = skill.deleted_at or time.time()
            return {"id": skill_id, "deleted": True, "history_preserved": True}

    def describe_action(self, action_id, description):
        with self.db.transaction() as s:
            tool = s.get(Tool, action_id, with_for_update=True)
            if not tool:
                raise ValueError("Action not found")
            tool.description = description
            return row_dict(tool)

    def build(self, name, description, instruction, spec=None):
        with self.db.transaction() as s:
            skill = s.scalar(select(Skill).where(Skill.name == name).with_for_update())
            if not skill:
                skill = Skill(name=name)
                s.add(skill)
            if skill.deleted_at:
                raise ValueError(
                    "This name belongs to a deleted skill; choose a new name"
                )
            skill.description = description
            skill.instruction = instruction
            if spec is not None:
                skill.spec = spec
            s.flush()
            return row_dict(skill)

    @staticmethod
    def action_name(name):
        """Accept display names and short identifiers; expose the actual callable name."""
        if re.fullmatch(r"action_[a-zA-Z0-9_]{1,55}", name.strip()):
            return name.strip()
        name = (
            unicodedata.normalize("NFKD", name.strip())
            .encode("ascii", "ignore")
            .decode()
        )
        name = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
        if not name.startswith("action_"):
            name = "action_" + name.lower()
        if not re.fullmatch(r"action_[a-zA-Z0-9_]{1,55}", name):
            raise ValueError(
                "Action name must produce 1–55 letters, numbers or underscores after action_"
            )
        return name

    def prepare_create(self, args):
        """Validate without writing or claiming an effect, so the model can correct input."""
        args = {**args, "name": self.action_name(args["name"])}
        if not args["source"].strip():
            raise ValueError("Action source cannot be empty")
        ast.parse(args["source"])
        compile(args["source"], "action.py", "exec")
        if args["parameters"].get("type") != "object":
            raise ValueError("Parameters must be an object JSON Schema")
        validator_for(args["parameters"]).check_schema(args["parameters"])
        with self.db.transaction() as s:
            if not (skill := s.get(Skill, args["skill_id"])) or skill.deleted_at:
                raise ValueError("Skill not found")
            tool = s.scalar(select(Tool).where(Tool.name == args["name"]))
            if tool and tool.skill_id != args["skill_id"]:
                raise ValueError(
                    "Action name belongs to another skill; include this skill's name"
                )
        return args

    def create(
        self,
        skill_id,
        name,
        description,
        source,
        parameters,
        dependencies=None,
        requires_approval=False,
        secrets=None,
        build_job_id=None,
    ):
        name = self.prepare_create(
            {
                "skill_id": skill_id,
                "name": name,
                "source": source,
                "parameters": parameters,
            }
        )["name"]
        with self.db.transaction() as s:
            if (
                not (skill := s.get(Skill, skill_id, with_for_update=True))
                or skill.deleted_at
            ):
                raise ValueError("Skill not found")
            tool = s.scalar(select(Tool).where(Tool.name == name).with_for_update())
            if not tool:
                tool = Tool(skill_id=skill_id, name=name, description=description)
                s.add(tool)
                s.flush()
            if tool.skill_id != skill_id:
                raise ValueError("Action name belongs to another skill")
            tool.description = description
            version = ToolVersion(
                tool_id=tool.id,
                source=source,
                parameters=parameters,
                dependencies=dependencies or [],
                requires_approval=requires_approval,
                secrets=secrets or [],
            )
            s.add(version)
            s.flush()
            if build_job_id:
                job = s.get(Job, build_job_id, with_for_update=True)
                build = dict(job.input["builder"])
                build["created_versions"] = [
                    *build.get("created_versions", []),
                    version.id,
                ]
                job.input = {**job.input, "builder": build}
            return {**row_dict(version), "name": tool.name, "function_name": tool.name}

    def version(self, version_id):
        with self.db.transaction() as s:
            version = s.get(ToolVersion, version_id)
            if not version:
                raise ValueError("Version not found")
            return row_dict(version)

    def test(
        self, version_id, params, emit=lambda *_: None, stop=lambda: False, answer=None
    ):
        from jsonschema import validate

        version = self.version(version_id)
        validate(params, version["parameters"])
        result = self.runner.run(version, params, "tests", emit, stop, answer)
        passed = result.get("success") is True and "waiting" not in result
        with self.db.transaction() as s:
            row = s.get(ToolVersion, version_id, with_for_update=True)
            if row.state != "active":
                row.state = "tested" if passed else "candidate"
            row.test_result = result
        return {"passed": passed, **result}

    def prepare_test(self, args):
        from jsonschema import validate

        validate(args["params"], self.version(args["version_id"])["parameters"])
        return args

    def activate(self, version_id):
        with self.db.transaction() as s:
            version = s.get(ToolVersion, version_id, with_for_update=True)
            if not version or version.state not in ("tested", "active"):
                raise ValueError("A passing test is required before activation")
            tool = s.get(Tool, version.tool_id, with_for_update=True)
            tool.active_version = version.id
            version.state = "active"
            return {"name": tool.name, "version_id": version.id, "active": True}
