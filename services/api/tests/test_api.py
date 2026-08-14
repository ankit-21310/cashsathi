from fastapi.testclient import TestClient

from cashsathi_api.domain import AuthenticatedUser
from cashsathi_api.repository import InMemoryRepository


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_readiness_are_public(client: TestClient) -> None:
    health = client.get("/healthz")
    ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert health.headers["x-request-id"]


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "test-request-123"})
    assert response.headers["x-request-id"] == "test-request-123"


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "unsafe request id"})
    assert response.headers["x-request-id"] != "unsafe request id"


def test_protected_route_requires_token(client: TestClient) -> None:
    response = client.get("/api/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_forged_token_is_rejected(client: TestClient) -> None:
    response = client.get("/api/me", headers=auth("forged"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_business_onboarding_is_idempotent(client: TestClient) -> None:
    first = client.post(
        "/api/businesses", headers=auth("alice-token"), json={"name": "Alice Studio"}
    )
    second = client.post(
        "/api/businesses", headers=auth("alice-token"), json={"name": "Changed Name"}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["name"] == "Alice Studio"


def test_current_business_is_derived_from_authenticated_user(client: TestClient) -> None:
    client.post("/api/businesses", headers=auth("alice-token"), json={"name": "Alice Studio"})
    client.post("/api/businesses", headers=auth("bob-token"), json={"name": "Bob Consulting"})

    alice = client.get("/api/businesses/current", headers=auth("alice-token"))
    bob = client.get("/api/businesses/current", headers=auth("bob-token"))

    assert alice.status_code == 200
    assert bob.status_code == 200
    assert alice.json()["owner_user_id"] == "alice"
    assert bob.json()["owner_user_id"] == "bob"
    assert alice.json()["id"] != bob.json()["id"]


def test_repository_tenant_context_cannot_cross_businesses(
    client: TestClient, repository: InMemoryRepository
) -> None:
    client.post("/api/businesses", headers=auth("alice-token"), json={"name": "Alice Studio"})
    client.post("/api/businesses", headers=auth("bob-token"), json={"name": "Bob Consulting"})

    alice_context = repository.require_tenant(AuthenticatedUser("alice", None, None))
    bob_context = repository.require_tenant(AuthenticatedUser("bob", None, None))

    assert alice_context.business_id != bob_context.business_id


def test_validation_uses_safe_error_envelope(client: TestClient) -> None:
    response = client.post("/api/businesses", headers=auth("alice-token"), json={"name": "x"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "input" not in str(response.json()["error"]["details"])
