"""
Citation and BibTeX Management Tool for SynapseForge.
Enables AI Agents and human authors to search, validate, and append clean BibTeX references,
resolve DOIs via CrossRef APIs, and validate citation graphs across document sections.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from synapseforge.core.ast_parser import MarkdownASTParser


def _matching_brace(text: str, open_idx: int) -> Optional[int]:
    """Return the index of the brace that closes ``text[open_idx]``, skipping quoted ``}``."""
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return None
    depth = 0
    in_quote = False
    escape = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _iter_bib_entries(content: str):
    """Yield ``(entry_type, cite_key, body, raw)`` for each BibTeX entry.

    Brace-matched so compact one-line entries (no newline before ``}``) parse
    the same as the conventional multiline form.
    """
    i = 0
    n = len(content)
    while i < n:
        at = content.find("@", i)
        if at < 0:
            return
        header = re.match(r"@([a-zA-Z]+)\s*\{\s*([a-zA-Z0-9_\-:]+)\s*,", content[at:])
        if not header:
            i = at + 1
            continue
        brace = content.find("{", at)
        end = _matching_brace(content, brace)
        if end is None:
            return
        body = content[at + header.end() : end]
        raw = content[at : end + 1]
        yield header.group(1), header.group(2), body, raw
        i = end + 1


def _bib_field(body: str, name: str) -> str:
    """Read a BibTeX field, keeping quoted titles and nested braces intact."""
    match = re.search(rf"{re.escape(name)}\s*=\s*", body, re.IGNORECASE)
    if not match:
        return ""
    rest = body[match.end():].lstrip()
    if rest.startswith("{"):
        depth = 0
        for i, ch in enumerate(rest):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return rest[1:i].strip()
        return rest[1:].strip()
    if rest.startswith('"'):
        escape = False
        chars = []
        for ch in rest[1:]:
            if escape:
                chars.append(ch)
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                return "".join(chars).strip()
            chars.append(ch)
        return "".join(chars).strip()
    token = re.match(r"([^,}\s]+)", rest)
    return token.group(1).strip() if token else ""


def _bib_year(body: str) -> str:
    value = _bib_field(body, "year")
    year = re.search(r"(\d{4})", value)
    return year.group(1) if year else ""


class CiteTool:
    """Manages BibTeX citations, DOI lookups, CrossRef search, and citation graph references."""

    def __init__(self, bib_path: Optional[Path] = None, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()
        self.bib_file = bib_path or self.workspace_root / "bibliography.bib"
        self.parser = MarkdownASTParser()

    def list_citations(self) -> List[Dict[str, str]]:
        """Parses all existing entries in bibliography.bib."""
        if not self.bib_file.exists():
            return []

        content = self.bib_file.read_text(encoding="utf-8")
        entries = []
        for entry_type, cite_key, body, raw in _iter_bib_entries(content):
            entries.append({
                "key": cite_key,
                "type": entry_type,
                "title": _bib_field(body, "title"),
                "author": _bib_field(body, "author"),
                "year": _bib_year(body),
                "journal": _bib_field(body, "journal") or _bib_field(body, "booktitle"),
                "raw": raw,
            })
        return entries

    def add_bibtex_entry(
        self, key: str, entry_type: str, title: str, author: str, year: str, journal_or_book: str = "", doi: str = ""
    ) -> Dict[str, Any]:
        """Appends a well-formed BibTeX entry to bibliography.bib."""
        self.bib_file.parent.mkdir(parents=True, exist_ok=True)

        # Check if already exists
        existing = [c["key"] for c in self.list_citations()]
        if key in existing:
            return {"ok": False, "error": f"Citation key '@{key}' already exists in bibliography.bib"}

        doi_field = f"\n  doi       = {{{doi}}}," if doi else ""
        bib_text = f"""
@{entry_type}{{{key},
  author    = {{{author}}},
  title     = {{{title}}},
  year      = {{{year}}},
  journal   = {{{journal_or_book}}}{doi_field}
}}
"""
        with open(self.bib_file, "a", encoding="utf-8") as f:
            f.write(bib_text)

        try:
            rel_file = str(self.bib_file.relative_to(self.workspace_root))
        except ValueError:
            rel_file = str(self.bib_file)

        return {
            "ok": True,
            "key": key,
            "title": title,
            "author": author,
            "year": year,
            "file": rel_file,
        }

    def clean_doi(self, raw_doi: str) -> str:
        """Strips URL prefixes or doi: protocol prefix to yield standard DOI string."""
        doi = raw_doi.strip()
        doi = re.sub(r'^(?:https?://)?(?:dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
        doi = re.sub(r'^doi:\s*', '', doi, flags=re.IGNORECASE)
        return doi.strip()

    def lookup_doi(self, raw_doi: str, timeout: float = 8.0) -> Dict[str, Any]:
        """Queries CrossRef API for a given DOI and generates clean BibTeX metadata."""
        doi = self.clean_doi(raw_doi)
        if not doi:
            return {"ok": False, "error": "Empty or invalid DOI string provided"}

        url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SynapseForge-CiteTool/0.2.0 (mailto:dev@synapseforge.org)",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            message = data.get("message", {})
            title_list = message.get("title", [])
            title = title_list[0] if title_list else "Untitled"
            
            # Extract authors
            authors_list = message.get("author", [])
            formatted_authors = []
            first_author_surname = "Author"
            for i, a in enumerate(authors_list):
                given = a.get("given", "")
                family = a.get("family", "")
                name = f"{given} {family}".strip() or a.get("name", "")
                if name:
                    formatted_authors.append(name)
                if i == 0 and family:
                    first_author_surname = re.sub(r'[^a-zA-Z0-9]', '', family)

            author_str = " and ".join(formatted_authors) if formatted_authors else "Unknown"

            # Extract year
            published_date = message.get("published-print") or message.get("published-online") or message.get("created", {})
            date_parts = published_date.get("date-parts", [[]])[0]
            year = str(date_parts[0]) if date_parts else "2026"

            # Extract container / journal
            containers = message.get("container-title", [])
            journal = containers[0] if containers else message.get("publisher", "")

            # Generate canonical cite key
            clean_title_word = re.sub(r'[^a-zA-Z0-9]', '', (title_list[0].split()[0] if title_list else "paper")).lower()
            key = f"{first_author_surname.lower()}{year}{clean_title_word}"

            entry_type = "article" if message.get("type") in ("journal-article", "article") else "inproceedings"

            return {
                "ok": True,
                "doi": doi,
                "key": key,
                "type": entry_type,
                "title": title,
                "author": author_str,
                "year": year,
                "journal": journal,
                "url": message.get("URL", f"https://doi.org/{doi}"),
            }
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"CrossRef HTTP Error {e.code}: {e.reason}", "doi": doi}
        except urllib.error.URLError as e:
            return {"ok": False, "error": f"Network Error: {str(e.reason)}", "doi": doi}
        except Exception as e:
            return {"ok": False, "error": f"DOI Lookup failed: {str(e)}", "doi": doi}

    def search_crossref(self, query: str, limit: int = 5, timeout: float = 8.0) -> Dict[str, Any]:
        """Searches CrossRef works for query and returns list of candidate references."""
        if not query.strip():
            return {"ok": False, "error": "Query string is required"}

        encoded_q = urllib.parse.quote(query.strip())
        url = f"https://api.crossref.org/works?query={encoded_q}&rows={limit}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SynapseForge-CiteTool/0.2.0 (mailto:dev@synapseforge.org)",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("message", {}).get("items", [])
            results = []
            for item in items:
                title_list = item.get("title", [])
                title = title_list[0] if title_list else "Untitled"
                authors_list = item.get("author", [])
                authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors_list if a.get('family')]
                author_str = " and ".join(authors) if authors else "Unknown"
                pub_date = item.get("published-print") or item.get("published-online") or item.get("created", {})
                date_parts = pub_date.get("date-parts", [[]])[0]
                year = str(date_parts[0]) if date_parts else ""
                doi = item.get("DOI", "")
                containers = item.get("container-title", [])
                journal = containers[0] if containers else ""

                first_surname = re.sub(r'[^a-zA-Z0-9]', '', authors_list[0].get("family", "paper")) if authors_list else "paper"
                clean_title_word = re.sub(r'[^a-zA-Z0-9]', '', (title.split()[0] if title else "item")).lower()
                key = f"{first_surname.lower()}{year or '2026'}{clean_title_word}"

                results.append({
                    "key": key,
                    "doi": doi,
                    "title": title,
                    "author": author_str,
                    "year": year,
                    "journal": journal,
                    "url": item.get("URL", f"https://doi.org/{doi}" if doi else ""),
                })

            return {"ok": True, "query": query, "count": len(results), "results": results}
        except Exception as e:
            return {"ok": False, "error": f"Search failed: {str(e)}", "query": query}

    def validate_citations(self, sections_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Cross-checks citations in sections/*.md against bibliography.bib."""
        s_dir = sections_dir or self.workspace_root / "sections"
        bib_entries = self.list_citations()
        bib_keys = {entry["key"]: entry for entry in bib_entries}

        cited_keys: Set[str] = set()
        citations_by_file: Dict[str, List[str]] = {}

        if s_dir.exists():
            for p in sorted(s_dir.glob("*.md")):
                content = p.read_text(encoding="utf-8")
                extracted = self.parser.extract_citations(content)
                cited_keys.update(extracted)
                citations_by_file[p.name] = extracted

        # Identify issues
        unresolved = [k for k in sorted(cited_keys) if k not in bib_keys]
        unused = [k for k in sorted(bib_keys.keys()) if k not in cited_keys]
        incomplete = []
        for k, entry in bib_keys.items():
            missing_fields = []
            if not entry.get("title"):
                missing_fields.append("title")
            if not entry.get("author"):
                missing_fields.append("author")
            if not entry.get("year"):
                missing_fields.append("year")
            if missing_fields:
                incomplete.append({"key": k, "missing": missing_fields})

        is_valid = len(unresolved) == 0 and len(incomplete) == 0

        return {
            "ok": True,
            "valid": is_valid,
            "total_cited_in_document": len(cited_keys),
            "total_in_bibliography": len(bib_keys),
            "unresolved_citations": unresolved,
            "unused_in_bibliography": unused,
            "incomplete_entries": incomplete,
            "by_file": citations_by_file,
        }
