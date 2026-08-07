#!/usr/bin/env python3
"""Import public arXiv source packages as local FAROS papers for ReviewX.

Imported paper data is written under backend/data/papers, which is intentionally
git-ignored. The importer records provenance and never labels external papers as
FAROS-generated papers.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT = "FAROS-ReviewX/1.0 (research evaluation)"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 300 * 1024 * 1024
MAX_MEMBERS = 10_000


class ArxivMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.metadata: dict[str, Any] = {"authors": []}
        self._in_license = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "meta":
            name = values.get("name", "")
            content = html.unescape(values.get("content", "")).strip()
            if name == "citation_author" and content:
                self.metadata["authors"].append(content)
            elif name.startswith("citation_") and content:
                self.metadata[name.removeprefix("citation_")] = content
        elif tag == "div" and "abs-license" in values.get("class", "").split():
            self._in_license = True
        elif tag == "a" and self._in_license and values.get("href"):
            self.metadata.setdefault("license", values["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_license:
            self._in_license = False


def fetch(url: str, timeout: int) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_ARCHIVE_BYTES:
            raise ValueError(f"download exceeds {MAX_ARCHIVE_BYTES} bytes: {url}")
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"download exceeds {MAX_ARCHIVE_BYTES} bytes: {url}")
    return data


def normalize_arxiv_id(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf|e-print)/", "", value)
    value = re.sub(r"\.pdf$", "", value)
    value = re.sub(r"v\d+$", "", value)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})", value, re.IGNORECASE):
        raise ValueError(f"invalid arXiv id: {value}")
    return value


def stable_paper_id(arxiv_id: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", arxiv_id).strip("_").lower()
    return f"paper_arxiv_{slug}"


def load_metadata(arxiv_id: str, timeout: int) -> dict[str, Any]:
    page_url = f"https://arxiv.org/abs/{arxiv_id}"
    parser = ArxivMetadataParser()
    parser.feed(fetch(page_url, timeout).decode("utf-8", errors="replace"))
    metadata = parser.metadata
    metadata["arxivId"] = arxiv_id
    metadata["absUrl"] = page_url
    metadata["sourceUrl"] = f"https://export.arxiv.org/e-print/{arxiv_id}"
    if not metadata.get("title"):
        raise ValueError(f"arXiv metadata has no title: {arxiv_id}")
    return metadata


def safe_member_path(name: str) -> Path | None:
    normalized = name.replace("\\", "/").lstrip("./")
    if not normalized:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member: {name}")
    return Path(*path.parts)


def extract_source(data: bytes, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    total_size = 0
    try:
        archive = tarfile.open(fileobj=BytesIO(data), mode="r:*")
    except tarfile.ReadError:
        target = destination / "source.tex"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return [target]

    with archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError(f"archive contains too many members: {len(members)}")
        for member in members:
            relative = safe_member_path(member.name)
            if relative is None or member.isdir():
                continue
            if not member.isfile():
                continue
            total_size += max(0, member.size)
            if total_size > MAX_EXTRACTED_BYTES:
                raise ValueError(f"extracted source exceeds {MAX_EXTRACTED_BYTES} bytes")
            source = archive.extractfile(member)
            if source is None:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            extracted.append(target)
    return extracted


def root_tex_score(path: Path) -> tuple[int, int, str]:
    text = path.read_text(encoding="utf-8", errors="replace")[:300_000]
    score = 0
    score += 10 if "\\documentclass" in text else 0
    score += 8 if "\\begin{document}" in text else 0
    score += 4 if "\\title" in text else 0
    score += 2 if "\\abstract" in text or "\\begin{abstract}" in text else 0
    score += 4 if path.name.lower() in {"main.tex", "paper.tex", "manuscript.tex"} else 0
    return score, -len(path.parts), str(path)


def select_root_tex(latex_dir: Path) -> Path:
    candidates = [path for path in latex_dir.rglob("*.tex") if path.is_file()]
    if not candidates:
        raise ValueError("source package contains no TeX files")
    return max(candidates, key=root_tex_score)


def write_main_entry(latex_dir: Path, root_tex: Path) -> None:
    main_path = latex_dir / "main.tex"
    if root_tex.resolve() == main_path.resolve():
        return
    relative = root_tex.relative_to(latex_dir).as_posix()
    main_path.write_text(
        "% FAROS external-paper entry point. Original arXiv root follows.\n"
        f"% Original root: {relative}\n"
        f"\\input{{{relative.removesuffix('.tex')}}}\n",
        encoding="utf-8",
    )


def source_stats(latex_dir: Path) -> dict[str, Any]:
    tex_files = [path for path in latex_dir.rglob("*.tex") if path.is_file()]
    bib_files = [path for path in latex_dir.rglob("*.bib") if path.is_file()]
    tex_chars = sum(len(path.read_text(encoding="utf-8", errors="replace")) for path in tex_files)
    return {
        "texFileCount": len(tex_files),
        "bibFileCount": len(bib_files),
        "texCharacterCount": tex_chars,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_paper(
    arxiv_id: str,
    papers_dir: Path,
    timeout: int,
    overwrite: bool,
) -> dict[str, Any]:
    metadata = load_metadata(arxiv_id, timeout)
    paper_id = stable_paper_id(arxiv_id)
    target_dir = papers_dir / paper_id
    if target_dir.exists():
        if not overwrite:
            raise FileExistsError(f"{target_dir} exists; rerun with --overwrite")
        shutil.rmtree(target_dir)

    source_data = fetch(metadata["sourceUrl"], timeout)
    now = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory(prefix="faros-arxiv-") as temp_dir:
        staged = Path(temp_dir) / paper_id
        latex_dir = staged / "latex"
        latex_dir.mkdir(parents=True)
        extract_source(source_data, latex_dir)
        root_tex = select_root_tex(latex_dir)
        original_root = root_tex.relative_to(latex_dir).as_posix()
        write_main_entry(latex_dir, root_tex)
        stats = source_stats(latex_dir)
        source_sha256 = hashlib.sha256(source_data).hexdigest()
        meta = {
            "id": paper_id,
            "title": metadata["title"],
            "paperType": "system",
            "targetVenue": "generic",
            "status": "imported",
            "planLinkId": None,
            "projectId": None,
            "experimentIds": [],
            "figureIds": [],
            "runIds": [],
            "providerName": "external",
            "model": None,
            "notes": f"External real paper imported from arXiv:{arxiv_id} for ReviewX evaluation.",
            "briefJson": None,
            "briefUserEdits": "",
            "briefStatus": "missing",
            "outlineJson": None,
            "pdfAvailable": False,
            "logs": [],
            "createdAt": now,
            "updatedAt": now,
            "externalPaper": {
                **metadata,
                "sourceSha256": source_sha256,
                "originalRootTex": original_root,
                **stats,
            },
        }
        write_json(staged / "meta.json", meta)
        write_json(staged / "source.json", meta["externalPaper"])
        papers_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(target_dir))
    return meta


def write_manifest(path: Path, papers: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for paper in papers:
            external = paper["externalPaper"]
            row = {
                "paperId": paper["id"],
                "title": paper["title"],
                "arxivId": external["arxivId"],
                "sourceUrl": external["absUrl"],
                "license": external.get("license"),
                "include": True,
                "notes": "External real paper; not generated by FAROS.",
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arxiv-id", action="append", required=True, help="arXiv ID or URL; may be repeated")
    parser.add_argument("--backend-data", default="backend/data")
    parser.add_argument("--manifest-output", default="docs/tempdocs/arxiv_real_sources.jsonl")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    arxiv_ids = []
    for value in args.arxiv_id:
        for item in value.split(","):
            normalized = normalize_arxiv_id(item)
            if normalized not in arxiv_ids:
                arxiv_ids.append(normalized)

    if args.dry_run:
        for arxiv_id in arxiv_ids:
            metadata = load_metadata(arxiv_id, args.timeout)
            print(f"{arxiv_id}\t{metadata['title']}\t{metadata.get('license') or 'license-unspecified'}")
        return 0

    papers_dir = Path(args.backend_data) / "papers"
    imported = []
    for arxiv_id in arxiv_ids:
        try:
            paper = import_paper(arxiv_id, papers_dir, args.timeout, args.overwrite)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, tarfile.TarError) as exc:
            raise SystemExit(f"failed to import arXiv:{arxiv_id}: {exc}") from exc
        imported.append(paper)
        stats = paper["externalPaper"]
        print(
            f"imported {arxiv_id} -> {paper['id']} "
            f"tex={stats['texFileCount']} bib={stats['bibFileCount']} chars={stats['texCharacterCount']}"
        )

    write_manifest(Path(args.manifest_output), imported)
    print(f"papers={len(imported)} manifest={args.manifest_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
