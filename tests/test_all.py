"""Tests for CLI commands and batch conversion."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from gh_copilot_transcripts import (
    cli,
    generate_batch_html,
    inject_gist_preview_js,
    create_gist,
)


def _make_session_jsonl(title="Test Session", user_text="Hello"):
    """Build minimal JSONL content for a Copilot session."""
    lines = [
        {
            "kind": 0,
            "v": {
                "version": 3,
                "sessionId": "test-001",
                "customTitle": title,
                "creationDate": 1700000000000,
                "requests": [],
            },
        },
        {
            "kind": 1,
            "k": ["requests", "0"],
            "v": {
                "requestId": "req-1",
                "message": {
                    "text": user_text,
                    "parts": [{"text": user_text, "kind": "text"}],
                },
                "response": [],
                "timestamp": 1700000010000,
                "modelId": "copilot/claude-sonnet-4.5",
                "agent": {"id": "github.copilot.editsAgent", "name": "agent"},
                "isCanceled": False,
                "contentReferences": [],
                "codeCitations": [],
                "followups": [],
                "result": {"timings": {"firstProgress": 500, "totalElapsed": 5000}},
            },
        },
        {
            "kind": 1,
            "k": ["requests", "0", "response", "0"],
            "v": {"value": "Sure, I can help with that."},
        },
    ]
    return "\n".join(json.dumps(line) for line in lines)


@pytest.fixture
def mock_workspace_dir():
    """Create a mock VS Code workspace storage structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir)

        # Project A: 2 sessions
        ws_a = ws_root / "hash_a"
        ws_a.mkdir()
        (ws_a / "workspace.json").write_text(
            json.dumps({"folder": "file:///Users/dev/project-alpha"})
        )
        chat_a = ws_a / "chatSessions"
        chat_a.mkdir()
        (chat_a / "session1.jsonl").write_text(
            _make_session_jsonl("Fix bug in auth", "Fix the login bug")
        )
        (chat_a / "session2.jsonl").write_text(
            _make_session_jsonl("Add tests", "Add unit tests for auth")
        )

        # Project B: 1 session
        ws_b = ws_root / "hash_b"
        ws_b.mkdir()
        (ws_b / "workspace.json").write_text(
            json.dumps({"folder": "file:///Users/dev/project-beta"})
        )
        chat_b = ws_b / "chatSessions"
        chat_b.mkdir()
        (chat_b / "session3.jsonl").write_text(
            _make_session_jsonl("Refactor utils", "Refactor the utils module")
        )

        yield ws_root


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# --- json command tests ---


class TestJsonCommand:
    """Tests for the json CLI command."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["json", "--help"])
        assert result.exit_code == 0
        assert "JSONL" in result.output or "jsonl" in result.output.lower()

    def test_converts_file(self, output_dir):
        """Test converting a specific JSONL file."""
        jsonl_file = output_dir / "input.jsonl"
        jsonl_file.write_text(_make_session_jsonl("My Session", "Hello world"))

        html_out = output_dir / "html"
        runner = CliRunner()
        result = runner.invoke(cli, ["json", str(jsonl_file), "-o", str(html_out)])

        assert result.exit_code == 0
        assert (html_out / "index.html").exists()
        assert (html_out / "page-001.html").exists()
        assert "1 page" in result.output

    def test_missing_file(self, output_dir):
        """Test error on missing file."""
        runner = CliRunner()
        result = runner.invoke(
            cli, ["json", "/nonexistent/file.jsonl", "-o", str(output_dir)]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "Error" in result.output

    def test_open_flag_triggers_browser(self, output_dir, mock_webbrowser_open):
        """Test --open flag opens browser."""
        jsonl_file = output_dir / "input.jsonl"
        jsonl_file.write_text(_make_session_jsonl())

        html_out = output_dir / "html"
        runner = CliRunner()
        runner.invoke(cli, ["json", str(jsonl_file), "-o", str(html_out), "--open"])

        mock_webbrowser_open.assert_called_once()

    def test_default_output_is_transcripts_subdir(self, mock_webbrowser_open):
        """Test default output goes to ./_transcripts/ when no -o is specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_file = Path(tmpdir) / "input.jsonl"
            jsonl_file.write_text(_make_session_jsonl())

            runner = CliRunner()
            with patch("gh_copilot_transcripts.Path") as mock_path_cls:
                mock_cwd = Path(tmpdir)
                mock_path_cls.cwd.return_value = mock_cwd
                # Let Path(path) still work normally for the argument
                mock_path_cls.side_effect = Path
                mock_path_cls.cwd = lambda: mock_cwd
                # Just invoke directly and check the output mentions _transcripts
                result = runner.invoke(cli, ["json", str(jsonl_file)])

            assert result.exit_code == 0
            assert "_transcripts" in result.output
            mock_webbrowser_open.assert_not_called()


# --- all command tests ---


class TestAllCommand:
    """Tests for the all CLI command."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["all", "--help"])
        assert result.exit_code == 0
        assert "batch" in result.output.lower() or "archive" in result.output.lower()

    def test_dry_run(self, mock_workspace_dir, output_dir):
        """Test dry-run mode lists projects without generating files."""
        from gh_copilot_transcripts import find_all_sessions as _real_find

        runner = CliRunner()
        with patch(
            "gh_copilot_transcripts.find_all_sessions",
            side_effect=lambda wp=None: _real_find(mock_workspace_dir),
        ):
            result = runner.invoke(cli, ["all", "-o", str(output_dir), "--dry-run"])

        assert result.exit_code == 0
        assert "project" in result.output.lower()
        # Dry run should not create files
        assert not (output_dir / "index.html").exists()

    def test_no_sessions_error(self):
        """Test error when no sessions found."""
        runner = CliRunner()
        with patch("gh_copilot_transcripts.find_all_sessions", return_value=[]):
            result = runner.invoke(cli, ["all", "-o", "/tmp/test"])

        assert result.exit_code != 0
        assert "No Copilot" in result.output or "not found" in result.output.lower()

    def test_quiet_flag(self, mock_workspace_dir, output_dir):
        """Test --quiet suppresses progress output."""
        from gh_copilot_transcripts import find_all_sessions as _real_find

        runner = CliRunner()
        with patch(
            "gh_copilot_transcripts.find_all_sessions",
            side_effect=lambda wp=None: _real_find(mock_workspace_dir),
        ):
            result = runner.invoke(cli, ["all", "-o", str(output_dir), "-q"])

        assert result.exit_code == 0
        # Quiet mode should produce minimal output
        assert "Generating" not in result.output


# --- generate_batch_html tests ---


class TestGenerateBatchHtml:
    """Tests for batch HTML generation."""

    def test_creates_master_index(self, mock_workspace_dir, output_dir):
        result = generate_batch_html(output_dir, workspace_path=mock_workspace_dir)

        assert (output_dir / "index.html").exists()
        assert result["total_projects"] >= 1
        assert result["total_sessions"] >= 1

    def test_creates_project_directories(self, mock_workspace_dir, output_dir):
        generate_batch_html(output_dir, workspace_path=mock_workspace_dir)

        # Should have project directories
        project_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
        assert len(project_dirs) >= 1

    def test_creates_project_indexes(self, mock_workspace_dir, output_dir):
        generate_batch_html(output_dir, workspace_path=mock_workspace_dir)

        for proj_dir in output_dir.iterdir():
            if proj_dir.is_dir():
                assert (proj_dir / "index.html").exists()

    def test_creates_session_html(self, mock_workspace_dir, output_dir):
        generate_batch_html(output_dir, workspace_path=mock_workspace_dir)

        # At least one session directory should have page files
        found_page = False
        for proj_dir in output_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            for session_dir in proj_dir.iterdir():
                if session_dir.is_dir() and (session_dir / "index.html").exists():
                    found_page = True
                    break
        assert found_page

    def test_master_index_lists_projects(self, mock_workspace_dir, output_dir):
        generate_batch_html(output_dir, workspace_path=mock_workspace_dir)

        index_html = (output_dir / "index.html").read_text()
        assert "project" in index_html.lower()

    def test_returns_statistics(self, mock_workspace_dir, output_dir):
        result = generate_batch_html(output_dir, workspace_path=mock_workspace_dir)

        assert "total_projects" in result
        assert "total_sessions" in result
        assert "errors" in result
        assert result["errors"] == 0

    def test_progress_callback(self, mock_workspace_dir, output_dir):
        calls = []

        def on_progress(current, total):
            calls.append((current, total))

        generate_batch_html(
            output_dir,
            workspace_path=mock_workspace_dir,
            progress_callback=on_progress,
        )

        assert len(calls) >= 1
        # Last call should have current == total
        assert calls[-1][0] == calls[-1][1]

    def test_no_sessions_returns_zero(self, output_dir):
        with tempfile.TemporaryDirectory() as empty_dir:
            result = generate_batch_html(
                output_dir, workspace_path=Path(empty_dir), quiet=True
            )
        assert result["total_projects"] == 0
        assert result["total_sessions"] == 0

    def test_handles_corrupt_session(self, output_dir):
        """Test that a corrupt session file doesn't crash batch generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_root = Path(tmpdir)

            ws_dir = ws_root / "hash_corrupt"
            ws_dir.mkdir()
            (ws_dir / "workspace.json").write_text(
                json.dumps({"folder": "file:///Users/dev/my-project"})
            )
            chat_dir = ws_dir / "chatSessions"
            chat_dir.mkdir()

            # One good session
            (chat_dir / "good.jsonl").write_text(
                _make_session_jsonl("Good session", "Hello")
            )
            # One corrupt session
            (chat_dir / "bad.jsonl").write_text("not valid json at all\n{broken")

            result = generate_batch_html(output_dir, workspace_path=ws_root, quiet=True)

            # Should process the good one and report errors
            assert result["total_sessions"] >= 1 or result["errors"] >= 1


# --- local command tests (limited since it's interactive) ---


class TestLocalCommand:
    """Tests for the local CLI command."""

    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["local", "--help"])
        assert result.exit_code == 0
        assert "local" in result.output.lower() or "session" in result.output.lower()

    def test_no_sessions_error(self):
        """Test error when no sessions found."""
        runner = CliRunner()
        with patch("gh_copilot_transcripts.find_all_sessions", return_value=[]):
            result = runner.invoke(cli, ["local"])

        assert result.exit_code != 0
        assert "No Copilot" in result.output

    def test_project_filter_no_match(self):
        """Test error when project filter has no matches."""
        runner = CliRunner()
        with patch(
            "gh_copilot_transcripts.find_all_sessions",
            return_value=[
                {
                    "name": "alpha",
                    "path": Path("/fake"),
                    "sessions": [
                        {
                            "path": Path("/fake/s.jsonl"),
                            "title": "T",
                            "mtime": 0,
                            "size": 100,
                        }
                    ],
                }
            ],
        ):
            result = runner.invoke(cli, ["local", "-p", "nonexistent"])

        assert result.exit_code != 0
        assert "No projects found matching" in result.output or "No sessions found" in result.output


# --- version command tests ---


class TestVersionCommand:
    """Test --version flag."""

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert (
            "gh-copilot-transcripts" in result.output
            or "version" in result.output.lower()
        )


# --- gist support tests ---


class TestGistSupport:
    """Tests for gist-related functionality."""

    def test_inject_gist_preview_js(self, output_dir):
        """Test that gist preview JS is injected into HTML files."""
        (output_dir / "index.html").write_text("<html><body><p>Hello</p></body></html>")
        (output_dir / "page-001.html").write_text(
            "<html><body><p>Page 1</p></body></html>"
        )

        inject_gist_preview_js(output_dir)

        index_html = (output_dir / "index.html").read_text()
        assert "gisthost.github.io" in index_html
        assert "<script>" in index_html

        page_html = (output_dir / "page-001.html").read_text()
        assert "gisthost.github.io" in page_html

    def test_inject_gist_preview_js_no_body_tag(self, output_dir):
        """Test that files without </body> are not modified."""
        (output_dir / "fragment.html").write_text("<p>No body tag</p>")

        inject_gist_preview_js(output_dir)

        content = (output_dir / "fragment.html").read_text()
        assert "gisthost" not in content

    def test_create_gist_no_gh_cli(self, output_dir):
        """Test error when gh CLI is not available."""
        (output_dir / "index.html").write_text("<html></html>")

        with patch(
            "gh_copilot_transcripts.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            with pytest.raises(click.ClickException, match="gh CLI not found"):
                create_gist(output_dir)

    def test_create_gist_no_html_files(self, output_dir):
        """Test error when no HTML files exist."""
        with pytest.raises(click.ClickException, match="No HTML files"):
            create_gist(output_dir)

    def test_create_gist_success(self, output_dir):
        """Test successful gist creation."""
        (output_dir / "index.html").write_text("<html></html>")

        mock_result = type(
            "Result",
            (),
            {
                "stdout": "https://gist.github.com/user/abc123\n",
                "stderr": "",
            },
        )()

        with patch(
            "gh_copilot_transcripts.subprocess.run",
            return_value=mock_result,
        ):
            gist_id, gist_url = create_gist(output_dir)

        assert gist_id == "abc123"
        assert "gist.github.com" in gist_url


# --- --json option tests ---


class TestJsonOption:
    """Tests for --json (copy source JSONL) option."""

    def test_json_cmd_with_json_flag(self, output_dir):
        """Test that --json copies JSONL source to output."""
        jsonl_file = output_dir / "my_session.jsonl"
        jsonl_file.write_text(_make_session_jsonl("Session", "Hello"))

        html_out = output_dir / "html"
        runner = CliRunner()
        result = runner.invoke(
            cli, ["json", str(jsonl_file), "-o", str(html_out), "--json"]
        )

        assert result.exit_code == 0
        assert (html_out / "my_session.jsonl").exists()
        assert "Copied source JSONL" in result.output

    def test_json_cmd_without_json_flag(self, output_dir):
        """Test that without --json, source is not copied."""
        jsonl_file = output_dir / "my_session.jsonl"
        jsonl_file.write_text(_make_session_jsonl("Session", "Hello"))

        html_out = output_dir / "html"
        runner = CliRunner()
        result = runner.invoke(cli, ["json", str(jsonl_file), "-o", str(html_out)])

        assert result.exit_code == 0
        assert not (html_out / "my_session.jsonl").exists()


# --- --gist option in json command ---


class TestJsonGistOption:
    """Tests for --gist option in json command."""

    def test_json_cmd_gist_flag_in_help(self):
        """Test that --gist appears in json command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["json", "--help"])
        assert "--gist" in result.output

    def test_local_cmd_gist_flag_in_help(self):
        """Test that --gist appears in local command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["local", "--help"])
        assert "--gist" in result.output

    def test_json_cmd_json_flag_in_help(self):
        """Test that --json appears in json command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["json", "--help"])
        assert "--json" in result.output
