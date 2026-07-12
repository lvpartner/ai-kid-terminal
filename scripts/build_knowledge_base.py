#!/usr/bin/env python3
"""Build a local, source-attributed curriculum index from authorized public sources."""

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from pypdf import PdfReader


def chunks(text: str, size: int = 1200, overlap: int = 120):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    start = 0
    while start < len(text):
        yield text[start : start + size]
        start += size - overlap


def ocr_page(pdf: Path, page: int, directory: Path) -> tuple[int, str]:
    image = directory / f"page-{page}"
    subprocess.run(  # noqa: S603
        [
            "/usr/bin/pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-scale-to",
            "700",
            "-jpeg",
            str(pdf),
            str(image),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        result = subprocess.run(  # noqa: S603
            [
                "/usr/bin/tesseract",
                f"{image}.jpg",
                "stdout",
                "-l",
                "chi_sim+eng",
                "--psm",
                "6",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = result.stdout
    except subprocess.TimeoutExpired:
        text = ""
    Path(f"{image}.jpg").unlink(missing_ok=True)
    return page, text


def extract_pdf(pdf: Path, cache: Path, workers: int) -> list[tuple[int, str]]:
    text_cache = cache.with_suffix(".ocr.json")
    if text_cache.exists():
        return [(int(page), text) for page, text in json.loads(text_cache.read_text())]
    reader = PdfReader(pdf)
    extracted = [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, 1)]
    if sum(len(text) for _, text in extracted) >= 1000:
        return extracted
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            extracted = list(
                pool.map(
                    lambda page: ocr_page(pdf, page, directory), range(1, len(reader.pages) + 1)
                )
            )
    text_cache.write_text(json.dumps(extracted, ensure_ascii=False))
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("knowledge/sources.json"))
    parser.add_argument("--output", type=Path, default=Path("knowledge/curriculum.db"))
    parser.add_argument("--cache", type=Path, default=Path("data/knowledge-sources"))
    parser.add_argument("--outlines", type=Path, default=Path("knowledge/outlines"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    sources = json.loads(args.manifest.read_text())
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    db = sqlite3.connect(args.output)
    db.executescript(
        """
        CREATE TABLE sources(
          id TEXT PRIMARY KEY, title TEXT, url TEXT, authority TEXT, subject TEXT, sha256 TEXT,
          verified INTEGER NOT NULL
        );
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, source_id TEXT, page INTEGER, content TEXT,
          FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE INDEX chunks_source_idx ON chunks(source_id);
        """
    )
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for source in sources:
            extracted: list[tuple[int, str]] = []
            digest = ""
            # Indexing, OCR, or model summarization never implies human verification.
            verified = bool(source.get("verified", False))
            if summary := source.get("public_summary"):
                extracted.append((1, summary))
                digest = hashlib.sha256(summary.encode()).hexdigest()
                verified = False
            else:
                pdf = args.cache / f"{source['id']}.pdf"
                if not pdf.exists():
                    response = client.get(source["url"])
                    response.raise_for_status()
                    pdf.write_bytes(response.content)
                data = pdf.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                outline = args.outlines / f"{source['id']}.txt"
                if outline.exists():
                    extracted = [(1, outline.read_text())]
                    # Generated outlines are navigation aids, never primary-source evidence.
                    verified = False
                else:
                    extracted = extract_pdf(pdf, args.cache / source["id"], args.workers)
                if sum(len(text) for _, text in extracted) < 1000:
                    raise RuntimeError(
                        f"source {source['id']} produced insufficient searchable text; "
                        "run scripts/build_curriculum_outlines.py"
                    )
            db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?,?)",
                (
                    source["id"],
                    source["title"],
                    source["url"],
                    source["authority"],
                    source["subject"],
                    digest,
                    int(verified),
                ),
            )
            for page, text in extracted:
                db.executemany(
                    "INSERT INTO chunks(source_id,page,content) VALUES(?,?,?)",
                    ((source["id"], page, part) for part in chunks(text)),
                )
            db.commit()
            print(f"indexed {source['id']} pages={len(extracted)}")
    count = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
    db.close()
    print(f"knowledge_base={args.output} chunks={count} sources={len(sources)}")


if __name__ == "__main__":
    main()
