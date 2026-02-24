# Fastmail MCP Server

A remote [MCP](https://modelcontextprotocol.io/) server that gives Claude (claude.ai and iOS app) read-only access to a Fastmail email account via [JMAP](https://jmap.io/). Authenticates with OAuth 2.1 using AWS Cognito, runs as a macOS launchd service, and is exposed to the internet via Tailscale Funnel.

## Features

- **5 email tools:** list mailboxes, list emails, get email, search emails, get thread
- **OAuth 2.1 authentication** via AWS Cognito with PKCE (S256)
- **Streamable HTTP transport** (MCP spec 2025-06-18)
- **Always-on** via macOS launchd with auto-restart
- **Internet-accessible** via Tailscale Funnel (automatic HTTPS)

## Architecture

```
claude.ai ──HTTPS──▶ Tailscale Funnel (:443)
                         │
                         ▼
                    FastMCP server (localhost:8000)
                    ├── OAuth: validates JWT from Cognito
                    └── Tools: calls Fastmail JMAP API
```

The server acts as an OAuth 2.1 **resource server**. AWS Cognito is the **authorization server**. The server proxies Cognito's OIDC metadata as RFC 8414 OAuth AS metadata and provides a Dynamic Client Registration endpoint — both required by claude.ai.

## Tools

| Tool | Description |
|------|-------------|
| `list_mailboxes` | List all mailboxes with message counts |
| `list_emails` | List emails in a mailbox (paginated) with sender, subject, date, snippet |
| `get_email` | Get full email content by ID |
| `search_emails` | Search by sender, subject, date range, body text, has-attachment |
| `get_thread` | Get all emails in a conversation thread |

All tools are annotated as read-only and non-destructive.

## Prerequisites

- macOS with Python 3.12+
- [Tailscale](https://tailscale.com/) with [Funnel](https://tailscale.com/kb/1223/funnel) enabled
- AWS account (for Cognito)
- Fastmail account with an [API token](https://www.fastmail.com/help/clients/apppassword.html)
- Domain with DNS on Route 53 (for Cognito custom domain)

## Setup

### 1. AWS Cognito

Create a Cognito User Pool with:
- Email-based login
- A single user account
- **Two App Clients:**
  - A **confidential** client (with secret) — for clients that support `client_secret_basic`/`client_secret_post`
  - A **public** client (no secret) — for clients like claude.ai that use `token_endpoint_auth_method: none`
- Redirect URIs: `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`
- Allowed OAuth scopes: `openid`, `email`
- Custom domain (e.g. `auth.yourdomain.com`) with ACM certificate

### 2. Environment Variables

```bash
# Fastmail
FASTMAIL_API_TOKEN=fmu1-...
FASTMAIL_BASE_URL=https://api.fastmail.com

# Cognito
COGNITO_ISSUER_URL=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXX
COGNITO_JWKS_URI=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXX/.well-known/jwks.json
COGNITO_AUDIENCE=<confidential-client-id>
COGNITO_PUBLIC_CLIENT_ID=<public-client-id>
COGNITO_CLIENT_SECRET=<confidential-client-secret>

# Server
MCP_RESOURCE_URL=https://your-tailscale-funnel-url
```

### 3. Install and Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Local development
python server.py
```

### 4. Deploy

Set up Tailscale Funnel on port 443:

```bash
tailscale funnel --bg --https=443 http://127.0.0.1:8000
```

Create a launchd plist (see `com.evie.fastmail-mcp.plist` as a template) with all environment variables set explicitly, then load it:

```bash
cp com.evie.fastmail-mcp.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.evie.fastmail-mcp.plist
```

### 5. Connect from claude.ai

In claude.ai: Settings > MCP Connectors > Add > enter `https://your-funnel-url/mcp`

You'll be redirected to your Cognito login page. After authenticating, Claude can access your email.

## Claude.ai OAuth Compatibility

Getting OAuth to work with claude.ai required solving several compatibility issues. These lessons apply to **any** remote MCP server using an external OAuth provider with claude.ai:

### Requirements

- **Standard port 443 only.** Claude.ai's infrastructure cannot reach non-standard ports (8443, 8080, etc.).
- **RFC 8414 OAuth AS metadata** at `/.well-known/oauth-authorization-server`. If your auth provider only serves OIDC metadata (like Cognito), you need to proxy it at this path.
- **Dynamic Client Registration (DCR)** is mandatory. Claude.ai POSTs to `registration_endpoint` to get client credentials. You need an endpoint that returns your pre-registered App Client ID (and secret for confidential clients).
- **Required metadata fields** that Cognito doesn't advertise:
  - `code_challenge_methods_supported: ["S256"]`
  - `grant_types_supported: ["authorization_code", "refresh_token"]`
  - `token_endpoint_auth_methods_supported: ["client_secret_basic", "client_secret_post", "none"]`
  - `registration_endpoint` pointing to your DCR endpoint
- **Issuer must match `authorization_servers`** (RFC 8414). If you proxy AS metadata, override `issuer` to your server's URL.

### Gotchas

- **Pydantic `AnyHttpUrl` trailing slash.** `AnyHttpUrl("https://example.com")` serializes to `"https://example.com/"`, causing clients to construct `https://example.com//.well-known/...` (double slash) which 404s. Build metadata JSON dicts manually with `str(url).rstrip("/")`, or add middleware to normalize double slashes.
- **Cognito access tokens don't have an `aud` claim.** They use `client_id` instead. If you have multiple App Clients, you may need to skip audience validation and rely on issuer validation alone.
- **Two App Clients needed.** Claude.ai registers as a public client (`token_endpoint_auth_method: none`), so you need a client without a secret. Your DCR endpoint should return the appropriate client based on the requested auth method.
- **FastMCP's `RemoteAuthProvider`** registers `/.well-known/oauth-protected-resource` at a path-suffixed URL (e.g. `/.well-known/oauth-protected-resource/mcp`). Claude.ai also needs it at the root path — add a custom route.

### OAuth Flow (what claude.ai does)

1. `POST /mcp` → 401 Unauthorized
2. `GET /.well-known/oauth-protected-resource/mcp` → discovers `authorization_servers`
3. `GET /.well-known/oauth-authorization-server` → discovers endpoints + `registration_endpoint`
4. `POST /oauth/register` (DCR) → gets `client_id`
5. Redirects user to Cognito's `authorization_endpoint` with PKCE
6. User logs in, Cognito redirects back with auth code
7. `POST` to Cognito's `token_endpoint` → gets access + refresh tokens
8. `POST /mcp` with `Authorization: Bearer <token>` → authenticated MCP session

## Project Structure

```
server.py           # FastMCP server setup, auth config, tool registration
auth.py             # Cognito OAuth provider (metadata proxy, DCR, JWT verification)
jmap_client.py      # JMAP session discovery and API calls
tools.py            # MCP tool implementations
tests/
  test_tools.py     # Unit tests with mocked JMAP responses
```

## Documentation

- [Specification](docs/spec.md) — Requirements and scope
- [Implementation Plan](docs/plan.md) — Architecture and phased build plan

## License

MIT
