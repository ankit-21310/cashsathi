import pytest
from pydantic import ValidationError

from cashsathi_api.config import Settings
from cashsathi_api.domain import PolicyDefaults


def test_policy_defaults_use_minor_units() -> None:
    policies = PolicyDefaults()

    assert policies.reminder_cooldown_hours == 72
    assert policies.high_value_threshold_minor == 5_000_000
    assert policies.high_value_currency == "INR"
    assert policies.dispute_requires_human is True
    assert policies.legal_language_allowed is False
    assert policies.payment_confirmation_required is True


def test_production_rejects_emulator_hosts() -> None:
    with pytest.raises(ValidationError, match="must not be set in production"):
        Settings(
            app_env="production",
            gcp_project_id="cashsathi-production",
            cors_allowed_origins="https://example.run.app",
            firebase_auth_emulator_host="127.0.0.1:9099",
        )


def test_production_requires_https_cors() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            app_env="production",
            gcp_project_id="cashsathi-production",
            cors_allowed_origins="http://example.test",
        )


def test_strict_production_validates_integrations_scheduler_and_admin() -> None:
    with pytest.raises(ValidationError, match="scheduler audience must use HTTPS"):
        Settings(
            app_env="production",
            strict_production_readiness=True,
            gcp_project_id="cashsathi-production",
            cors_allowed_origins="https://web.example.test",
            web_base_url="https://web.example.test",
            firebase_auth_emulator_host=None,
            firestore_emulator_host=None,
            gemini_api_key="configured",
            gmail_oauth_client_id="configured",
            gmail_oauth_client_secret="configured",
            gmail_oauth_redirect_uri="https://api.example.test/api/integrations/gmail/callback",
            gmail_kms_key_name="projects/test/locations/test/keyRings/test/cryptoKeys/test",
            scheduler_service_account_email="scheduler@example.test",
            scheduler_audience="http://api.example.test",
            platform_admin_uids="admin-uid",
        )
