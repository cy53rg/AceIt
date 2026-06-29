"""Connector base class and action metadata."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

from atlas_policy import RiskClass


@dataclass(frozen=True)
class ConnectorActionSpec:
    method: str
    risk_class: RiskClass
    description: str = ""


def connector_action(
    method: str,
    risk_class: RiskClass,
    *,
    description: str = "",
) -> Callable:
    """Register explicit risk_class on a connector method (firewall rule)."""

    def decorator(fn: Callable) -> Callable:
        fn._connector_action = ConnectorActionSpec(method, risk_class, description)  # type: ignore
        return fn

    return decorator


class Connector(ABC):
    connector_id: str = ""
    display_name: str = ""

    def __init__(self, token_store) -> None:
        self._token_store = token_store

    @property
    @abstractmethod
    def scopes_requested(self) -> list[str]:
        """Minimal OAuth scopes — never over-request."""

    @abstractmethod
    def scope_boundary_text(self) -> str:
        """Plain-language description of what Atlas can and cannot do."""

    @abstractmethod
    def connect(self) -> tuple[bool, str]:
        """Start OAuth / credential flow; store encrypted token on success."""

    @abstractmethod
    def disconnect(self) -> tuple[bool, str]:
        """Revoke token and delete local encrypted copy."""

    def is_connected(self) -> bool:
        return self._token_store.load_token(self.connector_id) is not None

    def granted_scopes(self) -> list[str]:
        return self._token_store.get_scopes(self.connector_id)

    def status_dict(self) -> dict[str, Any]:
        return {
            "id": self.connector_id,
            "display_name": self.display_name,
            "connected": self.is_connected(),
            "scopes_requested": list(self.scopes_requested),
            "scopes_granted": self.granted_scopes(),
            "boundary_text": self.scope_boundary_text(),
        }

    def list_actions(self) -> list[ConnectorActionSpec]:
        specs: list[ConnectorActionSpec] = []
        for name in dir(self):
            if name.startswith("_"):
                continue
            fn = getattr(self, name, None)
            spec = getattr(fn, "_connector_action", None)
            if spec:
                specs.append(spec)
        return specs
