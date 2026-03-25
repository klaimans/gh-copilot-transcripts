"""Tests for JSONL parsing and patch reconstruction."""

import json
import pytest

from gh_copilot_transcripts import (
    reconstruct_session,
    parse_response_stream,
    _clean_terminal_output,
)


class TestReconstructSession:
    """Test JSONL patch reconstruction."""

    def _write_jsonl(self, tmp_path, lines):
        path = tmp_path / "session.jsonl"
        path.write_text("\n".join(json.dumps(line) for line in lines))
        return path

    def test_kind_0_only(self, tmp_path):
        """A single kind:0 line returns the initial state."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {
                        "version": 3,
                        "sessionId": "abc-123",
                        "customTitle": None,
                        "requests": [],
                    },
                }
            ],
        )
        result = reconstruct_session(path)
        assert result["version"] == 3
        assert result["sessionId"] == "abc-123"
        assert result["requests"] == []

    def test_kind_1_set_simple_key(self, tmp_path):
        """kind:1 sets a value at a key path."""
        path = self._write_jsonl(
            tmp_path,
            [
                {"kind": 0, "v": {"version": 3, "customTitle": None, "requests": []}},
                {"kind": 1, "k": ["customTitle"], "v": "My Session Title"},
            ],
        )
        result = reconstruct_session(path)
        assert result["customTitle"] == "My Session Title"

    def test_kind_1_set_nested_key(self, tmp_path):
        """kind:1 navigates nested dicts."""
        path = self._write_jsonl(
            tmp_path,
            [
                {"kind": 0, "v": {"version": 3, "metadata": {}, "requests": []}},
                {"kind": 1, "k": ["metadata", "foo"], "v": "bar"},
            ],
        )
        result = reconstruct_session(path)
        assert result["metadata"]["foo"] == "bar"

    def test_kind_1_set_array_element(self, tmp_path):
        """kind:1 extends a list and sets at an index."""
        path = self._write_jsonl(
            tmp_path,
            [
                {"kind": 0, "v": {"version": 3, "requests": []}},
                {
                    "kind": 1,
                    "k": ["requests", "0"],
                    "v": {"requestId": "req-1", "message": {"text": "hello"}},
                },
            ],
        )
        result = reconstruct_session(path)
        assert len(result["requests"]) == 1
        assert result["requests"][0]["requestId"] == "req-1"

    def test_kind_2_extends_list(self, tmp_path):
        """kind:2 with a list value extends (appends to) an existing list."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {
                        "version": 3,
                        "requests": [{"requestId": "req-1", "response": ["initial"]}],
                    },
                },
                {
                    "kind": 2,
                    "k": ["requests", 0, "response"],
                    "v": ["batch2a", "batch2b"],
                },
            ],
        )
        result = reconstruct_session(path)
        assert result["requests"][0]["response"] == ["initial", "batch2a", "batch2b"]

    def test_kind_2_extends_list_multiple_batches(self, tmp_path):
        """Multiple kind:2 patches on the same list accumulate all items."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {"version": 3, "requests": []},
                },
                {
                    "kind": 2,
                    "k": ["requests"],
                    "v": [
                        {
                            "requestId": "req-1",
                            "message": {"text": "q1"},
                            "response": [],
                        }
                    ],
                },
                {
                    "kind": 2,
                    "k": ["requests", 0, "response"],
                    "v": [{"value": "chunk1"}],
                },
                {
                    "kind": 2,
                    "k": ["requests", 0, "response"],
                    "v": [{"value": "chunk2"}, {"value": "chunk3"}],
                },
                {
                    "kind": 2,
                    "k": ["requests"],
                    "v": [
                        {
                            "requestId": "req-2",
                            "message": {"text": "q2"},
                            "response": [],
                        }
                    ],
                },
                {
                    "kind": 2,
                    "k": ["requests", 1, "response"],
                    "v": [{"value": "answer2"}],
                },
            ],
        )
        result = reconstruct_session(path)
        assert len(result["requests"]) == 2
        assert result["requests"][0]["message"]["text"] == "q1"
        assert result["requests"][0]["response"] == [
            {"value": "chunk1"},
            {"value": "chunk2"},
            {"value": "chunk3"},
        ]
        assert result["requests"][1]["message"]["text"] == "q2"
        assert result["requests"][1]["response"] == [{"value": "answer2"}]

    def test_kind_2_replaces_non_list(self, tmp_path):
        """kind:2 with a non-list value still replaces (only lists extend)."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {"version": 3, "customTitle": "old"},
                },
                {"kind": 2, "k": ["customTitle"], "v": "new title"},
            ],
        )
        result = reconstruct_session(path)
        assert result["customTitle"] == "new title"

    def test_deep_nested_path(self, tmp_path):
        """Patches can navigate deeply nested structures."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {
                        "version": 3,
                        "requests": [
                            {"requestId": "req-1", "response": [], "result": {}}
                        ],
                    },
                },
                {
                    "kind": 1,
                    "k": ["requests", "0", "response", "0"],
                    "v": {"value": "Hello ", "supportThemeIcons": False},
                },
                {
                    "kind": 1,
                    "k": ["requests", "0", "response", "1"],
                    "v": {
                        "kind": "toolInvocationSerialized",
                        "toolId": "copilot_readFile",
                    },
                },
                {
                    "kind": 1,
                    "k": ["requests", "0", "response", "2"],
                    "v": {"value": "world!", "supportThemeIcons": False},
                },
            ],
        )
        result = reconstruct_session(path)
        resp = result["requests"][0]["response"]
        assert len(resp) == 3
        assert resp[0]["value"] == "Hello "
        assert resp[1]["kind"] == "toolInvocationSerialized"
        assert resp[2]["value"] == "world!"

    def test_multiple_requests_built_incrementally(self, tmp_path):
        """Requests can be added one at a time via patches."""
        path = self._write_jsonl(
            tmp_path,
            [
                {"kind": 0, "v": {"version": 3, "requests": []}},
                {
                    "kind": 1,
                    "k": ["requests", "0"],
                    "v": {
                        "requestId": "req-1",
                        "message": {"text": "first question"},
                        "response": [],
                        "timestamp": 1700000000000,
                        "modelId": "copilot/claude-sonnet-4.5",
                    },
                },
                {
                    "kind": 1,
                    "k": ["requests", "0", "response", "0"],
                    "v": {"value": "First answer"},
                },
                {
                    "kind": 1,
                    "k": ["requests", "1"],
                    "v": {
                        "requestId": "req-2",
                        "message": {"text": "second question"},
                        "response": [],
                        "timestamp": 1700000060000,
                        "modelId": "copilot/gpt-5-codex",
                    },
                },
                {
                    "kind": 1,
                    "k": ["requests", "1", "response", "0"],
                    "v": {"value": "Second answer"},
                },
            ],
        )
        result = reconstruct_session(path)
        assert len(result["requests"]) == 2
        assert result["requests"][0]["message"]["text"] == "first question"
        assert result["requests"][0]["response"][0]["value"] == "First answer"
        assert result["requests"][1]["message"]["text"] == "second question"
        assert result["requests"][1]["modelId"] == "copilot/gpt-5-codex"

    def test_creates_intermediate_dicts(self, tmp_path):
        """Missing intermediate dicts are created automatically."""
        path = self._write_jsonl(
            tmp_path,
            [
                {"kind": 0, "v": {"version": 3}},
                {"kind": 1, "k": ["metadata", "timings", "elapsed"], "v": 1234},
            ],
        )
        result = reconstruct_session(path)
        assert result["metadata"]["timings"]["elapsed"] == 1234

    def test_empty_file_raises(self, tmp_path):
        """An empty file should raise an error."""
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        with pytest.raises(ValueError, match="empty"):
            reconstruct_session(path)

    def test_non_kind_0_first_line_raises(self, tmp_path):
        """First line must be kind:0."""
        path = self._write_jsonl(
            tmp_path,
            [{"kind": 1, "k": ["foo"], "v": "bar"}],
        )
        with pytest.raises(ValueError, match="kind.*0"):
            reconstruct_session(path)

    def test_kind_2_delete_list_item(self, tmp_path):
        """kind:2 with 'i' but no 'v' deletes a list item at that index."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {
                        "version": 3,
                        "pendingRequests": ["pending-1", "pending-2"],
                        "requests": [],
                    },
                },
                {"kind": 2, "k": ["pendingRequests"], "i": 0},
            ],
        )
        result = reconstruct_session(path)
        assert result["pendingRequests"] == ["pending-2"]

    def test_kind_2_delete_last_item(self, tmp_path):
        """kind:2 delete on a single-element list empties it."""
        path = self._write_jsonl(
            tmp_path,
            [
                {
                    "kind": 0,
                    "v": {"version": 3, "items": ["only"]},
                },
                {"kind": 2, "k": ["items"], "i": 0},
            ],
        )
        result = reconstruct_session(path)
        assert result["items"] == []

    def test_patch_without_v_on_dict_key_skipped(self, tmp_path):
        """A patch with no 'v' and no 'i' on a dict key is safely skipped."""
        path = self._write_jsonl(
            tmp_path,
            [
                {"kind": 0, "v": {"version": 3, "foo": "bar"}},
                {"kind": 2, "k": ["foo"]},
            ],
        )
        result = reconstruct_session(path)
        # Should not crash; foo unchanged since there's no 'v' or 'i'
        assert result["foo"] == "bar"


class TestParseResponseStream:
    """Test parsing of the heterogeneous response stream."""

    def test_text_chunks_concatenated(self):
        """Consecutive text chunks are merged into one TextSection."""
        stream = [
            {"value": "Hello ", "supportThemeIcons": False},
            {"value": "world!", "supportThemeIcons": False},
        ]
        sections = parse_response_stream(stream)
        text_sections = [s for s in sections if s["type"] == "text"]
        assert len(text_sections) == 1
        assert text_sections[0]["markdown"] == "Hello world!"

    def test_tool_invocation(self):
        """toolInvocationSerialized becomes a ToolSection."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading file.py"},
                "pastTenseMessage": {"value": "Read file.py"},
                "isConfirmed": {"type": 1},
                "isComplete": True,
                "source": {"type": "internal"},
                "toolCallId": "tc-1",
            }
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "tool"
        assert sections[0]["tool_id"] == "copilot_readFile"
        assert sections[0]["past_tense_message"] == "Read file.py"

    def test_thinking_block(self):
        """thinking items become ThinkingSections."""
        stream = [
            {"kind": "thinking", "value": "Let me think about this...", "id": "t-1"}
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "thinking"
        assert sections[0]["text"] == "Let me think about this..."

    def test_text_edit_group(self):
        """textEditGroup becomes an EditGroupSection."""
        stream = [
            {
                "kind": "textEditGroup",
                "uri": {"fsPath": "/path/to/file.py"},
                "edits": [[{"text": "new code", "range": {"startLineNumber": 1}}]],
                "done": True,
            }
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "edit_group"
        assert sections[0]["file_path"] == "/path/to/file.py"

    def test_interleaved_text_and_tools(self):
        """Text broken by tool invocations creates separate text sections."""
        stream = [
            {"value": "Let me check "},
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading file"},
                "pastTenseMessage": {"value": "Read file"},
                "isConfirmed": {"type": 1},
                "isComplete": True,
                "source": {"type": "internal"},
                "toolCallId": "tc-1",
            },
            {"value": "Based on the file, "},
            {"value": "here is the answer."},
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 3
        assert sections[0]["type"] == "text"
        assert sections[0]["markdown"] == "Let me check "
        assert sections[1]["type"] == "tool"
        assert sections[2]["type"] == "text"
        assert sections[2]["markdown"] == "Based on the file, here is the answer."

    def test_inline_references_skipped(self):
        """inlineReference items are filtered out (content is in surrounding text)."""
        stream = [
            {"value": "See "},
            {"kind": "inlineReference", "inlineReference": {"name": "foo.py"}},
            {"value": " for details."},
        ]
        sections = parse_response_stream(stream)
        text_sections = [s for s in sections if s["type"] == "text"]
        assert len(text_sections) == 1
        assert text_sections[0]["markdown"] == "See  for details."

    def test_skipped_kinds(self):
        """Non-content kinds like mcpServersStarting and prepareToolInvocation are skipped."""
        stream = [
            {"kind": "mcpServersStarting", "didStartServerIds": []},
            {"kind": "prepareToolInvocation", "toolName": "copilot_readFile"},
            {"value": "Hello"},
            {"kind": "undoStop", "id": "u-1"},
            {"kind": "codeblockUri", "uri": {}, "isEdit": True},
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "text"
        assert sections[0]["markdown"] == "Hello"

    def test_empty_stream(self):
        """Empty response stream returns empty sections."""
        assert parse_response_stream([]) == []

    def test_elicitation(self):
        """Elicitation items are captured."""
        stream = [
            {
                "kind": "elicitation",
                "title": {"value": "Terminal input"},
                "message": {"value": "Allow sending input?"},
                "state": "accepted",
            }
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "elicitation"

    def test_confirmation(self):
        """Confirmation items are captured."""
        stream = [
            {
                "kind": "confirmation",
                "title": "Continue to iterate?",
                "message": {"value": "Copilot has been working..."},
                "buttons": [],
                "isUsed": True,
                "isLive": False,
            }
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "confirmation"

    def test_progress_task(self):
        """progressTaskSerialized items are skipped (transient UI)."""
        stream = [
            {
                "kind": "progressTaskSerialized",
                "content": {"value": "Optimizing..."},
                "progress": [],
            },
            {"value": "Done."},
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "text"

    def test_empty_thinking_blocks_removed(self):
        """Thinking blocks with empty text are filtered out regardless of id."""
        stream = [
            {"kind": "thinking", "value": "Real thought", "id": "t-1"},
            {"kind": "thinking", "value": "", "id": ""},
            {"kind": "thinking", "value": "", "id": "t-2"},
            {"kind": "thinking", "value": "  \n  ", "id": "t-3"},
            {"value": "Some text"},
        ]
        sections = parse_response_stream(stream)
        thinking = [s for s in sections if s["type"] == "thinking"]
        assert len(thinking) == 1
        assert thinking[0]["text"] == "Real thought"

    def test_duplicate_thinking_keeps_longest(self):
        """When the same thinking id appears multiple times, only the longest is kept."""
        stream = [
            {"kind": "thinking", "value": "short", "id": "t-1"},
            {"value": "interleaved text"},
            {"kind": "thinking", "value": "short but growing longer", "id": "t-1"},
            {
                "kind": "thinking",
                "value": "short but growing longer with even more",
                "id": "t-1",
            },
            {"kind": "thinking", "value": "", "id": ""},
        ]
        sections = parse_response_stream(stream)
        thinking = [s for s in sections if s["type"] == "thinking"]
        assert len(thinking) == 1
        assert thinking[0]["text"] == "short but growing longer with even more"

    def test_different_thinking_ids_kept_separately(self):
        """Thinking blocks with different ids are not deduplicated."""
        stream = [
            {"kind": "thinking", "value": "First thought", "id": "t-1"},
            {"kind": "thinking", "value": "", "id": ""},
            {"value": "text"},
            {"kind": "thinking", "value": "Second thought", "id": "t-2"},
            {"kind": "thinking", "value": "", "id": ""},
        ]
        sections = parse_response_stream(stream)
        thinking = [s for s in sections if s["type"] == "thinking"]
        assert len(thinking) == 2
        assert thinking[0]["text"] == "First thought"
        assert thinking[1]["text"] == "Second thought"

    def test_duplicate_tool_calls_deduplicated(self):
        """Same toolCallId appearing twice keeps only the one with more info."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "runTests",
                "invocationMessage": {"value": "Running tests..."},
                "pastTenseMessage": {"value": ""},
                "isComplete": True,
                "toolCallId": "tc-1",
            },
            {
                "kind": "toolInvocationSerialized",
                "toolId": "runTests",
                "invocationMessage": {"value": "Running tests..."},
                "pastTenseMessage": {"value": "5/5 tests passed"},
                "isComplete": True,
                "toolCallId": "tc-1",
            },
        ]
        sections = parse_response_stream(stream)
        tools = [s for s in sections if s["type"] == "tool"]
        assert len(tools) == 1
        assert tools[0]["past_tense_message"] == "5/5 tests passed"

    def test_duplicate_tools_with_interleaved_content(self):
        """Duplicate tool calls separated by text still get deduplicated."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading file.py"},
                "pastTenseMessage": {"value": "Read file.py"},
                "isComplete": True,
                "toolCallId": "tc-1",
            },
            {"value": "Some text"},
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading file.py"},
                "pastTenseMessage": {"value": "Read file.py"},
                "isComplete": True,
                "toolCallId": "tc-1",
            },
        ]
        sections = parse_response_stream(stream)
        tools = [s for s in sections if s["type"] == "tool"]
        assert len(tools) == 1

    def test_different_tool_call_ids_not_deduplicated(self):
        """Tools with different callIds remain separate."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading a.py"},
                "pastTenseMessage": {"value": "Read a.py"},
                "isComplete": True,
                "toolCallId": "tc-1",
            },
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading b.py"},
                "pastTenseMessage": {"value": "Read b.py"},
                "isComplete": True,
                "toolCallId": "tc-2",
            },
        ]
        sections = parse_response_stream(stream)
        tools = [s for s in sections if s["type"] == "tool"]
        assert len(tools) == 2

    def test_tool_with_result_details(self):
        """Tool invocations with resultDetails preserve them."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_findTextInFiles",
                "invocationMessage": {"value": "Searching for 'foo'"},
                "pastTenseMessage": {"value": "Searched for 'foo', 5 results"},
                "isConfirmed": {"type": 1},
                "isComplete": True,
                "source": {"type": "internal"},
                "resultDetails": [{"uri": {"fsPath": "/a.py"}, "lineNumber": 10}],
                "toolCallId": "tc-1",
            }
        ]
        sections = parse_response_stream(stream)
        assert sections[0]["result_details"] == [
            {"uri": {"fsPath": "/a.py"}, "lineNumber": 10}
        ]

    def test_terminal_tool_specific_data(self):
        """Terminal toolSpecificData extracts command and output."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "run_in_terminal",
                "invocationMessage": {"value": "Running command"},
                "pastTenseMessage": {"value": "Ran command"},
                "isConfirmed": {"type": 1},
                "isComplete": True,
                "source": {"type": "internal"},
                "toolCallId": "tc-1",
                "toolSpecificData": {
                    "kind": "terminal",
                    "commandLine": {"original": "ls -la"},
                    "cwd": {
                        "fsPath": "/home/user/project",
                        "path": "/home/user/project",
                    },
                    "terminalCommandState": {"exitCode": 0},
                    "terminalCommandOutput": {
                        "text": "total 42\ndrwxr-xr-x 5 user user 160 Jan 1 file.py",
                        "lineCount": 2,
                    },
                },
            }
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "tool"
        assert sections[0]["tool_id"] == "run_in_terminal"
        tsd = sections[0]["tool_specific_data"]
        assert tsd["kind"] == "terminal"
        assert tsd["commandLine"]["original"] == "ls -la"
        assert "total 42" in tsd["terminalCommandOutput"]["text"]
        assert tsd["terminalCommandState"]["exitCode"] == 0

    def test_todo_tool_specific_data(self):
        """Todo toolSpecificData extracts the todo list items."""
        stream = [
            {
                "kind": "toolInvocationSerialized",
                "toolId": "manage_todo_list",
                "invocationMessage": {"value": "Updating todo list"},
                "pastTenseMessage": {"value": "Updated todo list"},
                "isConfirmed": {"type": 1},
                "isComplete": True,
                "source": {"type": "internal"},
                "toolCallId": "tc-1",
                "toolSpecificData": {
                    "kind": "todoList",
                    "todoList": [
                        {"id": "1", "title": "First task", "status": "completed"},
                        {"id": "2", "title": "Second task", "status": "in-progress"},
                        {"id": "3", "title": "Third task", "status": "not-started"},
                    ],
                },
            }
        ]
        sections = parse_response_stream(stream)
        assert len(sections) == 1
        assert sections[0]["type"] == "tool"
        tsd = sections[0]["tool_specific_data"]
        assert tsd["kind"] == "todoList"
        assert len(tsd["todoList"]) == 3
        assert tsd["todoList"][0]["status"] == "completed"
        assert tsd["todoList"][1]["status"] == "in-progress"

    def test_standalone_triple_backticks_stripped(self):
        """Text chunks that are only triple backticks are filtered out."""
        stream = [
            {"value": "Here is some text."},
            {"value": "```"},
            {
                "kind": "toolInvocationSerialized",
                "toolId": "copilot_readFile",
                "invocationMessage": {"value": "Reading file"},
                "pastTenseMessage": {"value": "Read file"},
                "isComplete": True,
                "toolCallId": "tc-1",
            },
            {"value": "```"},
            {"value": "More text after tool."},
        ]
        sections = parse_response_stream(stream)
        text_sections = [s for s in sections if s["type"] == "text"]
        for ts in text_sections:
            assert "```" not in ts["markdown"]
        assert text_sections[0]["markdown"] == "Here is some text."
        assert text_sections[1]["markdown"] == "More text after tool."

    def test_backticks_inside_real_text_preserved(self):
        """Triple backticks that are part of larger text are preserved."""
        stream = [
            {"value": "Use ```python\\nprint('hi')\\n``` for code blocks."},
        ]
        sections = parse_response_stream(stream)
        assert "```" in sections[0]["markdown"]


class TestRenderSection:
    """Test HTML rendering of parsed sections."""

    def test_terminal_renders_command_and_output(self):
        """Terminal tool renders command line, output, and exit code."""
        from gh_copilot_transcripts import render_section

        section = {
            "type": "tool",
            "tool_id": "run_in_terminal",
            "invocation_message": "Running ls -la",
            "past_tense_message": "Ran ls -la",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "terminal",
                "commandLine": {"original": "ls -la"},
                "cwd": {"fsPath": "/home/user/project"},
                "terminalCommandState": {"exitCode": 0},
                "terminalCommandOutput": {
                    "text": "total 42\nfile.py",
                    "lineCount": 2,
                },
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "ls -la" in html
        assert "total 42" in html
        assert "file.py" in html

    def test_terminal_renders_without_output(self):
        """Terminal tool renders gracefully when there is no output."""
        from gh_copilot_transcripts import render_section

        section = {
            "type": "tool",
            "tool_id": "run_in_terminal",
            "invocation_message": "Running mkdir foo",
            "past_tense_message": "Ran mkdir foo",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "terminal",
                "commandLine": {"original": "mkdir foo"},
                "terminalCommandState": {},
                "terminalCommandOutput": {"text": "", "lineCount": 0},
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "mkdir foo" in html

    def test_terminal_renders_nonzero_exit_code(self):
        """Terminal tool shows exit code when non-zero."""
        from gh_copilot_transcripts import render_section

        section = {
            "type": "tool",
            "tool_id": "run_in_terminal",
            "invocation_message": "Running bad-cmd",
            "past_tense_message": "Ran bad-cmd",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "terminal",
                "commandLine": {"original": "bad-cmd"},
                "terminalCommandState": {"exitCode": 1},
                "terminalCommandOutput": {
                    "text": "command not found",
                    "lineCount": 1,
                },
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "bad-cmd" in html
        assert "exit" in html.lower() or "1" in html

    def test_todo_renders_items(self):
        """Todo tool renders the list of todo items with statuses."""
        from gh_copilot_transcripts import render_section

        section = {
            "type": "tool",
            "tool_id": "manage_todo_list",
            "invocation_message": "Updating todo list",
            "past_tense_message": "Updated todo list",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": {
                "kind": "todoList",
                "todoList": [
                    {"id": "1", "title": "First task", "status": "completed"},
                    {"id": "2", "title": "Second task", "status": "in-progress"},
                    {"id": "3", "title": "Third task", "status": "not-started"},
                ],
            },
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "First task" in html
        assert "Second task" in html
        assert "Third task" in html

    def test_todo_renders_without_data(self):
        """Todo tool renders gracefully when toolSpecificData is None."""
        from gh_copilot_transcripts import render_section

        section = {
            "type": "tool",
            "tool_id": "manage_todo_list",
            "invocation_message": "Updating todo list",
            "past_tense_message": "Updated todo list",
            "is_confirmed": {"type": 1},
            "is_complete": True,
            "source": {"type": "internal"},
            "result_details": None,
            "tool_specific_data": None,
            "tool_call_id": "tc-1",
        }
        html = render_section(section)
        assert "Task List" in html


class TestCleanTerminalOutput:
    """Test ANSI escape code stripping from terminal output."""

    def test_strips_ansi_color_codes(self):
        assert _clean_terminal_output("\x1b[32mgreen\x1b[0m") == "green"

    def test_strips_cursor_codes(self):
        assert _clean_terminal_output("\x1b[?1h\x1b[?66h") == ""

    def test_strips_carriage_returns(self):
        assert _clean_terminal_output("line1\r\nline2") == "line1\nline2"

    def test_preserves_normal_text(self):
        assert _clean_terminal_output("hello world") == "hello world"

    def test_strips_whitespace_only_output(self):
        text = "   \x1b[?1h\x1b[?66h   "
        assert _clean_terminal_output(text) == ""

    def test_real_terminal_output(self):
        """Realistic terminal output with mixed ANSI and content."""
        text = "total 42\r\n\x1b[34mdir1\x1b[0m\r\nfile.py\r\n"
        result = _clean_terminal_output(text)
        assert "total 42" in result
        assert "dir1" in result
        assert "file.py" in result
        assert "\x1b" not in result
        assert "\r" not in result
