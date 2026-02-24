from jmap_client import JMAPClient

jmap = JMAPClient()


async def _ensure_account(jmap: JMAPClient) -> str:
    """Ensure JMAP session is discovered, return account_id."""
    if not jmap.account_id:
        import httpx

        async with httpx.AsyncClient() as c:
            await jmap._discover(c)
    return jmap.account_id


async def list_mailboxes() -> str:
    """List all mailboxes with message counts."""
    account_id = await _ensure_account(jmap)
    results = await jmap.call(
        [
            [
                "Mailbox/get",
                {
                    "accountId": account_id,
                    "properties": [
                        "name",
                        "parentId",
                        "role",
                        "totalEmails",
                        "unreadEmails",
                    ],
                },
                "0",
            ]
        ]
    )
    mailboxes = results[0][1]["list"]
    lines = []
    for mb in sorted(mailboxes, key=lambda m: m["name"]):
        unread = f" ({mb['unreadEmails']} unread)" if mb["unreadEmails"] else ""
        lines.append(
            f"- {mb['name']}: {mb['totalEmails']} emails{unread} [id:{mb['id']}]"
        )
    return "\n".join(lines)


async def list_emails(mailbox_id: str, limit: int = 20, position: int = 0) -> str:
    """List emails in a mailbox, showing sender, subject, date, and snippet.

    Args:
        mailbox_id: The mailbox ID (from list_mailboxes).
        limit: Number of emails to return (default 20, max 50).
        position: Offset for pagination (default 0).
    """
    limit = min(limit, 50)
    account_id = await _ensure_account(jmap)
    results = await jmap.call(
        [
            [
                "Email/query",
                {
                    "accountId": account_id,
                    "filter": {"inMailbox": mailbox_id},
                    "sort": [{"property": "receivedAt", "isAscending": False}],
                    "position": position,
                    "limit": limit,
                },
                "0",
            ],
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "#ids": {"resultOf": "0", "name": "Email/query", "path": "/ids"},
                    "properties": [
                        "id",
                        "threadId",
                        "from",
                        "subject",
                        "receivedAt",
                        "preview",
                    ],
                },
                "1",
            ],
        ]
    )
    total = results[0][1].get("total", "?")
    emails = results[1][1]["list"]
    lines = [f"Showing {len(emails)} of {total} emails (offset {position}):"]
    for e in emails:
        sender = e["from"][0]["email"] if e.get("from") else "unknown"
        name = e["from"][0].get("name", "") if e.get("from") else ""
        sender_display = f"{name} <{sender}>" if name else sender
        date = e["receivedAt"][:10]
        lines.append(f"\n**{e['subject']}**")
        lines.append(f"  From: {sender_display}")
        lines.append(f"  Date: {date}")
        lines.append(f"  {e['preview'][:150]}")
        lines.append(f"  [id:{e['id']}] [thread:{e['threadId']}]")
    return "\n".join(lines)


async def get_email(email_id: str) -> str:
    """Get the full content of an email by ID.

    Args:
        email_id: The email ID (from list_emails or search_emails).
    """
    account_id = await _ensure_account(jmap)
    results = await jmap.call(
        [
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "ids": [email_id],
                    "properties": [
                        "id",
                        "threadId",
                        "from",
                        "to",
                        "cc",
                        "subject",
                        "receivedAt",
                        "bodyValues",
                        "textBody",
                        "htmlBody",
                        "hasAttachment",
                        "attachments",
                    ],
                    "fetchTextBodyValues": True,
                    "fetchHTMLBodyValues": True,
                },
                "0",
            ]
        ]
    )
    emails = results[0][1]["list"]
    if not emails:
        return f"Email {email_id} not found."
    e = emails[0]

    def format_addrs(addrs):
        if not addrs:
            return "none"
        return ", ".join(
            f"{a.get('name', '')} <{a['email']}>" if a.get("name") else a["email"]
            for a in addrs
        )

    # Prefer text body, fall back to HTML
    body = ""
    for part in e.get("textBody", []):
        part_id = part["partId"]
        if part_id in e.get("bodyValues", {}):
            body += e["bodyValues"][part_id].get("value", "")
    if not body:
        for part in e.get("htmlBody", []):
            part_id = part["partId"]
            if part_id in e.get("bodyValues", {}):
                body += e["bodyValues"][part_id].get("value", "")

    attachments = ""
    if e.get("attachments"):
        att_names = [a.get("name", "unnamed") for a in e["attachments"]]
        attachments = f"\nAttachments: {', '.join(att_names)}"

    return (
        f"**{e['subject']}**\n"
        f"From: {format_addrs(e.get('from'))}\n"
        f"To: {format_addrs(e.get('to'))}\n"
        f"CC: {format_addrs(e.get('cc'))}\n"
        f"Date: {e['receivedAt']}\n"
        f"{attachments}\n"
        f"---\n{body}"
    )


async def search_emails(
    query: str | None = None,
    from_address: str | None = None,
    subject: str | None = None,
    after: str | None = None,
    before: str | None = None,
    has_attachment: bool | None = None,
    limit: int = 20,
) -> str:
    """Search emails by various criteria.

    Args:
        query: Full-text search across all fields.
        from_address: Filter by sender email address.
        subject: Filter by subject text.
        after: Only emails after this date (YYYY-MM-DD).
        before: Only emails before this date (YYYY-MM-DD).
        has_attachment: Filter emails with/without attachments.
        limit: Max results (default 20, max 50).
    """
    limit = min(limit, 50)
    account_id = await _ensure_account(jmap)

    filter_conditions = []
    if query:
        filter_conditions.append({"text": query})
    if from_address:
        filter_conditions.append({"from": from_address})
    if subject:
        filter_conditions.append({"subject": subject})
    if after:
        filter_conditions.append({"after": f"{after}T00:00:00Z"})
    if before:
        filter_conditions.append({"before": f"{before}T23:59:59Z"})
    if has_attachment is not None:
        filter_conditions.append({"hasAttachment": has_attachment})

    if not filter_conditions:
        return "Please provide at least one search criterion."

    email_filter = (
        filter_conditions[0]
        if len(filter_conditions) == 1
        else {"operator": "AND", "conditions": filter_conditions}
    )

    results = await jmap.call(
        [
            [
                "Email/query",
                {
                    "accountId": account_id,
                    "filter": email_filter,
                    "sort": [{"property": "receivedAt", "isAscending": False}],
                    "limit": limit,
                },
                "0",
            ],
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "#ids": {"resultOf": "0", "name": "Email/query", "path": "/ids"},
                    "properties": [
                        "id",
                        "threadId",
                        "from",
                        "subject",
                        "receivedAt",
                        "preview",
                    ],
                },
                "1",
            ],
        ]
    )
    total = results[0][1].get("total", "?")
    emails = results[1][1]["list"]
    if not emails:
        return "No emails found matching your search."
    lines = [f"Found {total} results (showing {len(emails)}):"]
    for e in emails:
        sender = e["from"][0]["email"] if e.get("from") else "unknown"
        name = e["from"][0].get("name", "") if e.get("from") else ""
        sender_display = f"{name} <{sender}>" if name else sender
        date = e["receivedAt"][:10]
        lines.append(f"\n**{e['subject']}**")
        lines.append(f"  From: {sender_display}")
        lines.append(f"  Date: {date}")
        lines.append(f"  {e['preview'][:150]}")
        lines.append(f"  [id:{e['id']}] [thread:{e['threadId']}]")
    return "\n".join(lines)


async def get_thread(thread_id: str) -> str:
    """Get all emails in a conversation thread.

    Args:
        thread_id: The thread ID (from list_emails or search_emails).
    """
    account_id = await _ensure_account(jmap)
    results = await jmap.call(
        [
            [
                "Thread/get",
                {"accountId": account_id, "ids": [thread_id]},
                "0",
            ],
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "#ids": {
                        "resultOf": "0",
                        "name": "Thread/get",
                        "path": "/list/*/emailIds",
                    },
                    "properties": [
                        "id",
                        "from",
                        "to",
                        "subject",
                        "receivedAt",
                        "bodyValues",
                        "textBody",
                        "preview",
                    ],
                    "fetchTextBodyValues": True,
                },
                "1",
            ],
        ]
    )
    threads = results[0][1]["list"]
    if not threads:
        return f"Thread {thread_id} not found."

    emails = results[1][1]["list"]
    # Sort by date ascending for conversation order
    emails.sort(key=lambda e: e["receivedAt"])

    lines = [
        f"Thread: {emails[0]['subject'] if emails else 'unknown'} ({len(emails)} messages)"
    ]
    for e in emails:
        sender = e["from"][0]["email"] if e.get("from") else "unknown"
        name = e["from"][0].get("name", "") if e.get("from") else ""
        sender_display = f"{name} <{sender}>" if name else sender
        body = ""
        for part in e.get("textBody", []):
            part_id = part["partId"]
            if part_id in e.get("bodyValues", {}):
                body += e["bodyValues"][part_id].get("value", "")
        if not body:
            body = e.get("preview", "")
        lines.append(f"\n--- {sender_display} ({e['receivedAt']}) ---")
        lines.append(body[:2000])
    return "\n".join(lines)
