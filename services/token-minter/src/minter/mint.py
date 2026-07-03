"""Mint poToken/visitorData from pot-provider, push to Lavalink, persist for cold starts."""

import asyncio
import logging
import os
import re

import aiohttp

log = logging.getLogger("minter")

# Values are injected into a shell-sourced env file and sed'd into YAML at
# lavalink cold start; restrict to base64/URL-safe charset so neither can break.
# '%' is required: ADR-0004 pins contentBinding as URL-encoded (e.g. ...%3D%3D).
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9+/=%_-]+$")
_RETRY_SECONDS = 300


class MintError(RuntimeError):
    """A mint cycle step failed; the loop retries after a delay."""


async def fetch_tokens(session: aiohttp.ClientSession, provider_url: str) -> tuple[str, str]:
    # Contract pinned by ADR-0004: empty body -> provider generates fresh
    # visitor data and a matching poToken. Response is camelCase; the
    # visitorData arrives as "contentBinding".
    async with session.post(f"{provider_url}/get_pot", json={}) as resp:
        if resp.status != 200:
            raise MintError(f"pot-provider returned HTTP {resp.status}")
        data = await resp.json(content_type=None)
    po_token = data.get("poToken")
    visitor_data = data.get("contentBinding")
    if not po_token or not visitor_data:
        raise MintError(f"pot-provider response missing fields, got keys {sorted(data)}")
    return po_token, visitor_data


async def push_to_lavalink(
    session: aiohttp.ClientSession,
    lavalink_url: str,
    password: str,
    po_token: str,
    visitor_data: str,
) -> None:
    async with session.post(
        f"{lavalink_url}/youtube",
        json={"poToken": po_token, "visitorData": visitor_data},
        headers={"Authorization": password},
    ) as resp:
        if resp.status != 204:
            raise MintError(f"lavalink rejected token push: HTTP {resp.status}")


def write_tokens_file(path: str, po_token: str, visitor_data: str) -> None:
    for name, value in (("po_token", po_token), ("visitor_data", visitor_data)):
        if not _SAFE_VALUE.match(value):
            raise MintError(f"{name} contains characters unsafe for env-file injection")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="ascii") as f:
        f.write(f"POT_TOKEN={po_token}\nPOT_VISITOR_DATA={visitor_data}\n")
    os.replace(tmp, path)


async def run(settings, stop: asyncio.Event) -> None:
    """Mint immediately on start, then every refresh_hours; retry failures after 5 min."""
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while not stop.is_set():
            try:
                po_token, visitor_data = await fetch_tokens(session, settings.pot_provider_url)
                await push_to_lavalink(
                    session,
                    settings.lavalink_url,
                    settings.lavalink_password,
                    po_token,
                    visitor_data,
                )
                write_tokens_file(settings.tokens_file, po_token, visitor_data)
                log.info("minted and pushed fresh poToken (visitorData %.12s...)", visitor_data)
            except (MintError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("mint cycle failed: %s — retrying in %ss", exc, _RETRY_SECONDS)
                await _interruptible_sleep(stop, _RETRY_SECONDS)
                continue
            await _interruptible_sleep(stop, settings.refresh_hours * 3600)


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
