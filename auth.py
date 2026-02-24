import os

import httpx
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.auth import cors_middleware
from fastmcp.server.auth.providers.jwt import JWTVerifier
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# Cache the Cognito OIDC metadata so we don't fetch it on every request
_cognito_metadata_cache: dict | None = None


async def _get_cognito_metadata() -> dict:
    """Fetch and cache Cognito OIDC metadata, augmented for OAuth AS discovery."""
    global _cognito_metadata_cache
    if _cognito_metadata_cache:
        return _cognito_metadata_cache

    issuer_url = os.environ["COGNITO_ISSUER_URL"]
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{issuer_url}/.well-known/openid-configuration")
        r.raise_for_status()
        metadata = r.json()

    # Add PKCE support (Cognito supports S256 but doesn't advertise it)
    metadata["code_challenge_methods_supported"] = ["S256"]
    # Add response type required by MCP
    if "code" not in metadata.get("response_types_supported", []):
        metadata.setdefault("response_types_supported", []).append("code")

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


class CognitoAuthProvider(RemoteAuthProvider):
    """RemoteAuthProvider adapted for Cognito.

    Fixes two compatibility issues:
    1. Claude.ai requests /.well-known/oauth-protected-resource (no /mcp suffix)
       but FastMCP registers it at /.well-known/oauth-protected-resource/mcp.
    2. Cognito doesn't serve /.well-known/oauth-authorization-server (RFC 8414),
       only /.well-known/openid-configuration. We proxy the OIDC metadata at the
       RFC 8414 path so claude.ai can discover the OAuth endpoints.
    """

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)

        if self.base_url and mcp_path:
            resource_url = self._get_resource_url(mcp_path)
            if resource_url:
                # Root well-known path (without /mcp suffix)
                metadata = ProtectedResourceMetadata(
                    resource=resource_url,
                    authorization_servers=self.authorization_servers,
                    scopes_supported=(
                        self._scopes_supported
                        if self._scopes_supported is not None
                        else self.token_verifier.scopes_supported
                    ),
                    resource_name=self.resource_name,
                    resource_documentation=self.resource_documentation,
                )
                handler = ProtectedResourceMetadataHandler(metadata)
                routes.append(
                    Route(
                        "/.well-known/oauth-protected-resource",
                        endpoint=cors_middleware(handler.handle, ["GET", "OPTIONS"]),
                        methods=["GET", "OPTIONS"],
                    )
                )

        # Proxy Cognito OIDC metadata as OAuth AS metadata
        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=_oauth_authorization_server_metadata,
                methods=["GET", "OPTIONS"],
            )
        )

        return routes


def create_auth_provider() -> CognitoAuthProvider:
    """Create the auth provider for Cognito JWT verification."""
    issuer_url = os.environ["COGNITO_ISSUER_URL"]
    base_url = os.environ["MCP_RESOURCE_URL"]

    token_verifier = JWTVerifier(
        jwks_uri=os.environ["COGNITO_JWKS_URI"],
        issuer=issuer_url,
        audience=os.environ["COGNITO_AUDIENCE"],
    )

    # Point authorization_servers to our own server so claude.ai fetches
    # /.well-known/oauth-authorization-server from us (where we proxy Cognito).
    return CognitoAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(base_url)],
        base_url=base_url,
    )
