import sqlite3
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: sqlite_backup.py SOURCE DESTINATION")
    source_path = Path(sys.argv[1])
    destination_path = Path(sys.argv[2])
    if not source_path.is_file():
        raise SystemExit("source database not found")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
        source.backup(destination)
    with sqlite3.connect(destination_path) as check:
        result = check.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        destination_path.unlink(missing_ok=True)
        raise SystemExit("backup integrity check failed")
    print("SQLite backup integrity check: ok")


if __name__ == "__main__":
    main()
