"""Google sign-in through the OpenID Connect userinfo endpoint.

httpx-oauth's stock Google client looks the user up through the People API,
which is a separate Google Cloud API that must be enabled on the project and
identifies accounts by a `people/…` resource name. The OpenID userinfo
endpoint needs nothing beyond the `openid email profile` scopes the consent
screen already grants, and returns the stable `sub` identifier plus whether
the email address is verified.
"""

from __future__ import annotations

from typing import Any, cast

from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.exceptions import GetIdEmailError, GetProfileError

USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
SCOPES = ["openid", "email", "profile"]


class GoogleOpenIdOAuth2(GoogleOAuth2):
    def __init__(self, client_id: str, client_secret: str) -> None:
        super().__init__(client_id, client_secret, scopes=SCOPES)

    async def get_profile(self, token: str) -> dict[str, Any]:
        async with self.get_httpx_client() as client:
            response = await client.get(
                USERINFO_ENDPOINT,
                headers={**self.request_headers, "Authorization": f"Bearer {token}"},
            )
            if response.status_code >= 400:
                raise GetProfileError(response=response)
            return cast(dict[str, Any], response.json())

    async def get_id_email(self, token: str) -> tuple[str, str | None]:
        try:
            profile = await self.get_profile(token)
        except GetProfileError as error:
            raise GetIdEmailError(response=error.response) from error
        return id_and_email(profile)


def id_and_email(profile: dict[str, Any]) -> tuple[str, str | None]:
    subject = profile.get("sub")
    if not isinstance(subject, str) or not subject:
        raise GetIdEmailError(response=None)
    email = profile.get("email")
    # An unverified address must not be trusted for account association: a
    # Google account can carry any email it has not proven it owns.
    if not isinstance(email, str) or not email or profile.get("email_verified") is not True:
        return subject, None
    return subject, email
