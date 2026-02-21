# Project: Fastmail MCP Server on Eviebot

## Goal

Set up a remote Fastmail MCP server running on Eviebot (Mac mini) so Evie can access her email from the Claude iOS app on her iPhone. The server reads email from Fastmail via JMAP, exposes tools over Streamable HTTP via Tailscale Funnel, and authenticates requests using OAuth 2.1 with AWS Cognito.

## Scope: V1

- **In scope:** Email read and search (list mailboxes, list emails, get email by ID, search emails, get thread)
- **Deferred:** Send email, calendar, contacts
- **Out of scope for v1:** Calendar read, contacts, email management (delete/move/mark read)

## Environment

This project is built and deployed entirely on Eviebot. Claude Code runs directly on this machine.

- **Eviebot:** Mac mini (Apple Silicon), macOS Tahoe, always-on headless server
- **Tailscale IP:** 100.73.184.13
- **Installed:** Node.js, Python 3.12+, Homebrew, Claude Code, AWS CLI, git
- **Fastmail:** API token available as `FASTMAIL_API_TOKEN` env var, base URL as `FASTMAIL_BASE_URL` (https://api.fastmail.com)
- **GitHub:** Username EvieHwang, repo `EvieHwang/fastmail-mcp-server` (private)
- **AWS:** Active account with CLI configured
- **Domain:** evehwang.com, managed on Route 53

### Why Eviebot (not AWS)

Eviebot is already always-on, already has the Fastmail API token, and already has Tailscale providing HTTPS exposure via Funnel. Hosting the MCP server on AWS (EC2, Fargate, Lambda) would add compute costs, require storing the Fastmail token in AWS Secrets Manager, and introduce VPC networking complexity — all for zero benefit over a machine that’s already running. AWS is used here only for Cognito (managed auth), while the MCP server runs on local hardware. Simplest architecture, fewest moving parts.

## Architecture

```
Claude iOS App
      │
      │ HTTPS (Streamable HTTP + Bearer token)
      ▼
Tailscale Funnel (automatic TLS, port 443)
      │
      ▼
Fastmail MCP Server (FastMCP Python, on Eviebot, port 8000)
   ├── Token Verification ← validates JWTs from Cognito via JWKS
   ├── Protected Resource Metadata endpoint (RFC 9728)
   ├── JMAP email tools (read/search)
   └── Runs as launchd service
      │
      │ JMAP over HTTPS
      ▼
Fastmail API (api.fastmail.com)

AWS Cognito (Authorization Server)
   ├── User Pool with single user (Evie)
   ├── Custom domain: auth.evehwang.com
   ├── Pre-registered App Client (for claude.ai)
   ├── Hosted UI / Managed Login for one-time authentication
   ├── Issues JWTs validated by MCP server
   └── PKCE + Authorization Code flow
```

### How the pieces connect

1. Evie adds the MCP connector in claude.ai Settings, entering the Tailscale Funnel URL and pre-registered Client ID + Client Secret in Advanced Settings.
1. On first use, claude.ai initiates OAuth: it discovers the Protected Resource Metadata from the MCP server, which points to Cognito as the authorization server.
1. Evie sees the Cognito login page at auth.evehwang.com **once**, authenticates, and grants consent.
1. Cognito issues a JWT access token (+ refresh token) to claude.ai.
1. All subsequent MCP requests from Claude carry the Bearer token. The MCP server validates the JWT signature against Cognito’s JWKS endpoint.
1. Refresh tokens keep the session alive automatically — no further manual login required.

## Technology Choice: FastMCP (Python)

### Repo Evaluation

Five existing Fastmail MCP servers were evaluated:

|Repo                                   |Language      |Transport       |Auth                      |Email Features                                                |Verdict                                                            |
|---------------------------------------|--------------|----------------|--------------------------|--------------------------------------------------------------|-------------------------------------------------------------------|
|**MadLlama25/fastmail-mcp**            |TypeScript    |stdio only      |None (Fastmail token only)|Rich: search, threading, attachments, send, contacts, calendar|Best JMAP feature coverage, but no HTTP transport or OAuth         |
|**jeffjjohnston/fastmail-mcp-server**  |Python/FastMCP|Streamable HTTP |Static bearer token       |Minimal: list inbox only                                      |Best architectural match (already remote-capable), but feature-thin|
|**gr3enarr0w/fastmail-mcp-server**     |Node.js       |stdio via Docker|None                      |Email management + scripts                                    |Docker-focused, no HTTP transport                                  |
|**jahfer/jmap-mcp-server**             |Node.js       |stdio           |None                      |Email read/search only                                        |Simple but stdio-only                                              |
|**alexdiazdecerio/fastmail-mcp-server**|TypeScript    |stdio           |None                      |Unknown                                                       |Claude Desktop focused                                             |

**None of these are forkable as-is** for this project’s requirements (Streamable HTTP + OAuth + email read/search). The gap between what exists and what’s needed is significant enough that building on the FastMCP framework directly is cleaner than forking.

### Why FastMCP (Python)

- **Built-in Streamable HTTP transport** — runs as a standard HTTP server, no adapter needed
- **Built-in Token Verification** — FastMCP’s `TokenVerifier` class validates JWTs from external providers (Cognito) out of the box
- **Built-in OAuth Protected Resource Metadata** — serves the RFC 9728 discovery endpoint that claude.ai needs to find Cognito
- **Production-recommended pattern** — FastMCP’s own docs recommend external IdP (Cognito, Auth0, Okta) over self-hosted OAuth for production
- **JMAP is straightforward** — JMAP is a simple HTTP/JSON protocol; implementing email read/search in Python is less work than adding HTTP transport + OAuth to a TypeScript server that already has JMAP
- **Reference code available** — MadLlama25’s JMAP implementation provides clear patterns for Fastmail API interactions that can be adapted to Python

### Reference Materials

- **empires-security/mcp-oauth2-aws-cognito** — Complete reference implementation of MCP + OAuth 2.1 + Cognito, including CloudFormation templates for Cognito setup (Node.js/Express but patterns transfer directly)
- **FastMCP Token Verification docs** — https://gofastmcp.com/servers/auth/token-verification
- **FastMCP HTTP Deployment docs** — https://gofastmcp.com/deployment/http
- **Cognito-based MCP Servers guide** (Marcin Sodkiewicz) — https://sodkiewiczm.medium.com/cognito-based-mcp-servers-a426ca2544c5
- **MadLlama25/fastmail-mcp** — Reference for JMAP tool implementation patterns

## Authentication Strategy: OAuth 2.1 with Cognito

### Architecture Pattern: Resource Server + External Authorization Server

The MCP server acts as a **Resource Server** (validates tokens, serves tools). AWS Cognito acts as the **Authorization Server** (authenticates users, issues tokens). This is the standard pattern recommended by FastMCP, the MCP spec, and the broader OAuth community.

There is no fallback. This is the approach Anthropic designed for remote MCP connectors, and it’s how this project will be built.

### Cognito Configuration

- **User Pool:** Single user (Evie), email-based login
- **Custom domain:** `auth.evehwang.com` (requires ACM certificate in us-east-1 and Route 53 alias record)
- **App Client:** Pre-registered with fixed Client ID and Client Secret
- **Auth flow:** Authorization Code with PKCE (S256), required by MCP spec
- **Redirect URIs:** `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`
- **Token settings:** Access token expiry ~1 hour, refresh token expiry ~30 days
- **Scopes:** `openid`, `email` (coarse-grained; single user, fine-grained scopes add complexity without value)

**Important Cognito detail:** The OIDC discovery endpoints (`.well-known/openid-configuration` and `.well-known/jwks.json`) live at `cognito-idp.{region}.amazonaws.com/{pool-id}/`, NOT on the custom domain. The custom domain is only for the hosted UI login page and OAuth endpoints (authorize, token). The MCP server’s token validation points to the Cognito regional endpoint for JWKS.

### Custom Domain Setup (auth.evehwang.com)

Requirements:

1. ACM certificate in **us-east-1** (N. Virginia) — mandatory regardless of User Pool region. Use wildcard `*.evehwang.com` or specific `auth.evehwang.com`.
1. Route 53 A record (alias) for `auth.evehwang.com` pointing to the CloudFront distribution that Cognito creates.
1. A parent A record for `evehwang.com` must exist in Route 53 (can point to any IP; Cognito just requires it to resolve).
1. Propagation: Custom domain takes ~5 minutes after Cognito setup, but the CloudFront distribution can take up to 1 hour.

### MCP Server Auth Implementation

Using FastMCP’s built-in capabilities:

- **TokenVerifier** subclass that validates Cognito JWTs (checks signature via JWKS, issuer, audience, expiry)
- **AuthSettings** configured with Cognito’s issuer URL and the server’s resource URL
- **Protected Resource Metadata** endpoint served automatically by FastMCP, pointing claude.ai to Cognito for token acquisition

### Claude.ai Configuration (manual step — Evie does this)

1. Settings → Connectors → Add custom connector
1. Enter MCP server URL (Tailscale Funnel HTTPS endpoint)
1. Advanced Settings → enter pre-registered Client ID and Client Secret
1. Complete OAuth flow (one-time Cognito login at auth.evehwang.com)

### DCR Bug Context

Multiple GitHub issues (December 2025–January 2026) document failures with Dynamic Client Registration in claude.ai. Pre-registration with fixed Client ID/Secret in Advanced Settings sidesteps this entirely. This is also the MCP spec’s highest-priority registration method.

Relevant issues: `anthropics/claude-ai-mcp#5`, `modelcontextprotocol#2157`, `anthropics/claude-code#11814`, `anthropics/claude-code#1674`

## MCP Server Tools (V1)

Based on MadLlama25’s JMAP implementation patterns, adapted for email-read-only scope:

|Tool            |Description                                                           |Read-only|
|----------------|----------------------------------------------------------------------|---------|
|`list_mailboxes`|List all mailboxes with message counts                                |Yes      |
|`list_emails`   |List emails in a mailbox (paginated, with sender/subject/date/snippet)|Yes      |
|`get_email`     |Get full email content by ID                                          |Yes      |
|`search_emails` |Search by sender, subject, date range, body text, has-attachment      |Yes      |
|`get_thread`    |Get all emails in a conversation thread                               |Yes      |

All tools annotated with `readOnlyHint: true`, `destructiveHint: false`.

## Infrastructure

### Tailscale Funnel

```bash
tailscale funnel --bg 8000
```

- Provides automatic TLS via Let’s Encrypt
- Generates stable HTTPS URL (e.g., `https://eviebot.tail[xxxxx].ts.net`)
- `--bg` flag persists across terminal disconnects and reboots
- Funnel can only listen on ports 443, 8443, and 10000 (maps to local port 8000)

**Bandwidth:** Not a concern for this use case. Funnel traffic routes through Tailscale’s DERP relay servers (not peer-to-peer like normal Tailscale traffic). Tailscale imposes undisclosed bandwidth limits, but these are generous — community reports confirm 4K video streaming works without hitting them. MCP tool calls are tiny JSON payloads (a few KB per request/response). Even a full email body is small relative to the available bandwidth. The 10–80ms latency overhead through the relay is negligible for conversational AI tool calls.

### launchd Service

The MCP server runs as a macOS launchd service:

- **Auto-start on boot** (`RunAtLoad: true`)
- **Auto-restart on failure** (`KeepAlive: { SuccessfulExit: false }`)
- **Environment variables** in the plist: `FASTMAIL_API_TOKEN`, `FASTMAIL_BASE_URL`, Cognito issuer URL, JWKS URI, audience, resource URL. (launchd does not source shell profiles, so all env vars must be explicitly set in the plist.)
- **Logging:** stdout/stderr redirected to log files (e.g., `~/Library/Logs/fastmail-mcp/server.log`)
- **Working directory:** Set to the project directory

### Tailscale Funnel runs separately

Tailscale Funnel with `--bg` is its own persistent process managed by Tailscale, not by launchd. The two services (MCP server via launchd, Funnel via `--bg`) are independent.

## Setup Checklist (Evie completes before engaging Claude Code)

These are manual steps Evie completes before handing this plan to Claude Code.

### On Eviebot

- [ ] **Test Tailscale Funnel:**
  
  ```bash
  python3 -m http.server 8000 &
  tailscale funnel 8000
  # Note the HTTPS URL it prints
  ```
- [ ] **From iPhone browser:** visit the HTTPS URL — confirm you see the Python directory listing
- [ ] **Clean up:** `tailscale funnel off && kill %1`
- [ ] **Confirm Python 3.12+:** `python3 --version`
- [ ] **Confirm pip:** `python3 -m pip --version`
- [ ] **Confirm AWS CLI:** `aws sts get-caller-identity`
- [ ] **Note AWS region:** `aws configure get region`
- [ ] **Confirm git:** `git config user.name && git config user.email`
- [ ] **Confirm Fastmail token:** `echo $FASTMAIL_API_TOKEN | head -c 10`

### On GitHub

- [ ] Create new **private** repo: `EvieHwang/fastmail-mcp-server`
- [ ] Do NOT initialize with README, .gitignore, or license (Claude Code will set up the project)

### On Route 53 (AWS Console)

- [ ] Confirm `evehwang.com` hosted zone exists
- [ ] Note the **Hosted Zone ID**
- [ ] Check if an A record exists for `evehwang.com` (the root domain) — if not, note it; Claude Code will create one

### Values to provide to Claude Code

|Item                                    |Value                 |
|----------------------------------------|----------------------|
|Tailscale Funnel URL                    |`https://_____.ts.net`|
|AWS region                              |                      |
|Python version                          |                      |
|Route 53 hosted zone ID for evehwang.com|                      |
|Root A record exists for evehwang.com?  |Yes / No              |

## Implementation Sequence (for Claude Code)

### Phase 1: Set Up Cognito

1. Request ACM certificate for `auth.evehwang.com` in **us-east-1**
1. Validate certificate via Route 53 DNS (automated with CLI)
1. Wait for certificate to be issued (~3 minutes with Route 53)
1. Create Cognito User Pool (in Evie’s primary AWS region)
1. Create Evie’s user account in the pool
1. Create App Client with fixed Client ID/Secret, PKCE, authorization code grant
1. Configure redirect URIs for claude.ai callbacks
1. Configure custom domain `auth.evehwang.com` with the ACM certificate
1. Create Route 53 alias record for `auth.evehwang.com` → Cognito’s CloudFront distribution
1. Wait for domain propagation (up to 1 hour for CloudFront)
1. **Verify:** `curl -I https://auth.evehwang.com/oauth2/authorize` returns a response (not a DNS error)

### Phase 2: Build MCP Server

1. Clone the GitHub repo, initialize project structure
1. Set up Python venv with FastMCP and dependencies
1. Implement TokenVerifier for Cognito JWT validation
1. Configure AuthSettings and Protected Resource Metadata
1. Implement JMAP client (session discovery, auth with Fastmail API token)
1. Implement email tools: `list_mailboxes`, `list_emails`, `get_email`, `search_emails`, `get_thread`
1. Write basic tests
1. **Test locally** with MCP Inspector
1. Push to GitHub

### Phase 3: Deploy and Connect

1. Create launchd plist with all environment variables, logging, auto-restart
1. Load and start launchd service: `launchctl load ~/Library/LaunchAgents/com.evie.fastmail-mcp.plist`
1. Verify server is running: `curl http://localhost:8000/mcp`
1. Enable Tailscale Funnel: `tailscale funnel --bg 8000`
1. **Test end-to-end:** curl the Funnel URL from outside the tailnet
1. **Evie does manually:** Add connector in claude.ai Settings → Connectors → Add custom connector
1. **Evie does manually:** Enter Funnel URL, then Advanced Settings → Client ID + Client Secret
1. **Evie does manually:** Complete one-time OAuth login at auth.evehwang.com
1. **Test:** Open Claude iOS app and ask it to check your email

## Constraints

- Evie is a PM, not a software engineer — Claude Code should provide clear explanations when asking for decisions or input, and should not assume deep programming knowledge
- All development and deployment happens on this machine (Eviebot)
- Staying within ToS for all services (Anthropic, Tailscale, Fastmail, AWS)

## Technical References

- Anthropic MCP connector docs: https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers
- MCP Authorization Specification (2025-06-18): https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- FastMCP Token Verification: https://gofastmcp.com/servers/auth/token-verification
- FastMCP HTTP Deployment: https://gofastmcp.com/deployment/http
- empires-security/mcp-oauth2-aws-cognito: https://github.com/empires-security/mcp-oauth2-aws-cognito
- Cognito-based MCP Servers (Sodkiewicz): https://sodkiewiczm.medium.com/cognito-based-mcp-servers-a426ca2544c5
- Cognito custom domain setup: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-add-custom-domain.html
- MCP Inspector: https://github.com/modelcontextprotocol/inspector
- Tailscale Funnel docs: https://tailscale.com/kb/1223/funnel
- FastMCP Python SDK: https://github.com/jlowin/fastmcp
- MCP Python SDK (official): https://github.com/modelcontextprotocol/python-sdk
- Aaron Parecki’s MCP OAuth explainer: https://aaronparecki.com/2025/04/03/15/oauth-for-model-context-protocol
- Fastmail JMAP samples: https://github.com/fastmail/JMAP-Samples
- MadLlama25/fastmail-mcp (JMAP reference): https://github.com/MadLlama25/fastmail-mcp
