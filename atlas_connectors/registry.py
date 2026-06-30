"""Connector registry — single execution gate through PolicyEngine."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

from atlas_connectors.base import Connector, ConnectorActionSpec
from atlas_connectors.gmail import GmailConnector
from atlas_connectors.github import GitHubConnector
from atlas_connectors.google_calendar import GoogleCalendarConnector
from atlas_connectors.notion import NotionConnector
from atlas_connectors.paystack import PaystackConnector
from atlas_connectors.tokens import TokenStore
from atlas_data import DEFAULT_SAFETY_MODE
from atlas_logging import get_logger
from atlas_policy import (
    AuthorizationResult,
    PolicyContext,
    PolicyEngine,
    PolicyOutcome,
    RiskClass,
)

log = get_logger("connectors.registry")

PermissionHandler = Callable[[str, str], bool]
TypedConfirmHandler = Callable[[dict], bool]


def _build_action_risk_map(connectors: dict[str, Connector]) -> dict[str, RiskClass]:
    mapping: dict[str, RiskClass] = {}
    for cid, conn in connectors.items():
        for spec in conn.list_actions():
            mapping[f"{cid}.{spec.method}"] = spec.risk_class
    return mapping


# Firewall rules — explicit per action method (review like ACLs).
ACTION_RISK_MAP: dict[str, RiskClass] = {}


class ConnectorRegistry:
    """Owns connectors, token store, and policy-gated execution."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        user_id: int = 0,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        if db_path is None:
            try:
                from atlas_data import atlas_db_path
                db_path = atlas_db_path()
            except ImportError:
                db_path = Path("atlas_memory.sqlite3")
        self._db_path = Path(db_path)
        self._user_id = user_id
        self._tokens = TokenStore(self._db_path)
        self._policy = policy_engine or PolicyEngine(self._db_path, user_id=user_id)
        self._connectors: dict[str, Connector] = {
            "github": GitHubConnector(self._tokens),
            "google_calendar": GoogleCalendarConnector(self._tokens),
            "paystack": PaystackConnector(self._tokens),
            "gmail": GmailConnector(self._tokens),
            "notion": NotionConnector(self._tokens),
        }
        if (os.environ.get("COMPOSIO_API_KEY") or "").strip():
            from atlas_connectors.composio_bridge import ComposioBridgeConnector

            self._connectors["composio"] = ComposioBridgeConnector(self._tokens)
        global ACTION_RISK_MAP
        ACTION_RISK_MAP = _build_action_risk_map(self._connectors)
        self._permission_handler: Optional[PermissionHandler] = None
        self._typed_confirm_handler: Optional[TypedConfirmHandler] = None

    def set_permission_handler(self, handler: PermissionHandler) -> None:
        self._permission_handler = handler

    def set_typed_confirm_handler(self, handler: TypedConfirmHandler) -> None:
        self._typed_confirm_handler = handler

    def get(self, connector_id: str) -> Connector | None:
        return self._connectors.get(connector_id)

    def list_connectors(self) -> list[dict[str, Any]]:
        return [c.status_dict() for c in self._connectors.values()]

    def connect(self, connector_id: str) -> tuple[bool, str]:
        conn = self.get(connector_id)
        if not conn:
            return False, f"Unknown connector: {connector_id}"
        return conn.connect()

    def disconnect(self, connector_id: str) -> tuple[bool, str]:
        conn = self.get(connector_id)
        if not conn:
            return False, f"Unknown connector: {connector_id}"
        return conn.disconnect()

    def authorize_only(
        self,
        connector_id: str,
        method: str,
        *,
        safety_mode: str = DEFAULT_SAFETY_MODE,
        fs_access_active: bool = True,
        execution_blocked: bool = False,
        **params: Any,
    ) -> AuthorizationResult:
        key = f"{connector_id}.{method}"
        risk = ACTION_RISK_MAP.get(key)
        if risk is None:
            return AuthorizationResult(
                decision=PolicyOutcome.DENY,
                reason=f"Unknown connector action: {key}",
                risk_class=RiskClass.WRITE_SCOPED,
                audit_id="",
            )
        detail = f"connector://{connector_id}/{method}?{json.dumps(params, sort_keys=True)}"
        ctx = PolicyContext(
            safety_mode=safety_mode,
            fs_access_active=fs_access_active,
            execution_blocked=execution_blocked,
        )
        return self._policy.authorize(
            f"connector.{method}",
            detail,
            risk,
            context=ctx,
            user_id=self._user_id,
        )

    def execute(
        self,
        connector_id: str,
        method: str,
        *,
        safety_mode: str = DEFAULT_SAFETY_MODE,
        fs_access_active: bool = True,
        execution_blocked: bool = False,
        **params: Any,
    ) -> dict[str, Any]:
        auth = self.authorize_only(
            connector_id,
            method,
            safety_mode=safety_mode,
            fs_access_active=fs_access_active,
            execution_blocked=execution_blocked,
            **params,
        )
        if auth.decision == PolicyOutcome.DENY:
            return {
                "ok": False,
                "denied": True,
                "decision": auth.decision.value,
                "reason": auth.reason,
            }
        if auth.decision == PolicyOutcome.CONFIRM_TYPED:
            if not self._typed_confirm_handler:
                return {
                    "ok": False,
                    "denied": True,
                    "decision": auth.decision.value,
                    "reason": "Typed confirmation required but no handler registered.",
                }
            approved = self._typed_confirm_handler({
                "confirm_phrase": auth.confirm_phrase,
                "reason": auth.reason,
                "path": f"connector://{connector_id}/{method}",
                "audit_id": auth.audit_id,
            })
            if not approved:
                return {
                    "ok": False,
                    "denied": True,
                    "decision": auth.decision.value,
                    "reason": "Typed confirmation denied.",
                }
        elif auth.decision == PolicyOutcome.ASK:
            if not self._permission_handler:
                return {
                    "ok": False,
                    "denied": True,
                    "decision": auth.decision.value,
                    "reason": "Confirmation required but no permission handler registered.",
                }
            approved = self._permission_handler(
                f"connector.{method}",
                f"connector://{connector_id}/{method}",
            )
            if not approved:
                return {
                    "ok": False,
                    "denied": True,
                    "decision": auth.decision.value,
                    "reason": "User denied connector action.",
                }

        conn = self.get(connector_id)
        if not conn:
            return {"ok": False, "denied": True, "reason": f"Unknown connector: {connector_id}"}
        fn = getattr(conn, method, None)
        if not fn or not getattr(fn, "_connector_action", None):
            return {"ok": False, "denied": True, "reason": f"Unknown action: {method}"}
        try:
            result = fn(**params)
            return {"ok": True, "result": result, "decision": auth.decision.value}
        except Exception as exc:
            log.exception("connector %s.%s failed", connector_id, method)
            return {"ok": False, "error": str(exc)}


def execute_connector_action(
    registry: ConnectorRegistry,
    connector_id: str,
    method: str,
    *,
    context: PolicyContext | None = None,
    **params: Any,
) -> dict[str, Any]:
    ctx = context or PolicyContext()
    return registry.execute(
        connector_id,
        method,
        safety_mode=ctx.safety_mode,
        fs_access_active=ctx.fs_access_active,
        execution_blocked=ctx.execution_blocked,
        **params,
    )
