import os
from pathlib import Path

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///./data/test.db",
        "ADMIN_API_KEY": "test-admin-key-that-is-long-enough-000000",
        "TOKEN_PEPPER": "test-token-pepper-that-is-long-enough-0000",
        "AI_PROVIDER": "mock",
        "RELEASE_DIR": "releases-test",
    }
)

Path("data/test.db").unlink(missing_ok=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from kid_terminal.app import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers():
    return {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}


@pytest.fixture
def enrolled(client, admin_headers):
    code = client.post(
        "/v1/admin/enrollments", headers=admin_headers, json={"label": "pytest"}
    ).json()["enrollment_token"]
    result = client.post(
        "/v1/enroll",
        json={
            "enrollment_token": code,
            "device_name": "test-device",
            "app_version": "1.0",
            "os_version": "Android 15",
            "manufacturer": "Xiaomi",
            "device_model": "2201123C",
            "security_patch": "2026-05-01",
        },
    )
    assert result.status_code == 201
    return result.json()
