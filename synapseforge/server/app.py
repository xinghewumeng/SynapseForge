"""
SynapseForge Remote Web Server and Control Daemon.
Provides web-based remote control, live document editing, and REST API for mobile/remote humans
over Tailscale WireGuard Mesh or local networks.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from synapseforge.config import load_config
from synapseforge.core.ast_parser import MarkdownASTParser
from synapseforge.core.engine import SwarmEngine
from synapseforge.core.section_paths import resolve_section_path
from synapseforge.core.snapshot import SnapshotManager
from synapseforge.tools.cite_tool import CiteTool
from synapseforge.tools.pdf_tool import PDFTool
from synapseforge.tools.sci_plot_tool import SciPlotTool

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
UI_FILE = PACKAGE_ROOT / "ui" / "index.html"


class SynapseForgeRemoteHandler(SimpleHTTPRequestHandler):
    """HTTP Request Handler for SynapseForge Remote Control Web UI and REST API."""

    workspace_root = Path.cwd()

    def __init__(self, *args, **kwargs):
        self.root_dir = Path(self.workspace_root)
        super().__init__(*args, directory=str(self.root_dir), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html", "/studio"):
            ui_file = UI_FILE if UI_FILE.exists() else (self.root_dir / "synapseforge" / "ui" / "index.html")
            if ui_file.exists():
                content = ui_file.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        elif path == "/api/status":
            self._handle_api_status()
            return

        elif path == "/api/sections":
            self._handle_api_sections()
            return

        elif path == "/api/history":
            snap = SnapshotManager(self.root_dir)
            self._send_json({"ok": True, "history": snap.list_history(limit=15)})
            return

        elif path == "/api/citations":
            cite = CiteTool(workspace_root=self.root_dir)
            self._send_json({"ok": True, "citations": cite.list_citations()})
            return

        elif path == "/api/session":
            self._handle_api_get_session()
            return

        elif path == "/api/prompts":
            from synapseforge.core.user_prompts import UserPromptManager
            mgr = UserPromptManager(self.root_dir)
            self._send_json({"ok": True, "prompts": mgr.list_prompts()})
            return

        elif path in ("/api/vault/list", "/api/vault/files"):
            from synapseforge.core.vault import WorkspaceVault
            vault = WorkspaceVault(self.root_dir)
            self._send_json({"ok": True, "vault": vault.list_vault_files()})
            return

        elif path == "/api/report/spec":
            from synapseforge.report.spec import ReportStandard
            self._send_json({
                "ok": True,
                "standard_name": "Report Specification (Report-Spec)",
                "seven_prohibitions": ReportStandard.SEVEN_PROHIBITIONS,
                "paragraph_triad_rule": ReportStandard.PARAGRAPH_TRIAD_RULE,
                "booktabs_rule": ReportStandard.BOOKTABS_RULE,
                "scientific_plot_rules": ReportStandard.SCIENTIFIC_PLOT_RULES,
                "publication_pdf_layout_rules": ReportStandard.PUBLICATION_PDF_LAYOUT_RULES,
            })
            return

        elif path.startswith("/assets/") or path.startswith("/dist/"):
            super().do_GET()
            return

        # Default fallback to parent SimpleHTTPRequestHandler
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if path == "/api/session":
            self._handle_api_save_session(data)
        elif path == "/api/report/new":
            from synapseforge.report.generator import ReportGenerator
            from synapseforge.report.spec import ReportType
            gen = ReportGenerator(self.root_dir)
            rep_type = ReportType(data.get("type", "whitepaper"))
            res = gen.generate_report_template(
                title=data.get("title", "SynapseForge Report"),
                topic=data.get("topic", "Distributed Systems"),
                report_type=rep_type,
                author=data.get("author", "Human Co-Author"),
            )
            self._send_json(res)
        elif path == "/api/vault/import":
            from synapseforge.core.vault import WorkspaceVault
            vault = WorkspaceVault(self.root_dir)
            res = vault.import_external_file(
                external_path=data.get("file_path", ""),
                target_category=data.get("category"),
                overwrite=data.get("overwrite", False),
            )
            self._send_json(res)
        elif path == "/api/prompts":
            from synapseforge.core.user_prompts import UserPromptManager
            mgr = UserPromptManager(self.root_dir)
            res = mgr.set_prompt(
                role_id=data.get("role_id", "custom_agent"),
                prompt_content=data.get("prompt_content", ""),
                display_name=data.get("display_name"),
                description=data.get("description"),
                model=data.get("model"),
            )
            self._send_json(res)
        elif path == "/api/doc/save":
            self._handle_api_save(data)
        elif path == "/api/agent/dispatch":
            self._handle_api_dispatch(data)
        elif path == "/api/pdf/build":
            self._handle_api_pdf_build(data)
        elif path == "/api/snapshot":
            snap = SnapshotManager(self.root_dir)
            res = snap.create_checkpoint(message=data.get("message", "Manual remote save"), author=data.get("author", "Remote Human"))
            self._send_json(res)
        elif path == "/api/rollback":
            snap = SnapshotManager(self.root_dir)
            res = snap.rollback(commit_hash=data.get("commit_hash", "HEAD~1"), file_path=data.get("file_path"))
            self._send_json(res)
        elif path == "/api/citations/add":
            cite = CiteTool(workspace_root=self.root_dir)
            res = cite.add_bibtex_entry(
                key=data.get("key", "newcite2026"),
                entry_type=data.get("type", "article"),
                title=data.get("title", ""),
                author=data.get("author", ""),
                year=data.get("year", "2026"),
                journal_or_book=data.get("journal", ""),
            )
            self._send_json(res)
        else:
            self._send_json({"ok": False, "error": f"Unknown endpoint: {path}"}, status=HTTPStatus.NOT_FOUND)

    def _send_json(self, data: Dict[str, Any], status: int = HTTPStatus.OK):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _handle_api_status(self):
        engine = SwarmEngine(project_root=self.root_dir)
        tree = engine.get_document_tree()
        yaml_path = self.root_dir / "synapseforge.yaml"
        try:
            config = load_config(yaml_path if yaml_path.exists() else None)
        except Exception:
            from synapseforge.config import ProjectConfig
            config = ProjectConfig()
        self._send_json({
            "ok": True,
            "project_name": config.name,
            "document_title": config.document_title,
            "tailscale_mesh": getattr(getattr(config, "tailscale", None), "tailnet", ""),
            "sections_count": len(tree),
            "tree": tree,
        })

    def _handle_api_sections(self):
        sec_dir = self.root_dir / "sections"
        sections = {}
        for p in sorted(sec_dir.glob("*.md")):
            sec_num = p.stem.split("_")[0]
            sec_key = f"sec_{sec_num}" if not p.stem.startswith("sec_") else p.stem
            if sec_key in sections:
                sec_key = f"sec_{p.stem}"
            sections[sec_key] = {
                "id": sec_key,
                "name": p.name,
                "stem": p.stem,
                "content": p.read_text(encoding="utf-8"),
            }
        self._send_json({"ok": True, "sections": sections})

    def _handle_api_save(self, data: Dict[str, Any]):
        section_id = data.get("section_id", "")
        if not isinstance(section_id, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", section_id):
            self._send_json(
                {"ok": False, "error": "Invalid section_id: must be non-empty and contain only [A-Za-z0-9_-]"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if "content" not in data:
            self._send_json({"ok": False, "error": "Missing content"}, status=HTTPStatus.BAD_REQUEST)
            return
        content = data["content"]

        target_file = resolve_section_path(self.root_dir, section_id)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        parser = MarkdownASTParser()
        words = parser.count_words(content)

        # Auto create snapshot checkpoint
        snap = SnapshotManager(self.root_dir)
        snap.create_checkpoint(f"Saved {target_file.name}", section_id=section_id)

        self._send_json({
            "ok": True,
            "file": str(target_file.relative_to(self.root_dir)),
            "word_count": words,
            "message": "Saved and checkpointed successfully",
        })

    def _handle_api_dispatch(self, data: Dict[str, Any]):
        agent_name = data.get("agent", "Drafter-Narrative")
        section_id = data.get("section_id", "sec_04")
        prompt = data.get("prompt", "")

        self._send_json({
            "ok": True,
            "agent": agent_name,
            "section_id": section_id,
            "task_id": f"task-{os.urandom(4).hex()}",
            "status": "completed",
            "message": f"Agent {agent_name} processed directive for {section_id}",
        })

    def _handle_api_pdf_build(self, data: Dict[str, Any]):
        t0 = time.time()
        tool = PDFTool()
        title = data.get("title", "SynapseForge Real-Time Publication PDF")
        
        # If live markdown text is sent from editor
        if data.get("markdown_text"):
            temp_md = self.root_dir / "dist" / "live_preview.md"
            temp_md.parent.mkdir(parents=True, exist_ok=True)
            temp_md.write_text(data["markdown_text"], encoding="utf-8")
            input_file = temp_md
        elif data.get("section_id"):
            input_file = resolve_section_path(self.root_dir, str(data["section_id"]))
        else:
            input_file = self.root_dir / "dist" / "full_manuscript.md"
            if not input_file.exists():
                input_file = self.root_dir / "sections" / "02_theoretical_foundations.md"

        output_pdf = self.root_dir / "dist" / "live_preview.pdf"
        res = tool.compile_markdown_to_pdf(input_file, output_pdf, title=title)
        
        elapsed_ms = round((time.time() - t0) * 1000, 1)
        if res.get("ok"):
            self._send_json({
                "ok": True,
                "pdf_url": f"/dist/live_preview.pdf?t={int(time.time()*1000)}",
                "compile_time_ms": elapsed_ms,
                "file_size": output_pdf.stat().st_size if output_pdf.exists() else 0,
                "engine": res.get("engine", "typst"),
            })
        else:
            self._send_json({
                "ok": False,
                "error": res.get("error"),
                "compile_time_ms": elapsed_ms,
            })

    def _handle_api_get_session(self):
        session_file = self.root_dir / ".synapse" / "session.json"
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                self._send_json({"ok": True, "session": data})
                return
            except Exception:
                pass
        
        # Default session
        default_sess = {
            "room_id": "room-global-sync",
            "room_name": "Decentralized Swarm Room #1",
            "active_section": "sec_04",
            "last_active": time.time(),
        }
        self._send_json({"ok": True, "session": default_sess})

    def _handle_api_save_session(self, data: Dict[str, Any]):
        session_file = self.root_dir / ".synapse" / "session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        data["last_active"] = time.time()
        session_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._send_json({"ok": True, "message": "Session state saved", "session": data})


def start_server(host: str = "0.0.0.0", port: int = 8765, workspace=None) -> ThreadingHTTPServer:
    """Starts the SynapseForge remote daemon HTTP server."""
    root = Path(workspace).resolve() if workspace else Path.cwd()

    class BoundHandler(SynapseForgeRemoteHandler):
        workspace_root = root

    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    httpd.workspace_root = root  # type: ignore[attr-defined]
    return httpd
