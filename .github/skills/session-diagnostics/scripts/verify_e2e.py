"""E2E verification: parse session, check dedup results, and validate generated HTML.

Usage:
    python verify_e2e.py                          # auto-discover, pick first session
    python verify_e2e.py --path /path/to.jsonl    # verify a specific file
    python verify_e2e.py --search graphchat       # find by project name substring
"""

import argparse
import sys
import tempfile
from pathlib import Path

from gh_copilot_transcripts import (
    find_all_sessions,
    generate_html,
    parse_response_stream,
    reconstruct_session,
)


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


def main():
    parser = argparse.ArgumentParser(
        description="E2E verify session parsing and HTML generation"
    )
    parser.add_argument("--path", help="Direct path to a .jsonl session file")
    parser.add_argument("--search", help="Substring to match against project names")
    args = parser.parse_args()

    session_path = resolve_session_path(args)
    print(f"Verifying: {session_path}")
    session = reconstruct_session(session_path)
    requests = session.get("requests", [])

    print(f"Title: {session.get('customTitle')}")
    print(f"Total requests: {len(requests)}")

    print("\n=== Dedup Results ===")
    issues = []
    for ri, req in enumerate(requests):
        if not isinstance(req, dict):
            continue
        response = req.get("response", [])
        sections = parse_response_stream(response)
        thinking = [s for s in sections if s["type"] == "thinking"]
        tools = [s for s in sections if s["type"] == "tool"]
        text = [s for s in sections if s["type"] == "text"]
        print(
            f"Request {ri}: {len(sections)} sections ({len(thinking)} thinking, {len(tools)} tools, {len(text)} text)"
        )

        for s in thinking:
            t = s.get("text", "")
            tlen = len(str(t))
            empty = not str(t).strip()
            if empty:
                issues.append(f"Request {ri}: empty thinking block id={s.get('id')}")
            print(f"  THINKING id={s.get('id')} len={tlen} empty={empty}")

        for s in tools:
            print(f"  TOOL {s['tool_id']} past={s['past_tense_message'][:60]}")

    print("\n=== HTML Generation ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        result = generate_html(session_path, tmpdir)
        print(
            f"Pages: {result['total_pages']}, Requests: {result['total_requests']}, Tools: {result['total_tool_calls']}"
        )

        for page_file in sorted(Path(tmpdir).glob("page-*.html")):
            content = page_file.read_text()
            empty_thinking = content.count('<div class="truncatable-content"></div>')
            total_thinking = content.count('class="thinking"')
            if empty_thinking > 0:
                issues.append(
                    f"{page_file.name}: {empty_thinking} empty thinking blocks in HTML"
                )
            print(
                f"  {page_file.name}: {total_thinking} thinking blocks, {empty_thinking} empty"
            )

    if issues:
        print(f"\n=== ISSUES FOUND ({len(issues)}) ===")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
