from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from cashsathi_api.config import Settings
from cashsathi_api.errors import ApiError


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    id: str
    status: str


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    id: str
    order_id: str
    amount: int
    currency: str
    status: str
    amount_refunded: int
    fee: int
    tax: int
    method: str | None
    email: str | None
    error_code: str | None
    error_description: str | None
    created_at: int


class PaymentGateway(Protocol):
    public_key_id: str

    def create_or_find_order(
        self, *, amount_minor: int, currency: str, receipt: str, billing_order_id: str
    ) -> ProviderOrder: ...

    def fetch_payment(self, payment_id: str) -> ProviderPayment: ...

    def verify_checkout_signature(
        self, *, order_id: str, payment_id: str, signature: str
    ) -> bool: ...

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool: ...


class RazorpayGateway:
    base_url = "https://api.razorpay.com/v1"

    def __init__(self, settings: Settings) -> None:
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise ApiError(503, "billing_not_configured", "Payment checkout is not configured.")
        self.public_key_id = settings.razorpay_key_id
        self._key_secret = settings.razorpay_key_secret.get_secret_value()
        self._webhook_secrets = [
            secret.get_secret_value()
            for secret in (
                settings.razorpay_webhook_secret,
                settings.razorpay_previous_webhook_secret,
            )
            if secret is not None
        ]
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=(self.public_key_id, self._key_secret),
            timeout=settings.razorpay_timeout_seconds,
            headers={"Accept": "application/json"},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            structlog.get_logger("billing").error(
                "payment_provider_request_failed",
                category="billing_provider_failure",
                method=method,
                path=path,
            )
            raise ApiError(
                503,
                "payment_provider_unavailable",
                "The payment provider could not complete the request. Try again shortly.",
            ) from exc
        if not isinstance(payload, dict):
            raise ApiError(
                503, "payment_provider_invalid", "The payment provider response was invalid."
            )
        return payload

    def create_or_find_order(
        self, *, amount_minor: int, currency: str, receipt: str, billing_order_id: str
    ) -> ProviderOrder:
        try:
            payload = self._request(
                "POST",
                "/orders",
                json={
                    "amount": amount_minor,
                    "currency": currency,
                    "receipt": receipt,
                    "partial_payment": False,
                    "notes": {"billing_order_id": billing_order_id},
                },
            )
        except ApiError:
            # Razorpay treats receipt as unique. A read-after-ambiguous-write recovers
            # safely without creating a second payable order.
            payload = self._request("GET", "/orders", params={"receipt": receipt, "count": 1})
            items = payload.get("items")
            if not isinstance(items, list) or not items or not isinstance(items[0], dict):
                raise
            payload = items[0]
        provider_id = payload.get("id")
        if not isinstance(provider_id, str):
            raise ApiError(503, "payment_provider_invalid", "The payment order was invalid.")
        return ProviderOrder(id=provider_id, status=str(payload.get("status", "created")))

    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        payload = self._request("GET", f"/payments/{payment_id}")
        return provider_payment_from_payload(payload, payment_id)

    def verify_checkout_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        expected = hmac.new(
            self._key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        return any(
            hmac.compare_digest(
                hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), signature
            )
            for secret in self._webhook_secrets
        )


def provider_payment_from_payload(
    payload: dict[str, Any], fallback_payment_id: str = ""
) -> ProviderPayment:
    order_id = payload.get("order_id")
    if not isinstance(order_id, str):
        raise ApiError(422, "payment_order_missing", "The payment is not linked to an order.")
    return ProviderPayment(
        id=str(payload.get("id", fallback_payment_id)),
        order_id=order_id,
        amount=int(payload.get("amount", 0)),
        currency=str(payload.get("currency", "")),
        status=str(payload.get("status", "")),
        amount_refunded=int(payload.get("amount_refunded", 0) or 0),
        fee=int(payload.get("fee", 0) or 0),
        tax=int(payload.get("tax", 0) or 0),
        method=str(payload["method"]) if payload.get("method") else None,
        email=str(payload["email"]) if payload.get("email") else None,
        error_code=str(payload["error_code"]) if payload.get("error_code") else None,
        error_description=(
            str(payload["error_description"])[:300] if payload.get("error_description") else None
        ),
        created_at=int(payload.get("created_at", 0)),
    )
