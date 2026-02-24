from fastmcp import FastMCP
from starlette.middleware import Middleware

from auth import SlashNormalizationMiddleware, create_auth_provider
from tools import get_email, get_thread, list_emails, list_mailboxes, search_emails

mcp = FastMCP(
    name="Fastmail Email",
    instructions=(
        "You have access to Evie's Fastmail email. Use these tools to read and "
        "search her email. Start with list_mailboxes to see available folders, "
        "then use list_emails or search_emails to find specific messages. "
        "Use get_email for full content and get_thread for conversation context."
    ),
    auth=create_auth_provider(),
)

TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

mcp.tool(annotations=TOOL_ANNOTATIONS)(list_mailboxes)
mcp.tool(annotations=TOOL_ANNOTATIONS)(list_emails)
mcp.tool(annotations=TOOL_ANNOTATIONS)(get_email)
mcp.tool(annotations=TOOL_ANNOTATIONS)(search_emails)
mcp.tool(annotations=TOOL_ANNOTATIONS)(get_thread)

if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=8000,
        middleware=[Middleware(SlashNormalizationMiddleware)],
    )
