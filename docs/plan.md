# Implementation Plan

## Tech Stack

| Component | Choice | Why |
|-----------|--------|-----|
| MCP framework | **FastMCP** (Python) | Built-in Streamable HTTP transport, `TokenVerifier` for JWT validation, automatic Protected Resource Metadata (RFC 9728). No adapter or middleware needed. |
| Language | **Python 3.12+** | FastMCP is Python-native. JMAP is simple HTTP/JSON, straightforward with httpx. Matches Eviebot's environment. |
| HTTP client | **httpx** | Async-capable, used internally by FastMCP. Use it for JMAP calls too. |
| Auth server | **AWS Cognito** | Managed OAuth 2.1 provider. FastMCP docs recommend external IdP for production. Single user pool, minimal config. |
| Client registration | **Pre-registered** App Client | Known bugs with Dynamic Client Registration in claude.ai (Dec 2025 -- Jan 2026). Pre-registered Client ID/Secret entered in claude.ai Advanced Settings. |
| HTTPS exposure | **Tailscale Funnel** | Automatic TLS via Let's Encrypt, stable public URL, no port forwarding or reverse proxy. Already installed on Eviebot. |
| Process management | **launchd** | Native macOS. Auto-start on boot, auto-restart on failure. |

## Project Structure

```
fastmail-mcp-server/
  server.py                       # FastMCP server setup, auth config, tool registration
  jmap_client.py                  # JMAP session discovery and API calls
  tools.py                        # MCP tool implementations (5 email tools)
  auth.py                         # Cognito TokenVerifier subclass
  requirements.txt
  .envrc                          # direnv: activate venv + load .env
  .env.example                    # Template for required env vars
  com.evie.fastmail-mcp.plist     # launchd service definition
  tests/
    test_tools.py
  docs/
    spec.md
    project-plan.md
    plan.md                       # This file
```

Files stay flat per project conventions. Each file has a single responsibility and should remain well under 300 lines.

### Module responsibilities

**server.py** -- Entry point. Creates the `FastMCP` instance, configures `AuthSettings` with Cognito issuer URL and resource metadata, imports and registers tools from `tools.py`, starts the HTTP server on port 8000.

**auth.py** -- Subclass of FastMCP's `TokenVerifier`. Validates Cognito JWTs by checking signature against the regional JWKS endpoint, verifying issuer, audience, and expiry. Returns an `AccessToken` with user identity.

**jmap_client.py** -- Manages JMAP session discovery (`/.well-known/jmap` on Fastmail), caches session data (account ID, API URL), and provides a method to make authenticated JMAP method calls. Uses the `FASTMAIL_API_TOKEN` for Fastmail auth (separate from the OAuth token that protects the MCP server).

**tools.py** -- Implements the five MCP tools as functions decorated with `@mcp.tool()`. Each tool calls into `jmap_client.py` to execute JMAP methods and formats the response. All tools annotated `readOnlyHint: true`, `destructiveHint: false`.

## Configuration

Environment variables (all required):

| Variable | Description | Example |
|----------|-------------|---------|
| `FASTMAIL_API_TOKEN` | Fastmail API bearer token | `fmu1-...` |
| `FASTMAIL_BASE_URL` | Fastmail JMAP base URL | `https://api.fastmail.com` |
| `COGNITO_ISSUER_URL` | Cognito issuer (regional endpoint) | `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXX` |
| `COGNITO_JWKS_URI` | JWKS endpoint for token validation | `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXX/.well-known/jwks.json` |
| `COGNITO_AUDIENCE` | App Client ID (audience claim in JWT) | `abc123def456` |
| `MCP_RESOURCE_URL` | Public Tailscale Funnel URL | `https://eviebot.tail12345.ts.net` |

These are set in `.env` for local development and explicitly in the launchd plist for production (launchd does not source shell profiles).

## Implementation Phases

### Phase 1: Cognito Setup (AWS CLI)

**Depends on:** Human prerequisites completed (spec.md checklist). Evie provides: AWS region, Route 53 hosted zone ID, whether root A record exists.

**Steps:**

1. If no root A record for `evehwang.com` exists, create a placeholder (Cognito requires it to resolve).
2. Request ACM certificate for `auth.evehwang.com` in **us-east-1** (required regardless of User Pool region).
3. Create Route 53 validation record for the certificate.
4. Wait for certificate issuance (~3 minutes with DNS validation).
5. Create Cognito User Pool with email-based login.
6. Create Evie's user account in the pool.
7. Create App Client with: authorization code grant, PKCE (S256), fixed Client ID/Secret, `openid` and `email` scopes.
8. Set redirect URIs: `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`.
9. Configure custom domain `auth.evehwang.com` using the ACM certificate.
10. Create Route 53 alias record: `auth.evehwang.com` -> Cognito's CloudFront distribution.
11. Wait for CloudFront propagation (up to 1 hour).
12. Verify: `curl -I https://auth.evehwang.com/oauth2/authorize` returns a response.

**Outputs:** User Pool ID, App Client ID, App Client Secret, issuer URL, JWKS URI. These become env vars for Phase 2.

### Phase 2: Build MCP Server

**Depends on:** Phase 1 outputs (Cognito identifiers for auth config).

**Steps, in dependency order:**

1. **Project init** -- Create venv, install dependencies (`fastmcp`, `httpx`, `pyjwt`, `cryptography`), create `.envrc`, `.env.example`, `requirements.txt`.
2. **auth.py** -- Implement `CognitoTokenVerifier(TokenVerifier)`. Fetches JWKS from Cognito regional endpoint, validates JWT signature, issuer, audience, expiry. This is the foundation -- nothing works without valid auth.
3. **jmap_client.py** -- Implement `JMAPClient` class. Session discovery via `GET {base_url}/.well-known/jmap`, cache account ID and API URL, provide `call(methods)` for JMAP method invocations. Test independently with a simple JMAP call (e.g., `Mailbox/get`).
4. **tools.py** -- Implement the five tools:
   - `list_mailboxes` -- `Mailbox/get`, return name + message counts
   - `list_emails` -- `Email/query` + `Email/get` with mailbox filter, paginated
   - `get_email` -- `Email/get` by ID, full content including body
   - `search_emails` -- `Email/query` with filter conditions (from, subject, date range, body, hasAttachment)
   - `get_thread` -- `Thread/get` + `Email/get` for all messages in thread
5. **server.py** -- Wire it all together: create `FastMCP` instance, configure `AuthSettings` with Cognito issuer and resource URL, register tools, set host/port.
6. **Tests** -- Basic tests for tool logic (mock JMAP responses). Test that tools format output correctly and handle edge cases (empty mailbox, no results, etc.).
7. **Local testing** -- Run server, test with MCP Inspector to verify tools work and auth flow is correct.
8. **Push to GitHub.**

### Phase 3: Deploy and Connect

**Depends on:** Phase 2 complete (working server).

1. Create `com.evie.fastmail-mcp.plist` with:
   - All env vars from `.env` set explicitly in `EnvironmentVariables`
   - `RunAtLoad: true`
   - `KeepAlive: { SuccessfulExit: false }` (restart on crash, not on clean exit)
   - stdout/stderr to `~/Library/Logs/fastmail-mcp/`
   - Working directory set to project root
2. Load service: `launchctl load ~/Library/LaunchAgents/com.evie.fastmail-mcp.plist`
3. Verify: `curl http://localhost:8000/mcp` returns a valid response.
4. Enable Tailscale Funnel: `tailscale funnel --bg 8000`
5. End-to-end test: `curl` the Funnel URL from outside the tailnet.
6. **Human steps** (Evie does manually, tracked as GitHub issues):
   - Add connector in claude.ai: Settings -> Connectors -> Add custom connector
   - Enter Funnel URL as the MCP server URL
   - Advanced Settings: enter Client ID and Client Secret
   - Complete one-time OAuth login at `auth.evehwang.com`
   - Test from Claude iOS app: "Check my email"

## Key Technical Decisions

These decisions are documented in detail in `docs/project-plan.md`. Summary:

- **Build from scratch with FastMCP** rather than forking an existing repo. None of the five evaluated repos support Streamable HTTP + OAuth + email read/search together.
- **Cognito over self-hosted OAuth.** FastMCP recommends external IdP for production. Cognito is managed, handles hosted UI, token issuance, and JWKS. No OAuth server code to write or maintain.
- **Pre-registered client over DCR.** Known claude.ai bugs with Dynamic Client Registration. Pre-registration is also the MCP spec's highest-priority method.
- **Tailscale Funnel over direct port exposure.** Automatic TLS, stable URL, no firewall or router config. Already installed on Eviebot.
- **JWKS validation against Cognito regional endpoint**, NOT the custom domain. The custom domain (`auth.evehwang.com`) is only for the hosted UI and OAuth endpoints. OIDC discovery and JWKS live at `cognito-idp.{region}.amazonaws.com/{pool-id}/`.

## Risks and Open Questions

1. **Cognito custom domain propagation.** CloudFront distribution can take up to 1 hour. Phase 1 may require a wait step.
2. **FastMCP TokenVerifier API.** The exact subclassing interface needs to be verified against current FastMCP docs at implementation time. The framework is actively developed.
3. **JMAP session caching.** Fastmail's JMAP session endpoint returns URLs that may change. The client should re-discover if a request fails with a session error.
4. **Token refresh.** Claude handles refresh tokens automatically, but if Cognito's refresh token expires (30 days of inactivity), Evie would need to re-authenticate. This is acceptable for V1.
5. **Tailscale Funnel bandwidth limits.** Undisclosed by Tailscale, but community reports confirm generous limits. MCP payloads are tiny JSON. Not a real risk, but noted.

## Human Steps

Two sets of manual steps bracket the implementation work:

**Before implementation** (spec.md prerequisites): Evie verifies Tailscale Funnel, confirms tooling, provides AWS region and Route 53 hosted zone ID. Tracked via GitHub issues.

**After deployment** (Phase 3 final steps): Evie adds the MCP connector in claude.ai, enters credentials, completes OAuth flow. Tracked via GitHub issues.
