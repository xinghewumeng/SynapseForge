"""Regression tests for workspace-bound citations, compact BibTeX, snapshot paths, and vault isolation."""

import subprocess

from synapseforge.core.figure_linker import FigureLinker
from synapseforge.core.snapshot import SnapshotManager
from synapseforge.core.vault import WorkspaceVault
from synapseforge.tools.cite_tool import CiteTool


def test_cite_tool_parses_compact_one_line_entries(tmp_path):
    bib = tmp_path / "bibliography.bib"
    bib.write_text(
        '@article{compact2020, title={One Line Title}, author={Ada Lovelace}, year={2020}, journal={J}}\n',
        encoding="utf-8",
    )
    entries = {e["key"]: e for e in CiteTool(bib_path=bib, workspace_root=tmp_path).list_citations()}
    assert entries["compact2020"]["title"] == "One Line Title"
    assert entries["compact2020"]["author"] == "Ada Lovelace"
    assert entries["compact2020"]["year"] == "2020"


def test_cite_tool_quoted_brace_in_title_does_not_truncate(tmp_path):
    bib = tmp_path / "bibliography.bib"
    bib.write_text(
        '@article{odd2021, title="Braces } inside quotes", author={Eve}, year={2021}}\n'
        "@article{after2022,\n  title={Still Parsed},\n  author={Bob},\n  year={2022}\n}\n",
        encoding="utf-8",
    )
    entries = {e["key"]: e for e in CiteTool(bib_path=bib, workspace_root=tmp_path).list_citations()}
    assert "after2022" in entries
    assert entries["after2022"]["title"] == "Still Parsed"
    assert "Braces" in entries["odd2021"]["title"]


def test_snapshot_history_filters_by_resolved_section(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    sec = tmp_path / "sections"
    sec.mkdir()
    (sec / "01_abstract.md").write_text("one\n", encoding="utf-8")
    (sec / "10_conclusion.md").write_text("ten\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    snap = SnapshotManager(tmp_path)
    (sec / "10_conclusion.md").write_text("ten edited\n", encoding="utf-8")
    res = snap.create_checkpoint(message="edit conclusion", section_id="sec_10", author="T")
    assert res.get("ok") is True
    msgs_one = [h["message"] for h in snap.list_history(section_id="sec_01", limit=10)]
    msgs_ten = [h["message"] for h in snap.list_history(section_id="sec_10", limit=10)]
    assert any("edit conclusion" in m for m in msgs_ten)
    assert not any("edit conclusion" in m for m in msgs_one)


def test_snapshot_rollback_rejects_git_option_injection(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "01.md").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    snap = SnapshotManager(tmp_path)
    res = snap.rollback(commit_hash="--help")
    assert res.get("ok") is not True
    assert "Invalid commit hash" in (res.get("error") or "")


def test_vault_import_rejects_path_traversal_category(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("secret\n", encoding="utf-8")
    vault = WorkspaceVault(tmp_path)
    outside = tmp_path.parent / f"outside_drop_{tmp_path.name}"
    res = vault.import_external_file(str(src), target_category=f"../outside_drop_{tmp_path.name}")
    assert res.get("ok") is False
    assert not outside.exists()
    ok = vault.import_external_file(str(src), target_category="imports")
    assert ok.get("ok") is True
    assert (tmp_path / "imports" / "note.md").exists()


def test_figure_linker_uses_exact_section_resolution(tmp_path):
    sec = tmp_path / "sections"
    sec.mkdir()
    one = sec / "01_abstract.md"
    ten = sec / "10_conclusion.md"
    one.write_text("# one\n", encoding="utf-8")
    ten.write_text("# ten\n", encoding="utf-8")
    res = FigureLinker(tmp_path).insert_figure("sec_01", "figures/a.png", "Caption", fig_num=3)
    assert res["ok"] is True
    assert "01_abstract.md" in res["section_file"]
    assert "图 3" in one.read_text(encoding="utf-8")
    assert "图 3" not in ten.read_text(encoding="utf-8")
