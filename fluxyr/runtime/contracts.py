"""Deterministic checks for generated action contracts (not a Python sandbox)."""

import ast
import inspect
from functools import lru_cache
from pathlib import Path


def validate_secret_references(source, secrets):
    """Catch literal secret() names that differ from the declared Vault names.

    Dynamic names still use the runtime check. Aliased imports are supported;
    unrelated functions named secret are not inspected.
    """
    tree = ast.parse(source)
    validate_helper_calls(tree)
    functions, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fluxyr":
            functions.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "secret"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "fluxyr"
            )
    referenced = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            (isinstance(func, ast.Name) and func.id in functions)
            or (
                isinstance(func, ast.Attribute)
                and func.attr == "secret"
                and isinstance(func.value, ast.Name)
                and func.value.id in modules
            )
        ):
            continue
        name = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == "name"), None)
        )
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            referenced.add(name.value)
    missing = referenced - set(secrets or [])
    if missing:
        raise ValueError(
            f"secret() references undeclared Vault names: {', '.join(sorted(missing))}. "
            "Use the exact same names from vault_list in both source and the action secrets array; aliases are not resolved."
        )


def output_failed(value):
    return isinstance(value, dict) and value.get("success") is False


@lru_cache(maxsize=1)
def helper_contracts():
    # Parse the shipped helper without importing it (import would read stdin).
    tree = ast.parse(Path(__file__).with_name("helper.py").read_text())
    signatures = {}
    exports = {"params", "data_dir", "approval_response"}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        exports.add(node.name)
        args = node.args
        positional = [*args.posonlyargs, *args.args]
        required = len(positional) - len(args.defaults)
        parameters = [
            inspect.Parameter(
                a.arg,
                inspect.Parameter.POSITIONAL_ONLY
                if i < len(args.posonlyargs)
                else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty if i < required else None,
            )
            for i, a in enumerate(positional)
        ]
        if args.vararg:
            parameters.append(
                inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL)
            )
        parameters.extend(
            inspect.Parameter(
                a.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if default is None else None,
            )
            for a, default in zip(args.kwonlyargs, args.kw_defaults)
        )
        if args.kwarg:
            parameters.append(
                inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD)
            )
        signatures[node.name] = inspect.Signature(parameters)
    return exports, signatures


def validate_helper_calls(tree):
    exports, signatures = helper_contracts()
    aliases, modules = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fluxyr":
            for alias in node.names:
                if alias.name != "*" and alias.name not in exports:
                    raise ValueError(
                        f"Unknown Fluxyr helper: {alias.name}. Use the shipped Python helper contract."
                    )
                aliases[alias.asname or alias.name] = alias.name
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == "fluxyr")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = aliases.get(node.func.id) if isinstance(node.func, ast.Name) else None
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in modules
        ):
            name = node.func.attr
            if name not in exports:
                raise ValueError(f"Unknown Fluxyr helper: {name}")
        if (
            name not in signatures
            or any(isinstance(a, ast.Starred) for a in node.args)
            or any(k.arg is None for k in node.keywords)
        ):
            continue  # Dynamic argument expansion is checked by Python at execution.
        try:
            signatures[name].bind(
                *([None] * len(node.args)), **{k.arg: None for k in node.keywords}
            )
        except TypeError as exc:
            raise ValueError(
                f"Invalid Fluxyr helper at line {node.lineno}: {name}{signatures[name]}: {exc}. secret(name) reads the token resolved before execution; it has no force_refresh argument."
            ) from None
