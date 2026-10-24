"""Tests for ingestion step 5 (pipeline + CLI). Uses the fast fake HashEmbedder."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from insureassist import cli
from insureassist.ingestion.embedder import HashEmbedder
from insureassist.ingestion.pipeline import find_pdfs, ingest_files, save_report
from insureassist.ingestion.vector_store import VectorStore

MakePdf = Callable[..., Path]
EMBEDDER = HashEmbedder(dimension=256)


@pytest.fixture
def pdf_folder(make_pdf: MakePdf, tmp_path: Path) -> Path:
    """A folder with 2 good PDFs, 1 exact copy (duplicate) and 1 broken file."""
    folder = tmp_path / "pdfs"
    folder.mkdir()
    auto = make_pdf(["Collision deductible is 500 dollars.", "Rental car 40 dollars a day."])
    dental = make_pdf(["Waiting period for crowns is 12 months."], name="dental.pdf")
    (folder / "a-auto.pdf").write_bytes(auto.read_bytes())
    (folder / "b-dental.pdf").write_bytes(dental.read_bytes())
    (folder / "c-auto-copy.pdf").write_bytes(auto.read_bytes())
    (folder / "d-broken.pdf").write_text("not really a pdf")
    (folder / "notes.txt").write_text("ignored: not a pdf")
    return folder


def test_find_pdfs_only_returns_pdfs_in_fixed_order(pdf_folder: Path) -> None:
    names = [p.name for p in find_pdfs(pdf_folder)]
    assert names == ["a-auto.pdf", "b-dental.pdf", "c-auto-copy.pdf", "d-broken.pdf"]


def test_batch_reports_each_file_and_keeps_going(pdf_folder: Path, tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "index", EMBEDDER.model_name, EMBEDDER.dimension)

    report = ingest_files(find_pdfs(pdf_folder), store, EMBEDDER)

    statuses = {f.filename: f.status for f in report.files}
    assert statuses == {
        "a-auto.pdf": "ingested",
        "b-dental.pdf": "ingested",
        "c-auto-copy.pdf": "duplicate",
        "d-broken.pdf": "failed",
    }
    broken = report.files[3]
    assert broken.error_code == "not_pdf"
    assert report.count("ingested") == 2
    assert report.count("duplicate") == 1
    assert report.count("failed") == 1
    # The store was saved: a fresh instance sees both documents.
    reloaded = VectorStore(tmp_path / "index", EMBEDDER.model_name, EMBEDDER.dimension)
    assert len(reloaded.documents) == 2
    assert reloaded.index.ntotal == 3


def test_running_twice_ingests_nothing_new(pdf_folder: Path, tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "index", EMBEDDER.model_name, EMBEDDER.dimension)
    ingest_files(find_pdfs(pdf_folder), store, EMBEDDER)

    second = ingest_files(find_pdfs(pdf_folder), store, EMBEDDER)

    assert second.count("ingested") == 0
    assert second.count("duplicate") == 3


def test_report_is_saved_as_json(pdf_folder: Path, tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "index", EMBEDDER.model_name, EMBEDDER.dimension)
    report = ingest_files(find_pdfs(pdf_folder), store, EMBEDDER)

    path = save_report(report, tmp_path / "reports")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert path.name.startswith("ingest-")
    assert data["embedding_model"] == EMBEDDER.model_name
    assert len(data["files"]) == 4


# --- CLI ------------------------------------------------------------------------


@pytest.fixture
def run_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Callable[..., tuple[int, str]]:
    monkeypatch.setattr(cli, "make_embedder", lambda: EMBEDDER)  # no model download

    def run(*args: str) -> tuple[int, str]:
        code = cli.main(["--index-dir", str(tmp_path / "index"), *args])
        return code, capsys.readouterr().out

    return run


def test_cli_ingest_search_list_delete(
    run_cli: Callable[..., tuple[int, str]], pdf_folder: Path, tmp_path: Path
) -> None:
    code, out = run_cli("ingest", str(pdf_folder), "--report-dir", str(tmp_path / "reports"))
    assert code == 1  # one file failed -> non-zero exit code
    assert "Ingested 2, duplicates 1, failed 1" in out

    code, out = run_cli("search", "waiting period crowns", "--top-k", "1")
    assert code == 0
    assert "b-dental.pdf, page 1" in out

    code, out = run_cli("list")
    assert "2 documents, 3 chunks" in out

    dental_id = next(line.split()[0] for line in out.splitlines() if "b-dental.pdf" in line)
    code, out = run_cli("delete", dental_id)
    assert code == 0
    assert "1 chunks removed" in out


def test_cli_search_on_empty_store(run_cli: Callable[..., tuple[int, str]]) -> None:
    code, out = run_cli("search", "anything")
    assert code == 1
    assert "store is empty" in out
