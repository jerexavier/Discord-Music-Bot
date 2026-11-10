"""
Title: RemyBot, a Discord music bot that joins a voice channel and streams audio from YouTube.

Author: Jeremy Xavier Francisco

How it works:
    A user types `!play <song name or YouTube link>` in a text channel. The bot looks the
    song up with yt-dlp (in a background thread so that the bot stays responsive),
    gets a direct audio stream URL, gives it to FFmpeg, and inputs the decoded
    audio into the voice channel. Each Discord server has its own
    queue, so songs queued in one server never overlap with one another. When the
    queue runs empty the bot waits a few minutes, then leaves on its own.

To run:
    cp .env.example .env        # then paste your bot token into .env
    pip install -r requirements.txt
    python bot.py

Requires FFmpeg on your PATH (https://ffmpeg.org/download.html).
"""

import asyncio
import os
from datetime import datetime

import discord
import requests
import yt_dlp
from discord.ext import commands
from dotenv import load_dotenv

# load_dotenv() reads .env from the current directory into os.environ.
load_dotenv()

# TOKEN saves the token given
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and add your bot token.")

# COMMAND_PREFIX sets the command prefix
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

#IDLE_TIMEOUT_SECONDS set timeout timer to x seconds
IDLE_TIMEOUT_SECONDS = int(os.getenv("IDLE_TIMEOUT_SECONDS", "180"))

# yt-dlp options: audio only, no playlists, treat text as a YouTube search.
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",   # force IPv4 to avoid some throttling issues
}

# FFmpeg options: reconnect if the stream drops, and discard video track.
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ---------------------------------------------------------------------------
# Per-server states
# ---------------------------------------------------------------------------
song_queues: dict[int, list[tuple[str, str]]] = {}   # server id -> [(stream_url, title), ...]
now_playing: dict[int, str | None] = {}              # server id -> title currently playing
idle_tasks: dict[int, asyncio.Task] = {}             # server id -> pending auto-disconnect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def search_youtube(query: str) -> tuple[str, str]:
    """Send a search string or URL to (direct_stream_url, title)."""
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        entry = info["entries"][0] if info.get("entries") else info

        webpage_url = entry.get("webpage_url") or entry.get("url")
        if not webpage_url:
            raise ValueError("No results found.")

        full = ydl.extract_info(webpage_url, download=False)
        stream_url = full.get("url")
        if not stream_url:
            raise ValueError("Could not extract an audio stream for that video.")

        return stream_url, full.get("title", "Unknown title")


def cancel_idle_timer(guild_id: int) -> None:
    """Stop an auto-disconnect"""
    task = idle_tasks.pop(guild_id, None)
    if task:
        task.cancel()


def reset_guild_state(guild_id: int) -> None:
    """Clears the queue, the current song, and any idle timer for a server."""
    song_queues[guild_id] = []
    now_playing[guild_id] = None
    cancel_idle_timer(guild_id)


async def schedule_idle_disconnect(ctx: commands.Context) -> None:
    """Leave the voice channel if nothing has played for timeout time."""
    cancel_idle_timer(ctx.guild.id)
    await ctx.send(
        f" Queue is empty. I'll leave in {IDLE_TIMEOUT_SECONDS // 60} minutes if nothing plays."
    )

    async def disconnect_later():
        try:
            await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
            if ctx.voice_client and not ctx.voice_client.is_playing():
                await ctx.voice_client.disconnect()
                reset_guild_state(ctx.guild.id)
                await ctx.send(" Disconnected due to inactivity.")
        except asyncio.CancelledError:
            pass   # a new song started; the timer is simply dropped

    idle_tasks[ctx.guild.id] = asyncio.create_task(disconnect_later())


async def play_next(ctx: commands.Context) -> None:
    """ Pop the next song off this server's queue and start the next song."""
    queue = song_queues.get(ctx.guild.id, [])
    if not queue:
        now_playing[ctx.guild.id] = None
        await schedule_idle_disconnect(ctx)
        return

    url, title = queue.pop(0)
    now_playing[ctx.guild.id] = title
    cancel_idle_timer(ctx.guild.id)

    source = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)

    def after_playing(error: Exception | None):
        if error:
            print(f"Playback error in server {ctx.guild.id}: {error}")
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    ctx.voice_client.play(source, after=after_playing)
    await ctx.send(f" Now playing: **{title}**")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"{bot.user} is online and connected to {len(bot.guilds)} server.")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Turn mistakes into a message instead of a stack trace."""
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Usage: `{COMMAND_PREFIX}{ctx.command.name} {ctx.command.signature}`")
    elif isinstance(error, commands.CommandNotFound):
        pass   # ignore typos; don't spam the channel
    elif isinstance(error, commands.NotOwner):
        await ctx.send("Only the bot owner can do that.")
    else:
        await ctx.send(f"Something went wrong: {error}")
        raise error   # still print the traceback to the console for debugging


# ---------------------------------------------------------------------------
# Voice channel commands
# ---------------------------------------------------------------------------
@bot.command(help="Join your current voice channel.")
async def join(ctx: commands.Context):
    if ctx.author.voice is None:
        return await ctx.send("You need to be in a voice channel first.")
    if ctx.voice_client is not None:
        return await ctx.send("I'm already connected.")
    await ctx.author.voice.channel.connect()
    await ctx.send(f"Joined **{ctx.author.voice.channel.name}**")


@bot.command(aliases=["leave", "disconnect"], help="Leave the voice channel and clear the queue.")
async def stop(ctx: commands.Context):
    if ctx.voice_client is None:
        return await ctx.send("I'm not in a voice channel.")
    await ctx.voice_client.disconnect()
    reset_guild_state(ctx.guild.id)
    await ctx.send("Left the voice channel and cleared the queue.")


# ---------------------------------------------------------------------------
# Music commands
# ---------------------------------------------------------------------------
@bot.command(help="Play a song by name or YouTube link, or add it to the queue.")
async def play(ctx: commands.Context, *, search: str):
    if ctx.author.voice is None:
        return await ctx.send("You need to be in a voice channel first.")
    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()

    async with ctx.typing():   # shows "MusicBot is typing..." while yt-dlp works
        try:
            url, title = await asyncio.to_thread(search_youtube, search)
        except Exception as exc:
            return await ctx.send(f"Couldn't find that: {exc}")

    song_queues.setdefault(ctx.guild.id, []).append((url, title))

    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        await ctx.send(f"Added to queue (#{len(song_queues[ctx.guild.id])}): **{title}**")
    else:
        await play_next(ctx)


@bot.command(help="Pause the current song.")
async def pause(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command(help="Resume a paused song.")
async def resume(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed.")
    else:
        await ctx.send("Nothing is paused.")


@bot.command(help="Skip the current song.")
async def skip(ctx: commands.Context):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()   # triggers after_playing -> play_next
        await ctx.send("⏭Skipped.")
    else:
        await ctx.send("Nothing to skip.")


@bot.command(name="queue", aliases=["q"], help="Show song queue.")
async def show_queue(ctx: commands.Context):
    queue = song_queues.get(ctx.guild.id, [])
    current = now_playing.get(ctx.guild.id)
    if not queue and not current:
        return await ctx.send("The queue is empty.")

    lines = []
    if current:
        lines.append(f"Now playing: **{current}**")
    for i, (_, title) in enumerate(queue, start=1):
        lines.append(f"{i}. {title}")
    await ctx.send("\n".join(lines))


@bot.command(aliases=["np"], help="Show what's playing right now.")
async def nowplaying(ctx: commands.Context):
    title = now_playing.get(ctx.guild.id)
    await ctx.send(f"Now playing: **{title}**" if title else "Nothing is playing.")


@bot.command(help="Clear the queue (keeps the current song playing).")
async def clear(ctx: commands.Context):
    song_queues[ctx.guild.id] = []
    await ctx.send("Cleared the queue.")


@bot.command(hidden=True)
@commands.is_owner()
async def shutdown(ctx: commands.Context):
    await ctx.send("Shutting down.")
    await bot.close()


if __name__ == "__main__":
    bot.run(TOKEN)
