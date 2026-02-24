import os

import httpx


class JMAPClient:
    """Fastmail JMAP client with session caching."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("FASTMAIL_BASE_URL", "https://api.fastmail.com")
        self.token = os.environ["FASTMAIL_API_TOKEN"]
        self.api_url: str | None = None
        self.account_id: str | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _discover(self, client: httpx.AsyncClient) -> None:
        """Fetch JMAP session and cache account ID and API URL."""
        r = await client.get(
            f"{self.base_url}/.well-known/jmap",
            headers=self._headers(),
            follow_redirects=True,
        )
        r.raise_for_status()
        session = r.json()
        self.api_url = session["apiUrl"]
        self.account_id = next(iter(session["accounts"]))

    async def call(self, methods: list[list]) -> list:
        """Make a JMAP API call. Re-discovers session on failure."""
        async with httpx.AsyncClient() as client:
            if not self.api_url:
                await self._discover(client)

            body = {
                "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                "methodCalls": methods,
            }
            r = await client.post(
                self.api_url,
                json=body,
                headers=self._headers(),
            )

            # Re-discover on session errors
            if r.status_code in (400, 401, 403):
                self.api_url = None
                self.account_id = None
                await self._discover(client)
                r = await client.post(
                    self.api_url,
                    json=body,
                    headers=self._headers(),
                )

            r.raise_for_status()
            return r.json()["methodResponses"]
