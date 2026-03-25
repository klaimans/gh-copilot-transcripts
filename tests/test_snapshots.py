"""Snapshot tests for HTML rendering output.

Uses syrupy SingleFileSnapshotExtension to store each snapshot as a
separate .html file under __snapshots__/test_snapshots/. This makes
diffs easy to review and lets you open snapshots directly in a browser.

Update snapshots with: pytest --snapshot-update
"""

from pathlib import Path

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from gh_copilot_transcripts import (
    generate_html,
    render_section,
)

SAMPLE_SESSION = Path(__file__).parent / "sample_session.jsonl"


class HTMLSnapshotExtension(SingleFileSnapshotExtension):
    _write_mode = WriteMode.TEXT
    file_extension = "html"


@pytest.fixture
def snapshot_html(snapshot):
    return snapshot.use_extension(HTMLSnapshotExtension)


class TestRenderSectionSnapshots:
    """Snapshot the HTML output of render_section for each section type."""

    def test_text_section(self, snapshot_html):
        section = {"type": "text", "markdown": "Hello **world**, here is `code`."}
        assert render_section(section) == snapshot_html

    def test_thinking_section(self, snapshot_html):
        section = {
            "type": "thinking",
            "text": "Let me analyze the error.\n\nThe issue is in `main.py` line 25.",
            "id": "t-1",
        }
        assert render_section(section) == snapshot_html

    def test_tool_read_file(self, snapshot_html):
        section = {
            "type": "tool",
            "tool_id": "copilot_readFile",
            "invocation_message": "Reading src/app.py, lines 1 to 80",
            "past_tense_message": "Read src/app.py, lines 1 to 80",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-read",
        }
        assert render_section(section) == snapshot_html

    def test_tool_terminal_with_output(self, snapshot_html):
        section = {
            "type": "tool",
            "tool_id": "run_in_terminal",
            "invocation_message": "",
            "past_tense_message": "",
            "is_confirmed": {"type": 4},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "terminal",
                "commandLine": {"original": "pytest tests/ -v"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {"text": "===== 12 passed in 1.23s ====="},
            },
            "tool_call_id": "tc-term",
        }
        assert render_section(section) == snapshot_html

    def test_tool_terminal_with_error(self, snapshot_html):
        section = {
            "type": "tool",
            "tool_id": "run_in_terminal",
            "invocation_message": "",
            "past_tense_message": "",
            "is_confirmed": {"type": 4},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "terminal",
                "commandLine": {"original": "npm run build"},
                "terminalCommandState": {"exitCode": 1},
                "terminalCommandOutput": {"text": "Error: Module not found"},
            },
            "tool_call_id": "tc-term-err",
        }
        assert render_section(section) == snapshot_html

    def test_tool_replace_string(self, snapshot_html):
        section = {
            "type": "tool",
            "tool_id": "copilot_replaceString",
            "invocation_message": "Replacing in utils.py",
            "past_tense_message": "Replaced text in utils.py",
            "is_confirmed": {"type": 3},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-replace",
        }
        assert render_section(section) == snapshot_html

    def test_tool_mcp(self, snapshot_html):
        section = {
            "type": "tool",
            "tool_id": "mcp_context7_get-library-docs",
            "invocation_message": "Running Get Library Docs",
            "past_tense_message": "Ran Get Library Docs",
            "is_confirmed": {"type": 3},
            "is_complete": True,
            "source": {"type": "mcp", "serverLabel": "Context7"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-mcp",
        }
        assert render_section(section) == snapshot_html

    def test_edit_group(self, snapshot_html):
        section = {
            "type": "edit_group",
            "file_path": "/Users/test/project/main.py",
            "edits": [
                [
                    {
                        "text": "count = int(input_value)",
                        "range": {"startLineNumber": 25},
                    }
                ]
            ],
            "done": True,
        }
        assert render_section(section) == snapshot_html

    def test_todo_list(self, snapshot_html):
        section = {
            "type": "tool",
            "tool_id": "manage_todo_list",
            "invocation_message": "",
            "past_tense_message": "Created 3 todos",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "todoList",
                "todoList": [
                    {"id": "1", "title": "Set up project", "status": "completed"},
                    {"id": "2", "title": "Write tests", "status": "in-progress"},
                    {"id": "3", "title": "Deploy app", "status": "not-started"},
                ],
            },
            "tool_call_id": "tc-todo",
        }
        assert render_section(section) == snapshot_html


class TestFullPageSnapshots:
    """Snapshot full HTML page output for regression detection."""

    def test_sample_session_page(self, snapshot_html, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert page_html == snapshot_html

    def test_sample_session_index(self, snapshot_html, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        index_html = (tmp_path / "index.html").read_text()
        assert index_html == snapshot_html
