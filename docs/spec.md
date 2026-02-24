# Fastmail MCP Server -- Specification

## Overview

A remote MCP server that gives Claude (iOS app and claude.ai) read-only access to Evie's Fastmail email. The server runs on Eviebot (Mac mini), is reachable over the internet via Tailscale Funnel, and authenticates requests using OAuth 2.1 with AWS Cognito.

## User

Single user: Evie. She is a PM (not a software engineer) who uses the Claude iOS app as her primary AI interface. She wants to ask Claude questions about her email -- find messages, read threads, search for information -- without switching apps or copying text manually.

## Problem

The Claude iOS app supports remote MCP servers, but there is no existing Fastmail MCP server that meets the requirements for remote access. Existing open-source implementations are either stdio-only (requiring Claude Desktop), lack OAuth authentication, or have insufficient email features. Evie cannot access her Fastmail email from Claude on her phone.

## Solution

Build a lightweight MCP server that:

- Connects to Fastmail via JMAP to read and search email
- Runs on Eviebot as an always-on service
- Exposes tools over Streamable HTTP, made internet-accessible via Tailscale Funnel (automatic HTTPS)
- Authenticates requests via OAuth 2.1, using AWS Cognito as the authorization server and JWT validation on the MCP server

After a one-time OAuth login, Evie can ask Claude to check her email with no further authentication steps.

## V1 Scope

**In scope:**
- Email read and search: list mailboxes, list emails in a mailbox, get a single email, search emails, get a conversation thread
- OAuth 2.1 authentication via Cognito (one-time login, refresh tokens for persistence)
- Always-on deployment via launchd and Tailscale Funnel

**Deferred (future versions):**
- Send email
- Calendar access
- Contacts access

**Out of scope:**
- Email management (delete, move, mark read/unread, archive)
- Multi-user support
- Web UI or admin interface

## MCP Tools

| Tool | Description | Annotations |
|------|-------------|-------------|
| `list_mailboxes` | List all mailboxes with message counts | `readOnlyHint: true`, `destructiveHint: false` |
| `list_emails` | List emails in a mailbox (paginated), showing sender, subject, date, and snippet | `readOnlyHint: true`, `destructiveHint: false` |
| `get_email` | Get full email content by ID | `readOnlyHint: true`, `destructiveHint: false` |
| `search_emails` | Search by sender, subject, date range, body text, has-attachment | `readOnlyHint: true`, `destructiveHint: false` |
| `get_thread` | Get all emails in a conversation thread | `readOnlyHint: true`, `destructiveHint: false` |

## Authentication

**Pattern:** OAuth 2.1 with an external authorization server.

- **Authorization server:** AWS Cognito (User Pool with a single user, custom domain at `auth.evehwang.com`)
- **Resource server:** The MCP server itself (validates JWTs, serves tools)
- **Flow:** Authorization Code with PKCE (S256), as required by the MCP spec
- **Client registration:** Pre-registered App Client with fixed Client ID and Client Secret, entered manually in claude.ai's Advanced Settings (avoids known issues with Dynamic Client Registration)
- **Token lifecycle:** Access tokens expire after ~1 hour; refresh tokens last ~30 days. Claude handles refresh automatically -- no repeated logins.
- **Discovery:** The MCP server serves a Protected Resource Metadata endpoint (RFC 9728) that points claude.ai to Cognito for token acquisition.

**User experience:** Evie logs in once at `auth.evehwang.com` when she first adds the connector. After that, it just works.

## Infrastructure

- **Server host:** Eviebot (Mac mini, Apple Silicon, macOS, always-on headless)
- **HTTPS exposure:** Tailscale Funnel provides automatic TLS via Let's Encrypt and a stable public HTTPS URL
- **Process management:** macOS launchd service with auto-start on boot and auto-restart on failure
- **Logging:** stdout/stderr to log files under `~/Library/Logs/fastmail-mcp/`
- **DNS:** `auth.evehwang.com` points to Cognito's CloudFront distribution (Route 53 alias record)

## Prerequisites

The following must be in place before implementation begins:

**On Eviebot:**
- Tailscale Funnel verified working (test with a simple HTTP server, confirm reachable from iPhone)
- Python 3.12+ installed
- AWS CLI configured and authenticated (`aws sts get-caller-identity` succeeds)
- Git configured with name and email
- Fastmail API token available as `FASTMAIL_API_TOKEN` environment variable

**On AWS / Route 53:**
- `evehwang.com` hosted zone exists in Route 53
- Hosted Zone ID noted
- Root A record for `evehwang.com` exists (Cognito requires the root domain to resolve)

**On GitHub:**
- Private repo `EvieHwang/fastmail-mcp-server` created

**Values needed at implementation time:**
- Tailscale Funnel HTTPS URL
- AWS region
- Python version
- Route 53 hosted zone ID
- Whether a root A record for `evehwang.com` already exists

## Non-functional Requirements

- **Single-user:** No multi-tenancy, no user management beyond Evie's single Cognito account
- **Read-only:** The server never modifies email state. All tools are annotated as read-only and non-destructive.
- **Always-on:** The server should survive reboots and recover from crashes automatically (launchd + Tailscale Funnel `--bg`)
- **Low maintenance:** Once deployed, this should not require regular attention. Refresh tokens keep auth alive; launchd keeps the process alive.
- **Privacy:** Email data is only transmitted over HTTPS (Tailscale Funnel TLS + Fastmail JMAP TLS). The Fastmail API token never leaves Eviebot.

## Technical References

- [Anthropic MCP Connector Docs](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers)
- [MCP Authorization Specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [FastMCP Token Verification](https://gofastmcp.com/servers/auth/token-verification)
- [FastMCP HTTP Deployment](https://gofastmcp.com/deployment/http)
- [Cognito-based MCP Servers (Sodkiewicz)](https://sodkiewiczm.medium.com/cognito-based-mcp-servers-a426ca2544c5)
- [empires-security/mcp-oauth2-aws-cognito](https://github.com/empires-security/mcp-oauth2-aws-cognito)
- [Tailscale Funnel Docs](https://tailscale.com/kb/1223/funnel)
- [Fastmail JMAP Samples](https://github.com/fastmail/JMAP-Samples)
- [MadLlama25/fastmail-mcp (JMAP reference)](https://github.com/MadLlama25/fastmail-mcp)
- [Aaron Parecki's MCP OAuth Explainer](https://aaronparecki.com/2025/04/03/15/oauth-for-model-context-protocol)
