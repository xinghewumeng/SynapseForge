"""
Academic Figure Cross-Referencing and Text Bridge Injector for SynapseForge.
Enforces top-journal standards: prevents orphan figures, binds figure captions, and injects discussion text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class FigureLinker:
    """Binds scientific figures to section narrative with numbered labels and discussion bridges."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()

    def insert_figure(
        self,
        section_id: str,
        image_path: str,
        caption: str,
        fig_num: int = 1,
        discussion_bridge: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inserts a figure with caption and narrative discussion bridge into section Markdown."""
        from synapseforge.core.section_paths import resolve_section_path

        target_file = resolve_section_path(self.workspace_root, section_id, create_dir=False)
        if not target_file.exists():
            return {"ok": False, "error": f"Section '{section_id}' file not found"}

        content = target_file.read_text(encoding="utf-8")
        
        # Build figure markdown block
        fig_block = f"""\n\n![图 {fig_num}：{caption}]({image_path})\n*图 {fig_num}：{caption}*"""
        
        bridge_text = discussion_bridge or f"如图 {fig_num} 所示，系统在不同并发负载下的性能演进清晰地验证了上述理论推导。"
        bridge_block = f"\n\n{bridge_text}"

        updated_content = content.rstrip() + fig_block + bridge_block + "\n"
        target_file.write_text(updated_content, encoding="utf-8")

        return {
            "ok": True,
            "section_file": str(target_file.relative_to(self.workspace_root)),
            "fig_num": fig_num,
            "caption": caption,
            "image_path": image_path,
            "bridge_injected": bridge_text,
        }
