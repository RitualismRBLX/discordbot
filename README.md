# Discord Mod Bot (Railway)

A modern Discord moderation bot written in Python using `discord.py` with **slash commands**. Built for Railway deployment.

## Features

- **Inactivity Prune** — Scan for inactive members and reset their roles to `OUTSIDER & UNRANKED`.
- **Promotion / Demotion** — Move users up or down the role ladder with permission checks.
- **Strike System** — Track strikes; 3 strikes triggers a 7-day tempban.
- **Warnings** — Issue, list, and clear warnings per user.
- **Moderation** — Kick, ban, mute (timeout), unmute, and unban with audit logging.
- **SQLite Persistence** — Local database for strikes, tempbans, and warnings.
- **Audit Logging** — Sends formatted logs to `punishment-logs` and `promo-logs` channels.

## Discord Developer Portal Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new Application and add a Bot.
3. Under the **Bot** tab, enable these **Privileged Gateway Intents**:
   - **Server Members Intent**
   - **Message Content Intent**
4. Go to **OAuth2 > URL Generator**.
5. Select scopes: `bot` and `applications.commands`.
6. Select permissions:
   - `Manage Roles`
   - `Read Message History`
   - `View Channels`
   - `Kick Members`
   - `Ban Members`
   - `Moderate Members` (for timeouts)
7. Copy the URL, invite the bot to your server.

## Server Setup

- Ensure a role named exactly `OUTSIDER & UNRANKED` exists.
- Ensure the Bot's role is **higher** in Server Settings > Roles than the roles it needs to remove, and higher than `OUTSIDER & UNRANKED`.
- Create channels named `punishment-logs` and `promo-logs` for audit logging.

## Railway Deployment

### 1. Push to GitHub

Initialize a repo and push this project to GitHub:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

### 2. Create Railway Project

1. Go to [Railway](https://railway.app/) and log in.
2. Click **New Project** > **Deploy from GitHub repo**.
3. Select your repository.
4. Railway will auto-detect the `Dockerfile` and `railway.toml`.

### 3. Set Environment Variable

1. In your Railway project dashboard, go to the **Variables** tab.
2. Add a new variable:
   - **Name:** `BOT_TOKEN`
   - **Value:** Your Discord bot token from the Developer Portal.
3. Click **Deploy** (or let it auto-deploy).

### 4. Verify

Check the **Deploy Logs** in Railway. You should see:

```
Logged in as YourBotName (ID: 1234567890)
Slash commands synced.
```

## Commands

| Command | Required Role | Description |
|---------|---------------|-------------|
| `/inactivityprune` | `Manage Roles` perm | Scan for inactive members (5 days). |
| `/confirmprune` | `Manage Roles` perm | Apply `OUTSIDER & UNRANKED` to pending members. |
| `/cancelprune` | `Manage Roles` perm | Cancel pending prune. |
| `/promo` | Overseer+ | Promote user to next higher role. |
| `/demote` | Overseer+ | Demote user to next lower role. |
| `/strike` | STAFF+ | Issue a strike (3 = tempban). |
| `/removestrike` | STAFF+ | Remove most recent strike. |
| `/ban` | Overseer+ | Ban a member. |
| `/kick` | Admin+ | Kick a member. |
| `/mute` | STAFF+ | Timeout a member (e.g., `4h`, `1d`). |
| `/unmute` | STAFF+ | Remove timeout. |
| `/unban` | Overseer+ | Unban by user ID. |
| `/warn` | STAFF+ | Warn a member. |
| `/warnings` | STAFF+ | List warnings for a member. |
| `/clearwarnings` | STAFF+ | Clear all warnings for a member. |

## Local Development

Create a `.env` file:

```env
BOT_TOKEN=your_bot_token_here
```

Install dependencies:

```bash
pip install -r requirements.txt
python bot.py
```

## Notes

- **SQLite persistence**: Railway's filesystem persists across restarts but not full redeploys. For heavy production use, consider Railway's PostgreSQL addon and migrating the SQLite schema.
- **Role hierarchy**: The bot cannot assign or remove roles above its own highest role.
- **Slash commands** may take up to 1 hour to sync globally on first invite, but usually appear immediately for the guild.
