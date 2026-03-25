"""Tests for HTML generation pipeline."""

import json
from pathlib import Path


from gh_copilot_transcripts import (
    CSS,
    JS,
    generate_html,
    render_markdown_text,
    render_section,
    format_tool_stats,
    count_tools_in_response,
    make_msg_id,
)

SAMPLE_SESSION = Path(__file__).parent / "sample_session.jsonl"


class TestRenderMarkdownText:
    """Test markdown to HTML conversion."""

    def test_basic_text(self):
        result = render_markdown_text("Hello world")
        assert "<p>Hello world</p>" in result

    def test_code_block(self):
        result = render_markdown_text("```python\nprint('hi')\n```")
        assert "<code" in result
        assert "print" in result

    def test_inline_code(self):
        result = render_markdown_text("Use `int()` to convert")
        assert "<code>int()</code>" in result


class TestMakeMsgId:
    """Test message ID generation."""

    def test_integer_timestamp(self):
        assert make_msg_id(1700000000000) == "msg-1700000000000"

    def test_string_timestamp(self):
        assert make_msg_id("2025-01-01T00:00:00Z") == "msg-2025-01-01T00:00:00Z"


class TestRenderSection:
    """Test rendering of individual response sections."""

    def test_text_section(self):
        section = {"type": "text", "markdown": "Hello **world**"}
        html = render_section(section)
        assert "Hello" in html
        assert "<strong>world</strong>" in html

    def test_thinking_section(self):
        section = {"type": "thinking", "text": "Let me think...", "id": "t-1"}
        html = render_section(section)
        assert "Thinking" in html
        assert "Let me think" in html

    def test_thinking_section_with_list_value(self):
        """Thinking block where 'value' is a list (seen in real sessions)."""
        section = {
            "type": "thinking",
            "text": ["First thought.", "Second thought."],
            "id": "t-2",
        }
        html = render_section(section)
        assert "Thinking" in html
        assert "First thought" in html
        assert "Second thought" in html

    def test_tool_read_file(self):
        section = {
            "type": "tool",
            "tool_id": "copilot_readFile",
            "invocation_message": "Reading file.py",
            "past_tense_message": "Read file.py, lines 1 to 50",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Read File" in html
        assert "Read file.py" in html

    def test_tool_terminal(self):
        section = {
            "type": "tool",
            "tool_id": "run_in_terminal",
            "invocation_message": "npm test",
            "past_tense_message": "Ran npm test",
            "is_confirmed": {"type": 4},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Terminal" in html
        assert "npm test" in html

    def test_tool_replace_string(self):
        section = {
            "type": "tool",
            "tool_id": "copilot_replaceString",
            "invocation_message": "Replacing in file.py",
            "past_tense_message": "Replaced text in file.py",
            "is_confirmed": {"type": 3},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Replace String" in html

    def test_tool_find_text(self):
        section = {
            "type": "tool",
            "tool_id": "copilot_findTextInFiles",
            "invocation_message": "Searching for 'foo'",
            "past_tense_message": "Searched for 'foo', 5 results",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Find in Files" in html
        assert "5 results" in html

    def test_tool_mcp(self):
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
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Context7" in html

    def test_tool_generic(self):
        section = {
            "type": "tool",
            "tool_id": "copilot_someNewTool",
            "invocation_message": "Doing something",
            "past_tense_message": "Did something",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Did something" in html

    def test_edit_group_section(self):
        section = {
            "type": "edit_group",
            "file_path": "/Users/test/project/main.py",
            "edits": [[{"text": "new code", "range": {"startLineNumber": 1}}]],
            "done": True,
        }
        html = render_section(section)
        assert "main.py" in html
        assert "new code" in html

    def test_elicitation_section(self):
        section = {
            "type": "elicitation",
            "title": "Terminal input",
            "message": "Allow sending input?",
            "state": "accepted",
        }
        html = render_section(section)
        assert "Terminal input" in html
        assert "accepted" in html

    def test_confirmation_section(self):
        section = {
            "type": "confirmation",
            "title": "Continue to iterate?",
            "message": "Copilot has been working...",
            "buttons": [],
            "is_used": True,
        }
        html = render_section(section)
        assert "Continue to iterate?" in html

    def test_tool_terminal_with_command_from_tool_data(self):
        """Terminal tool shows command from toolSpecificData when invocationMessage is empty."""
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
                "commandLine": {"original": "uvx showboat --help"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {
                    "text": "showboat - Create executable demo documents"
                },
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Terminal" in html
        assert "uvx showboat --help" in html
        assert "showboat - Create executable demo documents" in html

    def test_tool_terminal_command_is_truncatable(self):
        """Terminal command block is wrapped in a truncatable container for scrollability."""
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
                "commandLine": {"original": "echo hello"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {"text": "hello"},
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "truncatable" in html
        assert "bash-command" in html

    def test_tool_terminal_output_is_truncatable(self):
        """Terminal output block is wrapped in a truncatable container for scrollability."""
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
                "commandLine": {"original": "cat bigfile.txt"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {"text": "line1\nline2\nline3"},
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        # Output should also have a truncatable wrapper
        assert html.count("truncatable") >= 2  # one for command, one for output

    def test_tool_terminal_shows_exit_code(self):
        """Terminal tool shows non-zero exit code."""
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
                "commandLine": {"original": "false"},
                "terminalCommandState": {"exitCode": 1},
                "terminalCommandOutput": {"text": ""},
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "exit code 1" in html.lower() or "Exit code: 1" in html

    def test_tool_todo_shows_items(self):
        """Todo tool renders the full list of todo items."""
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
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Task List" in html
        assert "Set up project" in html
        assert "Write tests" in html
        assert "Deploy app" in html


class TestHtmlEscaping:
    """Test that angle brackets in content are properly HTML-escaped."""

    def test_terminal_command_escapes_angle_brackets(self):
        """Terminal command containing < and > must be escaped in HTML output."""
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
                "commandLine": {"original": "echo <script>alert(1)</script>"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {"text": ""},
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_terminal_output_escapes_angle_brackets(self):
        """Terminal output containing HTML tags must be escaped."""
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
                "commandLine": {"original": "cat index.html"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {"text": "<div>hello</div>"},
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "&lt;div&gt;" in html
        assert "<div>hello</div>" not in html

    def test_edit_group_escapes_angle_brackets(self):
        """Edit group text with generics like List<String> must be escaped."""
        section = {
            "type": "edit_group",
            "file_path": "/Users/test/project/Main.java",
            "edits": [
                [
                    {
                        "text": "List<String> items = new ArrayList<>();",
                        "range": {"startLineNumber": 1},
                    }
                ]
            ],
            "done": True,
        }
        html = render_section(section)
        assert "&lt;String&gt;" in html
        assert "<String>" not in html

    def test_tool_past_tense_message_escapes_angle_brackets(self):
        """Tool descriptions containing angle brackets must be escaped."""
        section = {
            "type": "tool",
            "tool_id": "copilot_findTextInFiles",
            "invocation_message": "Searching for '<div>'",
            "past_tense_message": "Searched for '<div>', 3 results",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "&lt;div&gt;" in html
        assert "<div>" not in html


class TestFormatToolStats:
    """Test tool stats formatting."""

    def test_empty(self):
        assert format_tool_stats({}) == ""

    def test_single_tool(self):
        result = format_tool_stats({"copilot_readFile": 3})
        assert "readFile" in result
        assert "×3" in result

    def test_multiple_tools(self):
        result = format_tool_stats({"copilot_readFile": 2, "run_in_terminal": 1})
        assert "readFile" in result
        assert "run in terminal" in result


class TestCountToolsInResponse:
    """Test counting tool invocations in a response."""

    def test_counts_tools(self):
        response = [
            {"kind": "toolInvocationSerialized", "toolId": "copilot_readFile"},
            {"value": "some text"},
            {"kind": "toolInvocationSerialized", "toolId": "copilot_readFile"},
            {"kind": "toolInvocationSerialized", "toolId": "run_in_terminal"},
        ]
        counts = count_tools_in_response(response)
        assert counts == {"copilot_readFile": 2, "run_in_terminal": 1}

    def test_empty_response(self):
        assert count_tools_in_response([]) == {}


class TestGenerateHtml:
    """Test end-to-end HTML generation from sample session."""

    def test_generates_files(self, tmp_path):
        result = generate_html(SAMPLE_SESSION, tmp_path)
        assert result["total_requests"] == 2
        assert result["total_pages"] == 1
        assert (tmp_path / "index.html").exists()
        assert (tmp_path / "page-001.html").exists()

    def test_index_contains_title(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        index_html = (tmp_path / "index.html").read_text()
        assert "Test Session: Fixing a Bug" in index_html

    def test_index_contains_prompts(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        index_html = (tmp_path / "index.html").read_text()
        assert "TypeError" in index_html
        assert "run the tests" in index_html

    def test_page_contains_user_messages(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "TypeError" in page_html
        assert "User" in page_html

    def test_page_contains_assistant_response(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "Copilot" in page_html
        assert "I can see the issue" in page_html

    def test_page_contains_tool_invocations(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "Read File" in page_html
        assert "Replace String" in page_html
        assert "Run Tests" in page_html

    def test_page_contains_thinking(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "Thinking" in page_html
        assert "TypeError" in page_html

    def test_page_contains_edit_group(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "main.py" in page_html
        assert "int(input_value)" in page_html

    def test_page_contains_model_badge(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "claude-sonnet-4.5" in page_html

    def test_index_contains_tool_stats(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        index_html = (tmp_path / "index.html").read_text()
        assert "3 tool calls" in index_html

    def test_css_included(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "--user-border" in page_html
        assert "GitHub Copilot" in page_html

    def test_pagination_single_page(self, tmp_path):
        generate_html(SAMPLE_SESSION, tmp_path)
        page_html = (tmp_path / "page-001.html").read_text()
        assert "index.html" in page_html

    def test_empty_requests_filtered(self, tmp_path):
        """Empty/stub requests from JSONL patch artifacts are excluded."""
        lines = [
            {
                "kind": 0,
                "v": {
                    "version": 3,
                    "sessionId": "filter-test",
                    "customTitle": "Filter test",
                    "creationDate": 1700000000000,
                    "requests": [],
                },
            },
            # Real request with message and response
            {
                "kind": 1,
                "k": ["requests", "0"],
                "v": {
                    "requestId": "req-1",
                    "message": {
                        "text": "Hello!",
                        "parts": [{"text": "Hello!", "kind": "text"}],
                    },
                    "response": [{"value": "Hi there!"}],
                    "timestamp": 1700000010000,
                    "modelId": "copilot/claude-sonnet-4.5",
                },
            },
            # Empty stub (no keys at all)
            {"kind": 1, "k": ["requests", "1"], "v": {}},
            # Another empty stub
            {"kind": 1, "k": ["requests", "2"], "v": {}},
            # Orphan response-only (no message)
            {
                "kind": 1,
                "k": ["requests", "3"],
                "v": {"response": [{"value": "orphan"}], "result": {}},
            },
        ]
        session_file = tmp_path / "session.jsonl"
        session_file.write_text("\n".join(json.dumps(line) for line in lines))
        out = tmp_path / "out"
        result = generate_html(session_file, out)
        assert result["total_requests"] == 1
        page_html = (out / "page-001.html").read_text()
        assert "Hello!" in page_html
        assert "orphan" not in page_html


class TestCssAndJs:
    """Test CSS and JS constants for correctness."""

    def test_truncatable_content_is_scrollable(self):
        """Truncatable content must use max-height and overflow-y for scrolling."""
        assert "overflow-y: auto" in CSS
        assert "max-height" in CSS

    def test_no_expand_button_in_css(self):
        """The expand-btn styles should no longer exist."""
        assert "expand-btn" not in CSS

    def test_no_expand_logic_in_js(self):
        """The JS should not contain expand/collapse logic for truncatable blocks."""
        assert "expand-btn" not in JS
        assert "truncatable" not in JS
