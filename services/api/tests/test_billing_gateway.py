import hashlib
import hmac

from cashsathi_api.billing import RazorpayGateway, provider_payment_from_payload
from cashsathi_api.config import Settings


def gateway() -> RazorpayGateway:
    return RazorpayGateway(
        Settings(
            app_env="test",
            gcp_project_id="cashsathi-test",
            razorpay_key_id="rzp_test_public",
            razorpay_key_secret="checkout-secret",
            razorpay_webhook_secret="current-webhook-secret",
            razorpay_previous_webhook_secret="previous-webhook-secret",
        )
    )


def test_checkout_signature_uses_server_order_id() -> None:
    adapter = gateway()
    expected = hmac.new(b"checkout-secret", b"order_server|pay_1", hashlib.sha256).hexdigest()
    assert adapter.verify_checkout_signature(
        order_id="order_server", payment_id="pay_1", signature=expected
    )
    assert not adapter.verify_checkout_signature(
        order_id="order_tampered", payment_id="pay_1", signature=expected
    )


def test_webhook_signature_accepts_current_and_previous_rotation_secrets() -> None:
    adapter = gateway()
    body = b'{"event":"payment.captured"}'
    current = hmac.new(b"current-webhook-secret", body, hashlib.sha256).hexdigest()
    previous = hmac.new(b"previous-webhook-secret", body, hashlib.sha256).hexdigest()
    assert adapter.verify_webhook_signature(body, current)
    assert adapter.verify_webhook_signature(body, previous)
    assert not adapter.verify_webhook_signature(body, "invalid")


def test_provider_payment_parser_keeps_only_allowlisted_fields() -> None:
    payment = provider_payment_from_payload(
        {
            "id": "pay_1",
            "order_id": "order_1",
            "amount": 29_900,
            "currency": "INR",
            "status": "captured",
            "method": "upi",
            "email": "owner@example.com",
            "vpa": "sensitive@upi",
            "card": {"last4": "1234"},
        }
    )
    assert payment.id == "pay_1"
    assert payment.method == "upi"
    assert not hasattr(payment, "vpa")
    assert not hasattr(payment, "card")
