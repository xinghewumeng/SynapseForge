"""
Git-backed Version Snapshot and Rollback Engine for SynapseForge.
Enables fine-grained, non-destructive document versioning for solo human authors and AI agents.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from synapseforge.core.section_paths import resolve_section_path

_COMMIT_RE = re.compile(r"^(?:HEAD(?:~\d+)?|[0-9a-fA-F]{4,40})$")


class SnapshotManager:
    """Manages atomic Git checkpoints, branch tags, and section rollbacks."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path.cwd()

    def create_checkpoint(
        self,
        message: str = "",
        section_id: Optional[str] = None,
        author: str = "SynapseForge",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates an atomic Git snapshot commit for current workspace changes."""
        checkpoint_msg = message or reason or "Incremental snapshot"
        try:
            # Stage workspace files
            stage_paths = ["sections/", "assets/", "bibliography.bib", "synapseforge.yaml", "synapseforge.yml"]
            existing_stage = [p for p in stage_paths if (self.repo_root / p).exists()]
            if existing_stage:
                subprocess.run(["git", "add"] + existing_stage, cwd=self.repo_root, check=False)
            
            # Check if there are staged changes
            diff_res = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.repo_root)
            if diff_res.returncode == 0:
                # No changes to commit
                head_hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.repo_root, capture_output=True, text=True).stdout.strip()
                return {
                    "ok": True,
                    "created": False,
                    "commit_hash": head_hash,
                    "hash": head_hash,
                    "message": "No new changes to snapshot",
                }

            full_msg = f"checkpoint({section_id or 'doc'}): {checkpoint_msg} [by {author}]"
            env = os.environ.copy()
            env.setdefault("GIT_AUTHOR_NAME", author)
            env.setdefault("GIT_AUTHOR_EMAIL", "synapseforge@local")
            env.setdefault("GIT_COMMITTER_NAME", author)
            env.setdefault("GIT_COMMITTER_EMAIL", "synapseforge@local")
            res = subprocess.run(
                ["git", "commit", "-m", full_msg],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                env=env,
            )
            head_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "git commit failed").strip()
                return {
                    "ok": False,
                    "created": False,
                    "commit_hash": head_hash,
                    "hash": head_hash,
                    "error": err,
                    "message": full_msg,
                }

            return {
                "ok": True,
                "created": True,
                "commit_hash": head_hash,
                "hash": head_hash,
                "message": full_msg,
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_history(self, section_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieves recent snapshot history for a section or the whole document."""
        cmd = ["git", "log", f"-n{limit}", "--pretty=format:%h|%an|%at|%s"]
        if section_id:
            sec_file = resolve_section_path(self.repo_root, section_id, create_dir=False)
            if sec_file.exists():
                cmd.extend(["--", str(sec_file.relative_to(self.repo_root))])

        try:
            res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
            history = []
            for line in res.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("|", 3)
                if len(parts) == 4:
                    history.append({
                        "commit_hash": parts[0],
                        "author": parts[1],
                        "timestamp": int(parts[2]),
                        "message": parts[3],
                    })
            return history
        except Exception:
            return []

    def rollback(self, commit_hash: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Rolls back a specific file or section to a previous commit checkpoint."""
        ref = (commit_hash or "").strip()
        if not _COMMIT_RE.fullmatch(ref):
            return {"ok": False, "error": "Invalid commit hash", "commit_hash": commit_hash}
        try:
            if file_path:
                if file_path.startswith("-") or "\x00" in file_path:
                    return {"ok": False, "error": "Invalid file path", "commit_hash": ref}
                candidate = (self.repo_root / file_path).resolve()
                try:
                    candidate.relative_to(self.repo_root.resolve())
                except ValueError:
                    return {"ok": False, "error": "file_path escapes repository", "commit_hash": ref}
                target = file_path
            else:
                target = "sections/"
            res = subprocess.run(
                ["git", "checkout", ref, "--", target],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
            )
            return {
                "ok": res.returncode == 0,
                "target": target,
                "commit_hash": ref,
                "error": res.stderr if res.returncode != 0 else None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
