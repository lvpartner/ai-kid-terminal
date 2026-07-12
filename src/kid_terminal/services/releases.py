import hashlib
import zipfile
from pathlib import Path


def rollout_eligible(device_id: str, percent: float) -> bool:
    bucket = int(hashlib.sha256(device_id.encode()).hexdigest()[:8], 16) % 10_000 / 100
    return bucket < percent


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def is_android_apk(path: Path) -> bool:
    if path.suffix.lower() != ".apk" or not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    return {"AndroidManifest.xml", "classes.dex", "resources.arsc"} <= names
