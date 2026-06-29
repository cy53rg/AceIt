"""Paystack connector — TEST mode secret key; explicit financial actions."""
from __future__ import annotations

import os
from typing import Any

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.paystack")

_PAYSTACK_API = "https://api.paystack.co"


class PaystackConnector(Connector):
    connector_id = "paystack"
    display_name = "Paystack"

    @property
    def scopes_requested(self) -> list[str]:
        return ["transactions:read", "balance:read", "transfers:write"]

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can read Paystack balances and transactions (test or live mode per your key). "
            "Transfers and refunds always require typed confirmation — Atlas cannot move money "
            "without you typing the authorization phrase every time."
        )

    def connect(self) -> tuple[bool, str]:
        key = (os.environ.get("PAYSTACK_SECRET_KEY") or "").strip()
        if not key:
            return False, "Set PAYSTACK_SECRET_KEY in .env (use sk_test_… for sandbox)."
        if not key.startswith("sk_test_") and os.environ.get("ATLAS_ALLOW_LIVE_PAYSTACK", "") != "1":
            return False, "Only test keys (sk_test_…) allowed unless ATLAS_ALLOW_LIVE_PAYSTACK=1."
        mode = "test" if key.startswith("sk_test_") else "live"
        self._token_store.save_token(
            self.connector_id,
            {"secret_key": key, "mode": mode},
            scopes=self.scopes_requested,
        )
        return True, f"Paystack connected ({mode} mode)."

    def disconnect(self) -> tuple[bool, str]:
        self._token_store.delete_token(self.connector_id)
        return True, "Paystack disconnected."

    def _secret(self) -> str:
        tok = self._token_store.load_token(self.connector_id)
        if not tok or not tok.get("secret_key"):
            raise RuntimeError("Paystack not connected")
        return str(tok["secret_key"])

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret()}", "Content-Type": "application/json"}

    def _api_get(self, path: str) -> dict:
        r = requests.get(f"{_PAYSTACK_API}{path}", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def _api_post(self, path: str, payload: dict) -> dict:
        r = requests.post(f"{_PAYSTACK_API}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    @connector_action("get_balance", RiskClass.READ_ONLY)
    def get_balance(self) -> dict:
        return self._api_get("/balance")

    @connector_action("list_transactions", RiskClass.READ_ONLY)
    def list_transactions(self, *, count: int = 10) -> dict:
        return self._api_get(f"/transaction?perPage={min(count, 50)}")

    @connector_action("initiate_transfer", RiskClass.FINANCIAL)
    def initiate_transfer(self, recipient: str, amount_kobo: int, reason: str = "") -> dict:
        return self._api_post("/transfer", {
            "source": "balance",
            "recipient": recipient,
            "amount": int(amount_kobo),
            "reason": reason or "Atlas transfer",
        })

    @connector_action("refund", RiskClass.FINANCIAL)
    def refund(self, transaction_reference: str, amount_kobo: int | None = None) -> dict:
        payload: dict[str, Any] = {"transaction": transaction_reference}
        if amount_kobo is not None:
            payload["amount"] = int(amount_kobo)
        return self._api_post("/refund", payload)
