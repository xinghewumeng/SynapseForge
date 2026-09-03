"""
Centralized Workspace Vault and File Lifecycle Manager for SynapseForge.
Guarantees all project files are strictly structured in dedicated directories.
Automatically copies and sandboxes external files opened from arbitrary paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


VAULT_STRUCTURE = {
    "sections": "📝 Markdown document sections (01_intro.md, 02_theory.md...)",
    "imports": "📥 Auto-copied external documents, literature, and notes",
    "variants": "🔀 Parallel candidate draft branches from different agents",
    "prompts": "⚙️ User-defined custom agent system prompts",
    "references": "📚 Bibliography, BibTeX files, and citation data",
    "figures": "📊 SCI figures, charts, and vector diagrams",
    "dist": "📦 Publication builds (PDF, Word docx, HTML, ZIP packages)",
    "locks": "🔒 Cross-platform section mutex lease locks",
    "snapshots": "⏳ GitOps snapshots and version rollback points",
    "rooms": "🌐 Tailscale distributed mesh rooms & synchronization state",
}


class WorkspaceVault:
    """
    Manages dedicated folder hierarchy and automatically intercepts external file accesses
    to copy them into the isolated, managed workspace vault.
    """

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()
        self.meta_dir = self.workspace_root / ".synapse"
        self.manifest_file = self.meta_dir / "vault_manifest.json"
        self.ensure_vault_structure()

    def ensure_vault_structure(self) -> None:
        """Creates all standardized, dedicated directories in the workspace vault."""
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        for folder in VAULT_STRUCTURE.keys():
            folder_path = self.workspace_root / folder
            folder_path.mkdir(parents=True, exist_ok=True)

    def _get_file_hash(self, file_path: Path) -> str:
        """Computes SHA-256 checksum of a file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        if self.manifest_file.exists():
            try:
                return json.loads(self.manifest_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_manifest(self, manifest: Dict[str, Dict[str, Any]]) -> None:
        self.manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    def import_external_file(
        self,
        external_path: Union[str, Path],
        target_category: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        Auto-copies an external file from arbitrary OS location into the dedicated vault directory.
        Returns the managed vault path.
        """
        src = Path(external_path).resolve()
        if not src.exists():
            return {"ok": False, "error": f"Source file does not exist: {src}"}

        # Determine target folder category based on file extension if not specified
        category = target_category
        if not category:
            ext = src.suffix.lower()
            if ext in (".md", ".markdown", ".txt"):
                category = "imports"
            elif ext in (".bib", ".ris", ".enw"):
                category = "references"
            elif ext in (".png", ".jpg", ".jpeg", ".svg", ".pdf") and "plot" in src.stem.lower():
                category = "figures"
            else:
                category = "imports"

        if category not in VAULT_STRUCTURE:
            return {
                "ok": False,
                "error": f"Unknown vault category '{category}'. Allowed: {', '.join(VAULT_STRUCTURE)}",
            }

        target_dir = (self.workspace_root / category).resolve()
        try:
            target_dir.relative_to(self.workspace_root.resolve())
        except ValueError:
            return {"ok": False, "error": "Import destination escapes workspace vault"}
        target_dir.mkdir(parents=True, exist_ok=True)

        file_hash = self._get_file_hash(src)
        manifest = self._load_manifest()

        # Check if already imported
        for k, v in manifest.items():
            v_path = self.workspace_root / v.get("vault_path", "")
            if v.get("sha256") == file_hash and v_path.exists():
                return {
                    "ok": True,
                    "status": "already_imported",
                    "original_path": str(src),
                    "vault_path": v["vault_path"],
                    "category": v.get("category", category),
                    "sha256": file_hash,
                }

        # Resolve destination path
        dest = target_dir / src.name
        if dest.exists() and not overwrite:
            stem = src.stem
            suffix = src.suffix
            timestamp = int(time.time())
            dest = target_dir / f"{stem}_{timestamp}{suffix}"

        # Copy the file
        shutil.copy2(src, dest)

        # Update manifest
        manifest[str(dest)] = {
            "original_path": str(src),
            "vault_path": str(dest.relative_to(self.workspace_root)),
            "absolute_vault_path": str(dest),
            "category": category,
            "sha256": file_hash,
            "imported_at": time.time(),
            "file_size": dest.stat().st_size,
        }
        self._save_manifest(manifest)

        return {
            "ok": True,
            "status": "copied",
            "original_path": str(src),
            "vault_path": str(dest.relative_to(self.workspace_root)),
            "absolute_vault_path": str(dest),
            "category": category,
            "file_size": dest.stat().st_size,
            "sha256": file_hash,
        }

    def list_vault_files(self) -> Dict[str, Any]:
        """Lists all files in the dedicated workspace vault categorized by folder."""
        categories = {}
        for folder, desc in VAULT_STRUCTURE.items():
            dir_path = self.workspace_root / folder
            files = []
            if dir_path.exists():
                for p in sorted(dir_path.glob("**/*")):
                    if p.is_file():
                        files.append({
                            "name": p.name,
                            "relative_path": str(p.relative_to(self.workspace_root)),
                            "size_bytes": p.stat().st_size,
                            "modified_at": p.stat().st_mtime,
                        })
            categories[folder] = {
                "description": desc,
                "file_count": len(files),
                "files": files,
            }
        return {
            "ok": True,
            "workspace_root": str(self.workspace_root),
            "categories": categories,
        }
