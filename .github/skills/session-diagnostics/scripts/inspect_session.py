"""Inspect raw response items in a Copilot JSONL session.

Usage:
    python inspect_session.py                      # auto-discover, pick first session
    python inspect_session.py --path /path/to.jsonl # inspect a specific file
    python inspect_session.py --search graphchat    # find session by project name substring
    python inspect_session.py --requests 0,1,2      # limit to specific request indices
"""

import argparse
import json
import sys

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


def print_item(i, item):
    if not isinstance(item, dict):
        return
    kind = item.get("kind")

    if kind == "thinking":
        val = item.get("value", "")
        val_type = type(val).__name__
        if isinstance(val, str):
            preview = repr(val[:120])
        elif isinstance(val, list):
            preview = f"list len={len(val)}: {json.dumps(val[:3], default=str)[:120]}"
        else:
            preview = f"<{val_type}>"
        print(
            f"  [{i}] THINKING id={repr(item.get('id'))} type={val_type} val={preview}"
        )

    elif kind == "toolInvocationSerialized":
        tool_id = item.get("toolId", "unknown")
        past = item.get("pastTenseMessage", {})
        if isinstance(past, dict):
            past = past.get("value", "")
        inv = item.get("invocationMessage", {})
        if isinstance(inv, dict):
            inv = inv.get("value", "")
        print(f"  [{i}] TOOL {tool_id}")
        print(f"       inv: {str(inv)[:100]}")
        print(f"       past: {str(past)[:100]}")

        rd = item.get("resultDetails")
        if rd is not None:
            if isinstance(rd, dict):
                print(f"       resultDetails keys={list(rd.keys())}")
                for k, v in rd.items():
                    if isinstance(v, str):
                        print(f"         {k}: str len={len(v)} [{repr(v[:80])}]")
                    elif isinstance(v, dict):
                        print(f"         {k}: dict keys={list(v.keys())}")
                    elif isinstance(v, list):
                        print(f"         {k}: list len={len(v)}")
                        if v:
                            print(f"           [0]: {repr(v[0])[:100]}")
                    else:
                        print(f"         {k}: {type(v).__name__} = {repr(v)}")
            elif isinstance(rd, list):
                print(f"       resultDetails: list len={len(rd)}")
                if rd:
                    print(f"         [0]: {repr(rd[0])[:100]}")
            else:
                print(f"       resultDetails: {type(rd).__name__} = {repr(rd)[:100]}")

        tsd = item.get("toolSpecificData")
        if tsd is not None:
            if isinstance(tsd, dict):
                print(
                    f"       toolSpecificData kind={tsd.get('kind')} keys={list(tsd.keys())}"
                )
            else:
                print(f"       toolSpecificData: {type(tsd).__name__}")

    elif kind is None and "value" in item:
        val = item.get("value", "")
        if isinstance(val, str):
            print(f"  [{i}] TEXT: {repr(val[:80])}")
        else:
            print(f"  [{i}] TEXT type={type(val).__name__}")

    elif kind == "textEditGroup":
        print(f"  [{i}] {kind} file={item.get('uri', {}).get('fsPath', '')[-40:]}")

    elif kind in (
        "mcpServersStarting",
        "prepareToolInvocation",
        "undoStop",
        "codeblockUri",
        "progressTaskSerialized",
        "inlineReference",
        "notebookEditGroup",
    ):
        print(f"  [{i}] {kind}")

    else:
        print(f"  [{i}] OTHER kind={kind} keys={list(item.keys())}")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect raw Copilot session response items"
    )
    parser.add_argument("--path", help="Direct path to a .jsonl session file")
    parser.add_argument("--search", help="Substring to match against project names")
    parser.add_argument(
        "--requests", help="Comma-separated request indices to inspect (default: all)"
    )
    args = parser.parse_args()

    session_path = resolve_session_path(args)
    print(f"Inspecting: {session_path}")
    session = reconstruct_session(session_path)
    requests = session.get("requests", [])

    print(f"Title: {session.get('customTitle')}")
    print(f"Total requests: {len(requests)}")

    if args.requests:
        indices = [int(x.strip()) for x in args.requests.split(",")]
    else:
        indices = range(len(requests))

    for ri in indices:
        if ri >= len(requests):
            continue
        req = requests[ri]
        if not isinstance(req, dict):
            continue
        response = req.get("response", [])
        msg = req.get("message", {})
        msg_text = msg.get("text", "")[:60] if isinstance(msg, dict) else ""
        print(f"\n=== Request {ri}: '{msg_text}' ({len(response)} items) ===")

        for i, item in enumerate(response):
            print_item(i, item)


if __name__ == "__main__":
    main()
