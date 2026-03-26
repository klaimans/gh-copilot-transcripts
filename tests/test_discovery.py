"""Tests for session discovery and workspace mapping."""

import json
import os
import sys

from gh_copilot_transcripts import (
    get_workspace_storage_path,
    get_project_for_workspace,
    find_all_sessions,
    get_session_info,
)


class TestGetWorkspaceStoragePath:
    """Test cross-platform workspace storage path detection."""

    def test_macos_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("HOME", "/Users/testuser")
        path = get_workspace_storage_path()
        assert str(path) == (
            "/Users/testuser/Library/Application Support" "/Code/User/workspaceStorage"
        )

    def test_linux_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("HOME", "/home/testuser")
        path = get_workspace_storage_path()
        assert str(path) == "/home/testuser/.config/Code/User/workspaceStorage"

    def test_windows_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\testuser\\AppData\\Roaming")
        path = get_workspace_storage_path()
        # On macOS/Linux Path uses /, on Windows it uses \
        # Check logical components rather than exact string
        assert path.parts[-3:] == ("Code", "User", "workspaceStorage")
        assert "AppData" in str(path)


class TestGetProjectForWorkspace:
    """Test reading workspace.json to determine project path."""

    def test_file_uri(self, tmp_path):
        ws_dir = tmp_path / "abc123"
        ws_dir.mkdir()
        (ws_dir / "workspace.json").write_text(
            json.dumps({"folder": "file:///Users/foo/projects/my-project"})
        )
        result = get_project_for_workspace(ws_dir)
        assert result == "/Users/foo/projects/my-project"

    def test_windows_file_uri(self, tmp_path):
        ws_dir = tmp_path / "abc123"
        ws_dir.mkdir()
        (ws_dir / "workspace.json").write_text(
            json.dumps({"folder": "file:///C%3A/Users/foo/projects/my-project"})
        )
        result = get_project_for_workspace(ws_dir)
        assert result == "C:/Users/foo/projects/my-project"

    def test_remote_uri(self, tmp_path):
        ws_dir = tmp_path / "abc123"
        ws_dir.mkdir()
        (ws_dir / "workspace.json").write_text(
            json.dumps(
                {"folder": "vscode-remote://ssh-remote%2B10.0.0.1/opt/dev/project"}
            )
        )
        result = get_project_for_workspace(ws_dir)
        assert result == "vscode-remote://ssh-remote%2B10.0.0.1/opt/dev/project"

    def test_missing_workspace_json(self, tmp_path):
        ws_dir = tmp_path / "abc123"
        ws_dir.mkdir()
        result = get_project_for_workspace(ws_dir)
        assert result is None

    def test_workspace_key_fallback(self, tmp_path):
        """Falls back to 'workspace' key if 'folder' is absent."""
        ws_dir = tmp_path / "abc123"
        ws_dir.mkdir()
        (ws_dir / "workspace.json").write_text(
            json.dumps({"workspace": "file:///Users/foo/my-workspace.code-workspace"})
        )
        result = get_project_for_workspace(ws_dir)
        assert result == "/Users/foo/my-workspace.code-workspace"

    def test_display_name_from_path(self, tmp_path):
        """Project display name is the last path component."""
        ws_dir = tmp_path / "abc123"
        ws_dir.mkdir()
        (ws_dir / "workspace.json").write_text(
            json.dumps({"folder": "file:///Users/foo/projects/my-cool-project"})
        )
        result = get_project_for_workspace(ws_dir)
        assert result is not None
        assert result.split("/")[-1] == "my-cool-project"


class TestGetSessionInfo:
    """Test extracting session title and request count from a JSONL file."""

    def _write_session(self, tmp_path, init_v, patches=None):
        path = tmp_path / "session.jsonl"
        lines = [json.dumps({"kind": 0, "v": init_v})]
        for p in patches or []:
            lines.append(json.dumps(p))
        path.write_text("\n".join(lines))
        return path

    def test_title_from_custom_title(self, tmp_path):
        """Extracts customTitle when present."""
        path = self._write_session(
            tmp_path,
            {"customTitle": "My Session", "requests": [{"message": {"text": "hello"}}]},
        )
        title, req_count = get_session_info(path)
        assert title == "My Session"
        assert req_count == 1

    def test_title_from_first_request(self, tmp_path):
        """Falls back to first request message text."""
        path = self._write_session(
            tmp_path,
            {
                "customTitle": None,
                "requests": [{"message": {"text": "How do I sort a list in Python?"}}],
            },
        )
        title, req_count = get_session_info(path)
        assert title == "How do I sort a list in Python?"
        assert req_count == 1

    def test_title_truncated(self, tmp_path):
        """Long titles are truncated."""
        path = self._write_session(
            tmp_path,
            {
                "customTitle": None,
                "requests": [{"message": {"text": "x" * 200}}],
            },
        )
        title, req_count = get_session_info(path)
        assert len(title) <= 103  # 100 + "..."
        assert req_count == 1

    def test_title_from_patch(self, tmp_path):
        """Title set via a patch is picked up."""
        path = self._write_session(
            tmp_path,
            {"customTitle": None, "requests": [{"message": {"text": "foo"}}]},
            patches=[
                {"kind": 1, "k": ["customTitle"], "v": "Patched Title"},
            ],
        )
        title, req_count = get_session_info(path)
        assert title == "Patched Title"
        assert req_count == 1

    def test_empty_session(self, tmp_path):
        """Session with no title and no requests returns fallback and 0 count."""
        path = self._write_session(
            tmp_path,
            {"customTitle": None, "requests": []},
        )
        title, req_count = get_session_info(path)
        assert title == "Untitled session"
        assert req_count == 0


class TestFindAllSessions:
    """Test session discovery across workspace directories."""

    def _create_workspace(self, ws_root, hash_name, folder_uri, sessions=None):
        """Helper to create a mock workspace directory."""
        ws_dir = ws_root / hash_name
        ws_dir.mkdir(parents=True)
        (ws_dir / "workspace.json").write_text(json.dumps({"folder": folder_uri}))

        if sessions:
            chat_dir = ws_dir / "chatSessions"
            chat_dir.mkdir()
            for name, content in sessions.items():
                (chat_dir / name).write_text(
                    "\n".join(json.dumps(line) for line in content)
                )

    def test_finds_sessions_grouped_by_project(self, tmp_path):
        ws_root = tmp_path / "workspaceStorage"
        ws_root.mkdir()

        self._create_workspace(
            ws_root,
            "hash1",
            "file:///Users/foo/project-a",
            sessions={
                "s1.jsonl": [
                    {
                        "kind": 0,
                        "v": {
                            "customTitle": "Session One",
                            "requests": [{"message": {"text": "q"}}],
                            "creationDate": 1700000000000,
                        },
                    }
                ],
                "s2.jsonl": [
                    {
                        "kind": 0,
                        "v": {
                            "customTitle": "Session Two",
                            "requests": [{"message": {"text": "q"}}],
                            "creationDate": 1700001000000,
                        },
                    }
                ],
            },
        )
        self._create_workspace(
            ws_root,
            "hash2",
            "file:///Users/foo/project-b",
            sessions={
                "s3.jsonl": [
                    {
                        "kind": 0,
                        "v": {
                            "customTitle": "Session Three",
                            "requests": [{"message": {"text": "q"}}],
                            "creationDate": 1700002000000,
                        },
                    }
                ],
            },
        )

        projects = find_all_sessions(ws_root)
        assert len(projects) == 2

        names = {p["name"] for p in projects}
        assert names == {"project-a", "project-b"}

        proj_a = next(p for p in projects if p["name"] == "project-a")
        assert len(proj_a["sessions"]) == 2

    def test_skips_workspaces_without_sessions(self, tmp_path):
        ws_root = tmp_path / "workspaceStorage"
        ws_root.mkdir()

        self._create_workspace(
            ws_root,
            "hash1",
            "file:///Users/foo/project-a",
            sessions={},
        )
        # Also workspace without chatSessions dir at all
        self._create_workspace(
            ws_root,
            "hash2",
            "file:///Users/foo/project-b",
        )

        projects = find_all_sessions(ws_root)
        assert len(projects) == 0

    def test_skips_json_files(self, tmp_path):
        """Only .jsonl files are discovered, not .json."""
        ws_root = tmp_path / "workspaceStorage"
        ws_root.mkdir()

        ws_dir = ws_root / "hash1"
        ws_dir.mkdir()
        (ws_dir / "workspace.json").write_text(
            json.dumps({"folder": "file:///Users/foo/project"})
        )
        chat_dir = ws_dir / "chatSessions"
        chat_dir.mkdir()
        # JSON file should be skipped
        (chat_dir / "old.json").write_text(json.dumps({"requests": []}))
        # JSONL file should be found (with valid request)
        (chat_dir / "new.jsonl").write_text(
            json.dumps(
                {
                    "kind": 0,
                    "v": {
                        "customTitle": "JSONL Session",
                        "requests": [{"message": {"text": "q"}}],
                        "creationDate": 1700000000000,
                    },
                }
            )
        )

        projects = find_all_sessions(ws_root)
        assert len(projects) == 1
        assert len(projects[0]["sessions"]) == 1
        assert projects[0]["sessions"][0]["title"] == "JSONL Session"

    def test_skips_empty_blank_sessions(self, tmp_path):
        """Sessions with 0 actual requests (created by VS Code on empty tab) are skipped."""
        ws_root = tmp_path / "workspaceStorage"
        ws_root.mkdir()

        self._create_workspace(
            ws_root,
            "hash1",
            "file:///Users/foo/project",
            sessions={
                "valid.jsonl": [
                    {
                        "kind": 0,
                        "v": {
                            "customTitle": "Valid",
                            "requests": [{"message": {"text": "hi"}}],
                        },
                    }
                ],
                "empty.jsonl": [
                    {
                        "kind": 0,
                        "v": {
                            "customTitle": None,
                            "requests": [],
                        },
                    }
                ],
            },
        )

        projects = find_all_sessions(ws_root)
        sessions = projects[0]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["title"] == "Valid"

    def test_projects_sorted_by_most_recent_session(self, tmp_path):
        """Projects are returned ordered by their most-recent session mtime, newest first."""
        ws_root = tmp_path / "workspaceStorage"
        ws_root.mkdir()

        # project-a has an older most-recent session
        self._create_workspace(
            ws_root,
            "hash_a",
            "file:///Users/foo/project-a",
            sessions={
                "old.jsonl": [
                    {
                        "kind": 0,
                        "v": {"customTitle": "Old Session", "requests": [{"message": {"text": "q"}}]},
                    }
                ],
            },
        )
        old_path = ws_root / "hash_a" / "chatSessions" / "old.jsonl"
        os.utime(old_path, (1700000000, 1700000000))

        # project-b has a newer most-recent session
        self._create_workspace(
            ws_root,
            "hash_b",
            "file:///Users/foo/project-b",
            sessions={
                "new.jsonl": [
                    {
                        "kind": 0,
                        "v": {"customTitle": "New Session", "requests": [{"message": {"text": "q"}}]},
                    }
                ],
            },
        )
        new_path = ws_root / "hash_b" / "chatSessions" / "new.jsonl"
        os.utime(new_path, (1700001000, 1700001000))

        projects = find_all_sessions(ws_root)
        assert len(projects) == 2
        assert projects[0]["name"] == "project-b"
        assert projects[1]["name"] == "project-a"

    def test_sessions_sorted_by_mtime(self, tmp_path):
        """Sessions within a project are sorted newest first."""
        ws_root = tmp_path / "workspaceStorage"
        ws_root.mkdir()

        self._create_workspace(
            ws_root,
            "hash1",
            "file:///Users/foo/project",
            sessions={
                "old.jsonl": [
                    {
                        "kind": 0,
                        "v": {
                            "customTitle": "Old Session",
                            "requests": [{"message": {"text": "q"}}],
                            "creationDate": 1700000000000,
                        },
                    }
                ],
            },
        )
        # Touch the old file first, then create new one
        old_path = ws_root / "hash1" / "chatSessions" / "old.jsonl"
        os.utime(old_path, (1700000000, 1700000000))

        new_path = ws_root / "hash1" / "chatSessions" / "new.jsonl"
        new_path.write_text(
            json.dumps(
                {
                    "kind": 0,
                    "v": {
                        "customTitle": "New Session",
                        "requests": [{"message": {"text": "q"}}],
                        "creationDate": 1700001000000,
                    },
                }
            )
        )

        projects = find_all_sessions(ws_root)
        sessions = projects[0]["sessions"]
        assert sessions[0]["title"] == "New Session"
        assert sessions[1]["title"] == "Old Session"
