"""Convert GitHub Copilot chat sessions to HTML transcripts."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import unquote

import click
import jinja2
import markdown as markdown_lib
from click_default_group import DefaultGroup

PROMPTS_PER_PAGE = 5

# --- JSONL Parsing ---


def reconstruct_session(filepath):
    """Reconstruct a full session dict from a Copilot JSONL file.

    The JSONL format uses incremental patches:
    - Line 0 (kind: 0): initialization with full session state in 'v'
    - Subsequent lines (kind: 1): set value at key path 'k'
    - Subsequent lines (kind: 2): extend list at key path 'k' (if value is a list),
      or replace value (if value is not a list)

    Key path elements are strings; numeric strings index into lists.
    """
    filepath = Path(filepath)
    text = filepath.read_text()
    if not text.strip():
        raise ValueError(f"Session file is empty: {filepath}")

    lines = text.strip().split("\n")
    first = json.loads(lines[0])

    if first.get("kind") != 0:
        raise ValueError(f"First line must be kind: 0, got kind: {first.get('kind')}")

    state = first["v"]

    for line in lines[1:]:
        # print(f"Applying patch: {line}")
        patch = json.loads(line)
        keys = patch["k"]
        kind = patch.get("kind")

        if "v" in patch:
            if kind == 2 and isinstance(patch["v"], list):
                # kind:2 on a list value extends the existing list
                _extend_list(state, keys, patch["v"])
            else:
                # kind:1 (or kind:2 with non-list value) sets at key path
                _apply_patch(state, keys, patch["v"])
        elif "i" in patch:
            # Delete operation: remove item at index 'i' from list at key path
            _delete_from_list(state, keys, patch["i"])
        # else: unknown patch shape, skip

    return state


def _apply_patch(state, keys, val):
    """Navigate the key path and set the value."""
    obj = state
    for key in keys[:-1]:
        if isinstance(obj, list):
            idx = int(key)
            # Extend list if needed
            while len(obj) <= idx:
                obj.append({})
            obj = obj[idx]
        elif isinstance(obj, dict):
            if key not in obj:
                obj[key] = {}
            obj = obj[key]
        else:
            return  # Can't navigate into a non-container

    last_key = keys[-1]
    if isinstance(obj, list):
        idx = int(last_key)
        while len(obj) <= idx:
            obj.append(None)
        obj[idx] = val
    elif isinstance(obj, dict):
        obj[last_key] = val


def _extend_list(state, keys, items):
    """Navigate the key path and extend the list at that location."""
    obj = state
    for key in keys[:-1]:
        if isinstance(obj, list):
            idx = int(key)
            while len(obj) <= idx:
                obj.append({})
            obj = obj[idx]
        elif isinstance(obj, dict):
            if key not in obj:
                obj[key] = {}
            obj = obj[key]
        else:
            return

    last_key = keys[-1]
    if isinstance(obj, list):
        idx = int(last_key)
        while len(obj) <= idx:
            obj.append([])
        target = obj[idx]
    elif isinstance(obj, dict):
        if last_key not in obj:
            obj[last_key] = []
        target = obj[last_key]
    else:
        return

    if isinstance(target, list):
        target.extend(items)
    else:
        # Target isn't a list; fall back to replace
        if isinstance(obj, list):
            obj[int(last_key)] = items
        elif isinstance(obj, dict):
            obj[last_key] = items


def _delete_from_list(state, keys, index):
    """Navigate to the list at key path and delete the item at index."""
    obj = state
    for key in keys:
        if isinstance(obj, list):
            obj = obj[int(key)]
        elif isinstance(obj, dict):
            obj = obj[key]
        else:
            return
    if isinstance(obj, list) and 0 <= index < len(obj):
        del obj[index]


# --- Response Stream Parsing ---

# --- Terminal Output Cleaning ---

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")


def _clean_terminal_output(text):
    """Strip ANSI escape sequences and carriage returns from terminal output."""
    cleaned = _ANSI_RE.sub("", text)
    # Collapse runs of whitespace-only lines but preserve meaningful content
    return cleaned.strip()


# Response kinds that are skipped (transient UI elements)
_SKIPPED_KINDS = {
    "mcpServersStarting",
    "prepareToolInvocation",
    "undoStop",
    "codeblockUri",
    "progressTaskSerialized",
    "inlineReference",
    "notebookEditGroup",
}


def parse_response_stream(response_items):
    """Parse a heterogeneous Copilot response stream into structured sections.

    Returns a list of section dicts, each with a 'type' key:
    - type: 'text' — concatenated markdown text
    - type: 'tool' — tool invocation with metadata
    - type: 'thinking' — model thinking/reasoning
    - type: 'edit_group' — grouped file edits
    - type: 'elicitation' — user input request
    - type: 'confirmation' — continuation prompt
    """
    sections = []
    pending_text = []

    def _flush_text():
        if pending_text:
            sections.append({"type": "text", "markdown": "".join(pending_text)})
            pending_text.clear()

    for item in response_items:
        if not isinstance(item, dict):
            continue

        kind = item.get("kind")

        if kind is None and "value" in item:
            val = item["value"]
            if val.strip() == "```":
                continue
            pending_text.append(val)

        elif kind in _SKIPPED_KINDS:
            continue

        elif kind == "toolInvocationSerialized":
            _flush_text()
            inv_msg = item.get("invocationMessage", {})
            past_msg = item.get("pastTenseMessage", {})
            sections.append(
                {
                    "type": "tool",
                    "tool_id": item.get("toolId", "unknown"),
                    "invocation_message": (
                        inv_msg.get("value", "")
                        if isinstance(inv_msg, dict)
                        else str(inv_msg)
                    ),
                    "past_tense_message": (
                        past_msg.get("value", "")
                        if isinstance(past_msg, dict)
                        else str(past_msg)
                    ),
                    "is_confirmed": item.get("isConfirmed"),
                    "is_complete": item.get("isComplete", False),
                    "source": item.get("source"),
                    "result_details": item.get("resultDetails"),
                    "tool_specific_data": item.get("toolSpecificData"),
                    "tool_call_id": item.get("toolCallId"),
                }
            )

        elif kind == "thinking":
            _flush_text()
            sections.append(
                {
                    "type": "thinking",
                    "text": item.get("value", ""),
                    "id": item.get("id"),
                }
            )

        elif kind == "textEditGroup":
            _flush_text()
            uri = item.get("uri", {})
            sections.append(
                {
                    "type": "edit_group",
                    "file_path": uri.get("fsPath", ""),
                    "edits": item.get("edits", []),
                    "done": item.get("done", False),
                }
            )

        elif kind == "elicitation":
            _flush_text()
            title = item.get("title", {})
            message = item.get("message", {})
            sections.append(
                {
                    "type": "elicitation",
                    "title": (
                        title.get("value", "")
                        if isinstance(title, dict)
                        else str(title)
                    ),
                    "message": (
                        message.get("value", "")
                        if isinstance(message, dict)
                        else str(message)
                    ),
                    "state": item.get("state"),
                }
            )

        elif kind == "confirmation":
            _flush_text()
            message = item.get("message", {})
            sections.append(
                {
                    "type": "confirmation",
                    "title": item.get("title", ""),
                    "message": (
                        message.get("value", "")
                        if isinstance(message, dict)
                        else str(message)
                    ),
                    "buttons": item.get("buttons", []),
                    "is_used": item.get("isUsed", False),
                }
            )

        # else: unknown kind, skip

    _flush_text()
    return _dedup_sections(sections)


def _dedup_sections(sections):
    """Deduplicate thinking blocks and tool invocations.

    Copilot JSONL stores incremental snapshots of thinking and tool items,
    resulting in duplicates. This pass:
    - Removes empty thinking blocks (id='' with no text)
    - For thinking blocks with the same id, keeps only the longest (final snapshot)
    - For tool invocations with the same tool_call_id, keeps the most complete one
    """
    # --- Pass 1: Deduplicate thinking blocks ---
    # Find the best (longest) thinking section for each id
    best_thinking = {}
    for i, s in enumerate(sections):
        if s["type"] != "thinking":
            continue
        tid = s.get("id") or ""
        text = s.get("text", "")
        if isinstance(text, list):
            text_len = sum(len(str(x)) for x in text)
        elif isinstance(text, str):
            text_len = len(text.strip())
        else:
            text_len = 0
        if text_len == 0:
            continue
        key = tid if tid else f"_anon_{i}"
        prev_len, _ = best_thinking.get(key, (0, -1))
        if text_len >= prev_len:
            best_thinking[key] = (text_len, i)

    best_thinking_indices = {idx for _, idx in best_thinking.values()}

    # --- Pass 2: Deduplicate tool invocations ---
    # For each tool_call_id, find the best (most info) section
    best_tool = {}
    for i, s in enumerate(sections):
        if s["type"] != "tool":
            continue
        call_id = s.get("tool_call_id")
        if not call_id:
            best_tool[f"_notool_{i}"] = i
            continue
        past = s.get("past_tense_message", "")
        has_tsd = s.get("tool_specific_data") is not None
        has_rd = s.get("result_details") is not None
        score = len(past) + (100 if has_tsd else 0) + (50 if has_rd else 0)
        prev_score, _ = best_tool.get(call_id, (-1, -1))
        if score >= prev_score:
            best_tool[call_id] = (score, i)

    best_tool_indices = set()
    for v in best_tool.values():
        if isinstance(v, int):
            best_tool_indices.add(v)
        else:
            best_tool_indices.add(v[1])

    # --- Build filtered output ---
    result = []
    for i, s in enumerate(sections):
        if s["type"] == "thinking":
            if i not in best_thinking_indices:
                continue
        elif s["type"] == "tool":
            if i not in best_tool_indices:
                continue
        result.append(s)

    return result


# --- Session Discovery ---


def get_workspace_storage_path():
    """Return the VS Code workspace storage path for the current platform."""
    if sys.platform == "darwin":
        home = os.environ.get("HOME", str(Path.home()))
        return (
            Path(home)
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "workspaceStorage"
        )
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Code" / "User" / "workspaceStorage"
    else:
        # Linux and other Unix
        home = os.environ.get("HOME", str(Path.home()))
        return Path(home) / ".config" / "Code" / "User" / "workspaceStorage"


def get_project_for_workspace(ws_dir):
    """Read workspace.json and return the project path, or None."""
    ws_json = Path(ws_dir) / "workspace.json"
    if not ws_json.exists():
        return None
    try:
        data = json.loads(ws_json.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    uri = data.get("folder") or data.get("workspace")
    if not uri:
        return None

    if uri.startswith("file:///"):
        # Strip file:// prefix and decode percent-encoding
        path = unquote(uri[len("file://") :])
        # On Windows, file:///C:/... decodes to /C:/...
        # Strip the leading / before a drive letter
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path

    return uri


def get_session_title(filepath):
    """Extract a display title from a JSONL session file.

    Reconstructs just enough of the session to get the title,
    checking customTitle first, then first request message text.
    """
    session = reconstruct_session(filepath)

    title = session.get("customTitle")
    if title:
        return title

    requests = session.get("requests", [])
    for req in requests:
        if not isinstance(req, dict):
            continue
        msg = req.get("message", {})
        if isinstance(msg, dict):
            text = msg.get("text", "")
            if text:
                if len(text) > 100:
                    return text[:100] + "..."
                return text

    return "Untitled session"


def find_all_sessions(workspace_path=None):
    """Discover all Copilot chat sessions grouped by project.

    Returns a list of project dicts:
    [
        {
            "name": "project-name",
            "path": "/full/path/to/project",
            "sessions": [
                {
                    "path": Path,
                    "title": str,
                    "mtime": float,
                    "size": int,
                }
            ]
        }
    ]
    """
    if workspace_path is None:
        workspace_path = get_workspace_storage_path()
    workspace_path = Path(workspace_path)

    projects = {}

    if not workspace_path.exists():
        return []

    for ws_dir in workspace_path.iterdir():
        if not ws_dir.is_dir():
            continue

        project_path = get_project_for_workspace(ws_dir)
        if not project_path:
            continue

        chat_dir = ws_dir / "chatSessions"
        if not chat_dir.exists():
            continue

        sessions = []
        for session_file in chat_dir.glob("*.jsonl"):
            try:
                title = get_session_title(session_file)
                stat = session_file.stat()
                sessions.append(
                    {
                        "path": session_file,
                        "title": title,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                )
            except (ValueError, json.JSONDecodeError, OSError):
                continue

        if not sessions:
            continue

        # Sort sessions by mtime, newest first
        sessions.sort(key=lambda s: s["mtime"], reverse=True)

        # Extract display name from project path
        name = project_path.rstrip("/").split("/")[-1]

        if project_path in projects:
            projects[project_path]["sessions"].extend(sessions)
            projects[project_path]["sessions"].sort(
                key=lambda s: s["mtime"], reverse=True
            )
        else:
            projects[project_path] = {
                "name": name,
                "path": project_path,
                "sessions": sessions,
            }

    return sorted(projects.values(), key=lambda p: p["name"].lower())


# --- CSS and JS ---

CSS = """
:root { --bg-color: #f5f5f5; --card-bg: #ffffff; --user-bg: #e8f5e9; --user-border: #388e3c; --assistant-bg: #f5f5f5; --assistant-border: #78909c; --thinking-bg: #fff8e1; --thinking-border: #ffc107; --thinking-text: #666; --tool-bg: #e8eaf6; --tool-border: #5c6bc0; --tool-result-bg: #e8f5e9; --tool-error-bg: #ffebee; --text-color: #212121; --text-muted: #757575; --code-bg: #263238; --code-text: #aed581; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; padding: 16px; line-height: 1.6; }
.container { max-width: 800px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 24px; padding-bottom: 8px; border-bottom: 2px solid var(--user-border); }
.header-row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; border-bottom: 2px solid var(--user-border); padding-bottom: 8px; margin-bottom: 24px; }
.header-row h1 { border-bottom: none; padding-bottom: 0; margin-bottom: 0; flex: 1; min-width: 200px; }
.message { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.message.user { background: var(--user-bg); border-left: 4px solid var(--user-border); }
.message.assistant { background: var(--card-bg); border-left: 4px solid var(--assistant-border); }
.message-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; background: rgba(0,0,0,0.03); font-size: 0.85rem; }
.message-meta { display: flex; align-items: center; gap: 8px; }
.model-badge { font-size: 0.75rem; padding: 2px 8px; background: rgba(0,0,0,0.08); border-radius: 10px; color: var(--text-muted); }
.role-label { font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.user .role-label { color: var(--user-border); }
time { color: var(--text-muted); font-size: 0.8rem; }
.timestamp-link { color: inherit; text-decoration: none; }
.timestamp-link:hover { text-decoration: underline; }
.message:target { animation: highlight 2s ease-out; }
@keyframes highlight { 0% { background-color: rgba(56, 142, 60, 0.2); } 100% { background-color: transparent; } }
.message-content { padding: 16px; }
.message-content p { margin: 0 0 12px 0; }
.message-content p:last-child { margin-bottom: 0; }
.thinking { background: var(--thinking-bg); border: 1px solid var(--thinking-border); border-radius: 8px; padding: 12px; margin: 12px 0; font-size: 0.9rem; color: var(--thinking-text); }
.thinking-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; color: #f57c00; margin-bottom: 8px; }
.thinking p { margin: 8px 0; }
.assistant-text { margin: 8px 0; }
.tool-use { background: var(--tool-bg); border: 1px solid var(--tool-border); border-radius: 8px; padding: 12px; margin: 12px 0; }
.tool-header { font-weight: 600; color: var(--tool-border); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.tool-icon { font-size: 1.1rem; }
.tool-description { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 8px; font-style: italic; }
.file-tool { border-radius: 8px; padding: 12px; margin: 12px 0; }
.write-tool { background: linear-gradient(135deg, #e3f2fd 0%, #e8f5e9 100%); border: 1px solid #4caf50; }
.edit-tool { background: linear-gradient(135deg, #fff3e0 0%, #fce4ec 100%); border: 1px solid #ff9800; }
.file-tool-header { font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; font-size: 0.95rem; }
.write-header { color: #2e7d32; }
.edit-header { color: #e65100; }
.file-tool-icon { font-size: 1rem; }
.file-tool-path { font-family: monospace; background: rgba(0,0,0,0.08); padding: 2px 8px; border-radius: 4px; }
.file-tool-fullpath { font-family: monospace; font-size: 0.8rem; color: var(--text-muted); margin-bottom: 8px; word-break: break-all; }
.file-content { margin: 0; }
.edit-section { display: flex; margin: 4px 0; border-radius: 4px; overflow: hidden; }
.edit-label { padding: 8px 12px; font-weight: bold; font-family: monospace; display: flex; align-items: flex-start; }
.edit-new { background: #e8f5e9; }
.edit-new .edit-label { color: #1b5e20; background: #a5d6a7; }
.edit-new .edit-content { color: #1b5e20; }
.edit-content { margin: 0; flex: 1; background: transparent; font-size: 0.85rem; }
.todo-list { background: linear-gradient(135deg, #e8f5e9 0%, #f1f8e9 100%); border: 1px solid #81c784; border-radius: 8px; padding: 12px; margin: 12px 0; }
.todo-header { font-weight: 600; color: #2e7d32; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; font-size: 0.95rem; }
.search-tool { background: #e3f2fd; border-color: #1976d2; }
.search-tool .tool-header { color: #1976d2; }
.bash-tool { background: #eceff1; border-color: #546e7a; }
.bash-tool .tool-header { color: #37474f; }
.mcp-tool { background: #f3e5f5; border-color: #9c27b0; }
.mcp-tool .tool-header { color: #7b1fa2; }
.elicitation { background: #fff8e1; border-color: #ffc107; }
.elicitation .tool-header { color: #f57c00; }
.confirmation { background: #fce4ec; border-color: #e91e63; }
.confirmation .tool-header { color: #c2185b; }
.read-tool { background: #e8f5e9; border-color: #4caf50; }
.read-tool .tool-header { color: #2e7d32; }
pre { background: var(--code-bg); color: var(--code-text); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; line-height: 1.5; margin: 8px 0; white-space: pre-wrap; word-wrap: break-word; }
code { background: rgba(0,0,0,0.08); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
pre code { background: none; padding: 0; }
.user-content { margin: 0; }
.truncatable { position: relative; }
.truncatable .truncatable-content { max-height: 300px; overflow-y: auto; }
.bash-output { margin: 4px 0 0 0; background: #1e1e1e; color: #cccccc; font-size: 0.82rem; }
.bash-command { margin: 0; }
.exit-code { font-size: 0.78rem; font-weight: 400; color: #c62828; margin-left: 8px; }
.pagination { display: flex; justify-content: center; gap: 8px; margin: 24px 0; flex-wrap: wrap; }
.pagination a, .pagination span { padding: 5px 10px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; }
.pagination a { background: var(--card-bg); color: var(--user-border); border: 1px solid var(--user-border); }
.pagination a:hover { background: var(--user-bg); }
.pagination .current { background: var(--user-border); color: white; }
.pagination .disabled { color: var(--text-muted); border: 1px solid #ddd; }
.pagination .index-link { background: var(--user-border); color: white; }
.index-item { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); background: var(--user-bg); border-left: 4px solid var(--user-border); }
.index-item a { display: block; text-decoration: none; color: inherit; }
.index-item a:hover { background: rgba(56, 142, 60, 0.1); }
.index-item-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; background: rgba(0,0,0,0.03); font-size: 0.85rem; }
.index-item-number { font-weight: 600; color: var(--user-border); }
.index-item-content { padding: 16px; }
.index-item-stats { padding: 8px 16px 12px 32px; font-size: 0.85rem; color: var(--text-muted); border-top: 1px solid rgba(0,0,0,0.06); }
@media (max-width: 600px) { body { padding: 8px; } .message, .index-item { border-radius: 8px; } .message-content, .index-item-content { padding: 12px; } pre { font-size: 0.8rem; padding: 8px; } }
"""

JS = """
document.querySelectorAll('time[data-timestamp]').forEach(function(el) {
    var timestamp = el.getAttribute('data-timestamp');
    var date;
    if (/^\\d+$/.test(timestamp)) {
        date = new Date(parseInt(timestamp));
    } else {
        date = new Date(timestamp);
    }
    if (isNaN(date.getTime())) return;
    var now = new Date();
    var isToday = date.toDateString() === now.toDateString();
    var timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    if (isToday) { el.textContent = timeStr; }
    else { el.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr; }
});
"""


# --- Template Engine ---

_template_env = None
_macros = None


def _get_template_env():
    global _template_env
    if _template_env is None:
        template_dir = Path(__file__).parent / "templates"
        _template_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            autoescape=True,
        )
    return _template_env


def get_template(name):
    return _get_template_env().get_template(name)


def _get_macros():
    global _macros
    if _macros is None:
        _macros = _get_template_env().get_template("macros.html").module
    return _macros


# --- Markdown Rendering ---

_md = None


def _get_md():
    global _md
    if _md is None:
        _md = markdown_lib.Markdown(extensions=["fenced_code", "tables"])
    return _md


def render_markdown_text(text):
    """Convert markdown text to HTML."""
    if isinstance(text, list):
        text = "\n".join(str(item) for item in text)
    if not isinstance(text, str):
        text = str(text)
    md = _get_md()
    md.reset()
    return md.convert(text)


# --- HTML Rendering ---


def make_msg_id(timestamp):
    """Create a stable HTML id from a timestamp."""
    if isinstance(timestamp, (int, float)):
        return f"msg-{int(timestamp)}"
    return f"msg-{timestamp}"


def render_section(section):
    """Render a parsed response section to HTML."""
    macros = _get_macros()
    section_type = section["type"]

    if section_type == "text":
        html = render_markdown_text(section["markdown"])
        return macros.assistant_text(html)

    elif section_type == "thinking":
        html = render_markdown_text(section["text"])
        return macros.thinking(html)

    elif section_type == "tool":
        return _render_tool_section(section)

    elif section_type == "edit_group":
        return macros.text_edit_group(
            section["file_path"],
            section["edits"],
            section["done"],
        )

    elif section_type == "elicitation":
        return macros.elicitation_block(
            section["title"],
            section["message"],
            section.get("state"),
        )

    elif section_type == "confirmation":
        return macros.confirmation_block(
            section["title"],
            section["message"],
        )

    return ""


# Tool ID to renderer mapping
_TOOL_RENDERERS = {
    "copilot_readFile": "read_file",
    "copilot_createFile": "create_file",
    "copilot_createDirectory": "create_dir",
    "copilot_replaceString": "replace",
    "copilot_multiReplaceString": "replace",
    "copilot_applyPatch": "apply_patch",
    "copilot_findFiles": "find_files",
    "copilot_findTextInFiles": "find_text",
    "copilot_searchCodebase": "search_codebase",
    "copilot_listDirectory": "list_dir",
    "copilot_getErrors": "get_errors",
    "run_in_terminal": "terminal",
    "runTests": "run_tests",
    "manage_todo_list": "todo",
    "copilot_fetchWebPage": "fetch_web",
    "vscode_fetchWebPage_internal": "fetch_web",
    "copilot_githubRepo": "github_repo",
}


def _render_tool_section(section):
    """Render a tool invocation section using the appropriate macro."""
    macros = _get_macros()
    tool_id = section["tool_id"]
    past_msg = section["past_tense_message"]
    inv_msg = section["invocation_message"]

    renderer = _TOOL_RENDERERS.get(tool_id)

    if tool_id.startswith("mcp_"):
        return macros.mcp_tool(tool_id, past_msg, section.get("source"))

    if renderer == "read_file":
        return macros.read_file_tool(past_msg or inv_msg)
    elif renderer == "create_file":
        return macros.create_file_tool(past_msg or inv_msg)
    elif renderer == "create_dir":
        return macros.create_dir_tool(past_msg or inv_msg)
    elif renderer == "replace":
        display = "Replace String" if "multi" not in tool_id else "Multi-Replace"
        return macros.replace_tool(display, past_msg or inv_msg)
    elif renderer == "apply_patch":
        return macros.apply_patch_tool(past_msg or inv_msg)
    elif renderer == "find_files":
        return macros.find_files_tool(past_msg or inv_msg)
    elif renderer == "find_text":
        return macros.find_text_tool(past_msg or inv_msg)
    elif renderer == "search_codebase":
        return macros.search_codebase_tool(past_msg or inv_msg)
    elif renderer == "list_dir":
        return macros.list_dir_tool(past_msg or inv_msg)
    elif renderer == "get_errors":
        return macros.get_errors_tool(past_msg or inv_msg)
    elif renderer == "terminal":
        tsd = section.get("tool_specific_data") or {}
        command = inv_msg
        output = ""
        exit_code = None
        if isinstance(tsd, dict) and tsd.get("kind") == "terminal":
            cmd_line = tsd.get("commandLine", {})
            if isinstance(cmd_line, dict):
                command = command or cmd_line.get("original", "")
            cmd_output = tsd.get("terminalCommandOutput", {})
            if isinstance(cmd_output, dict):
                raw_output = cmd_output.get("text", "")
                output = _clean_terminal_output(raw_output)
            cmd_state = tsd.get("terminalCommandState", {})
            if isinstance(cmd_state, dict) and "exitCode" in cmd_state:
                exit_code = cmd_state["exitCode"]
        return macros.terminal_tool(command, past_msg, output, exit_code)
    elif renderer == "run_tests":
        return macros.run_tests_tool(past_msg or inv_msg)
    elif renderer == "todo":
        tsd = section.get("tool_specific_data") or {}
        todo_items = []
        if isinstance(tsd, dict) and tsd.get("kind") == "todoList":
            todo_items = tsd.get("todoList", [])
        return macros.todo_tool(past_msg, todo_items)
    elif renderer == "fetch_web":
        return macros.fetch_web_tool(past_msg or inv_msg)
    elif renderer == "github_repo":
        return macros.github_repo_tool(past_msg or inv_msg)
    else:
        # Generic tool display
        display_name = tool_id.replace("copilot_", "").replace("_", " ").title()
        return macros.tool_invocation(tool_id, display_name, past_msg or inv_msg, "⚙")


def format_tool_stats(tool_counts):
    """Format tool counts into a summary string."""
    if not tool_counts:
        return ""
    parts = []
    for tool_id, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        # Shorten tool names for display
        name = tool_id.replace("copilot_", "").replace("_", " ")
        if count > 1:
            parts.append(f"{name} ×{count}")
        else:
            parts.append(name)
    return " · ".join(parts)


def count_tools_in_response(response):
    """Count tool invocations in a response stream."""
    counts = {}
    if not isinstance(response, list):
        return counts
    for item in response:
        if isinstance(item, dict) and item.get("kind") == "toolInvocationSerialized":
            tool_id = item.get("toolId", "unknown")
            counts[tool_id] = counts.get(tool_id, 0) + 1
    return counts


# --- HTML Generation Pipeline ---


def generate_html(session_path, output_dir):
    """Generate paginated HTML transcripts from a Copilot JSONL session file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = reconstruct_session(session_path)
    session_title = session.get("customTitle") or "Untitled session"

    requests = session.get("requests", [])
    # Filter out None entries from list extension during reconstruction,
    # empty stubs from JSONL patch artifacts, and orphan requests without a message.
    requests = [
        r for r in requests if isinstance(r, dict) and r.get("message", {}).get("text")
    ]

    total_requests = len(requests)
    total_pages = max(1, (total_requests + PROMPTS_PER_PAGE - 1) // PROMPTS_PER_PAGE)

    # Generate page files
    for page_num in range(1, total_pages + 1):
        start_idx = (page_num - 1) * PROMPTS_PER_PAGE
        end_idx = min(start_idx + PROMPTS_PER_PAGE, total_requests)
        page_requests = requests[start_idx:end_idx]

        messages_html_parts = []
        for req in page_requests:
            messages_html_parts.append(_render_request(req))

        macros = _get_macros()
        pagination_html = macros.pagination(page_num, total_pages)

        page_template = get_template("page.html")
        page_content = page_template.render(
            css=CSS,
            js=JS,
            page_num=page_num,
            total_pages=total_pages,
            pagination_html=pagination_html,
            messages_html="".join(messages_html_parts),
        )
        (output_dir / f"page-{page_num:03d}.html").write_text(
            page_content, encoding="utf-8"
        )

    # Build index
    total_tool_counts = {}
    for req in requests:
        tool_counts = count_tools_in_response(req.get("response", []))
        for tool, count in tool_counts.items():
            total_tool_counts[tool] = total_tool_counts.get(tool, 0) + count
    total_tool_calls = sum(total_tool_counts.values())

    index_items = []
    macros = _get_macros()
    for i, req in enumerate(requests):
        msg = req.get("message", {})
        user_text = msg.get("text", "") if isinstance(msg, dict) else ""
        timestamp = req.get("timestamp", "")
        if isinstance(timestamp, (int, float)):
            timestamp = str(int(timestamp))

        page_num = (i // PROMPTS_PER_PAGE) + 1
        msg_id = make_msg_id(timestamp)
        link = f"page-{page_num:03d}.html#{msg_id}"

        rendered_content = render_markdown_text(user_text[:300] or "...")

        tool_counts = count_tools_in_response(req.get("response", []))
        stats_str = format_tool_stats(tool_counts)
        stats_html = macros.index_stats(stats_str)

        item_html = macros.index_item(
            i + 1, link, timestamp, rendered_content, stats_html
        )
        index_items.append(item_html)

    index_pagination = macros.index_pagination(total_pages)
    index_template = get_template("index.html")
    index_content = index_template.render(
        css=CSS,
        js=JS,
        pagination_html=index_pagination,
        prompt_count=total_requests,
        total_tool_calls=total_tool_calls,
        total_pages=total_pages,
        session_title=session_title,
        index_items_html="".join(index_items),
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")

    return {
        "total_requests": total_requests,
        "total_pages": total_pages,
        "total_tool_calls": total_tool_calls,
        "index_path": index_path,
    }


def _render_request(req):
    """Render a single Copilot request (user prompt + assistant response) to HTML."""
    macros = _get_macros()

    # User message
    msg = req.get("message", {})
    user_text = msg.get("text", "") if isinstance(msg, dict) else ""
    timestamp = req.get("timestamp", "")
    model_id = req.get("modelId", "")

    if isinstance(timestamp, (int, float)):
        ts_str = str(int(timestamp))
    else:
        ts_str = str(timestamp)

    msg_id = make_msg_id(ts_str)

    # Render user message
    user_html = render_markdown_text(user_text or "(empty prompt)")
    user_content = macros.user_content(user_html)
    user_msg = macros.message("user", "User", msg_id, ts_str, user_content, "")

    # Render assistant response
    response = req.get("response", [])
    if not isinstance(response, list):
        response = []

    sections = parse_response_stream(response)
    assistant_parts = []
    for section in sections:
        rendered = render_section(section)
        if rendered:
            assistant_parts.append(rendered)

    if assistant_parts:
        # Short model display name
        model_display = ""
        if model_id:
            model_display = model_id.replace("copilot/", "")

        assistant_msg = macros.message(
            "assistant",
            "Copilot",
            f"{msg_id}-response",
            ts_str,
            "".join(assistant_parts),
            model_display,
        )
        return user_msg + assistant_msg

    return user_msg


# --- Batch HTML Generation ---


def generate_batch_html(
    output_dir, workspace_path=None, quiet=False, progress_callback=None
):
    """Generate HTML for all discovered Copilot sessions.

    Creates a browseable archive with per-project and per-session pages.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    projects = find_all_sessions(workspace_path)
    if not projects:
        if not quiet:
            click.echo("No Copilot sessions found.")
        return {"total_projects": 0, "total_sessions": 0, "errors": 0}

    total_sessions = sum(len(p["sessions"]) for p in projects)
    errors = 0
    processed = 0

    project_info = []
    for project in projects:
        project_dir = output_dir / project["name"]
        project_dir.mkdir(exist_ok=True)

        session_info = []
        for session in project["sessions"]:
            session_name = session["path"].stem
            session_dir = project_dir / session_name
            try:
                generate_html(session["path"], session_dir)
                mtime = session.get("mtime", 0)
                date_str = datetime.datetime.fromtimestamp(mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
                session_info.append(
                    {
                        "name": session_name,
                        "title": session.get("title", "Untitled"),
                        "date": date_str,
                        "size_kb": session.get("size", 0) / 1024,
                    }
                )
                processed += 1
                if progress_callback:
                    progress_callback(processed, total_sessions)
                elif not quiet:
                    click.echo(
                        f"  [{processed}/{total_sessions}] {project['name']}/{session_name}"
                    )
            except Exception as e:
                errors += 1
                if not quiet:
                    click.echo(f"  Error processing {session['path']}: {e}", err=True)

        if session_info:
            # Generate project index
            proj_index = get_template("project_index.html")
            proj_html = proj_index.render(
                css=CSS,
                js=JS,
                project_name=project["name"],
                session_count=len(session_info),
                sessions=session_info,
            )
            (project_dir / "index.html").write_text(proj_html, encoding="utf-8")

            most_recent = max(s["mtime"] for s in project["sessions"])
            project_info.append(
                {
                    "name": project["name"],
                    "session_count": len(session_info),
                    "recent_date": datetime.datetime.fromtimestamp(
                        most_recent
                    ).strftime("%Y-%m-%d"),
                }
            )

    # Generate master index
    master_index = get_template("master_index.html")
    master_html = master_index.render(
        css=CSS,
        js=JS,
        total_projects=len(project_info),
        total_sessions=processed,
        projects=project_info,
    )
    (output_dir / "index.html").write_text(master_html, encoding="utf-8")

    return {
        "total_projects": len(project_info),
        "total_sessions": processed,
        "errors": errors,
    }


# --- Gist Support ---

GIST_PREVIEW_JS = r"""
(function() {
    var hostname = window.location.hostname;
    if (hostname !== 'gisthost.github.io' && hostname !== 'gistpreview.github.io') return;
    var match = window.location.search.match(/^\?([^/]+)/);
    if (!match) return;
    var gistId = match[1];

    function rewriteLinks(root) {
        (root || document).querySelectorAll('a[href]').forEach(function(link) {
            var href = link.getAttribute('href');
            if (href.startsWith('?')) return;
            if (href.startsWith('http') || href.startsWith('#') || href.startsWith('//')) return;
            var parts = href.split('#');
            var filename = parts[0];
            var anchor = parts.length > 1 ? '#' + parts[1] : '';
            link.setAttribute('href', '?' + gistId + '/' + filename + anchor);
        });
    }

    rewriteLinks();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { rewriteLinks(); });
    }

    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            mutation.addedNodes.forEach(function(node) {
                if (node.nodeType === 1) {
                    rewriteLinks(node);
                    if (node.tagName === 'A' && node.getAttribute('href')) {
                        var href = node.getAttribute('href');
                        if (!href.startsWith('?') && !href.startsWith('http') &&
                            !href.startsWith('#') && !href.startsWith('//')) {
                            var parts = href.split('#');
                            var filename = parts[0];
                            var anchor = parts.length > 1 ? '#' + parts[1] : '';
                            node.setAttribute('href', '?' + gistId + '/' + filename + anchor);
                        }
                    }
                }
            });
        });
    });

    function startObserving() {
        if (document.body) {
            observer.observe(document.body, { childList: true, subtree: true });
        } else {
            setTimeout(startObserving, 10);
        }
    }
    startObserving();

    function scrollToFragment() {
        var hash = window.location.hash;
        if (!hash) return false;
        var target = document.getElementById(hash.substring(1));
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return true;
        }
        return false;
    }

    if (!scrollToFragment()) {
        [100, 300, 500, 1000, 2000].forEach(function(delay) {
            setTimeout(scrollToFragment, delay);
        });
    }
})();
"""


def inject_gist_preview_js(output_dir):
    """Inject gist preview JavaScript into all HTML files in the output directory."""
    output_dir = Path(output_dir)
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        if "</body>" in content:
            content = content.replace(
                "</body>", f"<script>{GIST_PREVIEW_JS}</script>\n</body>"
            )
            html_file.write_text(content, encoding="utf-8")


def create_gist(output_dir, public=False):
    """Create a GitHub gist from the HTML files in output_dir.

    Returns (gist_id, gist_url) on success, or raises click.ClickException on failure.
    """
    output_dir = Path(output_dir)
    html_files = list(output_dir.glob("*.html"))
    if not html_files:
        raise click.ClickException("No HTML files found to upload to gist.")

    cmd = ["gh", "gist", "create"]
    cmd.extend(str(f) for f in sorted(html_files))
    if public:
        cmd.append("--public")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        gist_url = result.stdout.strip()
        gist_id = gist_url.rstrip("/").split("/")[-1]
        return gist_id, gist_url
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise click.ClickException(f"Failed to create gist: {error_msg}")
    except FileNotFoundError:
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'."
        )


# --- CLI ---


@click.group(cls=DefaultGroup, default="local", default_if_no_args=True)
@click.version_option(None, "-v", "--version", package_name="gh-copilot-transcripts")
def cli():
    """Convert GitHub Copilot chat sessions to mobile-friendly HTML pages."""
    pass


@cli.command()
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory (default: ./_transcripts)",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated HTML in your browser",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gisthost.github.io preview URL",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Copy source JSONL file to the output directory",
)
@click.option("--project", "-p", help="Filter sessions by project name")
@click.option(
    "--limit",
    type=int,
    default=50,
    help="Maximum number of sessions to show (default: 50)",
)
def local(output, open_browser, gist, include_json, project, limit):
    """Select and convert a local Copilot chat session (default command).

    Opens an interactive picker to choose from your VS Code workspaceStorage sessions.
    """
    import questionary

    projects = find_all_sessions()
    if not projects:
        raise click.ClickException(
            "No Copilot chat sessions found. Make sure VS Code workspace storage exists."
        )

    # Build flat list of sessions with project info
    choices = []
    for proj in projects:
        if project and project.lower() not in proj["name"].lower():
            continue
        for session in proj["sessions"]:
            mtime = session.get("mtime", 0)
            date_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            size_kb = session.get("size", 0) / 1024
            title = session.get("title", "Untitled")
            label = f"[{proj['name']}] {title} ({date_str}, {size_kb:.0f} KB)"
            choices.append(questionary.Choice(title=label, value=session["path"]))
            if len(choices) >= limit:
                break
        if len(choices) >= limit:
            break

    if not choices:
        raise click.ClickException(
            f"No sessions found matching project filter: {project}"
        )

    selected = questionary.select("Select a session:", choices=choices).ask()

    if selected is None:
        return

    output_dir = Path(output) if output else Path.cwd() / "_transcripts"

    result = generate_html(selected, output_dir)
    click.echo(
        f"Generated {result['total_pages']} page(s) with "
        f"{result['total_requests']} requests in {output_dir}"
    )

    if include_json:
        json_dest = output_dir / Path(selected).name
        shutil.copy(selected, json_dest)
        click.echo(f"Copied source JSONL to {json_dest}")

    if gist:
        inject_gist_preview_js(output_dir)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output_dir)
        preview_url = f"https://gisthost.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser:
        webbrowser.open(str(result["index_path"]))


@cli.command("json")
@click.argument("path")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory (default: ./_transcripts)",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated HTML in your browser",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gisthost.github.io preview URL",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Copy source JSONL file to the output directory",
)
def json_cmd(path, output, open_browser, gist, include_json):
    """Convert a specific Copilot JSONL session file to HTML.

    PATH: Path to the .jsonl session file to convert
    """
    session_path = Path(path)
    if not session_path.exists():
        raise click.ClickException(f"File not found: {path}")

    output_dir = Path(output) if output else Path.cwd() / "_transcripts"

    result = generate_html(session_path, output_dir)
    click.echo(
        f"Generated {result['total_pages']} page(s) with "
        f"{result['total_requests']} requests in {output_dir}"
    )

    if include_json:
        json_dest = output_dir / session_path.name
        shutil.copy(session_path, json_dest)
        click.echo(f"Copied source JSONL to {json_dest}")

    if gist:
        inject_gist_preview_js(output_dir)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output_dir)
        preview_url = f"https://gisthost.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser:
        webbrowser.open(str(result["index_path"]))


@cli.command()
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory (default: ./_transcripts)",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated master index in your browser",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be generated without creating files",
)
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output")
def all(output, open_browser, dry_run, quiet):
    """Batch convert all local Copilot sessions to a browseable HTML archive.

    Discovers all sessions in VS Code's workspaceStorage and generates a three-level
    hierarchy: master index → project indexes → paginated session transcripts.
    """
    projects = find_all_sessions()
    if not projects:
        raise click.ClickException("No Copilot chat sessions found.")

    total_sessions = sum(len(p["sessions"]) for p in projects)

    if dry_run:
        click.echo(f"Found {len(projects)} projects with {total_sessions} sessions:")
        for proj in projects:
            click.echo(f"  {proj['name']}: {len(proj['sessions'])} sessions")
        return

    output_dir = Path(output) if output else Path.cwd() / "_transcripts"

    if not quiet:
        click.echo(
            f"Generating archive for {len(projects)} projects, {total_sessions} sessions..."
        )

    result = generate_batch_html(output_dir, quiet=quiet)

    if not quiet:
        click.echo(
            f"\nArchive generated: {result['total_projects']} projects, "
            f"{result['total_sessions']} sessions"
        )
        if result["errors"]:
            click.echo(f"  ({result['errors']} errors)")
        click.echo(f"Output: {output_dir}")

    if open_browser:
        webbrowser.open(str(output_dir / "index.html"))
