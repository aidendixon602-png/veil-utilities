# Hosting veil utilities on Wispbyte

Wispbyte's free plan (512 MB RAM) is enough for this bot: it uses about 60 MB idle and ~150 MB at peak
while editing a large GIF. Member caches grow with server size, so very large servers will use more.

## 1. Prepare the bot in Discord
1. <https://discord.com/developers/applications> → your app → **Bot**.
2. Turn on all three **Privileged Gateway Intents**: Presence, Server Members, Message Content.
3. **Reset Token** and copy it. Never share it or post it anywhere.
4. Copy your own Discord user ID (Settings → Advanced → Developer Mode, then right-click your name → Copy User ID).

## 2. Create the server on Wispbyte
1. Sign up at <https://wispbyte.com> and create a new server.
2. Choose the **Python** (Discord bot) type. If you can pick a Python version, choose **3.11 or 3.12**
   (3.10 is the minimum).

## 3. Upload the files
1. Open the server's **File Manager**.
2. Upload `veil-utilities-wispbyte.zip` and **Unarchive** it. `main.py`, `requirements.txt`, `core/`, `cogs/`
   and `fonts/` must sit at the top level (not inside another folder).
3. Delete the zip afterwards.

## 4. Create your `.env`
1. In the File Manager, open `.env.example`, copy everything, then create a **New File** named `.env` and paste.
2. Fill in at least:
   ```
   DISCORD_TOKEN=your-token-here
   LOW_MEMORY=true
   ```
3. Add any optional keys you want (Last.fm, Spotify, AI, Twitch…). Save.

## 5. Startup settings
Open the **Startup** tab and set:
- **Main / app file** (often called `PY_FILE` or "App py file"): `main.py`
- **Requirements file**: `requirements.txt` — the panel installs the packages on each start.

If your panel has no requirements setting, put this in **Additional Python packages** instead:
`discord.py aiosqlite aiohttp python-dotenv wavelink Pillow gTTS feedparser`

## 6. Start it
Press **Start** and watch the **Console**. The first boot installs packages (a minute or two), then you should see:
```
Logged in as veil utilities#1234 (…) in N guilds
```
Invite the bot, drag its role to the top of the role list, and run `,help`.

## Keeping it online
- Wispbyte's free plan asks you to **log in to the panel every two weeks**. Set a reminder.
- `,restart` restarts the bot from Discord. `,system` shows memory use, and `,backup` DMs you the database.
  Run a backup every so often, since everything (settings, levels, cases) lives in `veil.db`.

## Music on a small plan
Music needs a **Lavalink** server (Java), which won't fit in 512 MB next to the bot, so by default the bot uses
**free public Lavalink nodes** and needs no setup. It connects to several at once and switches automatically when one
goes down. Public nodes can be slow or busy at times. For rock-solid music, host Lavalink 4.2+ somewhere else
(config in `lavalink/application.yml`) and set `LAVALINK_URI` / `LAVALINK_PASSWORD`. Set `MUSIC_ENABLED=false`
to turn music off.

## Web dashboard (optional)
The dashboard is **off** by default. It lets server owners edit role blacklist, log channels and antinuke
settings in a browser.

**Only on this machine:** set `DASHBOARD_ENABLED=true`. It listens on `127.0.0.1:8080` and nobody else can
reach it. Wispbyte has no way to open a local address, so use the next option there.

**Public, with Discord login:**
1. Developer portal → your app → **OAuth2**. Reset and copy the **Client Secret** (the Client ID is read from the bot itself).
2. Under **Redirects**, add your dashboard address followed by `/callback`, for example
   `http://123.45.67.89:25565/callback` (Wispbyte shows the address and port on the server's **Network** tab).
3. In `.env`:
   ```
   DASHBOARD_ENABLED=true
   DASHBOARD_PORT=25565                       # the port Wispbyte gave you
   DASHBOARD_URL=http://123.45.67.89:25565    # exactly what you added in step 2, without /callback
   DISCORD_CLIENT_SECRET=...
   DASHBOARD_SECRET=                          # optional: 32+ random characters keeps people logged in across restarts
   ```
4. Restart. People sign in with Discord and only see servers they **own**; developers see every server.

A plain `http://` address works, but login cookies then travel unencrypted. Put the dashboard behind HTTPS
(a domain with a reverse proxy such as Caddy or Cloudflare Tunnel) if other people will use it.

## Troubleshooting
| Console says | Fix |
|---|---|
| `Set DISCORD_TOKEN in your .env file` | `.env` is missing, misnamed, or not in the same folder as `main.py` |
| `Discord refused a privileged intent` | The bot still starts with fewer features. Turn on the three intents in the developer portal (step 1) and restart |
| `Discord rejected DISCORD_TOKEN` | Reset the token and paste the new one, with no spaces or quotes |
| `AI unavailable: … rejected the API key` | `AI_API_KEY` is wrong or revoked. Make a new key, or delete the line to use the free AI |
| `ModuleNotFoundError` | The requirements didn't install — check the Startup requirements setting and restart |
| `TypeError: unsupported operand type(s) for \|` | Python is too old — pick 3.10+ in Startup |
| `Dashboard login needs both DISCORD_CLIENT_SECRET and DASHBOARD_URL` | Fill in both, or clear both for a localhost-only dashboard |
| The bot leaves your server right away | Server authorization: it stays in servers owned by a developer (you, as the owner of the bot's Discord application); for any other server run `,authorize <server id>` |

---

# Deploying from GitHub (recommended)

Keeping the code on GitHub means you can edit it anywhere, keep a history of changes, and have Wispbyte pull
the newest version every time the bot restarts.

## 1. Put the code on GitHub
1. Create a **New repository** on <https://github.com>. Make it **Private**.
2. Upload the files, either way:
   - **Website:** on the empty repo page click *uploading an existing file*, drag in everything from the unzipped
     folder (including `.gitignore` — turn on "show hidden files" if you can't see it), then **Commit changes**.
   - **GitHub Desktop:** *File → Add local repository* → pick the unzipped folder → *Publish repository*
     (keep "Keep this code private" checked).
3. Check the repo on github.com: `.env`, `veil.db` and `veil.log` must **not** be there. `.gitignore` keeps them out.

## 2. Let Wispbyte read a private repo
1. GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained tokens* → **Generate new token**.
2. Repository access: **Only select repositories** → your bot repo.
3. Permissions → Repository → **Contents: Read-only**. Generate and copy the token.

## 3. Point Wispbyte at the repo
In the server's **Startup** tab (names vary slightly between panels):
- **Git Repo Address**: `https://github.com/<you>/<repo>.git`
- **Git Branch**: `main`
- **Git Username**: your GitHub username
- **Git Access Token**: the token from step 2
- **Auto Update**: **On** (pulls the latest code on every restart)
- **App py file**: `main.py` • **Requirements file**: `requirements.txt`

The panel only clones into an **empty** server folder. If you already uploaded the files by hand:
1. Download your `.env` and `veil.db` from the File Manager first (they hold your token and all settings).
2. Delete everything in the File Manager.
3. Start the server once, so it clones the repo.
4. Upload `.env` and `veil.db` back.

## 4. Updating the bot
Edit or upload changed files on GitHub (or commit + push with GitHub Desktop), then press **Restart** on Wispbyte
or run `,gitpull` in Discord. `,gitpull` reloads changed cogs straight away and restarts by itself when core files
changed. If `requirements.txt` changed, restart from the panel so new packages get installed. Your `.env` and database stay untouched because they aren't in git.

**If your token ever ends up on GitHub**, even for a second, reset it immediately in the Discord developer
portal. Bots scan GitHub for leaked tokens within minutes.
