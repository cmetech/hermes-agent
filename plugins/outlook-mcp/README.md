> Ported verbatim from the internal `loop_24` repo (`mcp/outlook_mcp`, 2026-07-13)
> into `ericsson-capabilities`. Windows-only at runtime (drives the local
> logged-in Outlook desktop via PowerShell → COM; no API keys). Staged by the
> capability-staging seam; configured via `mcp/mcp-servers.yaml`.

# outlook-cli

MCP server for Microsoft Outlook email and calendar, powered by COM automation.

## Requirements

- Windows with **Outlook desktop running and online** (the tool automates your logged-in session — Outlook must be open and connected, not in offline/cached mode)
- Python 3.10+
- WSL (if running from Linux — calls `powershell.exe` under the hood)

## Install

```bash
pip install .
```

Or with uv:
```bash
uv pip install .
```

## Usage

### As a CLI
```bash
outlook-cli mailbox list
outlook-cli message list --limit 5 --unread
outlook-cli message read 1
outlook-cli message send --to "user@example.com" --subject "Hello" --body "Hi there" --send
outlook-cli calendar list --days 7
```

### As an MCP server

Add to your MCP config (e.g. `~/.kiro/settings/mcp.json`):

```json
{
  "mcpServers": {
    "outlook": {
      "command": "outlook-mcp",
      "args": [],
      "transportType": "stdio"
    }
  }
}
```

Or if not installed globally:
```json
{
  "mcpServers": {
    "outlook": {
      "command": "python",
      "args": ["-m", "outlook_cli.server"],
      "cwd": "/path/to/outlook-cli",
      "transportType": "stdio"
    }
  }
}
```

### Adding to LangFlow

1. Open LangFlow UI (http://localhost:7860)
2. Go to **Settings** → **MCP Servers**
3. Click **Add New**
4. Configure:
   - **Name**: `outlook`
   - **Command**: `python`
   - **Arguments**: `mcp/outlook_mcp/run_server.py`

## Tools exposed via MCP

| Tool | Description |
|------|-------------|
| `mailbox_list` | List all mailboxes |
| `message_list` | List emails with filters (limit, folder, query, from, subject, to, category, unread, since, mailbox) |
| `message_read` | Read full email by ID |
| `message_send` | Compose and send email (or open as draft) |
| `message_reply` | Reply to email (opens draft) |
| `message_delete` | Delete email by ID |
| `message_attachments_download` | Download attachments |
| `calendar_list` | List upcoming events |
| `calendar_create` | Create event/meeting |
| `calendar_update` | Update event by ID |
| `calendar_delete` | Delete event by ID |
| `calendar_accept` | Accept meeting invite |

## How it works

```
MCP Client (Kiro, Claude Desktop, LangFlow, etc.)
  └── stdio → outlook-mcp (Python MCP server)
        └── powershell.exe → outlook-cli.ps1
              └── COM → Outlook.Application
```

No API keys, no Azure registration — it drives your local Outlook instance directly.
