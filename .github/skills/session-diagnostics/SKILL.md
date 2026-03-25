---
name: session-diagnostics
description: "Investigate and debug GitHub Copilot JSONL session data issues. USE WHEN: HTML transcripts show empty thinking blocks, missing tool results, duplicate entries, malformed content, or rendering errors. USE FOR: inspecting raw session structure, detecting duplicates, verifying deduplication, running E2E HTML generation checks."
---

# Session Diagnostics

Investigate issues with Copilot chat session JSONL data and the HTML transcripts generated from it.

## When to Use

- HTML output has empty or missing thinking blocks
- Tool invocation results are missing or duplicated
- Unexpected rendering artifacts in generated transcripts
- Need to understand the raw JSONL data structure for a session
- Verifying that `_dedup_sections()` is working correctly

## Key Concepts

### JSONL Incremental Snapshot Format

Copilot sessions are stored as JSONL files with an incremental patch system:
- `kind: 0` — Initialize the session object
- `kind: 1` — Set a field at a specific path
- `kind: 2` — Extend/replace content at a specific path

The response array for each request contains **incremental snapshots**:
- **Thinking blocks**: The same `id` appears many times as content grows (each snapshot is longer than the previous). Empty sentinel blocks (`id=''`, `value=''`) may appear after real blocks.
- **Tool invocations**: The same `toolCallId` appears first without results (incomplete), then again with `pastTenseMessage`, `resultDetails`, and `toolSpecificData` once complete.

`_dedup_sections()` handles this by keeping only the final/most-complete version of each.

### Response Item Kinds

| Kind | Description |
|------|-------------|
| `thinking` | LLM reasoning block, has `id` and `value` (string or list) |
| `toolInvocationSerialized` | Tool call with `toolId`, `toolCallId`, `invocationMessage`, `pastTenseMessage`, `resultDetails`, `toolSpecificData`, `isComplete` |
| `textEditGroup` | Code edit with `uri.fsPath` and edits array |
| `progressTaskSerialized` | Progress indicator |
| `inlineReference` | File/symbol reference |
| `codeblockUri` | URI for a code block |
| (no kind, has `value`) | Plain text/markdown content |

### Key API Functions

All imported from `gh_copilot_transcripts`:

| Function | Purpose |
|----------|---------|
| `find_all_sessions()` | Discover all sessions across VS Code workspaces |
| `reconstruct_session(path)` | Parse JSONL and apply patches to reconstruct final session state |
| `parse_response_stream(items)` | Convert raw response items into typed sections (applies dedup) |
| `_dedup_sections(sections)` | Remove empty thinking blocks, keep longest per id, keep most complete tool per callId |
| `generate_html(session_path, output_dir)` | Full E2E: parse session and write paginated HTML |

## Investigation Procedures

### 1. Discover and Select a Session

```python
from gh_copilot_transcripts import find_all_sessions, reconstruct_session

projects = find_all_sessions()
for p in projects:
    print(f"{p['name']}: {len(p['sessions'])} sessions")
    for s in p["sessions"][:3]:
        print(f"  {s['title']} — {s['path']}")
```

### 2. Inspect Raw Response Items

Reconstruct the session and iterate over the raw response items before parsing.
This reveals the incremental snapshot structure.

```python
session = reconstruct_session(session_path)
requests = session.get("requests", [])

for ri, req in enumerate(requests):
    if not isinstance(req, dict):
        continue
    response = req.get("response", [])
    msg = req.get("message", {})
    msg_text = msg.get("text", "")[:60] if isinstance(msg, dict) else ""
    print(f"\n=== Request {ri}: '{msg_text}' ({len(response)} items) ===")

    for i, item in enumerate(response):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")

        if kind == "thinking":
            val = item.get("value", "")
            val_type = type(val).__name__
            preview = repr(str(val)[:100])
            print(f"  [{i}] THINKING id={repr(item.get('id'))} type={val_type} len={len(str(val))} val={preview}")

        elif kind == "toolInvocationSerialized":
            tool_id = item.get("toolId", "")
            call_id = item.get("toolCallId", "")
            is_complete = item.get("isComplete")
            past = item.get("pastTenseMessage", {})
            if isinstance(past, dict):
                past = past.get("value", "")
            inv = item.get("invocationMessage", {})
            if isinstance(inv, dict):
                inv = inv.get("value", "")
            has_rd = item.get("resultDetails") is not None
            has_tsd = item.get("toolSpecificData") is not None
            print(f"  [{i}] TOOL {tool_id} callId={call_id[:20] if call_id else 'none'}")
            print(f"       complete={is_complete} past={repr(str(past)[:60])} hasRD={has_rd} hasTSD={has_tsd}")

        elif kind is None and "value" in item:
            val = item.get("value", "")
            print(f"  [{i}] TEXT: {repr(str(val)[:80])}")

        else:
            print(f"  [{i}] {kind or 'UNKNOWN'} keys={list(item.keys())}")
```

### 3. Detect Duplicates

Check for duplicate thinking block IDs and tool call IDs in the raw response.

```python
from collections import Counter

for ri, req in enumerate(requests):
    if not isinstance(req, dict):
        continue
    response = req.get("response", [])

    thinking_ids = []
    tool_call_ids = []
    for item in response:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "thinking":
            thinking_ids.append(item.get("id", ""))
        elif kind == "toolInvocationSerialized":
            tool_call_ids.append(item.get("toolCallId", ""))

    thinking_dupes = {k: v for k, v in Counter(thinking_ids).items() if v > 1}
    tool_dupes = {k: v for k, v in Counter(tool_call_ids).items() if v > 1}

    if thinking_dupes or tool_dupes:
        print(f"Request {ri}: thinking dupes={thinking_dupes}, tool dupes={tool_dupes}")
```

### 4. Verify Dedup + HTML Output (E2E)

Run the full pipeline and check the results.

```python
import tempfile
from pathlib import Path
from gh_copilot_transcripts import parse_response_stream, generate_html

# Check parsed sections after dedup
for ri, req in enumerate(requests):
    if not isinstance(req, dict):
        continue
    sections = parse_response_stream(req.get("response", []))
    thinking = [s for s in sections if s["type"] == "thinking"]
    tools = [s for s in sections if s["type"] == "tool"]
    print(f"Request {ri}: {len(sections)} sections, {len(thinking)} thinking, {len(tools)} tools")
    for s in thinking:
        text = s.get("text", "")
        print(f"  THINKING id={s.get('id')} len={len(str(text))} empty={not str(text).strip()}")

# Generate HTML and check for empty blocks
with tempfile.TemporaryDirectory() as tmpdir:
    result = generate_html(session_path, tmpdir)
    print(f"Pages: {result['total_pages']}, Requests: {result['total_requests']}")

    for page_file in sorted(Path(tmpdir).glob("page-*.html")):
        content = page_file.read_text()
        empty = content.count('<div class="truncatable-content"></div>')
        total = content.count('class="thinking"')
        print(f"  {page_file.name}: {total} thinking blocks, {empty} empty")
```

### 5. Inspect a Specific Thinking Block Value

When a thinking block renders incorrectly, check its raw value type and content.

```python
for item in response:
    if not isinstance(item, dict) or item.get("kind") != "thinking":
        continue
    val = item.get("value", "")
    vid = item.get("id", "")
    if isinstance(val, list):
        print(f"WARNING: thinking id={vid} has list value (len={len(val)})")
        for chunk in val[:3]:
            print(f"  chunk type={type(chunk).__name__}: {repr(str(chunk)[:100])}")
    elif isinstance(val, str):
        if not val.strip():
            print(f"EMPTY: thinking id={repr(vid)} value is whitespace-only")
        else:
            print(f"OK: thinking id={vid} len={len(val)}")
    else:
        print(f"UNEXPECTED: thinking id={vid} value type={type(val).__name__}")
```

## Common Issues and Fixes

| Symptom | Likely Cause | Where to Look |
|---------|-------------|---------------|
| Empty thinking blocks in HTML | Dedup not filtering empty-text blocks | `_dedup_sections()` — check empty text filter |
| Duplicate tool results | Same `toolCallId` not being deduplicated | `_dedup_sections()` — check tool scoring logic |
| `AttributeError: 'list' has no 'strip'` | Thinking `value` is a list, not string | `render_markdown_text()` — needs type coercion |
| Missing tool results | Tool only appears in incomplete state | Check `isComplete` and `pastTenseMessage` fields |
| Garbled text in thinking | List of incremental chunks joined wrong | `render_markdown_text()` list handling |

## Scripts

Generalized CLI scripts bundled in [./scripts/](./scripts/). All accept `--path`, `--search`, and (where applicable) `--requests` flags.

| Script | Purpose |
|--------|---------|
| [inspect_session.py](./scripts/inspect_session.py) | Inspect raw response items (thinking, tools, text, edits) |
| [detect_dupes.py](./scripts/detect_dupes.py) | Detect duplicate thinking IDs and tool callIds |
| [verify_e2e.py](./scripts/verify_e2e.py) | Parse, dedup, generate HTML, and check for empty blocks |

```bash
# Auto-discover first available session
uv run python .github/skills/session-diagnostics/scripts/inspect_session.py

# Inspect a specific session file
uv run python .github/skills/session-diagnostics/scripts/detect_dupes.py --path /path/to/session.jsonl

# Search by project name
uv run python .github/skills/session-diagnostics/scripts/verify_e2e.py --search graphchat
```
