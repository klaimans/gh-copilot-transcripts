# gh-copilot-transcripts

Convert GitHub Copilot VS Code chat sessions into paginated, mobile-friendly HTML transcripts.

Inspired by and based on Simon Willison's [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts).

## What it does

Discovers Copilot chat sessions stored as `.jsonl` files in VS Code's `workspaceStorage`, reconstructs the incremental snapshot format, deduplicates thinking blocks and tool invocations, and renders everything into browseable HTML with:

- Paginated transcripts (5 prompts per page) with an index showing tool-usage stats
- Specialized rendering for 20+ tool types (terminal commands, file edits, search, MCP tools, etc.)
- Collapsible thinking/reasoning blocks
- Batch mode that generates a multi-project archive (master index → project → session)
- One-click sharing via GitHub Gist
- Cross-platform support (macOS, Windows, Linux)

## Installation

Requires [uv](https://docs.astral.sh/uv/).

### From a Git repository

No publishing or registry needed — users just need access to the repo:

```bash
uvx --from git+https://github.com/klaimans/gh-copilot-transcripts.git gh-copilot-transcripts
```

Pin to a specific tag:

```bash
uvx --from "gh-copilot-transcripts @ git+https://github.com/klaimans/gh-copilot-transcripts@v0.1" gh-copilot-transcripts
```

### From a local clone

```bash
git clone https://github.com/klaimans/gh-copilot-transcripts
cd gh-copilot-transcripts
uv run gh-copilot-transcripts
```

## Usage

### Commands

#### Interactive mode (default)

```bash
gh-copilot-transcripts
```

Opens an interactive picker to select a session from your local VS Code workspaceStorage.

#### Convert a specific JSONL file

```bash
gh-copilot-transcripts json path/to/session.jsonl
```

Converts a single Copilot session file to HTML.

**Example:**

```bash
gh-copilot-transcripts json ~/path/to/session.jsonl -o output/ --open --gist
```

#### Batch convert all sessions

```bash
gh-copilot-transcripts all
```

Discovers all Copilot sessions in your local VS Code workspaceStorage and generates a browseable archive with a master index, per-project indexes, and paginated session transcripts.

**Example:**

```bash
gh-copilot-transcripts all -o archive/ --open
```

### Options

| Flag | Description | Available for |
|------|-------------|---|
| `-o, --output PATH` | Output directory (default: `./_transcripts`) | `local`, `json`, `all` |
| `--open` | Open the result in your browser | `local`, `json`, `all` |
| `--gist` | Upload to GitHub Gist and output a gisthost.github.io preview URL | `local`, `json` |
| `--json` | Copy the source `.jsonl` file to the output directory | `local`, `json` |
| `-p, --project NAME` | Filter sessions by project name | `local` |
| `--limit INT` | Maximum number of sessions to show (default: 50) | `local` |
| `--dry-run` | Show what would be generated without creating files | `all` |
| `-q, --quiet` | Suppress progress output | `all` |

## Agent skill: session-diagnostics

This project includes a [Copilot agent skill](.github/skills/session-diagnostics/SKILL.md) for investigating and debugging issues in Copilot JSONL session data.

Use it when HTML transcripts show empty thinking blocks, missing tool results, duplicate entries, or rendering errors. The skill provides investigation procedures, reference tables for the JSONL format, and diagnostic scripts in `scripts/`.
