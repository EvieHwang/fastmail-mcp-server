import os
import re

import httpx
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.auth import cors_middleware
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

# Cache the Cognito OIDC metadata
_cognito_metadata_cache: dict | None = None

# Regex to collapse repeated slashes in URL paths (e.g. //foo → /foo)
_DOUBLE_SLASH_RE = re.compile(r"/{2,}")


async def _get_cognito_metadata() -> dict:
    """Fetch and cache Cognito OIDC metadata, augmented for MCP compatibility."""
    global _cognito_metadata_cache
    if _cognito_metadata_cache:
        return _cognito_metadata_cache

    issuer_url = os.environ["COGNITO_ISSUER_URL"]
    base_url = os.environ["MCP_RESOURCE_URL"]
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{issuer_url}/.well-known/openid-configuration")
        r.raise_for_status()
        metadata = r.json()

    # Fields required by MCP spec / claude.ai that Cognito doesn't advertise
    metadata["code_challenge_methods_supported"] = ["S256"]
    metadata["grant_types_supported"] = ["authorization_code", "refresh_token"]
    metadata["token_endpoint_auth_methods_supported"] = [
        "client_secret_basic",
        "client_secret_post",
        "none",
    ]
    if "code" not in metadata.get("response_types_supported", []):
        metadata.setdefault("response_types_supported", []).append("code")

    # DCR endpoint — claude.ai requires this
    metadata["registration_endpoint"] = f"{base_url}/oauth/register"

    # RFC 8414 requires issuer to match the authorization server URL.
    # Our protected resource metadata advertises our server as the auth server
    # (so we can proxy Cognito's metadata), so issuer must match.
    metadata["issuer"] = base_url

    _cognito_metadata_cache = metadata
    return metadata


async def _oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    """Serve Cognito OIDC metadata as OAuth AS metadata (RFC 8414)."""
    metadata = await _get_cognito_metadata()
    return JSONResponse(
        metadata,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        },
    )


async def _oauth_register(request: Request) -> JSONResponse:
    """Dynamic Client Registration endpoint.

    Claude.ai POSTs here to register itself as an OAuth client.
    Returns our pre-registered Cognito App Client credentials.
    """
    if request.method == "OPTIONS":
        return JSONResponse(
            {},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    body = await request.json()
    redirect_uris = body.get("redirect_uris", [])
    client_name = body.get("client_name", "unknown")
    token_auth_method = body.get("token_endpoint_auth_method", "none")

    # Return the public client (no secret) for public clients,
    # or the confidential client for clients that support secrets
    if token_auth_method == "none":
        client_id = os.environ["COGNITO_PUBLIC_CLIENT_ID"]
    else:
        client_id = os.environ["COGNITO_AUDIENCE"]

    response: dict = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": token_auth_method,
    }

    # Provide the secret for confidential clients
    client_secret = os.environ.get("COGNITO_CLIENT_SECRET")
    if client_secret and token_auth_method != "none":
        response["client_secret"] = client_secret

    return JSONResponse(
        response,
        status_code=201,
        headers={
            "Access-Control-Allow-Origin": "*",
        },
    )


class SlashNormalizationMiddleware:
    """ASGI middleware that collapses double slashes in URL paths.

    Pydantic's AnyHttpUrl adds a trailing slash to domain-only URLs, so
    authorization_servers becomes "https://host:8443/". Clients then construct
    "https://host:8443//.well-known/..." which 404s without this middleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and "//" in scope["path"]:
            scope["path"] = _DOUBLE_SLASH_RE.sub("/", scope["path"])
        await self.app(scope, receive, send)


def _protected_resource_metadata_json(base_url: str, mcp_path: str) -> dict:
    """Build protected resource metadata dict with clean URLs (no trailing slashes)."""
    return {
        "resource": f"{base_url}{mcp_path}",
        "authorization_servers": [base_url],
        "scopes_supported": [],
        "bearer_methods_supported": ["header"],
    }


class CognitoAuthProvider(RemoteAuthProvider):
    """RemoteAuthProvider adapted for Cognito + claude.ai.

    Handles four compatibility issues:
    1. Serves /.well-known/oauth-protected-resource at root (without /mcp suffix)
    2. Proxies Cognito OIDC metadata as OAuth AS metadata (RFC 8414)
    3. Provides a DCR endpoint that returns pre-registered Cognito client credentials
    4. Normalizes double-slash paths caused by AnyHttpUrl trailing slashes
    """

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)

        if self.base_url and mcp_path:
            base_url = str(self.base_url).rstrip("/")
            metadata = _protected_resource_metadata_json(base_url, mcp_path)

            async def _root_protected_resource(request: Request) -> JSONResponse:
                return JSONResponse(
                    metadata,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, OPTIONS",
                        "Cache-Control": "public, max-age=3600",
                    },
                )

            routes.append(
                Route(
                    "/.well-known/oauth-protected-resource",
                    endpoint=cors_middleware(
                        _root_protected_resource, ["GET", "OPTIONS"]
                    ),
                    methods=["GET", "OPTIONS"],
                )
            )

        # OAuth AS metadata (proxied from Cognito OIDC)
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=_oauth_authorization_server_metadata,
                methods=["GET", "OPTIONS"],
            )
        )

        # Dynamic Client Registration endpoint
        routes.append(
            Route(
                "/oauth/register",
                endpoint=_oauth_register,
                methods=["POST", "OPTIONS"],
            )
        )

        return routes


def create_auth_provider() -> CognitoAuthProvider:
    """Create the auth provider for Cognito JWT verification."""
    issuer_url = os.environ["COGNITO_ISSUER_URL"]
    base_url = os.environ["MCP_RESOURCE_URL"]

    # Don't validate audience — Cognito access tokens use 'client_id' claim
    # instead of 'aud', and we have two client IDs (public + confidential).
    # Issuer validation is sufficient for a single-user server.
    token_verifier = JWTVerifier(
        jwks_uri=os.environ["COGNITO_JWKS_URI"],
        issuer=issuer_url,
    )

    return CognitoAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(base_url)],
        base_url=base_url,
    )
