from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from cerno.config import Settings

GOOGLE_DISCOVERY_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)


def build_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
