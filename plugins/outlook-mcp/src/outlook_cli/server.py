"""MCP server exposing Outlook COM automation as tools."""
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from outlook_cli import run

server = Server("outlook-cli")


@server.list_tools()
async def list_tools():
    return [
        Tool(name="mailbox_list", description="List all available mailboxes", inputSchema={"type": "object", "properties": {}}),
        Tool(name="message_list", description="List emails with filters", inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max emails (default 10)"},
                "folder": {"type": "string", "description": "Inbox, SentMail, Drafts, DeletedItems"},
                "query": {"type": "string", "description": "Free-text search"},
                "from_": {"type": "string", "description": "Filter by sender"},
                "subject": {"type": "string", "description": "Filter by subject"},
                "to": {"type": "string", "description": "Filter by recipient"},
                "category": {"type": "string", "description": "Filter by category"},
                "unread": {"type": "boolean", "description": "Only unread"},
                "since": {"type": "string", "description": "Time window e.g. 30m, 1h, 1d, 1w"},
                "mailbox": {"type": "string", "description": "Shared mailbox name"},
            }
        }),
        Tool(name="message_read", description="Read full email by ID from most recent list", inputSchema={
            "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]
        }),
        Tool(name="message_send", description="Compose and optionally send an email via Outlook", inputSchema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string", "description": "CC recipients"},
                "attachment": {"type": "string", "description": "File path(s), semicolon-separated"},
                "send": {"type": "boolean", "description": "If true, send immediately; otherwise open as draft"},
            },
            "required": ["to", "subject", "body"]
        }),
        Tool(name="message_reply", description="Reply to email by ID (opens draft)", inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "body": {"type": "string"},
                "reply_all": {"type": "boolean"},
            },
            "required": ["id", "body"]
        }),
        Tool(name="message_delete", description="Delete email by ID", inputSchema={
            "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]
        }),
        Tool(name="message_attachments_download", description="Download attachments from email by ID", inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "path": {"type": "string", "description": "Download directory"},
            },
            "required": ["id"]
        }),
        Tool(name="calendar_list", description="List calendar events", inputSchema={
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Search by subject"},
                "days": {"type": "integer", "description": "Days ahead (default 3)"},
            }
        }),
        Tool(name="calendar_create", description="Create calendar event", inputSchema={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "start": {"type": "string", "description": "e.g. 2026-03-18 10:00"},
                "end": {"type": "string"},
                "body": {"type": "string"},
                "location": {"type": "string"},
                "to": {"type": "string", "description": "Attendees, semicolon-separated"},
            },
            "required": ["subject", "start"]
        }),
        Tool(name="calendar_update", description="Update calendar event by ID", inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "subject": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "body": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["id"]
        }),
        Tool(name="calendar_delete", description="Delete calendar event by ID", inputSchema={
            "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]
        }),
        Tool(name="calendar_accept", description="Accept calendar event by ID", inputSchema={
            "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]
        }),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    args = []

    if name == "mailbox_list":
        args = ["mailbox", "list"]

    elif name == "message_list":
        args = ["message", "list"]
        if arguments.get("limit"): args += ["--limit", str(arguments["limit"])]
        if arguments.get("folder"): args += ["--folder", arguments["folder"]]
        if arguments.get("query"): args += ["--query", arguments["query"]]
        if arguments.get("from_"): args += ["--from", arguments["from_"]]
        if arguments.get("subject"): args += ["--subject", arguments["subject"]]
        if arguments.get("to"): args += ["--to", arguments["to"]]
        if arguments.get("category"): args += ["--category", arguments["category"]]
        if arguments.get("unread"): args += ["--unread"]
        if arguments.get("since"): args += ["--since", arguments["since"]]
        if arguments.get("mailbox"): args += ["--mailbox", arguments["mailbox"]]

    elif name == "message_read":
        args = ["message", "read", str(arguments["id"])]

    elif name == "message_send":
        args = ["message", "send", "--to", arguments["to"], "--subject", arguments["subject"], "--body", arguments["body"]]
        if arguments.get("cc"): args += ["--cc", arguments["cc"]]
        if arguments.get("attachment"): args += ["--attachment", arguments["attachment"]]
        if arguments.get("send"): args += ["--send"]

    elif name == "message_reply":
        args = ["message", "reply", str(arguments["id"]), "--body", arguments["body"]]
        if arguments.get("reply_all"): args += ["--replyAll"]

    elif name == "message_delete":
        args = ["message", "delete", str(arguments["id"])]

    elif name == "message_attachments_download":
        args = ["message", "attachments", "download", str(arguments["id"])]
        if arguments.get("path"): args += ["--path", arguments["path"]]

    elif name == "calendar_list":
        args = ["calendar", "list"]
        if arguments.get("subject"): args += ["--subject", arguments["subject"]]
        if arguments.get("days"): args += ["--days", str(arguments["days"])]

    elif name == "calendar_create":
        args = ["calendar", "create", "--subject", arguments["subject"], "--start", arguments["start"]]
        if arguments.get("end"): args += ["--end", arguments["end"]]
        if arguments.get("body"): args += ["--body", arguments["body"]]
        if arguments.get("location"): args += ["--location", arguments["location"]]
        if arguments.get("to"): args += ["--to", arguments["to"]]

    elif name == "calendar_update":
        args = ["calendar", "update", str(arguments["id"])]
        if arguments.get("subject"): args += ["--subject", arguments["subject"]]
        if arguments.get("start"): args += ["--start", arguments["start"]]
        if arguments.get("end"): args += ["--end", arguments["end"]]
        if arguments.get("body"): args += ["--body", arguments["body"]]
        if arguments.get("location"): args += ["--location", arguments["location"]]

    elif name == "calendar_delete":
        args = ["calendar", "delete", str(arguments["id"])]

    elif name == "calendar_accept":
        args = ["calendar", "accept", str(arguments["id"])]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    result = run(*args)
    return [TextContent(type="text", text=result)]


async def _run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
