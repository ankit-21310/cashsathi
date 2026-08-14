"""Seed two isolated businesses in local Firebase emulators only."""

import os

os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
os.environ.setdefault("GCLOUD_PROJECT", "cashsathi-local")

from firebase_admin import auth

from cashsathi_api.auth import initialize_firebase
from cashsathi_api.config import Settings
from cashsathi_api.domain import AuthenticatedUser
from cashsathi_api.repository import FirestoreRepository


def ensure_local(host: str) -> None:
    if not host.startswith(("127.0.0.1:", "localhost:")):
        raise RuntimeError("Seed script refuses to connect to a non-local emulator")


def main() -> None:
    ensure_local(os.environ["FIREBASE_AUTH_EMULATOR_HOST"])
    ensure_local(os.environ["FIRESTORE_EMULATOR_HOST"])
    settings = Settings(
        app_env="development",
        gcp_project_id="cashsathi-local",
        firebase_auth_emulator_host=os.environ["FIREBASE_AUTH_EMULATOR_HOST"],
        firestore_emulator_host=os.environ["FIRESTORE_EMULATOR_HOST"],
    )
    initialize_firebase(settings)
    repository = FirestoreRepository(settings)

    seeds = (
        ("demo-owner-a", "owner-a@example.test", "DemoPass!123", "Aster Studio"),
        ("demo-owner-b", "owner-b@example.test", "DemoPass!123", "Beacon Consulting"),
    )
    for uid, email, password, business_name in seeds:
        try:
            auth.get_user(uid)
        except auth.UserNotFoundError:
            auth.create_user(uid=uid, email=email, password=password, display_name=business_name)
        repository.get_or_create_business(
            AuthenticatedUser(uid=uid, email=email, display_name=business_name), business_name
        )
        print(f"Seeded {email} -> {business_name}")


if __name__ == "__main__":
    main()
