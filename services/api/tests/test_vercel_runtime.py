from __future__ import annotations

import base64
import json
import runpy
from pathlib import Path

import pytest

from cashsathi_api.auth import decode_firebase_service_account
from cashsathi_api.config import Settings


def encoded_service_account(project_id: str) -> str:
    payload = {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "private-id",
        "private_key": "private-material",
        "client_email": "cashsathi-vercel-api@example.test",
        "client_id": "123",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_service_account_is_parsed_in_memory_for_expected_project() -> None:
    settings = Settings(
        app_env="test",
        gcp_project_id="cashsathi",
        firebase_service_account_json_b64=encoded_service_account("cashsathi"),
    )
    payload = decode_firebase_service_account(settings)
    assert payload["project_id"] == "cashsathi"


@pytest.mark.parametrize("encoded", ["not-base64!", base64.b64encode(b"not-json").decode()])
def test_service_account_malformed_input_is_redacted(encoded: str) -> None:
    with pytest.raises(ValueError, match="malformed") as error:
        decode_firebase_service_account(
            Settings(app_env="test", firebase_service_account_json_b64=encoded)
        )
    assert encoded not in str(error.value)


def test_service_account_rejects_project_mismatch_without_leaking_key() -> None:
    encoded = encoded_service_account("wrong-project")
    with pytest.raises(ValueError, match="does not match") as error:
        decode_firebase_service_account(
            Settings(
                app_env="test",
                gcp_project_id="cashsathi",
                firebase_service_account_json_b64=encoded,
            )
        )
    assert "private-material" not in str(error.value)


def test_vercel_entrypoint_exports_fastapi_app() -> None:
    namespace = runpy.run_path(Path(__file__).parents[1] / "api" / "index.py")
    app = namespace["app"]

    assert app.title == "CashSathi API"
