import os

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.auth import cors_middleware
from fastmcp.server.auth.providers.jwt import JWTVerifier
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.routing import Route


class CognitoAuthProvider(RemoteAuthProvider):
    """RemoteAuthProvider that also serves metadata at the root well-known path.

    Claude.ai requests /.well-known/oauth-protected-resource (no path suffix)
    but FastMCP registers it at /.well-known/oauth-protected-resource/mcp.
    This subclass adds a route at both paths.
    """

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)

        if self.base_url and mcp_path:
            resource_url = self._get_resource_url(mcp_path)
            if resource_url:
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

        return routes


def create_auth_provider() -> CognitoAuthProvider:
    """Create the auth provider for Cognito JWT verification."""
    issuer_url = os.environ["COGNITO_ISSUER_URL"]

    token_verifier = JWTVerifier(
        jwks_uri=os.environ["COGNITO_JWKS_URI"],
        issuer=issuer_url,
        audience=os.environ["COGNITO_AUDIENCE"],
    )

    return CognitoAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(issuer_url)],
        base_url=os.environ["MCP_RESOURCE_URL"],
    )
