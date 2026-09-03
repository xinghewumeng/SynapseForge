#!/usr/bin/env python3
"""Dependency-free MCP stdio server for SynapseForge local Agent CLI collaboration.

Speaks both official Content-Length framing (Codex, Antigravity, MCP SDK) and
newline-delimited JSON (Grok). Responses match the incoming request format.
"""

import json
import os
import sys
import traceback
from pathlib import Path

from synapseforge.core.semantic_diff import SemanticASTDiffer
from synapseforge.core.team_bus import open_bus
from synapseforge.tools.cite_tool import CiteTool


def _cite_tool() -> CiteTool:
    root = os.environ.get("SYNAPSEFORGE_WORKSPACE") or os.getcwd()
    return CiteTool(workspace_root=Path(root))


SERVER_NAME = "synapseforge-team"
SERVER_VERSION = "0.2.0"
INSTRUCTIONS = (
    "SynapseForge local collaboration bus for Codex, Grok Build, Antigravity, and other host Agent CLIs. "
    "Call team_join first with a stable name (codex, grok, or antigravity). "
    "If join.already_online is true you are a DUPLICATE: do not claim, lock, post, or submit. "
    "kind=directive from human, and anything the user says in your own session, is the live instruction; act immediately. "
    "Do not wait forever on a silent lock holder — call team_reclaim_stale_locks. "
    "If codex is silent after one wait_for_activity timeout (coordinator_silent=true), freeze and continue; a live OS process is not a heartbeat. "
    "create_task returns deduplicated=true when the same files/work already has an open card — claim that card. "
    "Before submit/push/deploy, team_claim_action with a unique key so two agents cannot fire twice. "
    "Heartbeat by reading or waiting at least every 60s. The MCP does not bypass user approvals."
)

STDIN = sys.stdin.buffer
STDOUT = sys.stdout.buffer
STDERR = sys.stderr.buffer


def obj_schema(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


ROOM = {"type": "string", "description": "Shared room name. Defaults to SYNAPSEFORGE_ROOM or AGENT_TEAM_ROOM when omitted."}
AGENT = {"type": "string", "description": "Stable identity, normally codex, grok, or antigravity."}

TOOLS = [
    {
        "name": "team_join",
        "description": "Join/create a collaboration room and receive its documents, active tasks, recent discussion, and protocol. Always call this first.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "role": {"type": "string"}, "objective": {"type": "string"}, "workspace": {"type": "string"}}, ["agent"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "team_share_document",
        "description": "Register a local document in the room. Text up to 2 MiB is snapshotted; binary/PDF files remain readable by shared local path.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "path": {"type": "string"}, "title": {"type": "string"}, "copy_content": {"type": "boolean", "default": True}}, ["agent", "path"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "team_list_documents",
        "description": "List all documents shared in a room.",
        "inputSchema": obj_schema({"room": ROOM}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_read_document",
        "description": "Read a shared text document or obtain the local path/metadata for a PDF or binary document.",
        "inputSchema": obj_schema({"room": ROOM, "document_id": {"type": "integer"}, "path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0, "default": 0}, "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 30000}}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_post_message",
        "description": "Post analysis, proposals, decisions, questions, answers, blockers, reviews, or human directives. Duplicate identical posts within a few seconds are ignored.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "message": {"type": "string"}, "kind": {"type": "string", "enum": ["discussion", "proposal", "decision", "question", "answer", "blocker", "review", "directive"]}, "to_agent": {"type": "string"}, "reply_to": {"type": "integer"}}, ["agent", "message"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_read_messages",
        "description": "Read room messages visible to this agent. Call between work phases and before finalizing.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "after_id": {"type": "integer", "minimum": 0, "default": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}, "mark_read": {"type": "boolean", "default": True}}, ["agent"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_create_task",
        "description": "Add a concrete unit of work to the shared task board, optionally naming files it may edit. If an open/in_progress/blocked task already covers the same files or the same title, returns that task with deduplicated=true instead of creating a second card.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "title": {"type": "string"}, "description": {"type": "string"}, "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2}, "files": {"type": "array", "items": {"type": "string"}}}, ["agent", "title"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_list_tasks",
        "description": "List the shared task board, optionally filtered by status.",
        "inputSchema": obj_schema({"room": ROOM, "status": {"type": "string", "enum": ["open", "in_progress", "blocked", "done"]}}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_claim_task",
        "description": "Atomically claim a task. Its declared files are locked at the same time to prevent conflicting edits.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "task_id": {"type": "integer"}, "lock_minutes": {"type": "integer", "minimum": 1, "maximum": 240, "default": 30}}, ["agent", "task_id"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_update_task",
        "description": "Update a claimed task to open, in_progress, blocked, or done and attach a result. Done/open releases task file locks.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["open", "in_progress", "blocked", "done"]}, "result": {"type": "string"}}, ["agent", "task_id", "status"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_lock_files",
        "description": "Acquire expiring locks before editing files not already covered by a claimed task.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "task_id": {"type": "integer"}, "lock_minutes": {"type": "integer", "minimum": 1, "maximum": 240, "default": 30}}, ["agent", "paths"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_unlock_files",
        "description": "Release this agent's file locks (all its room locks when paths is omitted).",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "paths": {"type": "array", "items": {"type": "string"}}}, ["agent"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "team_status",
        "description": "Get a room dashboard: participants, documents, tasks, file locks, silent_agents, coordinator_silent, and latest message ID.",
        "inputSchema": obj_schema({"room": ROOM}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_wait_for_activity",
        "description": "Wait up to 30 seconds for messages after an ID. Also reports stale locks, silent_agents, and coordinator_silent so you can reclaim or continue instead of waiting forever.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "after_message_id": {"type": "integer", "minimum": 0, "default": 0}, "timeout_seconds": {"type": "integer", "minimum": 0, "maximum": 30, "default": 20}}, ["agent"]),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_list_rooms",
        "description": "List collaboration rooms with online-agent counts. Room argument is optional.",
        "inputSchema": obj_schema({"room": ROOM}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_reclaim_stale_locks",
        "description": "Release file locks whose holder has gone silent. Use this instead of waiting forever on a dead coordinator.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT}, ["agent"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_claim_action",
        "description": "Atomically claim a one-shot shared action (submit, push, deploy). Prevents two agents from doing the same irreversible step.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT, "action_key": {"type": "string"}, "ttl_seconds": {"type": "integer", "minimum": 5, "maximum": 3600, "default": 600}}, ["agent", "action_key"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_leave",
        "description": "Leave this agent's exclusive seat, drop its file locks, and mark the participant offline.",
        "inputSchema": obj_schema({"room": ROOM, "agent": AGENT}, ["agent"]),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "team_semantic_diff",
        "description": "Perform semantic AST block-level diff comparison between two text documents or files.",
        "inputSchema": obj_schema({"text_a": {"type": "string"}, "text_b": {"type": "string"}, "file_a": {"type": "string"}, "file_b": {"type": "string"}}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "team_cite_lookup",
        "description": "Lookup academic literature metadata and clean BibTeX by DOI or keyword query via CrossRef.",
        "inputSchema": obj_schema({"doi": {"type": "string"}, "query": {"type": "string"}, "add_to_bib": {"type": "boolean", "default": False}}),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "team_cite_validate",
        "description": "Validate document citation graph (@keys) against project bibliography.bib.",
        "inputSchema": obj_schema({}),
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]


def _log(message):
    try:
        STDERR.write(("synapseforge-team: %s\n" % message).encode("utf-8", errors="replace"))
        STDERR.flush()
    except Exception:
        pass


def _read_exactly(n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = STDIN.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message():
    """Read one JSON-RPC message. Returns (obj, mode) or (None, None) on EOF.

    mode is 'framed' (Content-Length) or 'ndjson' (one JSON object per line).
    """
    first = None
    while True:
        byte = STDIN.read(1)
        if not byte:
            return None, None
        if byte in b" \t\r\n":
            continue
        if byte == b"\xef":
            rest = _read_exactly(2)
            if rest == b"\xbb\xbf":
                continue
            first = byte + rest
            break
        first = byte
        break

    if first.startswith(b"{") or first.startswith(b"["):
        rest = STDIN.readline()
        raw = first + rest
        return json.loads(raw.decode("utf-8")), "ndjson"

    header_buf = first
    while True:
        byte = STDIN.read(1)
        if not byte:
            break
        header_buf += byte
        if header_buf.endswith(b"\r\n\r\n") or header_buf.endswith(b"\n\n"):
            break
        if len(header_buf) > 65536:
            raise ValueError("MCP headers too large")

    headers = {}
    for line in header_buf.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    length = int(headers.get("content-length") or 0)
    body = _read_exactly(length) if length else b""
    if length and len(body) < length:
        raise ValueError("truncated MCP frame (got %d of %d bytes)" % (len(body), length))
    if not body.strip():
        return None, "framed"
    return json.loads(body.decode("utf-8")), "framed"


def write_message(payload, mode):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if mode == "framed":
        STDOUT.write(("Content-Length: %d\r\n\r\n" % len(raw)).encode("ascii"))
        STDOUT.write(raw)
    else:
        STDOUT.write(raw + b"\n")
    STDOUT.flush()


def room_arg(store, args):
    room = args.pop("room", None) or os.environ.get("SYNAPSEFORGE_ROOM") or os.environ.get("AGENT_TEAM_ROOM")
    if not room:
        names = [item["name"] for item in store.list_rooms().get("rooms", [])[:8]]
        hint = ", ".join(names) if names else "(none)"
        raise ValueError("room is required (pass room= or set SYNAPSEFORGE_ROOM). Known rooms: %s" % hint)
    return room


def call_tool(store, name, args):
    args = dict(args or {})
    if name == "team_list_rooms":
        args.pop("room", None)
        return store.list_rooms()
    room = room_arg(store, args)
    dispatch = {
        "team_join": store.join,
        "team_share_document": store.share_document,
        "team_list_documents": store.list_documents,
        "team_read_document": store.read_document,
        "team_post_message": store.post_message,
        "team_read_messages": store.read_messages,
        "team_create_task": store.create_task,
        "team_list_tasks": store.list_tasks,
        "team_claim_task": store.claim_task,
        "team_update_task": store.update_task,
        "team_lock_files": store.lock_files,
        "team_unlock_files": store.unlock_files,
        "team_status": store.status,
        "team_wait_for_activity": store.wait_for_activity,
        "team_reclaim_stale_locks": store.reclaim_stale_locks,
        "team_claim_action": store.claim_action,
        "team_leave": store.leave,
    }
    if name == "team_semantic_diff":
        differ = SemanticASTDiffer()
        if "file_a" in args and "file_b" in args:
            res = differ.diff_files(args["file_a"], args["file_b"])
        else:
            res = differ.diff_texts(args.get("text_a", ""), args.get("text_b", ""), "Doc A", "Doc B")
        return {"ok": True, "diff": res.to_dict()}
    if name == "team_cite_lookup":
        cite = _cite_tool()
        if "doi" in args and args["doi"]:
            lookup_res = cite.lookup_doi(args["doi"])
            if lookup_res.get("ok") and args.get("add_to_bib"):
                cite.add_bibtex_entry(
                    key=lookup_res["key"],
                    entry_type=lookup_res.get("type", "article"),
                    title=lookup_res["title"],
                    author=lookup_res["author"],
                    year=lookup_res["year"],
                    journal_or_book=lookup_res.get("journal", ""),
                    doi=lookup_res.get("doi", ""),
                )
            return lookup_res
        elif "query" in args and args["query"]:
            return cite.search_crossref(args["query"])
        return {"ok": False, "error": "Either 'doi' or 'query' must be provided"}
    if name == "team_cite_validate":
        cite = _cite_tool()
        return cite.validate_citations()

    if name not in dispatch:
        raise ValueError("unknown tool: %s" % name)
    return dispatch[name](room=room, **args)


def result_payload(req_id, value):
    return {"jsonrpc": "2.0", "id": req_id, "result": value}


def error_payload(req_id, code, message, data=None):
    item = {"code": code, "message": message}
    if data is not None:
        item["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": item}


def handle_request(store, request):
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        return result_payload(req_id, {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "tools/list":
        return result_payload(req_id, {"tools": TOOLS})
    if method == "tools/call":
        value = call_tool(store, params.get("name", ""), params.get("arguments") or {})
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
        return result_payload(req_id, {
            "content": [{"type": "text", "text": rendered}],
            "structuredContent": {"result": value},
            "isError": False,
        })
    if method == "ping":
        return result_payload(req_id, {})
    if method == "resources/list":
        return result_payload(req_id, {"resources": []})
    if method == "prompts/list":
        return result_payload(req_id, {"prompts": []})
    if method in {"notifications/initialized", "notifications/cancelled", "logging/setLevel", "shutdown"}:
        return None
    if method == "exit":
        raise SystemExit(0)
    if req_id is not None:
        return error_payload(req_id, -32601, "Method not found: %s" % method)
    return None


def main():
    store = open_bus(workspace=os.environ.get("SYNAPSEFORGE_WORKSPACE") or os.getcwd())
    while True:
        mode = "ndjson"
        req_id = None
        request = None
        try:
            request, mode = read_message()
            if request is None:
                break
            mode = mode or "ndjson"
            req_id = request.get("id")
            payload = handle_request(store, request)
            if payload is not None:
                write_message(payload, mode)
        except SystemExit:
            raise
        except Exception as exc:
            if os.environ.get("SYNAPSEFORGE_TEAM_DEBUG") == "1":
                traceback.print_exc(file=sys.stderr)
            else:
                _log("%s: %s" % (type(exc).__name__, exc))
            if req_id is not None:
                if request and request.get("method") == "tools/call":
                    write_message(result_payload(req_id, {
                        "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
                        "isError": True,
                    }), mode or "ndjson")
                else:
                    write_message(error_payload(req_id, -32603, str(exc)), mode or "ndjson")


if __name__ == "__main__":
    main()
