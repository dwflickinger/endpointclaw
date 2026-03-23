"""Tests for agent.src.indexing.scanner.FileScanner.

Validates file classification, hashing, content extraction, tag inference,
and the full scan workflow using temporary directories and sample files.
"""

from __future__ import annotations

import csv
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.src.indexing.scanner import FileScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txt(directory: Path, name: str, content: str = "hello world") -> Path:
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _make_csv(directory: Path, name: str) -> Path:
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["col_a", "col_b"])
        writer.writerow(["value_1", "value_2"])
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config() -> MagicMock:
    cfg = MagicMock()
    cfg.company_id = "corvex"
    cfg.monitored_paths = []
    cfg.monitored_extensions = [".xlsx", ".pdf", ".docx", ".txt", ".csv", ".jpg", ".dwg"]
    cfg.max_cpu_percent = 90.0
    return cfg


@pytest.fixture
def mock_database() -> MagicMock:
    db = MagicMock()
    db.get_file_by_path = AsyncMock(return_value=None)
    db.upsert_file = AsyncMock(return_value=1)
    return db


@pytest.fixture
def scanner(mock_config: MagicMock, mock_database: MagicMock) -> FileScanner:
    return FileScanner(mock_config, mock_database)


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# Tests — File Classification (static method)
# ---------------------------------------------------------------------------


class TestClassifyFile:
    def test_classify_file_estimate(self) -> None:
        result = FileScanner._classify_file("job_estimate_2024.xlsx", "")
        assert result == "estimate"

    def test_classify_file_proposal(self) -> None:
        result = FileScanner._classify_file("roof_proposal.pdf", "")
        assert result == "proposal"

    def test_classify_file_photo(self) -> None:
        result = FileScanner._classify_file("site_image.jpg", "")
        assert result == "photo"

    def test_classify_file_drawing(self) -> None:
        result = FileScanner._classify_file("floorplan.dwg", "")
        assert result == "drawing"

    def test_classify_file_invoice(self) -> None:
        result = FileScanner._classify_file("invoice_march.pdf", "")
        assert result == "invoice"

    def test_classify_file_other(self) -> None:
        result = FileScanner._classify_file("random_notes.txt", "")
        assert result == "other"


# ---------------------------------------------------------------------------
# Tests — Hashing
# ---------------------------------------------------------------------------


class TestComputeHash:
    @pytest.mark.asyncio
    async def test_compute_hash(self, scanner: FileScanner, tmp_dir: Path) -> None:
        path = _make_txt(tmp_dir, "hashme.txt", "deterministic content")
        h1 = await scanner._compute_hash(path)
        h2 = await scanner._compute_hash(path)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest length

    @pytest.mark.asyncio
    async def test_compute_hash_different_files(self, scanner: FileScanner, tmp_dir: Path) -> None:
        p1 = _make_txt(tmp_dir, "file_a.txt", "content alpha")
        p2 = _make_txt(tmp_dir, "file_b.txt", "content beta")
        assert await scanner._compute_hash(p1) != await scanner._compute_hash(p2)


# ---------------------------------------------------------------------------
# Tests — Content Extraction
# ---------------------------------------------------------------------------


class TestExtractContent:
    @pytest.mark.asyncio
    async def test_extract_content_txt(self, scanner: FileScanner, tmp_dir: Path) -> None:
        path = _make_txt(tmp_dir, "readable.txt", "Hello from the text file")
        content = await scanner._extract_content(path)
        assert "Hello from the text file" in content

    @pytest.mark.asyncio
    async def test_extract_content_csv(self, scanner: FileScanner, tmp_dir: Path) -> None:
        path = _make_csv(tmp_dir, "data.csv")
        content = await scanner._extract_content(path)
        assert "col_a" in content

    @pytest.mark.asyncio
    async def test_extract_content_truncation(self, scanner: FileScanner, tmp_dir: Path) -> None:
        """Content longer than 5000 characters should be truncated."""
        long_text = "x" * 10_000
        path = _make_txt(tmp_dir, "big.txt", long_text)
        content = await scanner._extract_content(path)
        assert len(content) <= 5000


# ---------------------------------------------------------------------------
# Tests — scan_file
# ---------------------------------------------------------------------------


class TestScanFile:
    @pytest.mark.asyncio
    async def test_scan_file_basic(self, scanner: FileScanner, mock_database: MagicMock, tmp_dir: Path) -> None:
        path = _make_txt(tmp_dir, "test.txt", "sample content")
        record = await scanner.scan_file(path)
        assert record["filename"] == "test.txt"
        assert record["file_type"] == "other"
        assert record["content_extract"]
        mock_database.upsert_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_file_skips_unchanged(
        self, scanner: FileScanner, mock_database: MagicMock, tmp_dir: Path
    ) -> None:
        """If the database already has the file with the same hash, scan_file should skip it."""
        path = _make_txt(tmp_dir, "unchanged.txt", "constant content")
        file_hash = await scanner._compute_hash(path)
        mock_database.get_file_by_path = AsyncMock(
            return_value={"content_hash": file_hash, "file_path": str(path)}
        )
        result = await scanner.scan_file(path)
        assert result["content_hash"] == file_hash
        mock_database.upsert_file.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — Project Inference (static method)
# ---------------------------------------------------------------------------


class TestInferProject:
    def test_infer_project(self, tmp_path: Path) -> None:
        """A file at Documents/ProjectName/file.txt should infer 'ProjectName'."""
        project_dir = tmp_path / "Documents" / "Palm Bay Shopping Center"
        project_dir.mkdir(parents=True)
        file_path = project_dir / "takeoff.xlsx"
        file_path.touch()
        project = FileScanner._infer_project(file_path)
        assert project == "Palm Bay Shopping Center"


# ---------------------------------------------------------------------------
# Tests — Auto-Tagging (static method)
# ---------------------------------------------------------------------------


class TestExtractTags:
    def test_extract_tags_dollar_amounts(self) -> None:
        content = "Total contract price: $1,500 for materials."
        tags = FileScanner._extract_tags("test.txt", content)
        assert "has_dollar_amounts" in tags

    def test_extract_tags_materials(self) -> None:
        content = "Install TPO membrane on the west section."
        tags = FileScanner._extract_tags("test.txt", content)
        assert any("tpo" in t for t in tags)


# ---------------------------------------------------------------------------
# Tests — scan_all
# ---------------------------------------------------------------------------


class TestScanAll:
    @pytest.mark.asyncio
    async def test_scan_all_progress(
        self, scanner: FileScanner, mock_database: MagicMock, tmp_dir: Path
    ) -> None:
        """scan_all should process every monitored file in the directory tree."""
        _make_txt(tmp_dir, "extra1.txt", "one")
        _make_txt(tmp_dir, "extra2.csv", "col\nval")
        scanner.config.monitored_paths = [str(tmp_dir)]
        await scanner.scan_all()
        progress = scanner.get_progress()
        assert progress["processed"] >= 2
        assert progress["scanning"] is False
