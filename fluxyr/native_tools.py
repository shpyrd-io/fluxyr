"""Decorator tool schemas and validated calls to application Python functions."""

import asyncio
import inspect
import json
import re
from dataclasses import dataclass
from typing import get_type_hints

from pydantic import ConfigDict, create_model


@dataclass
class NativeTool:
    name: str
    description: str
    function: object
    arguments: object
    parameters: dict
    parallel_safe: bool
    side_effecting: bool

    @classmethod
    def from_function(
        cls,
        fn,
        *,
        name=None,
        description=None,
        parallel_safe=False,
        side_effecting=True,
    ):
        name = name or fn.__name__
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) or name.startswith(
            "action_"
        ):
            raise ValueError(
                "Tool name must be a Python identifier, <=64 characters, without the reserved action_ prefix"
            )
        description = description or inspect.getdoc(fn)
        if not description:
            raise ValueError(f"{name}: supply a docstring or description")
        hints, fields = get_type_hints(fn, include_extras=True), {}
        for key, param in inspect.signature(fn).parameters.items():
            if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
                raise ValueError(
                    f"{name}: positional-only and variadic parameters are not supported"
                )
            if key not in hints or key.startswith("__"):
                raise ValueError(f"{name}.{key}: a public, typed parameter is required")
            fields[key] = (
                hints[key],
                ... if param.default is param.empty else param.default,
            )
        model = create_model(
            name + "Arguments", __config__=ConfigDict(extra="forbid"), **fields
        )
        return cls(
            name,
            description,
            fn,
            model,
            model.model_json_schema(),
            parallel_safe,
            side_effecting,
        )

    def invoke(self, args, app):
        # JSON input -> typed Python arguments, including nested Pydantic models.
        values = self.arguments.model_validate_json(json.dumps(args), strict=True)
        kwargs = {name: getattr(values, name) for name in type(values).model_fields}
        try:
            with app.app_context():
                output = self.function(**kwargs)
                if inspect.isawaitable(output):
                    output = asyncio.run(output)
                json.dumps(output, allow_nan=False)
                return {"done": True, "success": True, "output": output}
        except Exception as exc:  # noqa: BLE001 - turn application failures into the executor contract
            return {
                "done": True,
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "side_effects_possible": self.side_effecting,
            }
