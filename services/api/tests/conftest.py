from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cashsathi_api.config import Settings
from cashsathi_api.domain import AuthenticatedUser
from cashsathi_api.errors import ApiError
from cashsathi_api.main import create_app
from cashsathi_api.repository import InMemoryRepository


class TestAuthVerifier:
    __test__ = False

    def verify(self, token: str) -> AuthenticatedUser:
        users = {
            "alice-token": AuthenticatedUser("alice", "alice@example.com", "Alice"),
            "bob-token": AuthenticatedUser("bob", "bob@example.com", "Bob"),
        }
        if token not in users:
            raise ApiError(401, "invalid_token", "The authentication token is invalid or expired.")
        return users[token]


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def client(repository: InMemoryRepository) -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        gcp_project_id="cashsathi-test",
        cors_allowed_origins="http://localhost:3000",
    )
    app = create_app(settings=settings, repository=repository, auth_verifier=TestAuthVerifier())
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
