"""Where a browser should point for a session.

Shared by GET /control/dashboard-url (the Dashboard key) and the voice
`open_dashboard` action, so the two open the same page by construction rather
than by two implementations that happen to agree.
"""


def _base(web_app_url: str) -> str:
    return web_app_url.rstrip("/")


def session_url(web_app_url: str, code: str) -> str:
    return f"{_base(web_app_url)}/dashboard/{code}"


def entry_url(web_app_url: str) -> str:
    return f"{_base(web_app_url)}/app"
