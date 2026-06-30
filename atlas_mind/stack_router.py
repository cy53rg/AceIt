"""
atlas_mind/stack_router.py — Zero-API fast path (Skales Stack pattern).

Answers time and simple math without calling Groq.
"""
from __future__ import annotations

import ast
import datetime
import operator
import re
from typing import Any, Optional

_TIME_PATTERNS = (
    re.compile(r"\bwhat(?:'s| is) the (?:time|date)\b", re.I),
    re.compile(r"\bwhat time is it\b", re.I),
    re.compile(r"\bcurrent (?:time|date)\b", re.I),
    re.compile(r"^time\??$", re.I),
)

_MATH_PREFIX = re.compile(
    r"(?i)^(?:what(?:'s| is)|calculate|compute|solve|evaluate)\s+(.+?)[\?\.!]?$"
)
_BARE_MATH = re.compile(r"^[\d\s+\-*/().,%]+$")

_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval_math(expr: str) -> Optional[float]:
    """Evaluate a numeric expression via AST (no eval())."""
    expr = (expr or "").strip().replace(",", "")
    if not expr or not _BARE_MATH.match(expr):
        return None
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None

    def _eval(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY_OPS:
            return float(_UNARY_OPS[type(n.op)](_eval(n.operand)))
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            return float(_BIN_OPS[type(n.op)](_eval(n.left), _eval(n.right)))
        raise ValueError("unsupported expression")

    try:
        return _eval(node)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6g}"


def try_stack_answer(text: str) -> Optional[str]:
    """
    Return a direct answer for time/math queries, or None to continue routing.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 200:
        return None

    for pat in _TIME_PATTERNS:
        if pat.search(raw):
            now = datetime.datetime.now()
            if re.search(r"\bdate\b", raw, re.I) and "time" not in raw.lower():
                return now.strftime("Today is %A, %B %d, %Y.")
            return now.strftime("It's %I:%M %p on %A, %B %d.")

    expr: Optional[str] = None
    m = _MATH_PREFIX.match(raw)
    if m:
        expr = m.group(1).strip()
    elif _BARE_MATH.match(raw):
        expr = raw

    if expr:
        result = _safe_eval_math(expr)
        if result is not None:
            return f"{_format_number(result)}"

    return None
