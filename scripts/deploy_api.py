#!/usr/bin/env python3
import os
import subprocess
import time

import httpx

from kid_terminal.config import get_settings


def docker(*args: str) -> None:
    subprocess.run(["sudo", "docker", "compose", *args], check=True)  # noqa: S603, S607


def main() -> None:
    settings = get_settings()
    headers = {"X-Admin-Key": settings.admin_api_key}
    docker("build", "api")
    draining = False
    try:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
            response = client.post("/v1/admin/drain", headers=headers, params={"enabled": True})
            response.raise_for_status()
            draining = True
            deadline = time.monotonic() + 600
        while response.json()["active_responses"]:
            if time.monotonic() >= deadline:
                raise TimeoutError("active voice response did not drain within 10 minutes")
            time.sleep(1)
            response = client.get("/v1/admin/activity", headers=headers)
            response.raise_for_status()
        docker("run", "--rm", "api", "alembic", "upgrade", "head")
        docker("up", "-d", "--no-build", "api")
        draining = False
    finally:
        if draining:
            with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10) as client:
                client.post("/v1/admin/drain", headers=headers, params={"enabled": False})

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            health_url = os.environ.get("DEPLOY_HEALTH_URL", "http://127.0.0.1:8000/health/ready")
            if httpx.get(health_url, timeout=5).is_success:
                print("API deployment completed after active responses drained")
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError("API did not become ready after deployment")


if __name__ == "__main__":
    main()
