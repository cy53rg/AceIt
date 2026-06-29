"""
atlas_policy.py — Code-enforced action policy (not prompt-based).

Every tool wrapper calls PolicyEngine.authorize() before real execution.
The model proposes actions; this module decides ALLOW / ASK / CONFIRM_TYPED / DENY.
Free-text descriptions from the model are never used to change rulings.
"""
from __future__ import annotations

from atlas_data import DEFAULT_SAFETY_MODE

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from atlas_logging import get_logger

log = get_logger("policy")

# Phrases the user must type verbatim for CONFIRM_TYPED actions (never click-only).
TYPED_CONFIRM_PHRASES: dict[str, str] = {
    "financial": "I AUTHORIZE THIS PAYMENT",
    "irreversible": "I CONFIRM DELETE",
    "deauth": "I CONFIRM REVOKE ACCESS",
}

# ── Risk classification (fixed enum — never set by the model) ─────────────────

class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    WRITE_SCOPED = "write_scoped"
    WRITE_SENSITIVE = "write_sensitive"
    SHELL_SAFE = "shell_safe"
    SHELL_DANGEROUS = "shell_dangerous"
    FINANCIAL = "financial"
    IRREVERSIBLE = "irreversible"


class PolicyOutcome(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    CONFIRM_TYPED = "confirm_typed"
    DENY = "deny"


# Backward-compatible aliases used by atlas_core / Phase 0 wiring.
class PolicyDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    CONFIRM_CLICK = "ask"
    CONFIRM_TYPED = "confirm_typed"
    DENY = "deny"
    BLOCK = "deny"

    @classmethod
    def from_outcome(cls, outcome: PolicyOutcome) -> "PolicyDecision":
        mapping = {
            PolicyOutcome.ALLOW: cls.ALLOW,
            PolicyOutcome.ASK: cls.CONFIRM_CLICK,
            PolicyOutcome.CONFIRM_TYPED: cls.CONFIRM_TYPED,
            PolicyOutcome.DENY: cls.BLOCK,
        }
        return mapping[outcome]


class ActionKind(str, Enum):
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    FS_DELETE = "fs.delete"
    FS_EXECUTE = "fs.execute"
    HANDS = "hands.execute"
    SHELL = "shell.exec"
    CONNECTOR_READ = "connector.read"
    CONNECTOR_WRITE = "connector.write"
    CONNECTOR_FINANCIAL = "connector.financial"
    CONNECTOR_DEAUTH = "connector.deauth"


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Code-maintained mapping: action_type → default risk_class.
ACTION_RISK_DEFAULTS: dict[str, RiskClass] = {
    ActionKind.FS_READ.value: RiskClass.READ_ONLY,
    ActionKind.CONNECTOR_READ.value: RiskClass.READ_ONLY,
    ActionKind.FS_WRITE.value: RiskClass.WRITE_SCOPED,
    ActionKind.CONNECTOR_WRITE.value: RiskClass.WRITE_SENSITIVE,
    ActionKind.HANDS.value: RiskClass.WRITE_SENSITIVE,
    ActionKind.FS_EXECUTE.value: RiskClass.SHELL_DANGEROUS,
    ActionKind.SHELL.value: RiskClass.SHELL_DANGEROUS,
    ActionKind.CONNECTOR_FINANCIAL.value: RiskClass.FINANCIAL,
    ActionKind.FS_DELETE.value: RiskClass.IRREVERSIBLE,
    ActionKind.CONNECTOR_DEAUTH.value: RiskClass.IRREVERSIBLE,
}

_FINANCIAL_PREFIXES = (
    "paystack://",
    "connector://paystack/",
    "atlas-connector://paystack/",
)
_DEAUTH_PREFIXES = (
    "connector://deauth/",
    "atlas-connector://deauth/",
    "oauth://revoke/",
)

# Read-only shell commands (explicit allowlist — SHELL_SAFE).
_SHELL_SAFE_VERBS: frozenset[str] = frozenset({
    "dir",
    "type",
    "more",
    "where",
    "echo",
    "pwd",
    "cd",
    "ls",
    "cat",
    "head",
    "tail",
    "git",
})

_SHELL_SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "remote",
})

# Hardcoded deny list — path fragments; never authorized regardless of risk_class.
_DENY_PATH_FRAGMENTS: tuple[str, ...] = (
    "/.ssh/",
    "\\.ssh\\",
    "/.ssh\\",
    "\\.ssh/",
    "/.env",
    "\\.env",
    "/google/chrome/user data",
    "\\google\\chrome\\user data",
    "/mozilla/firefox/profiles",
    "\\mozilla\\firefox\\profiles",
    "/microsoft/edge/user data",
    "\\microsoft\\edge\\user data",
    "/1password/",
    "\\1password\\",
    "/bitwarden",
    "\\bitwarden",
    "/lastpass",
    "\\lastpass",
    "/keepass",
    "\\keepass",
)


@dataclass(frozen=True)
class ActionRequest:
    kind: ActionKind
    resource: str
    summary: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyContext:
    safety_mode: str = DEFAULT_SAFETY_MODE
    fs_access_active: bool = False
    execution_blocked: bool = False
    write_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthorizationResult:
    decision: PolicyOutcome
    reason: str = ""
    confirm_phrase: str = ""
    risk_class: RiskClass = RiskClass.READ_ONLY
    audit_id: str = ""


@dataclass(frozen=True)
class PolicyResult:
    """Legacy result shape for evaluate_action() callers."""
    decision: PolicyDecision
    reason: str = ""
    confirm_phrase: str = ""
    risk: RiskTier = RiskTier.MEDIUM
    audit_id: str = ""
    request: Optional[ActionRequest] = None


ContextProvider = Callable[[], PolicyContext]

_policy_engine: Optional["PolicyEngine"] = None
_context_provider: Optional[ContextProvider] = None


def get_policy_engine(db_path: Path | str | None = None) -> "PolicyEngine":
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine(db_path=db_path)
    return _policy_engine


def set_policy_context_provider(provider: Optional[ContextProvider]) -> None:
    global _context_provider
    _context_provider = provider


def get_policy_context() -> PolicyContext:
    if _context_provider is not None:
        try:
            return _context_provider()
        except Exception as exc:
            log.warning("policy context provider failed: %s", exc)
    return PolicyContext()


def validate_typed_confirmation(phrase_key: str, user_input: str) -> bool:
    expected = TYPED_CONFIRM_PHRASES.get(phrase_key, "")
    if not expected:
        return False
    return (user_input or "") == expected


def _normalize_pathish(detail: str) -> str:
    expanded = str(detail or "").strip().replace("~", str(Path.home()))
    return expanded.replace("/", "\\").lower()


def is_deny_listed(detail: str) -> bool:
    """True when detail targets a hardcoded sensitive path (ignores prose padding)."""
    from atlas_fs_paths import is_path_denied

    # Strip connector/shell URI prefixes before path checks.
    raw = str(detail or "")
    for prefix in ("connector://", "shell://", "atlas-"):
        if raw.startswith(prefix):
            return False
    return is_path_denied(raw)


def _shell_tokens(command: str) -> list[str]:
    cmd = (command or "").strip()
    if not cmd:
        return []
    return re.split(r"\s+", cmd)


def is_shell_safe_command(detail: str) -> bool:
    tokens = _shell_tokens(detail)
    if not tokens:
        return False
    exe = tokens[0].strip("\"'").rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
    exe = re.sub(r"\.(exe|cmd|bat|ps1)$", "", exe)
    if exe == "git" and len(tokens) > 1:
        sub = tokens[1].lower()
        return sub in _SHELL_SAFE_GIT_SUBCOMMANDS
    return exe in _SHELL_SAFE_VERBS and exe != "git"


def classify_action(action_type: str, detail: str) -> RiskClass:
    """
    Map action_type + structured detail to risk_class using code tables only.

    Does not interpret free-text justifications — only path prefixes and command verbs.
    """
    at = (action_type or "").strip().lower()
    d = str(detail or "")

    if any(d.startswith(p) for p in _FINANCIAL_PREFIXES):
        return RiskClass.FINANCIAL
    if any(d.startswith(p) for p in _DEAUTH_PREFIXES):
        return RiskClass.IRREVERSIBLE

    if at in (ActionKind.CONNECTOR_FINANCIAL.value,):
        return RiskClass.FINANCIAL
    if at in (ActionKind.FS_DELETE.value, ActionKind.CONNECTOR_DEAUTH.value):
        return RiskClass.IRREVERSIBLE

    if at in (ActionKind.SHELL.value, ActionKind.FS_EXECUTE.value):
        return RiskClass.SHELL_SAFE if is_shell_safe_command(d) else RiskClass.SHELL_DANGEROUS

    if at in (ActionKind.FS_READ.value, ActionKind.CONNECTOR_READ.value):
        return RiskClass.READ_ONLY

    if at in (ActionKind.HANDS.value, ActionKind.CONNECTOR_WRITE.value):
        return RiskClass.WRITE_SENSITIVE

    return ACTION_RISK_DEFAULTS.get(at, RiskClass.WRITE_SCOPED)


class PolicyLogStore:
    """Append-only audit trail in SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    audit_id TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL
                )
                """
            )

    def record(
        self,
        *,
        action_type: str,
        detail: str,
        risk_class: RiskClass,
        decision: PolicyOutcome,
        user_id: int,
        reason: str,
        audit_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_log
                    (action_type, detail, risk_class, decision, user_id, reason, audit_id, created)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_type,
                    detail,
                    risk_class.value,
                    decision.value,
                    user_id,
                    reason,
                    audit_id,
                    time.time(),
                ),
            )


class PolicyEngine:
    """
    Single authorization gate for all tool execution.

    Call authorize() before running any filesystem, shell, or connector action.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        user_id: int = 0,
    ) -> None:
        if db_path is None:
            try:
                from atlas_data import atlas_db_path
                db_path = atlas_db_path()
            except ImportError:
                db_path = Path("atlas_memory.sqlite3")
        self._log = PolicyLogStore(db_path)
        self._user_id = user_id

    def authorize(
        self,
        action_type: str,
        detail: str,
        risk_class: RiskClass,
        *,
        safety_mode: str | None = None,
        fs_access_active: bool | None = None,
        execution_blocked: bool | None = None,
        write_scopes: tuple[str, ...] | None = None,
        user_id: int | None = None,
        context: PolicyContext | None = None,
    ) -> AuthorizationResult:
        ctx = context or PolicyContext()
        if write_scopes is not None:
            ctx = PolicyContext(
                safety_mode=ctx.safety_mode,
                fs_access_active=ctx.fs_access_active,
                execution_blocked=ctx.execution_blocked,
                write_scopes=write_scopes,
            )
        mode = (safety_mode if safety_mode is not None else ctx.safety_mode or DEFAULT_SAFETY_MODE).lower()
        fs_on = fs_access_active if fs_access_active is not None else ctx.fs_access_active
        blocked = (
            execution_blocked
            if execution_blocked is not None
            else ctx.execution_blocked
        )
        uid = self._user_id if user_id is None else user_id
        audit_id = uuid.uuid4().hex[:12]

        if blocked:
            return self._finish(
                action_type, detail, risk_class, uid, audit_id,
                PolicyOutcome.DENY, "Execution is blocked for this account.",
            )

        if is_deny_listed(detail):
            return self._finish(
                action_type, detail, risk_class, uid, audit_id,
                PolicyOutcome.DENY, "Path is on the hardcoded deny list.",
            )

        if action_type.startswith("shell") or risk_class in (
            RiskClass.SHELL_DANGEROUS,
            RiskClass.SHELL_SAFE,
        ):
            try:
                from atlas_shell import is_shell_denied

                if is_shell_denied(detail):
                    return self._finish(
                        action_type, detail, risk_class, uid, audit_id,
                        PolicyOutcome.DENY,
                        "Command matches Atlas shell deny list.",
                    )
            except ImportError:
                pass

        if risk_class in (RiskClass.FINANCIAL, RiskClass.IRREVERSIBLE):
            phrase_key = "financial" if risk_class == RiskClass.FINANCIAL else "irreversible"
            if risk_class == RiskClass.IRREVERSIBLE and "deauth" in action_type:
                phrase_key = "deauth"
            return self._finish(
                action_type, detail, risk_class, uid, audit_id,
                PolicyOutcome.CONFIRM_TYPED,
                f"{risk_class.value} actions require typed confirmation every time.",
                confirm_phrase=TYPED_CONFIRM_PHRASES.get(phrase_key, TYPED_CONFIRM_PHRASES["irreversible"]),
            )

        if risk_class in (RiskClass.READ_ONLY, RiskClass.SHELL_SAFE):
            return self._finish(
                action_type, detail, risk_class, uid, audit_id,
                PolicyOutcome.ALLOW, f"{risk_class.value} permitted.",
            )

        if risk_class == RiskClass.WRITE_SCOPED:
            from atlas_fs_paths import path_in_write_scope

            if not fs_on and not str(detail).startswith(("atlas-", "connector://")):
                return self._finish(
                    action_type, detail, risk_class, uid, audit_id,
                    PolicyOutcome.DENY, "Filesystem write access is disabled.",
                )
            scopes = ctx.write_scopes
            if not str(detail).startswith(("atlas-", "connector://")):
                if not path_in_write_scope(detail, scopes):
                    return self._finish(
                        action_type, detail, risk_class, uid, audit_id,
                        PolicyOutcome.DENY,
                        "Path is outside your granted write scopes (Settings → Filesystem).",
                    )
            if mode == "off":
                return self._finish(
                    action_type, detail, risk_class, uid, audit_id,
                    PolicyOutcome.ALLOW, "Scoped write permitted (safety off).",
                )
            return self._finish(
                action_type, detail, risk_class, uid, audit_id,
                PolicyOutcome.ASK, "Scoped write requires confirmation.",
            )

        if risk_class in (RiskClass.WRITE_SENSITIVE, RiskClass.SHELL_DANGEROUS):
            if mode == "off":
                return self._finish(
                    action_type, detail, risk_class, uid, audit_id,
                    PolicyOutcome.ALLOW, f"{risk_class.value} permitted (safety off).",
                )
            return self._finish(
                action_type, detail, risk_class, uid, audit_id,
                PolicyOutcome.ASK, f"{risk_class.value} requires confirmation.",
            )

        return self._finish(
            action_type, detail, risk_class, uid, audit_id,
            PolicyOutcome.DENY, f"Unhandled risk_class: {risk_class}",
        )

    def _finish(
        self,
        action_type: str,
        detail: str,
        risk_class: RiskClass,
        user_id: int,
        audit_id: str,
        decision: PolicyOutcome,
        reason: str,
        *,
        confirm_phrase: str = "",
    ) -> AuthorizationResult:
        self._log.record(
            action_type=action_type,
            detail=detail,
            risk_class=risk_class,
            decision=decision,
            user_id=user_id,
            reason=reason,
            audit_id=audit_id,
        )
        log.info(
            "policy [%s] %s %s → %s (%s)",
            audit_id, action_type, risk_class.value, decision.value, reason,
        )
        return AuthorizationResult(
            decision=decision,
            reason=reason,
            confirm_phrase=confirm_phrase,
            risk_class=risk_class,
            audit_id=audit_id,
        )

    # Legacy adapter used by existing FS gate.
    def evaluate(self, request: ActionRequest, context: PolicyContext) -> PolicyResult:
        action_type = request.kind.value
        risk = classify_action(action_type, request.resource)
        if request.kind == ActionKind.SHELL:
            try:
                from atlas_shell import classify_shell_command

                risk = classify_shell_command(
                    str(request.metadata.get("command") or request.resource or "")
                )
            except ImportError:
                cmd = str(request.metadata.get("command") or request.resource or "")
                risk = RiskClass.SHELL_SAFE if is_shell_safe_command(cmd) else RiskClass.SHELL_DANGEROUS
        auth = self.authorize(
            action_type,
            request.resource,
            risk,
            context=context,
        )
        tier = {
            RiskClass.READ_ONLY: RiskTier.LOW,
            RiskClass.SHELL_SAFE: RiskTier.LOW,
            RiskClass.WRITE_SCOPED: RiskTier.MEDIUM,
            RiskClass.WRITE_SENSITIVE: RiskTier.MEDIUM,
            RiskClass.SHELL_DANGEROUS: RiskTier.HIGH,
            RiskClass.FINANCIAL: RiskTier.CRITICAL,
            RiskClass.IRREVERSIBLE: RiskTier.CRITICAL,
        }.get(risk, RiskTier.MEDIUM)
        return PolicyResult(
            decision=PolicyDecision.from_outcome(auth.decision),
            reason=auth.reason,
            confirm_phrase=auth.confirm_phrase,
            risk=tier,
            audit_id=auth.audit_id,
            request=request,
        )


def evaluate_action(
    request: ActionRequest,
    context: Optional[PolicyContext] = None,
) -> PolicyResult:
    ctx = context if context is not None else get_policy_context()
    return get_policy_engine().evaluate(request, ctx)


def build_fs_request(action_value: str, path: str) -> ActionRequest:
    """Map AtlasFileSystem FSPermission + path into a policy ActionRequest."""
    p = str(path or "")
    av = (action_value or "").lower()

    if any(p.startswith(prefix) for prefix in _FINANCIAL_PREFIXES):
        return ActionRequest(
            kind=ActionKind.CONNECTOR_FINANCIAL,
            resource=p,
            summary=f"Financial connector action: {p}",
        )
    if any(p.startswith(prefix) for prefix in _DEAUTH_PREFIXES):
        return ActionRequest(
            kind=ActionKind.CONNECTOR_DEAUTH,
            resource=p,
            summary=f"Connector deauthorization: {p}",
        )
    if p.startswith("atlas-hands://"):
        return ActionRequest(
            kind=ActionKind.HANDS,
            resource=p,
            summary=f"Physical automation: {p}",
        )
    if p.startswith("atlas-task://"):
        return ActionRequest(
            kind=ActionKind.HANDS,
            resource=p,
            summary=f"Task automation step: {p}",
        )
    if p.startswith("atlas-routine://"):
        return ActionRequest(
            kind=ActionKind.HANDS,
            resource=p,
            summary=f"Routine replay: {p}",
        )
    if p.startswith("connector://") or p.startswith("atlas-connector://"):
        return ActionRequest(
            kind=ActionKind.CONNECTOR_WRITE,
            resource=p,
            summary=f"Connector write: {p}",
        )

    if av == "read":
        return ActionRequest(
            kind=ActionKind.FS_READ,
            resource=p,
            summary=f"Read file: {p}",
        )
    if av == "delete":
        return ActionRequest(
            kind=ActionKind.FS_DELETE,
            resource=p,
            summary=f"Delete file: {p}",
        )
    if av == "execute":
        return ActionRequest(
            kind=ActionKind.SHELL,
            resource=p,
            summary=f"Execute script: {p}",
            metadata={"command": p},
        )
    return ActionRequest(
        kind=ActionKind.FS_WRITE,
        resource=p,
        summary=f"Write file: {p}",
    )
