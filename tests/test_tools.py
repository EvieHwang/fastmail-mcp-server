"""Tests for MCP tools with mocked JMAP responses."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("FASTMAIL_API_TOKEN", "fake-token")
    monkeypatch.setenv("FASTMAIL_BASE_URL", "https://api.fastmail.com")
    monkeypatch.setenv(
        "COGNITO_ISSUER_URL",
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_FAKE",
    )
    monkeypatch.setenv(
        "COGNITO_JWKS_URI",
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_FAKE/.well-known/jwks.json",
    )
    monkeypatch.setenv("MCP_RESOURCE_URL", "https://example.ts.net")
    monkeypatch.setenv("COGNITO_PUBLIC_CLIENT_ID", "fake-public-client-id")


@pytest.fixture
def mock_jmap():
    """Patch the jmap client used by tools."""
    with patch("tools.jmap") as mock:
        mock.account_id = "test-account"
        mock.call = AsyncMock()
        yield mock


@pytest.mark.asyncio
async def test_list_mailboxes(mock_jmap):
    from tools import list_mailboxes

    mock_jmap.call.return_value = [
        [
            "Mailbox/get",
            {
                "list": [
                    {
                        "id": "P-F",
                        "name": "Inbox",
                        "parentId": None,
                        "role": "inbox",
                        "totalEmails": 10,
                        "unreadEmails": 3,
                    },
                    {
                        "id": "P2F",
                        "name": "Sent",
                        "parentId": None,
                        "role": "sent",
                        "totalEmails": 50,
                        "unreadEmails": 0,
                    },
                ]
            },
            "0",
        ]
    ]
    result = await list_mailboxes()
    assert "Inbox: 10 emails (3 unread)" in result
    assert "Sent: 50 emails" in result
    assert "(0 unread)" not in result  # zero unread should not show


@pytest.mark.asyncio
async def test_list_emails(mock_jmap):
    from tools import list_emails

    mock_jmap.call.return_value = [
        ["Email/query", {"ids": ["e1"], "total": 1}, "0"],
        [
            "Email/get",
            {
                "list": [
                    {
                        "id": "e1",
                        "threadId": "t1",
                        "from": [{"name": "Alice", "email": "alice@example.com"}],
                        "subject": "Hello",
                        "receivedAt": "2026-02-23T10:00:00Z",
                        "preview": "Hi there, this is a test email.",
                    }
                ]
            },
            "1",
        ],
    ]
    result = await list_emails("P-F", limit=5)
    assert "Hello" in result
    assert "Alice" in result
    assert "alice@example.com" in result


@pytest.mark.asyncio
async def test_get_email(mock_jmap):
    from tools import get_email

    mock_jmap.call.return_value = [
        [
            "Email/get",
            {
                "list": [
                    {
                        "id": "e1",
                        "threadId": "t1",
                        "from": [{"name": "Bob", "email": "bob@example.com"}],
                        "to": [{"name": "Evie", "email": "eve@evehwang.com"}],
                        "cc": None,
                        "subject": "Meeting notes",
                        "receivedAt": "2026-02-23T14:00:00Z",
                        "bodyValues": {
                            "1": {"value": "Here are the notes from today."}
                        },
                        "textBody": [{"partId": "1"}],
                        "htmlBody": [],
                        "hasAttachment": False,
                        "attachments": [],
                    }
                ]
            },
            "0",
        ]
    ]
    result = await get_email("e1")
    assert "Meeting notes" in result
    assert "Bob" in result
    assert "Here are the notes from today." in result


@pytest.mark.asyncio
async def test_get_email_not_found(mock_jmap):
    from tools import get_email

    mock_jmap.call.return_value = [["Email/get", {"list": []}, "0"]]
    result = await get_email("nonexistent")
    assert "not found" in result


@pytest.mark.asyncio
async def test_search_no_criteria(mock_jmap):
    from tools import search_emails

    result = await search_emails()
    assert "at least one search criterion" in result


@pytest.mark.asyncio
async def test_search_emails(mock_jmap):
    from tools import search_emails

    mock_jmap.call.return_value = [
        ["Email/query", {"ids": ["e1"], "total": 1}, "0"],
        [
            "Email/get",
            {
                "list": [
                    {
                        "id": "e1",
                        "threadId": "t1",
                        "from": [{"name": "Support", "email": "support@example.com"}],
                        "subject": "Your ticket update",
                        "receivedAt": "2026-02-22T09:00:00Z",
                        "preview": "Your ticket has been resolved.",
                    }
                ]
            },
            "1",
        ],
    ]
    result = await search_emails(query="ticket")
    assert "Your ticket update" in result
    assert "Found 1 results" in result


@pytest.mark.asyncio
async def test_get_thread(mock_jmap):
    from tools import get_thread

    mock_jmap.call.return_value = [
        ["Thread/get", {"list": [{"id": "t1", "emailIds": ["e1", "e2"]}]}, "0"],
        [
            "Email/get",
            {
                "list": [
                    {
                        "id": "e1",
                        "from": [{"name": "Alice", "email": "alice@example.com"}],
                        "to": [{"email": "eve@evehwang.com"}],
                        "subject": "Project update",
                        "receivedAt": "2026-02-20T10:00:00Z",
                        "bodyValues": {"1": {"value": "Here's the update."}},
                        "textBody": [{"partId": "1"}],
                        "preview": "Here's the update.",
                    },
                    {
                        "id": "e2",
                        "from": [{"name": "Evie", "email": "eve@evehwang.com"}],
                        "to": [{"email": "alice@example.com"}],
                        "subject": "Re: Project update",
                        "receivedAt": "2026-02-20T11:00:00Z",
                        "bodyValues": {"1": {"value": "Thanks, looks good!"}},
                        "textBody": [{"partId": "1"}],
                        "preview": "Thanks, looks good!",
                    },
                ]
            },
            "1",
        ],
    ]
    result = await get_thread("t1")
    assert "2 messages" in result
    assert "Here's the update." in result
    assert "Thanks, looks good!" in result


@pytest.mark.asyncio
async def test_get_thread_not_found(mock_jmap):
    from tools import get_thread

    mock_jmap.call.return_value = [
        ["Thread/get", {"list": []}, "0"],
        ["Email/get", {"list": []}, "1"],
    ]
    result = await get_thread("nonexistent")
    assert "not found" in result
