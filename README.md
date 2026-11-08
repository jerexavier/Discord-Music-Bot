# RemyBot

A Discord music bot written in Python. Type `!play` followed by a song name or a
YouTube link and it joins your voice channel, streams the audio, and keeps a
queue going. Each server gets its own queue, and the bot leaves on its own when
nobody is using it.

Built with [discord.py](https://github.com/Rapptz/discord.py), [yt-dlp](https://github.com/yt-dlp/yt-dlp), and FFmpeg.

## Commands

| Command | What it does |
| --- | --- |
| `!play <song or URL>` | Play a song, or add it to the queue if something is already playing |
| `!pause` / `!resume` | Pause and resume |
| `!skip` | Skip to the next song |
| `!queue` (`!q`) | Show what's playing and what's next |
| `!nowplaying` (`!np`) | Show the current song |
| `!clear` | Empty the queue |
| `!join` | Join your voice channel without playing anything |
| `!stop` (`!leave`) | Leave the voice channel and clear the queue |
| `!nbagames` | Today's NBA scores (needs a free balldontlie API key) |
| `!help` | List all commands |

## How it works

1. `!play` runs a YouTube search through yt-dlp **in a background thread** (`asyncio.to_thread`) so the bot keeps responding to other commands while the lookup happens.
2. yt-dlp returns a direct audio stream URL, which is handed to FFmpeg. FFmpeg decodes it and discord.py sends the Opus audio into the voice channel.
3. When a song finishes, discord.py fires a callback from its audio thread. The callback schedules `play_next()` back on the event loop, which pops the next song off that server's queue.
4. If the queue is empty, an `asyncio` task starts a timer; if nothing plays before it expires, the bot disconnects. Starting a new song cancels the timer.

All state (queue, current song, idle timer) is stored per server id, so the bot can sit in several servers at once.

## Setup

**1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications)**
- New Application → Bot → Reset Token (copy it, you'll need it below)
- Under *Privileged Gateway Intents*, turn on **Message Content Intent**
- OAuth2 → URL Generator → scopes `bot`, permissions `Connect`, `Speak`, `Send Messages`, `Read Message History` → open the generated URL to invite the bot to your server

**2. Install FFmpeg** and make sure `ffmpeg` runs from a terminal
- Windows: `winget install ffmpeg` (or download from ffmpeg.org and add to PATH)
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

**3. Run the bot**

```bash
git clone https://github.com/YOUR-USERNAME/remy-bot.git
cd remy-bot
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then paste your token into .env
python bot.py
```

You should see `RemyBot#1234 is online and connected to 1 server(s).`

## Known limitations

- YouTube stream URLs expire after a few hours, so a song that sits in the queue for a very long time can fail to play. Re-queue it.
- YouTube changes its site often; if `!play` suddenly stops working, `pip install -U yt-dlp` usually fixes it.
- Playlists are intentionally ignored (`noplaylist`); only the first result is queued.

## Ideas for extending it

- Slash commands (`/play`) via `discord.app_commands`
- A `!remove <n>` command and `!shuffle`
- Persist queues to disk so a restart doesn't lose them
- Spotify link support (resolve track name, then search YouTube)
