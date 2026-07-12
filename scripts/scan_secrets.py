#!/usr/bin/env python3
import argparse
import re
import shutil
import subprocess
from pathlib import Path

PATTERNS = {
    "generic-sk-key": re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "github-token": re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "cloud-access-key": re.compile(rb"\b(?:LTAI|AKIA)[A-Z0-9]{12,}\b"),
}
MAX_FILE_BYTES = 5_000_000
GIT = shutil.which("git") or "/usr/bin/git"


def findings(data: bytes) -> list[str]:
    return [name for name, pattern in PATTERNS.items() if pattern.search(data)]


def tracked_files() -> list[Path]:
    output = subprocess.check_output(  # noqa: S603
        [GIT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    return [Path(value.decode()) for value in output.split(b"\0") if value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    failed = False
    for path in tracked_files():
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        matches = findings(path.read_bytes())
        if matches:
            failed = True
            print(f"potential secret in {path}: {', '.join(matches)}")
    if args.history:
        history = subprocess.check_output(  # noqa: S603
            [GIT, "log", "-p", "--all", "--no-ext-diff", "--no-textconv"]
        )
        matches = findings(history)
        if matches:
            failed = True
            print(f"potential secret in Git history: {', '.join(matches)}")
    if failed:
        raise SystemExit(1)
    print("secret scan passed")


if __name__ == "__main__":
    main()
