#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import httpx

from kid_terminal.app import is_android_apk
from kid_terminal.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload and publish a signed Android release")
    parser.add_argument("apk", type=Path)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--rollout", type=float, default=100)
    parser.add_argument("--forced", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Required explicit release gate")
    args = parser.parse_args()
    if not args.publish:
        parser.error("--publish is required")
    if not is_android_apk(args.apk):
        parser.error("artifact is not a structurally valid APK")

    settings = get_settings()
    headers = {"X-Admin-Key": settings.admin_api_key}
    metadata = {
        "version_code": args.version_code,
        "version_name": args.version_name,
        "min_android": 26,
        "forced": args.forced,
        "rollout_percent": args.rollout,
        "release_notes": args.notes,
        "rollback_version_code": None,
    }
    with args.apk.open("rb") as artifact, httpx.Client(timeout=60) as client:
        upload = client.post(
            "http://127.0.0.1:8000/v1/admin/releases",
            headers=headers,
            data={"metadata": json.dumps(metadata)},
            files={"file": (args.apk.name, artifact, "application/vnd.android.package-archive")},
        )
        upload.raise_for_status()
        publish = client.post(
            f"http://127.0.0.1:8000/v1/admin/releases/{args.version_code}/publish",
            headers=headers,
        )
        publish.raise_for_status()
    print(f"published {args.version_name} ({args.version_code})")


if __name__ == "__main__":
    main()
