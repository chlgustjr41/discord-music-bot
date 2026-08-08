"""Discord OAuth2 (authorization-code, identify scope). Docs:
https://discord.com/developers/docs/topics/oauth2"""

from typing import Any

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
ME_URL = "https://discord.com/api/users/@me"


class OAuthError(Exception):
    pass


class DiscordOAuth:
    def __init__(self, http: Any, client_id: str, client_secret: str,
                 redirect_uri: str) -> None:
        self.http = http  # aiohttp.ClientSession
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def authorize_url(self, state: str) -> str:
        from urllib.parse import urlencode
        return AUTHORIZE_URL + "?" + urlencode({
            "client_id": self.client_id, "response_type": "code",
            "redirect_uri": self.redirect_uri, "scope": "identify",
            "state": state, "prompt": "none",
        })

    async def exchange_code(self, code: str) -> str:
        async with self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.redirect_uri,
        }) as resp:
            if resp.status != 200:
                raise OAuthError(f"token exchange failed: {resp.status}")
            return (await resp.json())["access_token"]

    async def fetch_identity(self, access_token: str) -> dict:
        async with self.http.get(
            ME_URL, headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            if resp.status != 200:
                raise OAuthError(f"identity fetch failed: {resp.status}")
            data = await resp.json()
            return {"id": str(data["id"]), "username": data.get("username", "")}
