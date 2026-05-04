import discord
from config import EMBED_COLOR


def now_playing_embed(track: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Now Playing",
        description=f"**{track['title']}**\n{track.get('artist', 'Unknown')}",
        color=EMBED_COLOR,
    )
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    duration = track.get("duration", 0)
    minutes, seconds = divmod(duration, 60)
    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}")
    if track.get("requestedBy"):
        embed.set_footer(text=f"Requested by {track['requestedBy']}")
    return embed


def queue_embed(queue: list, current_track: dict = None, page: int = 0, per_page: int = 10) -> discord.Embed:
    embed = discord.Embed(title="Queue", color=EMBED_COLOR)
    if current_track:
        embed.add_field(
            name="Now Playing",
            value=f"**{current_track['title']}** — {current_track.get('artist', 'Unknown')}",
            inline=False,
        )
    if not queue:
        embed.description = "Queue is empty."
        return embed
    start = page * per_page
    end = start + per_page
    lines = []
    for i, track in enumerate(queue[start:end], start=start + 1):
        duration = track.get("duration", 0)
        minutes, seconds = divmod(duration, 60)
        lines.append(f"`{i}.` **{track['title']}** — {minutes}:{seconds:02d}")
    embed.description = "\n".join(lines)
    total_pages = (len(queue) - 1) // per_page + 1
    embed.set_footer(text=f"Page {page + 1}/{total_pages} | {len(queue)} tracks")
    return embed


def session_embed(code: str, web_url: str) -> discord.Embed:
    direct_link = f"{web_url.rstrip('/')}/dashboard/{code}"
    embed = discord.Embed(
        title="🎵 Jacky Music Session Started",
        description=(
            f"## Session Code\n"
            # Triple-backtick code block: monospace, visually prominent,
            # and shows a hover copy button on desktop Discord clients.
            f"```\n{code}\n```\n"
            f"**[Open Web Player]({direct_link})**\n\n"
            f"Control playback, search songs, and manage the queue from your browser."
        ),
        color=EMBED_COLOR,
    )
    embed.add_field(name="Direct Link", value=direct_link, inline=False)
    return embed


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="Error",
        description=message,
        color=0xFF0000,
    )


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(
        description=message,
        color=EMBED_COLOR,
    )
