"""Detect duplicate thinking blocks and tool invocations in a Copilot session.

Usage:
    python detect_dupes.py                          # auto-discover, pick first session
    python detect_dupes.py --path /path/to.jsonl    # check a specific file
    python detect_dupes.py --search graphchat       # find session by project name substring
    python detect_dupes.py --requests 0,1           # limit to specific request indices
"""

import argparse
import sys
from collections import Counter

from gh_copilot_transcripts import find_all_sessions, reconstruct_session


def resolve_session_path(args):
    if args.path:
        return args.path

    projects = find_all_sessions()
    if args.search:
        for p in projects:
            if args.search.lower() in p["name"].lower() and p["sessions"]:
                return p["sessions"][0]["path"]
        print(f"No project matching '{args.search}' with sessions found")
        sys.exit(1)

    for p in projects:
        if p["sessions"]:
            return p["sessions"][0]["path"]

    print("No sessions found")
    sys.exit(1)


def inspect_dupes(response, request_index):
    thinking_items = []
    tool_items = []

    for i, item in enumerate(response):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")

        if kind == "thinking":
            val = item.get("value", "")
            vid = item.get("id", "")
            if isinstance(val, str):
                vlen = len(val)
            elif isinstance(val, list):
                vlen = sum(len(str(x)) for x in val)
            else:
                vlen = -1
            thinking_items.append((i, vid, vlen, repr(str(val)[:60])))

        elif kind == "toolInvocationSerialized":
            tool_id = item.get("toolId", "")
            call_id = item.get("toolCallId", "")
            is_complete = item.get("isComplete")
            past = item.get("pastTenseMessage", {})
            if isinstance(past, dict):
                past = past.get("value", "")
            has_rd = item.get("resultDetails") is not None
            has_tsd = item.get("toolSpecificData") is not None
            tool_items.append(
                (i, tool_id, call_id, is_complete, str(past)[:60], has_rd, has_tsd)
            )

    thinking_ids = [t[1] for t in thinking_items]
    tool_call_ids = [t[2] for t in tool_items]
    thinking_dupes = {k: v for k, v in Counter(thinking_ids).items() if v > 1}
    tool_dupes = {k: v for k, v in Counter(tool_call_ids).items() if v > 1}

    has_issues = thinking_dupes or tool_dupes or any(t[2] == 0 for t in thinking_items)

    print(f"\n=== Request {request_index} ({len(response)} raw items) ===")

    if thinking_items:
        print(
            f"  THINKING: {len(thinking_items)} blocks, {len(thinking_dupes)} duplicate IDs"
        )
        for idx, vid, vlen, preview in thinking_items:
            marker = " **DUPE**" if thinking_ids.count(vid) > 1 else ""
            empty = " **EMPTY**" if vlen == 0 else ""
            print(f"    [{idx}] id={repr(vid)} len={vlen}{empty}{marker} {preview}")

    if tool_items:
        print(
            f"  TOOLS: {len(tool_items)} invocations, {len(tool_dupes)} duplicate callIds"
        )
        for idx, tool_id, call_id, complete, past, has_rd, has_tsd in tool_items:
            cid_short = call_id[:20] if call_id else "none"
            marker = " **DUPE**" if tool_call_ids.count(call_id) > 1 else ""
            print(
                f"    [{idx}] {tool_id} callId={cid_short} complete={complete} hasRD={has_rd} hasTSD={has_tsd}{marker}"
            )

    if not has_issues:
        print("  No duplicates found")

    return has_issues


def main():
    parser = argparse.ArgumentParser(
        description="Detect duplicate thinking/tool items in a session"
    )
    parser.add_argument("--path", help="Direct path to a .jsonl session file")
    parser.add_argument("--search", help="Substring to match against project names")
    parser.add_argument(
        "--requests", help="Comma-separated request indices to check (default: all)"
    )
    args = parser.parse_args()

    session_path = resolve_session_path(args)
    print(f"Checking: {session_path}")
    session = reconstruct_session(session_path)
    requests = session.get("requests", [])

    print(f"Title: {session.get('customTitle')}")
    print(f"Total requests: {len(requests)}")

    if args.requests:
        indices = [int(x.strip()) for x in args.requests.split(",")]
    else:
        indices = range(len(requests))

    issues_found = False
    for ri in indices:
        if ri >= len(requests):
            continue
        req = requests[ri]
        if not isinstance(req, dict):
            continue
        response = req.get("response", [])
        if inspect_dupes(response, ri):
            issues_found = True

    if not issues_found:
        print("\nNo duplicates found in any request.")


if __name__ == "__main__":
    main()
