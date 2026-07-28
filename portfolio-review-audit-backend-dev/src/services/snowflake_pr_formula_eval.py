"""
Safe arithmetic evaluation for Snowflake PR → FinancialDataSnowflake formulas.

Supports ``+``, ``-``, ``*``, ``/``, parentheses, unary ``+``/``-``, and numeric literals.
Identifiers must resolve to keys in the caller-supplied variables mapping (whitelist enforced upstream).

Uses ``ast.parse(..., mode='eval')`` — no arbitrary Python execution.
"""
from __future__ import annotations

import ast
import math
import operator as op
from typing import Any, Optional

_ALLOWED_BINOPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: op.pos,
    ast.USub: op.neg,
}


class FormulaEvaluationError(ValueError):
    pass


def collect_formula_identifiers(expression: str) -> set[str]:
    """Return bare names referenced in ``expression`` (not validated against whitelist)."""
    expression = (expression or "").strip()
    if not expression:
        return set()
    tree = ast.parse(expression, mode="eval")

    names: set[str] = set()

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Expression):
            walk(node.body)
        elif isinstance(node, ast.BinOp):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, ast.UnaryOp):
            walk(node.operand)
        elif isinstance(node, ast.Constant):
            pass
        elif isinstance(node, ast.Num):  # py<3.8 compat unused but harmless
            pass
        else:
            raise FormulaEvaluationError(f"disallowed syntax in formula: {type(node).__name__}")

    walk(tree)
    return names


def validate_formula(expression: str, *, allowed_identifiers: frozenset[str]) -> None:
    """Raise ``FormulaEvaluationError`` if syntax or identifiers are invalid."""
    expression = (expression or "").strip()
    if not expression:
        raise FormulaEvaluationError("formula is empty")
    if len(expression) > 512:
        raise FormulaEvaluationError("formula exceeds maximum length")
    bad_chars = {"`", "'", '"', ";", "\\"}
    if any(c in expression for c in bad_chars):
        raise FormulaEvaluationError("formula contains forbidden characters")
    tree = ast.parse(expression, mode="eval")

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            walk(node.body)
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise FormulaEvaluationError("only numeric literals are allowed")
            if isinstance(node.value, float) and (math.isnan(node.value) or math.isinf(node.value)):
                raise FormulaEvaluationError("invalid numeric literal")
        elif isinstance(node, ast.Num):
            if isinstance(node.n, float) and (math.isnan(node.n) or math.isinf(node.n)):
                raise FormulaEvaluationError("invalid numeric literal")
        elif isinstance(node, ast.Name):
            if node.id not in allowed_identifiers:
                raise FormulaEvaluationError(f"unknown column identifier {node.id!r}")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _ALLOWED_BINOPS:
                raise FormulaEvaluationError(f"unsupported operator {type(node.op).__name__}")
            walk(node.left)
            walk(node.right)
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _ALLOWED_UNARYOPS:
                raise FormulaEvaluationError(f"unsupported unary operator {type(node.op).__name__}")
            walk(node.operand)
        else:
            raise FormulaEvaluationError(f"disallowed syntax: {type(node).__name__}")

    walk(tree)


def evaluate_formula(expression: str, *, variables: dict[str, Optional[float]]) -> Optional[float]:
    """
    Evaluate ``expression`` using ``variables`` (missing keys treated as ``None``).

    If any referenced operand is ``None``, binary/unary propagation yields ``None``.
    Division by zero yields ``None``.
    """
    expression = (expression or "").strip()
    if not expression:
        return None
    tree = ast.parse(expression, mode="eval")

    def eval_node(node: ast.AST) -> Optional[float]:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return None
            if isinstance(node.value, int):
                return float(node.value)
            if isinstance(node.value, float):
                x = float(node.value)
                if math.isnan(x) or math.isinf(x):
                    return None
                return x
            return None
        if isinstance(node, ast.Num):  # pragma: no cover - legacy AST
            if isinstance(node.n, bool):
                return None
            return float(node.n)
        if isinstance(node, ast.Name):
            v = variables.get(node.id)
            if v is None:
                return None
            try:
                x = float(v)
            except (TypeError, ValueError):
                return None
            if math.isnan(x) or math.isinf(x):
                return None
            return x
        if isinstance(node, ast.UnaryOp):
            inner = eval_node(node.operand)
            if inner is None:
                return None
            fn = _ALLOWED_UNARYOPS.get(type(node.op))
            if fn is None:
                return None
            try:
                out = float(fn(inner))
            except Exception:
                return None
            if math.isnan(out) or math.isinf(out):
                return None
            return out
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            if left is None or right is None:
                return None
            fn = _ALLOWED_BINOPS.get(type(node.op))
            if fn is None:
                return None
            try:
                if isinstance(node.op, ast.Div) and right == 0:
                    return None
                out = float(fn(left, right))
            except Exception:
                return None
            if math.isnan(out) or math.isinf(out):
                return None
            return out
        return None

    return eval_node(tree)


def numeric_identifier_whitelist_from_pr_submission_model(model_cls: Any) -> list[str]:
    """SQLAlchemy column keys on ``PRSubmissionDataRaw`` usable as formula variables."""
    from sqlalchemy import BigInteger, Float, Integer, Numeric, SmallInteger

    numeric_types = (Numeric, Float, Integer, BigInteger, SmallInteger)
    names: list[str] = []
    for col in model_cls.__table__.columns:
        if isinstance(col.type, numeric_types):
            names.append(col.key)
    names.sort()
    return names

