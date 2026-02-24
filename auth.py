import os

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl


def create_auth_provider() -> RemoteAuthProvider:
    """Create the auth provider for Cognito JWT verification."""
    issuer_url = os.environ["COGNITO_ISSUER_URL"]

    token_verifier = JWTVerifier(
        jwks_uri=os.environ["COGNITO_JWKS_URI"],
        issuer=issuer_url,
        audience=os.environ["COGNITO_AUDIENCE"],
    )

    return RemoteAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(issuer_url)],
        base_url=os.environ["MCP_RESOURCE_URL"],
    )
