from __future__ import annotations

from pathlib import Path

from pancli.models import MatchField
from pancli.selectors import select_local_files


def test_select_local_files_with_glob(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_text("a", encoding="utf-8")
    (tmp_path / "b.docx").write_text("b", encoding="utf-8")
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    matches = select_local_files([str(tmp_path)], globs=["*.pdf", "*.docx"], recursive=False)
    assert {item.basename for item in matches} == {"a.pdf", "b.docx"}


def test_select_local_files_with_regex_and_relpath(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "2026-report.pdf").write_text("x", encoding="utf-8")
    nested = docs / "nested"
    nested.mkdir()
    (nested / "2026-plan.pdf").write_text("x", encoding="utf-8")
    matches = select_local_files(
        [str(docs)],
        regex=r".*2026.*\.pdf$",
        recursive=True,
        match_field=MatchField.RELPATH,
    )
    assert {item.relative_path for item in matches} == {
        "docs/2026-report.pdf",
        "docs/nested/2026-plan.pdf",
    }
