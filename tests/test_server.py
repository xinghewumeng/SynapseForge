import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from synapseforge.server.app import start_server


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def remote_server(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src_sections = Path("sections")
    dest_sections = workspace / "sections"
    dest_sections.mkdir()
    if src_sections.exists():
        for path in sorted(src_sections.glob("*.md")):
            shutil.copy(path, dest_sections / path.name)
    if not any(dest_sections.glob("01*.md")):
        (dest_sections / "01_abstract.md").write_text("# Abstract\n\nHello.\n", encoding="utf-8")
    yaml_src = Path("synapseforge.yaml")
    if yaml_src.exists():
        shutil.copy(yaml_src, workspace / "synapseforge.yaml")
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=workspace,
        capture_output=True,
    )
    port = _free_port()
    server = start_server(host="127.0.0.1", port=port, workspace=workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}", workspace
    server.shutdown()
    server.server_close()


def test_server_get_index(remote_server):
    base, _ = remote_server
    with urllib.request.urlopen(f"{base}/") as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "SynapseForge Studio" in html
        assert "KaTeX" in html


def test_server_get_status_api(remote_server):
    base, _ = remote_server
    with urllib.request.urlopen(f"{base}/api/status") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert "sections_count" in data


def test_server_get_sections_api(remote_server):
    base, _ = remote_server
    with urllib.request.urlopen(f"{base}/api/sections") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["sections"]


def test_server_post_save_api(remote_server):
    base, workspace = remote_server
    payload = json.dumps({"section_id": "sec_01", "content": "# Updated Abstract\n\nContent..."}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/doc/save",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["word_count"] > 0
    from synapseforge.core.section_paths import resolve_section_path
    saved = resolve_section_path(workspace, "sec_01")
    assert saved.exists()
    assert "Updated Abstract" in saved.read_text(encoding="utf-8")


def test_server_session_get_and_post(remote_server):
    base, _ = remote_server
    with urllib.request.urlopen(f"{base}/api/session") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert "room_id" in data["session"]

    payload = json.dumps({
        "room_id": "room-special-sync",
        "room_name": "Special AGI Swarm",
        "active_section": "sec_05",
        "draftContent": "# Testing draft state"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/session",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["ok"] is True
        assert res_data["session"]["room_id"] == "room-special-sync"


def test_server_citations_use_workspace_bibliography(remote_server):
    base, workspace = remote_server
    (workspace / "bibliography.bib").write_text(
        "@article{workspaceonly2026,\n  title={Workspace Only},\n  author={Local Author},\n  year={2026}\n}\n",
        encoding="utf-8",
    )
    with urllib.request.urlopen(f"{base}/api/citations") as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert data["ok"] is True
    keys = [c["key"] for c in data["citations"]]
    assert "workspaceonly2026" in keys

    payload = json.dumps({
        "key": "addedfromstudio2026",
        "type": "article",
        "title": "Added From Studio",
        "author": "Remote Human",
        "year": "2026",
        "journal": "SynapseForge",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/citations/add",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        added = json.loads(resp.read().decode("utf-8"))
    assert added.get("ok") is True
    workspace_bib = (workspace / "bibliography.bib").read_text(encoding="utf-8")
    assert "addedfromstudio2026" in workspace_bib
    cwd_bib = Path("bibliography.bib")
    if cwd_bib.exists():
        assert "addedfromstudio2026" not in cwd_bib.read_text(encoding="utf-8")

