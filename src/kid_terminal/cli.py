import json
import os
from pathlib import Path
from typing import Any

import httpx
import typer

app = typer.Typer(help="Secure administration CLI for AI Kid Terminal", no_args_is_help=True)


def client(base_url: str) -> httpx.Client:
    key = os.getenv("ADMIN_API_KEY", "")
    if not key:
        raise typer.BadParameter("ADMIN_API_KEY is required")
    return httpx.Client(base_url=base_url, headers={"X-Admin-Key": key}, timeout=30)


def show(response: httpx.Response) -> None:
    if response.is_error:
        typer.echo(f"HTTP {response.status_code}: {response.text}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2, default=str))


@app.command("enrollment-create")
def enrollment_create(label: str, minutes: int = 30, base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.post("/v1/admin/enrollments", json={"label": label, "expires_minutes": minutes}))


@app.command("devices")
def devices(base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.get("/v1/admin/devices"))


@app.command("revoke")
def revoke(device_id: str, base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.post(f"/v1/admin/devices/{device_id}/revoke"))


@app.command("rotate")
def rotate(device_id: str, base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.post(f"/v1/admin/devices/{device_id}/rotate"))


@app.command("status")
def status(device_id: str, base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.get(f"/v1/admin/devices/{device_id}/status"))


@app.command("faults")
def faults(
    device_id: str = "",
    session_id: str = "",
    hours: int = 24,
    base_url: str = "http://127.0.0.1:8000",
):
    params: dict[str, Any] = {"since_hours": hours}
    if device_id:
        params["device_id"] = device_id
    if session_id:
        params["session_id"] = session_id
    with client(base_url) as api:
        show(api.get("/v1/admin/faults", params=params))


@app.command("delete-data")
def delete_data(device_id: str, base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.delete(f"/v1/admin/devices/{device_id}/data"))


@app.command("release-upload")
def release_upload(
    path: Path,
    version_code: int,
    version_name: str,
    rollout: float = 100,
    forced: bool = False,
    base_url: str = "http://127.0.0.1:8000",
):
    metadata = {
        "version_code": version_code,
        "version_name": version_name,
        "rollout_percent": rollout,
        "forced": forced,
    }
    with path.open("rb") as handle, client(base_url) as api:
        show(
            api.post(
                "/v1/admin/releases",
                data={"metadata": json.dumps(metadata)},
                files={"file": (path.name, handle)},
            )
        )


@app.command("release-action")
def release_action(
    version_code: int,
    action: str = typer.Argument(help="publish, pause, resume, or rollback"),
    base_url: str = "http://127.0.0.1:8000",
):
    with client(base_url) as api:
        show(api.post(f"/v1/admin/releases/{version_code}/{action}"))


@app.command("diagnose")
def diagnose(base_url: str = "http://127.0.0.1:8000"):
    with client(base_url) as api:
        show(api.get("/v1/admin/diagnose"))


if __name__ == "__main__":
    app()
