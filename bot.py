import discord
from discord.ext import commands
import os, sqlite3, datetime, asyncio, random, aiohttp, io
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN or TOKEN == "your_bot_token_here":
    print("ERROR: BOT_TOKEN missing"); exit(1)

DB_URL = os.getenv("DATABASE_URL")
IS_PG = bool(DB_URL)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="%", intents=intents, help_command=None, case_insensitive=True)
DB = "bot_data.db"

class UnifiedRow:
    def __init__(self, real_row, cols):
        self._row = real_row
        self._cols = cols
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._row.values())[key] if hasattr(self._row, 'values') else self._row[key]
        return self._row[key]
    def __iter__(self):
        if hasattr(self._row, 'values'):
            return iter(self._row.values())
        return iter(self._row)
    def keys(self):
        return self._row.keys() if hasattr(self._row, 'keys') else []
    def values(self):
        return self._row.values() if hasattr(self._row, 'values') else []
    def get(self, key, default=None):
        return self._row.get(key, default) if hasattr(self._row, 'get') else (self._row[key] if key in self._row else default)

class UnifiedCursor:
    def __init__(self, real_cursor, is_pg):
        self._c = real_cursor
        self._is_pg = is_pg
        self._lastrowid = None
    def execute(self, operation, parameters=()):
        if self._is_pg:
            operation = operation.replace("?", "%s")
            op_upper = operation.strip().upper()
            # Tables that have no auto-increment 'id' column (app provides the PK)
            no_id_tables = {"levels", "tags", "roblox_verify"}
            if op_upper.startswith("INSERT") and "RETURNING" not in op_upper:
                # Extract table name from INSERT INTO tablename ...
                parts = operation.strip().split()
                tbl = ""
                for i, p in enumerate(parts):
                    if p.upper() == "INTO" and i + 1 < len(parts):
                        tbl = parts[i + 1].strip('"').lower()
                        break
                if tbl not in no_id_tables:
                    operation = operation.rstrip(";") + " RETURNING id"
                    self._c.execute(operation, parameters)
                    row = self._c.fetchone()
                    self._lastrowid = row["id"] if row else None
                else:
                    self._c.execute(operation, parameters)
                    self._lastrowid = None
            else:
                self._c.execute(operation, parameters)
                self._lastrowid = None
        else:
            self._c.execute(operation, parameters)
            self._lastrowid = self._c.lastrowid
    def fetchone(self):
        row = self._c.fetchone()
        if row is None:
            return None
        if self._is_pg:
            return UnifiedRow(row, row.keys() if hasattr(row, 'keys') else [])
        return row
    def fetchall(self):
        if self._is_pg:
            return [UnifiedRow(r, r.keys() if hasattr(r, 'keys') else []) for r in self._c.fetchall()]
        return self._c.fetchall()
    @property
    def lastrowid(self):
        return self._lastrowid
    def __getattr__(self, name):
        return getattr(self._c, name)

class UnifiedConn:
    def __init__(self, real_conn, is_pg):
        self._conn = real_conn
        self._is_pg = is_pg
    def cursor(self):
        return UnifiedCursor(self._conn.cursor(), self._is_pg)
    def commit(self):
        self._conn.commit()
    def close(self):
        self._conn.close()
    def __getattr__(self, name):
        return getattr(self._conn, name)

def db():
    if IS_PG:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return UnifiedConn(psycopg2.connect(DB_URL, cursor_factory=RealDictCursor), True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return UnifiedConn(conn, False)

def init_db():
    conn = db()
    c = conn.cursor()
    if IS_PG:
        # PostgreSQL: all Discord ID columns must be BIGINT. Drop and recreate cleanly.
        pg_tables = [
            "DROP TABLE IF EXISTS event_logs CASCADE",
            "DROP TABLE IF EXISTS activity_checks CASCADE",
            "DROP TABLE IF EXISTS tickets CASCADE",
            "DROP TABLE IF EXISTS roblox_verify CASCADE",
            "DROP TABLE IF EXISTS invites CASCADE",
            "DROP TABLE IF EXISTS applications CASCADE",
            "DROP TABLE IF EXISTS tempbans CASCADE",
            "DROP TABLE IF EXISTS strikes CASCADE",
            "DROP TABLE IF EXISTS warns CASCADE",
            "DROP TABLE IF EXISTS tags CASCADE",
            "DROP TABLE IF EXISTS levels CASCADE",
            "CREATE TABLE levels(user_id BIGINT,guild_id BIGINT,xp BIGINT,level BIGINT,PRIMARY KEY(user_id,guild_id))",
            "CREATE TABLE tags(name TEXT,guild_id BIGINT,content TEXT,author_id BIGINT,uses BIGINT DEFAULT 0,created TEXT)",
            "CREATE TABLE warns(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,reason TEXT,mod_id BIGINT,ts TEXT)",
            "CREATE TABLE strikes(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,reason TEXT,ts TEXT,mod_id BIGINT,log_channel_id BIGINT,log_message_id BIGINT)",
            "CREATE TABLE tempbans(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,expiry_timestamp TEXT)",
            "CREATE TABLE applications(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,content TEXT,status TEXT DEFAULT 'pending',ts TEXT,message_id BIGINT)",
            "CREATE TABLE invites(id SERIAL PRIMARY KEY,inviter_id BIGINT,invitee_id BIGINT,guild_id BIGINT,code TEXT,ts TEXT)",
            "CREATE TABLE roblox_verify(user_id BIGINT PRIMARY KEY,roblox_id BIGINT,roblox_username TEXT,verified_ts TEXT)",
            "CREATE TABLE tickets(id SERIAL PRIMARY KEY,guild_id BIGINT,channel_id BIGINT UNIQUE,user_id BIGINT,claimer_id BIGINT,status TEXT DEFAULT 'open',created_ts TEXT)",
            "CREATE TABLE activity_checks(id SERIAL PRIMARY KEY,guild_id BIGINT,message_id BIGINT,channel_id BIGINT,ts TEXT)",
            "CREATE TABLE event_logs(id SERIAL PRIMARY KEY,num BIGINT,event_type TEXT,name TEXT,game_name TEXT,host_id BIGINT,guild_id BIGINT,channel_id BIGINT,message_id BIGINT,start_ts TEXT,end_ts TEXT,attendees TEXT,no_shows TEXT)",
        ]
        for sql in pg_tables:
            c.execute(sql)
    else:
        sqlite_tables = [
            "CREATE TABLE IF NOT EXISTS levels(user_id INT,guild_id INT,xp INT,level INT,PRIMARY KEY(user_id,guild_id))",
            "CREATE TABLE IF NOT EXISTS tags(name TEXT,guild_id INT,content TEXT,author_id INT,uses INT DEFAULT 0,created TEXT)",
            "CREATE TABLE IF NOT EXISTS warns(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,reason TEXT,mod_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS strikes(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,reason TEXT,ts TEXT,mod_id INT,log_channel_id INT,log_message_id INT)",
            "CREATE TABLE IF NOT EXISTS tempbans(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,expiry_timestamp TEXT)",
            "CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,content TEXT,status TEXT DEFAULT 'pending',ts TEXT,message_id INT)",
            "CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY,inviter_id INT,invitee_id INT,guild_id INT,code TEXT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS roblox_verify(user_id INT PRIMARY KEY,roblox_id INT,roblox_username TEXT,verified_ts TEXT)",
            "CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY,guild_id INT,channel_id INT UNIQUE,user_id INT,claimer_id INT,status TEXT DEFAULT 'open',created_ts TEXT)",
            "CREATE TABLE IF NOT EXISTS activity_checks(id INTEGER PRIMARY KEY,guild_id INT,message_id INT,channel_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS event_logs(id INTEGER PRIMARY KEY,num INTEGER,event_type TEXT,name TEXT,game_name TEXT,host_id INT,guild_id INT,channel_id INT,message_id INT,start_ts TEXT,end_ts TEXT,attendees TEXT,no_shows TEXT)",
        ]
        for t in sqlite_tables:
            c.execute(t)
        for col in ("log_channel_id", "log_message_id"):
            try: c.execute(f"ALTER TABLE strikes ADD COLUMN {col} INTEGER")
            except sqlite3.OperationalError: pass
    conn.commit()
    conn.close()

def get_role_by_name(guild, name): return discord.utils.get(guild.roles, name=name)

def has_role_or_higher(member, role_name):
    target = get_role_by_name(member.guild, role_name)
    if not target: return False
    for role in member.roles:
        if role.position >= target.position: return True
    return False

def is_staff(member): return has_role_or_higher(member, "STAFF")
def is_moderator(member): return has_role_or_higher(member, "Moderator")
def is_admin(member): return has_role_or_higher(member, "Admin")
def is_head_admin(member): return has_role_or_higher(member, "Head Admin")
def is_lieutenant(member): return has_role_or_higher(member, "Lieutenant")
def is_senior_lieutenant(member): return has_role_or_higher(member, "Senior Lieutenant")
def is_capo(member): return has_role_or_higher(member, "Capo")

def require_role(role_name):
    def predicate(ctx):
        if has_role_or_higher(ctx.author, role_name):
            return True
        raise commands.CheckFailure(f"You need the {role_name} role or higher.")
    return commands.check(predicate)

app_sessions = {}  # {user_id: guild_id}
invites_cache = {}  # {guild_id: {code: uses}}
active_events = {}  # {message_id: {type, name, game, host_id, guild_id, reactors:set(), channel_id, num}}
# Helper to find active event by guild+number
def _find_event_by_num(guild_id, num):
    for msg_id, ev in active_events.items():
        if ev.get("guild_id") == guild_id and ev.get("num") == num:
            return msg_id, ev
    return None, None

def get_log_channel(guild, name): return discord.utils.get(guild.text_channels, name=name)

async def _delayed_unban(guild, user_id, expiry_str, delay):
    await asyncio.sleep(delay)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM tempbans WHERE user_id=? AND guild_id=? AND expiry_timestamp=?", (user_id, guild.id, expiry_str))
    if not c.fetchone():
        conn.close(); return
    try:
        await guild.unban(discord.Object(id=user_id), reason="Tempban expired")
        log_ch = get_log_channel(guild, "punishment-logs")
        if log_ch:
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            user = await bot.fetch_user(user_id)
            await log_ch.send(f"[{ts}] action; auto-unban (tempban expired) | user; {user.mention if user else user_id}")
    except Exception: pass
    c.execute("DELETE FROM tempbans WHERE user_id=? AND guild_id=?", (user_id, guild.id))
    conn.commit(); conn.close()

async def check_tempbans():
    now = datetime.datetime.utcnow()
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id, guild_id FROM tempbans WHERE expiry_timestamp <= ?", (now.isoformat(),))
    for user_id, guild_id in c.fetchall():
        guild = bot.get_guild(guild_id)
        if guild:
            try:
                await guild.unban(discord.Object(id=user_id), reason="Tempban expired")
                log_ch = get_log_channel(guild, "punishment-logs")
                if log_ch:
                    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    user = await bot.fetch_user(user_id)
                    await log_ch.send(f"[{ts}] action; auto-unban (tempban expired) | user; {user.mention if user else user_id}")
            except Exception: pass
    c.execute("DELETE FROM tempbans WHERE expiry_timestamp <= ?", (now.isoformat(),))
    conn.commit()
    c.execute("SELECT user_id, guild_id, expiry_timestamp FROM tempbans")
    for user_id, guild_id, expiry_str in c.fetchall():
        try:
            expiry = datetime.datetime.fromisoformat(expiry_str)
            delay = (expiry - now).total_seconds()
            if delay > 0:
                guild = bot.get_guild(guild_id)
                if guild: asyncio.create_task(_delayed_unban(guild, user_id, expiry_str, delay))
        except Exception: pass
    conn.close()

async def cache_invites(guild):
    try:
        invs = await guild.invites()
        invites_cache[guild.id] = {i.code: i.uses for i in invs}
    except Exception:
        pass

@bot.event
async def on_ready():
    init_db()
    await check_tempbans()
    for guild in bot.guilds:
        await cache_invites(guild)
    bot.loop.create_task(daily_activity_check())
    print(f"Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_member_join(member):
    guild = member.guild
    try:
        invs = await guild.invites()
        old = invites_cache.get(guild.id, {})
        for i in invs:
            if old.get(i.code, 0) < i.uses:
                inviter = i.inviter
                conn = db(); c = conn.cursor()
                c.execute("INSERT INTO invites (inviter_id,invitee_id,guild_id,code,ts) VALUES (?,?,?,?,?)",
                          (inviter.id if inviter else None, member.id, guild.id, i.code, datetime.datetime.utcnow().isoformat()))
                conn.commit(); conn.close()
                break
        await cache_invites(guild)
    except Exception:
        pass

@bot.event
async def on_member_update(before, after):
    if before.roles == after.roles:
        return
    got_staff = any(r.name == "STAFF" for r in after.roles) and not any(r.name == "STAFF" for r in before.roles)
    if got_staff:
        conn = db(); c = conn.cursor()
        c.execute("SELECT roblox_id FROM roblox_verify WHERE user_id=?", (after.id,))
        row = c.fetchone(); conn.close()
        try:
            if row and row["roblox_id"]:
                await after.send(
                    "**Welcome to the CNA Staff Team!**\n\n"
                    "Your Roblox profile is already verified. You're all set to host events, deployments, and raids.\n\n"
                    "Use `%help` to see all available commands."
                )
            else:
                await after.send(
                    "**Welcome to the CNA Staff Team!**\n\n"
                    "You have been assigned the **STAFF** role. Before you can host events, deployments, or raids, "
                    "you must verify your Roblox profile.\n\n"
                    "**How to verify:**\n"
                    "1. Find your Roblox User ID (go to your profile, the number in the URL).\n"
                    "2. Run the command: `%verifyroblox <your_roblox_id>` in the server.\n\n"
                    "Example: `%verifyroblox 609720362`\n\n"
                    "Once verified, you'll be able to host events and your Roblox profile link will be shared with attendees."
                )
        except Exception:
            pass

async def daily_activity_check():
    await bot.wait_until_ready()
    while True:
        now = datetime.datetime.utcnow()
        # Noon CST = 18:00 UTC (CDT) or 17:00 UTC (CST). Using 18:00 UTC.
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        wait = (target - now).total_seconds()
        await asyncio.sleep(wait)
        for guild in bot.guilds:
            ch = get_log_channel(guild, "activity-check")
            if not ch:
                continue
            try:
                e = discord.Embed(title="Daily Activity Check", description="React with ✅ to confirm you're active.", color=discord.Color.green())
                e.set_footer(text=f"Posted at {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
                msg = await ch.send("@everyone", embed=e)
                await msg.add_reaction("✅")
                conn = db(); c = conn.cursor()
                c.execute("INSERT INTO activity_checks (guild_id, message_id, channel_id, ts) VALUES (?,?,?,?)",
                          (guild.id, msg.id, ch.id, datetime.datetime.utcnow().isoformat()))
                conn.commit(); conn.close()
            except Exception:
                pass

@bot.event
async def on_raw_reaction_add(payload):
    if str(payload.emoji) != "✅":
        return
    if payload.message_id in active_events:
        active_events[payload.message_id]["reactors"].add(payload.user_id)

@bot.event
async def on_message(msg):
    if msg.author.bot: return
    if not msg.guild:
        return
    if bot.user in msg.mentions and not msg.content.startswith('%'):
        replies = [
            f"Hey {msg.author.mention}! I'm a bot. Use `%help` to see what I can do.",
            f"What's up {msg.author.mention}? Try `%help` for my command list.",
            f"Yo {msg.author.mention}! Need help? Type `%help`.",
        ]
        await msg.channel.send(random.choice(replies))
    await bot.process_commands(msg)

def parse_dur(t):
    if not t: return None
    u = {"s":1,"m":60,"h":3600,"d":86400,"w":604800}
    x = t[-1].lower()
    if x not in u: return None
    try: n = int(t[:-1])
    except: return None
    if n <= 0: return None
    return datetime.timedelta(seconds=n*u[x])

def parse_event_time(time_str):
    time_str = time_str.strip()
    now = datetime.datetime.utcnow()
    # Check for relative duration like 15m, 2h, 1d
    dur = parse_dur(time_str)
    if dur:
        dt = now + dur
        return dt, int(dt.timestamp())
    # Try parsing as absolute time (various formats)
    # Try HH:MM format
    try:
        dt = datetime.datetime.strptime(time_str, "%H:%M")
        dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        if dt <= now:
            dt += datetime.timedelta(days=1)
        return dt, int(dt.timestamp())
    except ValueError:
        pass
    # Try H:MM AM/PM format (e.g. "10:30 PM")
    try:
        dt = datetime.datetime.strptime(time_str.upper(), "%I:%M %p")
        dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        if dt <= now:
            dt += datetime.timedelta(days=1)
        return dt, int(dt.timestamp())
    except ValueError:
        pass
    return None, None

# ─── MODERATION ───
async def _log_punishment(guild, action, member, mod, reason=""):
    log_ch = get_log_channel(guild, "punishment-logs")
    if not log_ch:
        return
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    r = f" | reason; {reason}" if reason else ""
    await log_ch.send(f"[{ts}] action; {action} | user; {member.mention} | mod; {mod.mention}{r}")

@bot.command(aliases=["mban"])
@commands.has_permissions(ban_members=True)
@require_role("Lieutenant")
async def ban(ctx, member: discord.Member, *, reason="No reason"):
    await member.ban(reason=reason)
    await _log_punishment(ctx.guild, "ban", member, ctx.author, reason)
    await ctx.send(f"Banned {member.mention}. `{reason}`")

@bot.command(aliases=["mkick"])
@commands.has_permissions(kick_members=True)
@require_role("Head Admin")
async def kick(ctx, member: discord.Member, *, reason="No reason"):
    await member.kick(reason=reason)
    await _log_punishment(ctx.guild, "kick", member, ctx.author, reason)
    await ctx.send(f"Kicked {member.mention}. `{reason}`")

@bot.command(aliases=["mmute"])
@commands.has_permissions(moderate_members=True)
@require_role("Moderator")
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason="No reason"):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration. Use `1h`, `30m`, `1d`, etc.")
    if d > datetime.timedelta(days=28): return await ctx.send("Max 28 days.")
    await member.timeout(d, reason=reason)
    await _log_punishment(ctx.guild, f"mute ({duration})", member, ctx.author, reason)
    await ctx.send(f"Muted {member.mention} for `{duration}`. `{reason}`")

@bot.command(aliases=["munmute"])
@commands.has_permissions(moderate_members=True)
@require_role("Moderator")
async def unmute(ctx, member: discord.Member, *, reason="No reason"):
    await member.timeout(None, reason=reason)
    await _log_punishment(ctx.guild, "unmute", member, ctx.author, reason)
    await ctx.send(f"Unmuted {member.mention}.")

@bot.command(aliases=["mtb"])
@commands.has_permissions(ban_members=True)
@require_role("Lieutenant")
async def tempban(ctx, member: discord.Member, duration: str, *, reason="No reason"):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration.")
    await member.ban(reason=f"Tempban ({duration}): {reason}")
    await _log_punishment(ctx.guild, f"tempban ({duration})", member, ctx.author, reason)
    await ctx.send(f"Tempbanned {member.mention} for `{duration}`.")
    async def _unban():
        await asyncio.sleep(d.total_seconds())
        try:
            await ctx.guild.unban(member, reason="Tempban expired")
            log_ch = get_log_channel(ctx.guild, "punishment-logs")
            if log_ch:
                ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                await log_ch.send(f"[{ts}] action; auto-unban (tempban expired) | user; {member.mention}")
        except: pass
    asyncio.create_task(_unban())

@bot.command()
@commands.has_permissions(ban_members=True)
@require_role("Capo")
async def unban(ctx, user_id: int, *, reason="No reason"):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await _log_punishment(ctx.guild, "unban", user, ctx.author, reason)
        await ctx.send(f"Unbanned `{user}`. `{reason}`")
    except discord.NotFound:
        await ctx.send("User not banned or does not exist.")
    except discord.Forbidden:
        await ctx.send("Missing permissions.")
    except discord.HTTPException as e:
        await ctx.send(f"Error: {e}")

@bot.command()
@commands.has_permissions(manage_messages=True)
@require_role("Admin")
async def purge(ctx, amount: int):
    await ctx.channel.purge(limit=amount+1)
    m = await ctx.send(f"Cleared {amount} messages")
    await asyncio.sleep(3); await m.delete()

@bot.command(aliases=["sm","slow"])
@commands.has_permissions(manage_channels=True)
@require_role("Admin")
async def slowmode(ctx, seconds: int = 0):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to `{seconds}s`")

@bot.command()
@commands.has_permissions(manage_channels=True)
@require_role("Admin")
async def lock(ctx, channel: discord.TextChannel = None):
    ch = channel or ctx.channel
    await ch.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send(f"Locked {ch.mention}")

@bot.command()
@commands.has_permissions(manage_channels=True)
@require_role("Admin")
async def unlock(ctx, channel: discord.TextChannel = None):
    ch = channel or ctx.channel
    await ch.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send(f"Unlocked {ch.mention}")

@bot.command()
@commands.has_permissions(manage_nicknames=True)
@require_role("Head Admin")
async def nick(ctx, member: discord.Member, *, name):
    await member.edit(nick=name)
    await ctx.send(f"Changed {member.mention} nickname to `{name}`")

@bot.command()
@commands.has_permissions(manage_roles=True)
@require_role("Lieutenant")
async def addrole(ctx, member: discord.Member, *, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"Added {role.mention} to {member.mention}")

@bot.command()
@commands.has_permissions(manage_roles=True)
@require_role("Lieutenant")
async def removerole(ctx, member: discord.Member, *, role: discord.Role):
    await member.remove_roles(role)
    await ctx.send(f"Removed {role.mention} from {member.mention}")

@bot.command(aliases=["mwarn"])
@commands.has_permissions(manage_messages=True)
@require_role("STAFF")
async def warn(ctx, member: discord.Member, *, reason):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO warns(user_id,guild_id,reason,mod_id,ts) VALUES (?,?,?,?,?)",
              (member.id,ctx.guild.id,reason,ctx.author.id,datetime.datetime.utcnow().isoformat()))
    conn.commit()
    c.execute("SELECT COUNT(*) FROM warns WHERE user_id=? AND guild_id=?", (member.id,ctx.guild.id))
    n = c.fetchone()[0]; conn.close()
    await _log_punishment(ctx.guild, "warn", member, ctx.author, reason)
    await ctx.send(f"Warned {member.mention} (`{reason}`) — Total: {n}")

@bot.command(aliases=["warns","warnings"])
@commands.has_permissions(manage_messages=True)
@require_role("STAFF")
async def warnlist(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT reason,ts FROM warns WHERE user_id=? AND guild_id=? ORDER BY id", (member.id,ctx.guild.id))
    rows = c.fetchall(); conn.close()
    if not rows: return await ctx.send("No warnings.")
    lines = [f"**Warnings for {member.name}:**"]
    for i,r in enumerate(rows,1): lines.append(f"{i}. {r['reason']} — {r['ts'][:10]}")
    await ctx.send("\n".join(lines)[:2000])

@bot.command()
@commands.has_permissions(manage_messages=True)
@require_role("STAFF")
async def unwarn(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT id, reason FROM warns WHERE user_id=? AND guild_id=? ORDER BY id DESC LIMIT 1", (member.id,ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send("No warnings found for this user.")
    wid, reason = row
    c.execute("DELETE FROM warns WHERE id=?", (wid,))
    conn.commit(); conn.close()
    await _log_punishment(ctx.guild, "unwarn (revert)", member, ctx.author, reason)
    await ctx.send(f"Removed 1 warning from {member.mention}")

@bot.command(aliases=["clearwarns"])
@commands.has_permissions(manage_messages=True)
@require_role("STAFF")
async def clearwarnings(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM warns WHERE user_id=? AND guild_id=?", (member.id,ctx.guild.id))
    n = c.fetchone()[0]
    c.execute("DELETE FROM warns WHERE user_id=? AND guild_id=?", (member.id,ctx.guild.id))
    conn.commit(); conn.close()
    await _log_punishment(ctx.guild, f"clearwarnings ({n} removed)", member, ctx.author, "")
    await ctx.send(f"Cleared all warnings for {member.mention}")

@bot.command(aliases=["mstrike"])
@require_role("STAFF")
async def strike(ctx, member: discord.Member, *, reason: str):
    now = datetime.datetime.utcnow().isoformat()
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO strikes (user_id,guild_id,reason,ts,mod_id) VALUES (?,?,?,?,?)",
              (member.id, ctx.guild.id, reason, now, ctx.author.id))
    conn.commit(); sid = c.lastrowid
    c.execute("SELECT COUNT(*) FROM strikes WHERE user_id=? AND guild_id=?", (member.id, ctx.guild.id))
    count = c.fetchone()[0]
    conn.close()
    try:
        o = ctx.guild.owner
        if o: await o.send(f"**Strike Issued**\nUser: {member.mention}\nReason: {reason}\nMod: {ctx.author.name}\nCount: {count}/3")
    except Exception: pass
    log_ch = get_log_channel(ctx.guild, "punishment-logs")
    log_msg = None
    if log_ch:
        ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        log_msg = await log_ch.send(f"[{ts}] action; strike ({count}/3) | user; {member.mention} | mod; {ctx.author.mention} | reason; {reason}")
        conn = db(); c = conn.cursor()
        c.execute("UPDATE strikes SET log_channel_id=?, log_message_id=? WHERE id=?", (log_ch.id, log_msg.id, sid))
        conn.commit(); conn.close()
    if count >= 3:
        expiry = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat()
        conn = db(); c = conn.cursor()
        c.execute("INSERT INTO tempbans (user_id,guild_id,expiry_timestamp) VALUES (?,?,?)", (member.id, ctx.guild.id, expiry))
        conn.commit(); conn.close()
        try:
            await member.ban(reason="3 strikes - 7 day tempban")
            await ctx.send(f"{member.mention} reached **3 strikes** and was tempbanned for 7 days.")
            await _log_punishment(ctx.guild, "auto-tempban (3 strikes)", member, ctx.author, reason)
            asyncio.create_task(_delayed_unban(ctx.guild, member.id, expiry, 7*24*60*60))
        except discord.Forbidden:
            await ctx.send("Missing permissions to ban.")
        except discord.HTTPException as e:
            await ctx.send(f"Error banning: {e}")
    else:
        await ctx.send(f"Strike issued to {member.mention}. Reason: `{reason}`. Strike {count}/3.")

@bot.command(aliases=["mremovestrike","unstrike"])
@require_role("STAFF")
async def removestrike(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT id, reason, log_channel_id, log_message_id FROM strikes WHERE user_id=? AND guild_id=? ORDER BY id DESC LIMIT 1", (member.id, ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send("No strikes found for this user.")
    sid, reason, lcid, lmid = row
    c.execute("DELETE FROM strikes WHERE id=?", (sid,)); conn.commit()
    c.execute("SELECT COUNT(*) FROM strikes WHERE user_id=? AND guild_id=?", (member.id, ctx.guild.id))
    count = c.fetchone()[0]; conn.close()
    await ctx.send(f"Removed 1 strike from {member.mention}. Current strikes: {count}/3.")
    await _log_punishment(ctx.guild, f"removestrike (revert) — was: {reason}", member, ctx.author, "")
    if lcid and lmid:
        try:
            ch = ctx.guild.get_channel(lcid)
            if ch:
                msg = await ch.fetch_message(lmid)
                await msg.reply("Reverted")
        except Exception: pass
    try:
        o = ctx.guild.owner
        if o: await o.send(f"**Strike Reverted**\nUser: {member.mention}\nReason: {reason}\nMod: {ctx.author.name}\nStrike count now: {count}/3")
    except Exception: pass

# ─── FUN ───
@bot.command(aliases=["8ball"])
async def eightball(ctx, *, question):
    a = ["Yes","No","Maybe","Ask again later","Definitely","Absolutely not","I don't know","Most likely"]
    await ctx.send(f"🎱 {random.choice(a)}")

@bot.command(aliases=["cf","coin"])
async def coinflip(ctx):
    await ctx.send(random.choice(["Heads","Tails"]))

@bot.command(aliases=["r"])
async def roll(ctx, dice="1d6"):
    try:
        n,s = map(int,dice.lower().split("d"))
        if n>100 or s>1000: return await ctx.send("Too high")
        rolls = [random.randint(1,s) for _ in range(n)]
        await ctx.send(f"{' + '.join(map(str,rolls))} = **{sum(rolls)}**")
    except: await ctx.send("Format: `%roll 2d20`")

@bot.command()
async def rps(ctx, choice: str):
    c = choice.lower()
    if c not in ["rock","paper","scissors"]: return await ctx.send("Use rock, paper, or scissors")
    b = random.choice(["rock","paper","scissors"])
    w = {"rock":"scissors","paper":"rock","scissors":"paper"}
    res = "You win!" if w[c]==b else "You lose!" if w[b]==c else "Tie!"
    await ctx.send(f"You: {c} | Bot: {b} → {res}")

@bot.command(aliases=["pick","select"])
async def choose(ctx, *, options):
    opts = [o.strip() for o in options.split(",") if o.strip()]
    if len(opts)<2: return await ctx.send("Give options separated by commas")
    await ctx.send(f"I choose: **{random.choice(opts)}**")

@bot.command()
async def rate(ctx, *, thing):
    await ctx.send(f"I rate **{thing}** a **{random.randint(0,100)}/100**")

@bot.command()
async def reverse(ctx, *, text):
    await ctx.send(text[::-1])

@bot.command()
async def mock(ctx, *, text):
    await ctx.send("".join(c.upper() if random.random()>0.5 else c.lower() for c in text))

@bot.command()
async def cat(ctx):
    async with aiohttp.ClientSession() as s:
        async with s.get("https://api.thecatapi.com/v1/images/search") as r:
            d = await r.json()
            await ctx.send(d[0]["url"])

@bot.command()
async def dog(ctx):
    async with aiohttp.ClientSession() as s:
        async with s.get("https://dog.ceo/api/breeds/image/random") as r:
            d = await r.json()
            await ctx.send(d["message"])

@bot.command()
async def meme(ctx):
    async with aiohttp.ClientSession() as s:
        async with s.get("https://meme-api.com/gimme") as r:
            d = await r.json()
            await ctx.send(d["url"])

# ─── UTILITY / INFO ───
@bot.command(aliases=["av","pfp"])
async def avatar(ctx, member: discord.Member = None):
    m = member or ctx.author
    e = discord.Embed(title=f"{m.name}'s Avatar")
    e.set_image(url=m.display_avatar.url)
    await ctx.send(embed=e)

@bot.command(aliases=["whois","ui"])
async def userinfo(ctx, member: discord.Member = None):
    m = member or ctx.author
    e = discord.Embed(title=f"User Info: {m}")
    e.add_field(name="ID", value=m.id, inline=True)
    e.add_field(name="Joined", value=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "N/A", inline=True)
    e.add_field(name="Created", value=m.created_at.strftime("%Y-%m-%d"), inline=True)
    e.add_field(name="Roles", value=", ".join(r.mention for r in m.roles[1:]) or "None", inline=False)
    e.set_thumbnail(url=m.display_avatar.url)
    await ctx.send(embed=e)

@bot.command(aliases=["si","guildinfo","server"])
async def serverinfo(ctx):
    g = ctx.guild
    e = discord.Embed(title=g.name)
    e.add_field(name="ID", value=g.id, inline=True)
    e.add_field(name="Owner", value=g.owner.mention if g.owner else "N/A", inline=True)
    e.add_field(name="Members", value=g.member_count, inline=True)
    e.add_field(name="Channels", value=len(g.channels), inline=True)
    e.add_field(name="Roles", value=len(g.roles), inline=True)
    e.add_field(name="Created", value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    if g.icon: e.set_thumbnail(url=g.icon.url)
    await ctx.send(embed=e)

@bot.command(aliases=["ri"])
async def roleinfo(ctx, *, role: discord.Role):
    e = discord.Embed(title=f"Role: {role.name}", color=role.color)
    e.add_field(name="ID", value=role.id, inline=True)
    e.add_field(name="Color", value=str(role.color), inline=True)
    e.add_field(name="Members", value=len(role.members), inline=True)
    e.add_field(name="Position", value=role.position, inline=True)
    e.add_field(name="Created", value=role.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.send(embed=e)

@bot.command(aliases=["ci"])
async def channelinfo(ctx, channel: discord.TextChannel = None):
    ch = channel or ctx.channel
    e = discord.Embed(title=f"Channel: {ch.name}")
    e.add_field(name="ID", value=ch.id, inline=True)
    e.add_field(name="Type", value=str(ch.type), inline=True)
    e.add_field(name="Created", value=ch.created_at.strftime("%Y-%m-%d"), inline=True)
    e.add_field(name="NSFW", value=ch.is_nsfw(), inline=True)
    await ctx.send(embed=e)

@bot.command(aliases=["ei"])
async def emojiinfo(ctx, emoji: discord.Emoji):
    e = discord.Embed(title=f"Emoji: {emoji.name}")
    e.add_field(name="ID", value=emoji.id, inline=True)
    e.add_field(name="Animated", value=emoji.animated, inline=True)
    e.add_field(name="Created", value=emoji.created_at.strftime("%Y-%m-%d"), inline=True)
    e.set_thumbnail(url=emoji.url)
    await ctx.send(embed=e)

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! `{round(bot.latency*1000)}ms`")

@bot.command()
async def invite(ctx):
    url = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions.all())
    await ctx.send(f"<{url}>")

@bot.command()
async def say(ctx, *, text):
    await ctx.message.delete()
    await ctx.send(text)

@bot.command()
async def embed(ctx, *, text):
    await ctx.message.delete()
    await ctx.send(embed=discord.Embed(description=text, color=discord.Color.random()))

@bot.command()
async def poll(ctx, question, *options):
    if len(options)>10: return await ctx.send("Max 10 options")
    if not options: return await ctx.send("Need options")
    em = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    d = "\n".join(f"{em[i]} {opt}" for i,opt in enumerate(options))
    msg = await ctx.send(embed=discord.Embed(title=f"📊 {question}", description=d))
    for i in range(len(options)): await msg.add_reaction(em[i])

@bot.command()
async def remind(ctx, duration: str, *, reminder):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration.")
    await ctx.send(f"⏰ Reminder set for `{duration}`.")
    await asyncio.sleep(d.total_seconds())
    await ctx.send(f"⏰ {ctx.author.mention}: {reminder}")

@bot.command()
async def timer(ctx, duration: str):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration.")
    await ctx.send(f"Timer started for `{duration}`.")
    await asyncio.sleep(d.total_seconds())
    await ctx.send(f"⏰ Timer done! {ctx.author.mention}")

# ─── TAGS (CarlBot-style) ───
@bot.command(aliases=["t"])
async def tag(ctx, name: str):
    conn = db(); c = conn.cursor()
    c.execute("SELECT content,uses FROM tags WHERE name=? AND guild_id=?", (name.lower(), ctx.guild.id))
    row = c.fetchone()
    if not row: return await ctx.send("Tag not found."), conn.close()
    c.execute("UPDATE tags SET uses=uses+1 WHERE name=? AND guild_id=?", (name.lower(), ctx.guild.id))
    conn.commit(); conn.close()
    await ctx.send(row["content"])

@bot.command(aliases=["tc","tagadd","+tag"])
async def tagcreate(ctx, name: str, *, content):
    conn = db(); c = conn.cursor()
    c.execute("SELECT 1 FROM tags WHERE name=? AND guild_id=?", (name.lower(), ctx.guild.id))
    if c.fetchone(): return await ctx.send("Tag already exists."), conn.close()
    c.execute("INSERT INTO tags VALUES (?,?,?,?,?,?)", (name.lower(), ctx.guild.id, content, ctx.author.id, 0, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await ctx.send(f"Tag `{name}` created.")

@bot.command(aliases=["te"])
async def tagedit(ctx, name: str, *, content):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE tags SET content=? WHERE name=? AND guild_id=?", (content, name.lower(), ctx.guild.id))
    conn.commit(); conn.close()
    await ctx.send(f"Tag `{name}` updated.")

@bot.command(aliases=["td","tagrm","-tag"])
async def tagdelete(ctx, name: str):
    conn = db(); c = conn.cursor()
    c.execute("DELETE FROM tags WHERE name=? AND guild_id=?", (name.lower(), ctx.guild.id))
    conn.commit(); conn.close()
    await ctx.send(f"Tag `{name}` deleted.")

@bot.command(aliases=["tags","tl"])
async def taglist(ctx):
    conn = db(); c = conn.cursor()
    c.execute("SELECT name,uses FROM tags WHERE guild_id=? ORDER BY uses DESC", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    if not rows: return await ctx.send("No tags.")
    await ctx.send("**Tags:** " + ", ".join(f"`{r['name']}` ({r['uses']})" for r in rows)[:2000])

@bot.command(aliases=["ti"])
async def taginfo(ctx, name: str):
    conn = db(); c = conn.cursor()
    c.execute("SELECT * FROM tags WHERE name=? AND guild_id=?", (name.lower(), ctx.guild.id))
    row = c.fetchone(); conn.close()
    if not row: return await ctx.send("Tag not found.")
    a = ctx.guild.get_member(row["author_id"])
    e = discord.Embed(title=f"Tag: {name}")
    e.add_field(name="Author", value=a.mention if a else "Unknown")
    e.add_field(name="Uses", value=row["uses"])
    e.add_field(name="Created", value=row["created"][:10])
    await ctx.send(embed=e)

# ─── APPLICATIONS ───
def _is_valid_answer(text):
    t = text.strip().lower()
    if len(t) < 2:
        return False
    if len(set(t)) <= 1:
        return False
    blocklist = {"nigger","nigga","n1gger","n1gga","fag","faggot","chink","kike","spic","retard","jew","coon","dyke","tranny","wetback","gook","raghead","sandnigger","nigg","fagot","faget","niger","niga","negro","neger","k1ke","sp1c","r3tard","retarted","f4g","f4ggot","idk","n/a","na","none","nah","nope","pass","skip","dk","dunno","idkk","???","??","...","what","dont know","don't know"}
    words = set(t.split())
    if words & blocklist:
        return False
    return True

@bot.command()
async def apply(ctx):
    if not ctx.guild:
        return await ctx.send("Use this command in the server.")
    if ctx.author.id in app_sessions:
        return await ctx.send("You already have an application in progress. Check your DMs.")
    conn = db(); c = conn.cursor()
    c.execute("SELECT 1 FROM applications WHERE user_id=? AND guild_id=? AND status='pending'", (ctx.author.id, ctx.guild.id))
    if c.fetchone():
        conn.close()
        return await ctx.send("You already have a pending application. Please wait for a staff member to review it.")
    conn.close()
    try:
        await ctx.message.delete()
    except Exception:
        pass
    try:
        dm = await ctx.author.create_dm()
    except Exception:
        return await ctx.send("I couldn't open a DM with you. Please enable DMs from server members.")
    form = (
        "─── 𝕬𝕻𝕻𝕷𝕴𝕮𝕬𝕮𝕴𝕺𝕹 𝕱𝕺𝕽𝕸 ───\n\n"
        "If you seek to earn your place within Cártel Nueva Alianza, complete the application below. We value skill, loyalty, and the ability to follow orders.\n\n"
        "HOW TO ANSWER:\n"
        "• Reply in **ONE message** with all 10 answers\n"
        "• Number each answer like this:\n"
        "  1. YourDiscordName\n"
        "  2. YourRobloxName\n"
        "  3. Chicblocko / No Mercy\n"
        "  4. 500\n"
        "• Each answer must be at least a few words — single letters like 'h' or 'idk' will be rejected\n"
        "• Be honest and detailed. Staff will review your answers carefully.\n\n"
        "[ APPLICATION FOR CÁRTEL NUEVA ALIANZA ]\n\n"
        "1. Discord Username:\n"
        "2. Roblox Username:\n"
        "3. Which game are you applying for? (Chicblocko / No Mercy / Both):\n"
        "4. Current In-Game Level:\n"
        "5. Owned Gamepasses / Spawns (List all that apply):\n"
        "6. Experience in hood games like CB or NM / Similar?\n"
        "7. Are you currently in any other factions? (If so, list them):\n"
        "8. What is your primary playstyle? (e.g., Tactical/Combat, Logistics/Grinding, Enforcement):\n"
        "9. Are you willing to prioritize the Cartel's objectives over solo play?\n"
        "10. Why Cártel Nueva Alianza?\n\n"
        "─── APPLICATION POLICY ───\n\n"
        "• Expectations: Do not ping High Ranks for a response. Your application will be reviewed in the order it was received.\n"
        "• Requirements: You must have a working microphone for raids and coordination.\n"
        "• Integrity: Any lies discovered in your application regarding your history, gamepasses, or level will result in an immediate denial.\n\n"
        "Loyalty is our currency. Prove your worth."
    )
    await dm.send(form)
    app_sessions[ctx.author.id] = ctx.guild.id
    def check(m):
        return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
    try:
        msg = await bot.wait_for('message', check=check, timeout=600)
    except asyncio.TimeoutError:
        await dm.send("Application timed out after 10 minutes. Run `%apply` again if you still want to apply.")
        return
    finally:
        app_sessions.pop(ctx.author.id, None)
    lines = [l.strip() for l in msg.content.split('\n') if l.strip()]
    found = {}
    bad = []
    for line in lines:
        first = line.split(None, 1)[0] if line.split(None, 1) else ""
        num = first.rstrip('.):')
        if num.isdigit():
            n = int(num)
            if 1 <= n <= 10:
                rest = line[len(first):].strip()
                found[n] = rest
    if len(found) < 10:
        missing = [str(i) for i in range(1,11) if i not in found]
        await dm.send(f"Your application is incomplete. Missing answers for questions: {', '.join(missing)}. Please run `%apply` again and answer ALL 10 questions with the proper format.")
        return
    for n in range(1,11):
        if not _is_valid_answer(found[n]):
            bad.append(str(n))
    if bad:
        await dm.send(f"Your answers for question(s) {', '.join(bad)} are too short, lazy, or don't make sense. Please run `%apply` again and provide real, detailed answers. Single letters like 'h' or 'idk' are not accepted.")
        return
    formatted = "\n".join(f"**{n}.** {found[n]}" for n in range(1,11))
    pending_ch = discord.utils.get(ctx.guild.text_channels, name="pending-applications")
    if not pending_ch:
        await dm.send("Error: the #pending-applications channel doesn't exist. Contact staff.")
        return
    e = discord.Embed(title="New Application", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
    e.set_author(name=f"{ctx.author} ({ctx.author.id})", icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None)
    e.description = formatted[:4000]
    staff_role = discord.utils.get(ctx.guild.roles, name="STAFF")
    mention = staff_role.mention if staff_role else "@STAFF"
    try:
        app_msg = await pending_ch.send(f"{mention} New Application {ctx.author.mention}", embed=e)
    except Exception:
        await dm.send("Error submitting your application. Please contact staff.")
        return
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO applications (user_id,guild_id,content,status,ts,message_id) VALUES (?,?,?,?,?,?)",
              (ctx.author.id, ctx.guild.id, msg.content, "pending", datetime.datetime.utcnow().isoformat(), app_msg.id))
    conn.commit(); conn.close()
    await dm.send("Your application has been submitted successfully! A staff member will review it shortly.")

async def _resolve_member(ctx, arg):
    try:
        return await commands.MemberConverter().convert(ctx, arg)
    except commands.BadArgument:
        try:
            uid = int(arg)
            m = ctx.guild.get_member(uid)
            if m: return m
            return await ctx.guild.fetch_member(uid)
        except (ValueError, discord.NotFound, discord.HTTPException):
            return None

@bot.command()
@require_role("STAFF")
async def accept(ctx, *, target: str):
    member = await _resolve_member(ctx, target)
    if not member:
        return await ctx.send("User not found in this server. Mention them or use their User ID.")
    conn = db(); c = conn.cursor()
    c.execute("SELECT id FROM applications WHERE user_id=? AND guild_id=? AND status='pending'", (member.id, ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send("No pending application found for this user.")
    app_id = row["id"]
    c.execute("UPDATE applications SET status='accepted' WHERE id=?", (app_id,))
    conn.commit(); conn.close()
    member_role = discord.utils.get(ctx.guild.roles, name="Member")
    outsider_role = discord.utils.get(ctx.guild.roles, name="OUTSIDER & UNRANKED")
    if member_role:
        try: await member.add_roles(member_role, reason="Application accepted")
        except discord.Forbidden: pass
    if outsider_role:
        try: await member.remove_roles(outsider_role, reason="Application accepted")
        except discord.Forbidden: pass
    general = discord.utils.get(ctx.guild.text_channels, name="『💬』general-chat")
    if general:
        await general.send(f"Welcome {member.mention} enjoy your stay")
    await ctx.send(f"Accepted {member.mention}'s application.")

@bot.command()
@require_role("STAFF")
async def deny(ctx, *, target: str):
    member = await _resolve_member(ctx, target)
    if not member:
        return await ctx.send("User not found in this server. Mention them or use their User ID.")
    conn = db(); c = conn.cursor()
    c.execute("SELECT id FROM applications WHERE user_id=? AND guild_id=? AND status='pending'", (member.id, ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send("No pending application found for this user.")
    app_id = row["id"]
    c.execute("UPDATE applications SET status='denied' WHERE id=?", (app_id,))
    conn.commit(); conn.close()
    try:
        await member.send("Your application for Cártel Nueva Alianza has been denied. You are welcome to reapply at a later time.")
    except Exception:
        pass
    await ctx.send(f"Denied {member.mention}'s application.")

# ─── INVITES ───
@bot.command(aliases=["inv"])
async def invites(ctx, member: discord.Member = None):
    target = member or ctx.author
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM invites WHERE inviter_id=? AND guild_id=?", (target.id, ctx.guild.id))
    total = c.fetchone()[0]
    c.execute("SELECT invitee_id, ts FROM invites WHERE inviter_id=? AND guild_id=? ORDER BY ts DESC LIMIT 10", (target.id, ctx.guild.id))
    rows = c.fetchall(); conn.close()
    e = discord.Embed(title=f"Invite Stats: {target}", color=discord.Color.teal())
    e.add_field(name="Total Invites", value=str(total), inline=False)
    if rows:
        recent = []
        for r in rows:
            u = ctx.guild.get_member(r["invitee_id"])
            recent.append(f"{u.mention if u else r['invitee_id']} — {r['ts'][:10]}")
        e.add_field(name="Recent Joins", value="\n".join(recent), inline=False)
    await ctx.send(embed=e)

@bot.command(aliases=["invlb","ilb"])
async def inviteleaderboard(ctx):
    conn = db(); c = conn.cursor()
    c.execute("SELECT inviter_id, COUNT(*) as cnt FROM invites WHERE guild_id=? GROUP BY inviter_id ORDER BY cnt DESC LIMIT 10", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    if not rows:
        return await ctx.send("No invite data yet.")
    lines = []
    for i, r in enumerate(rows, 1):
        u = ctx.guild.get_member(r["inviter_id"])
        name = u.mention if u else f"<@{r['inviter_id']}>"
        lines.append(f"**{i}.** {name} — {r['cnt']} invites")
    e = discord.Embed(title="Invite Leaderboard", color=discord.Color.gold())
    e.description = "\n".join(lines)
    await ctx.send(embed=e)

# ─── TICKETS ───
@bot.command()
async def ticket(ctx, *, reason="No reason provided"):
    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    for role in guild.roles:
        if role.name == "STAFF":
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
    try:
        ch = await guild.create_text_channel(f"ticket-{ctx.author.name}", overwrites=overwrites, reason=f"Ticket by {ctx.author}: {reason}")
    except discord.Forbidden:
        return await ctx.send("I don't have permission to create channels.")
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO tickets (guild_id, channel_id, user_id, created_ts) VALUES (?,?,?,?)",
              (guild.id, ch.id, ctx.author.id, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await ch.send(f"{ctx.author.mention} Ticket created. Reason: `{reason}`\nStaff can use `%claim` to take ownership and `%rename <name>` to rename this channel.")
    log_ch = get_log_channel(guild, "ticket-logs")
    if log_ch:
        await log_ch.send(f"Ticket created by {ctx.author.mention} in {ch.mention} | Reason: `{reason}`")
    await ctx.send(f"Ticket created: {ch.mention}", delete_after=10)

@bot.command()
@require_role("STAFF")
async def claim(ctx):
    if not ctx.channel.name.startswith("ticket-"):
        return await ctx.send("This command only works in ticket channels.")
    conn = db(); c = conn.cursor()
    c.execute("UPDATE tickets SET claimer_id=? WHERE channel_id=?", (ctx.author.id, ctx.channel.id))
    conn.commit(); conn.close()
    await ctx.send(f"Ticket claimed by {ctx.author.mention}.")

@bot.command()
@require_role("STAFF")
async def rename(ctx, *, new_name):
    if not ctx.channel.name.startswith("ticket-"):
        return await ctx.send("This command only works in ticket channels.")
    try:
        await ctx.channel.edit(name=f"ticket-{new_name}")
        await ctx.send(f"Renamed to `ticket-{new_name}`.")
    except discord.Forbidden:
        await ctx.send("Missing permissions to rename channel.")

# ─── WAR RANKS ───
@bot.command()
@require_role("Lieutenant")
async def warrank(ctx, member: discord.Member, *, rank: str):
    rank = rank.lower().strip()
    valid = {"fragger", "lead", "assembly", "subs", "sub", "raid"}
    if rank not in valid and rank + "s" not in valid:
        return await ctx.send("Invalid rank. Use: fragger, lead, assembly, subs, raid")
    if rank == "sub": rank = "subs"
    if rank == "subs": rank = "subs"
    # Determine NM or CB
    await ctx.send("Which faction? Reply `NM` (NoMercy) or `CB` (Chicblocko).")
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.upper() in ("NM", "CB")
    try:
        msg = await bot.wait_for('message', check=check, timeout=30)
    except asyncio.TimeoutError:
        return await ctx.send("Timed out.")
    faction = msg.content.upper()
    role_map = {
        "fragger": f"{faction} | War Fragger",
        "lead": f"{faction} | War Lead",
        "assembly": f"{faction} | War Assembly",
        "subs": f"{faction} | WAR SUBS",
        "raid": f"{faction} | RAID TEAM",
    }
    role_name = role_map.get(rank)
    if not role_name:
        return await ctx.send("Invalid rank mapping.")
    role = get_role_by_name(ctx.guild, role_name)
    if not role:
        return await ctx.send(f"Role `{role_name}` not found.")
    try:
        await member.add_roles(role)
        await ctx.send(f"Assigned **{role_name}** to {member.mention}.")
    except discord.Forbidden:
        await ctx.send("Missing permissions to assign role.")

# ─── ROBLOX VERIFICATION ───
@bot.command()
async def verifyroblox(ctx, roblox_id: int):
    conn = db(); c = conn.cursor()
    c.execute("SELECT 1 FROM roblox_verify WHERE user_id=?", (ctx.author.id,))
    if c.fetchone():
        conn.close()
        return await ctx.send("You are already verified. Contact staff if you need to update your profile.")
    c.execute("INSERT INTO roblox_verify (user_id, roblox_id, verified_ts) VALUES (?,?,?)",
              (ctx.author.id, roblox_id, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    link = f"https://www.roblox.com/users/{roblox_id}/profile"
    await ctx.send(f"Roblox profile verified: {link}")

# ─── EVENTS / DEPLOYMENTS / RAIDS ───
async def _post_announcement(ctx, event_type, name, game_name, time_str, target_channel_name):
    ch = get_log_channel(ctx.guild, target_channel_name)
    if not ch:
        return await ctx.send(f"Channel #{target_channel_name} not found.")
    conn = db(); c = conn.cursor()
    c.execute("SELECT roblox_id FROM roblox_verify WHERE user_id=?", (ctx.author.id,))
    row = c.fetchone(); conn.close()
    if not row or not row["roblox_id"]:
        return await ctx.send("You must verify your Roblox profile first. Use `%verifyroblox <your_roblox_id>`.")
    dt, unix_ts = parse_event_time(time_str)
    if not dt:
        return await ctx.send("Invalid time format. Use relative like `15m`, `2h`, `1d` or absolute like `22:30` or `10:30 PM`.")
    # Get next event number for this guild from persisted event_logs
    conn = db(); c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(num),0) FROM event_logs WHERE guild_id=?", (ctx.guild.id,))
    max_num = c.fetchone()[0]
    num = max_num + 1
    conn.commit(); conn.close()
    roblox_id = row["roblox_id"]
    roblox_link = f"https://www.roblox.com/users/{roblox_id}/profile"
    e = discord.Embed(title=f"#{num} {'EVENT' if event_type=='event' else event_type.upper()}: {name}", color=discord.Color.red())
    e.add_field(name="Host", value=ctx.author.mention, inline=False)
    if game_name:
        e.add_field(name="Game", value=game_name, inline=False)
    # Discord timestamps auto-convert to viewer's local timezone
    e.add_field(name="Starts", value=f"<t:{unix_ts}:F> (<t:{unix_ts}:R>)", inline=False)
    e.add_field(name="RSVP", value="React with ✅ to confirm attendance.", inline=False)
    e.set_footer(text=f"Host Roblox: {roblox_link}")
    msg = await ch.send("@everyone", embed=e)
    await msg.add_reaction("✅")
    active_events[msg.id] = {
        "type": event_type,
        "name": name,
        "game": game_name or "",
        "host_id": ctx.author.id,
        "guild_id": ctx.guild.id,
        "reactors": set(),
        "channel_id": ch.id,
        "start_ts": datetime.datetime.utcnow().isoformat(),
        "event_time": dt.isoformat(),
        "unix_ts": unix_ts,
        "num": num
    }
    await ctx.send(f"{event_type.upper()} #{num} posted in {ch.mention}.")

async def _start_ping(ctx, event_type, number_str: str):
    if not number_str.startswith("#"):
        return await ctx.send(f"Number must start with #. Example: `%{event_type}start #2`")
    num_str = number_str[1:].strip()
    try:
        num = int(num_str)
    except ValueError:
        return await ctx.send(f"Invalid number. Use `%{event_type}start #2`.")
    msg_id, ev = _find_event_by_num(ctx.guild.id, num)
    if not ev:
        return await ctx.send(f"No active {event_type} #{num} found.")
    if ev["type"] != event_type:
        return await ctx.send(f"#{num} is a {ev['type']}, not a {event_type}.")
    if ev["host_id"] != ctx.author.id:
        return await ctx.send("Only the host can start this.")
    ch = bot.get_channel(ev["channel_id"])
    if not ch:
        return await ctx.send("Announcement channel not found.")
    conn = db(); c = conn.cursor()
    c.execute("SELECT roblox_id FROM roblox_verify WHERE user_id=?", (ev["host_id"],))
    row = c.fetchone(); conn.close()
    if row and row["roblox_id"]:
        link = f"https://www.roblox.com/users/{row['roblox_id']}/profile"
        pings = [f"<@{uid}>" for uid in ev["reactors"] if uid != ctx.author.id]
        if pings:
            await ch.send(f"{' '.join(pings[:75])}\n**{ev['name']}** is starting now! Join the host: {link}")
        else:
            await ch.send(f"**{ev['name']}** is starting now! Join the host: {link}")
    await ctx.send(f"{event_type.upper()} #{num} start ping sent.")

async def _end_event(ctx, event_type, number_str: str):
    if not number_str.startswith("#"):
        return await ctx.send(f"Number must start with #. Example: `%{event_type}end #2`")
    num_str = number_str[1:].strip()
    try:
        num = int(num_str)
    except ValueError:
        return await ctx.send(f"Invalid number. Use `%{event_type}end #2`.")
    msg_id, ev = _find_event_by_num(ctx.guild.id, num)
    if not ev:
        return await ctx.send(f"No active {event_type} #{num} found.")
    if ev["type"] != event_type:
        return await ctx.send(f"#{num} is a {ev['type']}, not a {event_type}.")
    if ev["host_id"] != ctx.author.id:
        return await ctx.send("Only the host can end this.")
    await ctx.send(f"Ending #{num}. Who attended? Mention all attendees (e.g., @user1 @user2). You have 5 minutes.")
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel
    try:
        msg = await bot.wait_for('message', check=check, timeout=300)
    except asyncio.TimeoutError:
        return await ctx.send("Timed out. Event not ended.")
    attendees = set(m.id for m in msg.mentions)
    reactors = ev["reactors"]
    no_shows = reactors - attendees - {ctx.author.id}
    for user_id in no_shows:
        now = datetime.datetime.utcnow().isoformat()
        conn = db(); c = conn.cursor()
        c.execute("INSERT INTO strikes (user_id,guild_id,reason,ts,mod_id) VALUES (?,?,?,?,?)",
                  (user_id, ctx.guild.id, f"Failure to attend a reacted {event_type}", now, ctx.author.id))
        conn.commit(); sid = c.lastrowid
        c.execute("SELECT COUNT(*) FROM strikes WHERE user_id=? AND guild_id=?", (user_id, ctx.guild.id))
        count = c.fetchone()[0]
        conn.close()
        member = ctx.guild.get_member(user_id)
        if member:
            try:
                await member.send(f"You were striked for failing to attend the **{ev['name']}** {event_type}. Strike {count}/3.")
            except Exception:
                pass
        if count >= 3 and member:
            expiry = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat()
            conn = db(); c = conn.cursor()
            c.execute("INSERT INTO tempbans (user_id,guild_id,expiry_timestamp) VALUES (?,?,?)", (user_id, ctx.guild.id, expiry))
            conn.commit(); conn.close()
            try:
                await member.ban(reason="3 strikes - 7 day tempban")
                asyncio.create_task(_delayed_unban(ctx.guild, user_id, expiry, 7*24*60*60))
            except Exception:
                pass
    log_ch = get_log_channel(ctx.guild, "deployment-event-logs")
    if log_ch:
        et_display = "Event" if event_type == "event" else event_type.capitalize()
        title = f"#{num} {ev['name']} {et_display}"
        lines = [f"• **Attended:** {', '.join(f'<@{u}>' for u in attendees) or 'None'}"]
        if no_shows:
            lines.append(f"• **No-Shows (Striked):** {', '.join(f'<@{u}>' for u in no_shows)}")
        e = discord.Embed(title=title, color=discord.Color.blue())
        e.description = "\n".join(lines)
        await log_ch.send(embed=e)
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO event_logs (num,event_type,name,game_name,host_id,guild_id,channel_id,message_id,start_ts,end_ts,attendees,no_shows) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (num, event_type, ev["name"], ev["game"], ev["host_id"], ev["guild_id"], ev["channel_id"], msg_id, ev["start_ts"], datetime.datetime.utcnow().isoformat(), ','.join(str(u) for u in attendees), ','.join(str(u) for u in no_shows)))
    conn.commit(); conn.close()
    del active_events[msg_id]
    await ctx.send(f"{event_type.upper()} #{num} ended. {len(attendees)} attended. {len(no_shows)} no-shows striked.")

@bot.command()
@require_role("STAFF")
async def event(ctx, name: str, *, time_str: str):
    await _post_announcement(ctx, "event", name, None, time_str, "『⚡』events")

@bot.command()
@require_role("STAFF")
async def eventstart(ctx, number: str):
    await _start_ping(ctx, "event", number)

@bot.command()
@require_role("STAFF")
async def eventend(ctx, number: str):
    await _end_event(ctx, "event", number)

@bot.command()
@require_role("STAFF")
async def deployment(ctx, game_name: str, *, time_str: str):
    await _post_announcement(ctx, "deployment", f"{game_name} Deployment", game_name, time_str, "『🎪』deployments-raids")

@bot.command()
@require_role("STAFF")
async def deploymentstart(ctx, number: str):
    await _start_ping(ctx, "deployment", number)

@bot.command()
@require_role("STAFF")
async def deploymentend(ctx, number: str):
    await _end_event(ctx, "deployment", number)

@bot.command()
@require_role("STAFF")
async def raid(ctx, game_name: str, *, time_str: str):
    await _post_announcement(ctx, "raid", f"{game_name} Raid", game_name, time_str, "『🎪』deployments-raids")

@bot.command()
@require_role("STAFF")
async def raidstart(ctx, number: str):
    await _start_ping(ctx, "raid", number)

@bot.command()
@require_role("STAFF")
async def raidend(ctx, number: str):
    await _end_event(ctx, "raid", number)

async def _cancel_event(ctx, event_type, number_str: str, reason: str):
    if not number_str.startswith("#"):
        return await ctx.send(f"Number must start with #. Example: `%{event_type}cancel #2 <reason>`")
    num_str = number_str[1:].strip()
    try:
        num = int(num_str)
    except ValueError:
        return await ctx.send(f"Invalid number. Use `%{event_type}cancel #2 <reason>`.")
    msg_id, ev = _find_event_by_num(ctx.guild.id, num)
    if not ev:
        return await ctx.send(f"No active {event_type} #{num} found.")
    if ev["type"] != event_type:
        return await ctx.send(f"#{num} is a {ev['type']}, not a {event_type}.")
    if ev["host_id"] != ctx.author.id:
        return await ctx.send("Only the host can cancel this.")
    ch = bot.get_channel(ev["channel_id"])
    if ch:
        await ch.send(f"**{ev['name']}** ({event_type.upper()} #{num}) has been **cancelled** by {ctx.author.mention}. Reason: `{reason}`")
    log_ch = get_log_channel(ctx.guild, "deployment-event-logs")
    if log_ch:
        ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        await log_ch.send(f"[{ts}] action; {event_type}cancel | #{num} {ev['name']} | host; <@{ev['host_id']}> | reason; {reason}")
    del active_events[msg_id]
    await ctx.send(f"{event_type.upper()} #{num} cancelled. Reason: `{reason}`")

@bot.command()
@require_role("STAFF")
async def eventcancel(ctx, number: str, *, reason: str):
    await _cancel_event(ctx, "event", number, reason)

@bot.command()
@require_role("STAFF")
async def deploymentcancel(ctx, number: str, *, reason: str):
    await _cancel_event(ctx, "deployment", number, reason)

@bot.command()
@require_role("STAFF")
async def raidcancel(ctx, number: str, *, reason: str):
    await _cancel_event(ctx, "raid", number, reason)

# ─── HELP ───
@bot.command()
async def help(ctx):
    e = discord.Embed(title="Command List", color=discord.Color.blurple())
    e.add_field(name="Moderation", value="**Capo**: unban\n**Senior Lieutenant+**: ban, tempban\n**Lieutenant+**: addrole, removerole\n**Head Admin+**: kick, nick\n**Admin+**: purge, slowmode, lock, unlock\n**Moderator+**: mute, unmute\n**STAFF+**: warn, warnlist, unwarn, clearwarnings, strike, removestrike", inline=False)
    e.add_field(name="Fun", value="8ball, coinflip, roll, rps, choose, rate, reverse, mock, cat, dog, meme", inline=False)
    e.add_field(name="Utility", value="avatar, userinfo, serverinfo, roleinfo, channelinfo, emojiinfo, ping, invite, say, embed, poll, remind, timer", inline=False)
    e.add_field(name="Tags", value="tag, tagcreate, tagedit, tagdelete, taglist, taginfo", inline=False)
    e.add_field(name="Invites", value="invites, inviteleaderboard", inline=False)
    e.add_field(name="Applications", value="apply, accept, deny", inline=False)
    e.add_field(name="Tickets", value="ticket, claim, rename", inline=False)
    e.add_field(name="War Ranks", value="warrank (Lieutenant+)", inline=False)
    e.add_field(name="Events", value="`%event <name> <time>` → `%eventstart #N` → `%eventend #N` / `%eventcancel #N <reason>` (STAFF+)", inline=False)
    e.add_field(name="Deployments", value="`%deployment <game> <time>` → `%deploymentstart #N` → `%deploymentend #N` / `%deploymentcancel #N <reason>` (STAFF+)", inline=False)
    e.add_field(name="Raids", value="`%raid <game> <time>` → `%raidstart #N` → `%raidend #N` / `%raidcancel #N <reason>` (STAFF+)", inline=False)
    e.add_field(name="Roblox", value="verifyroblox", inline=False)
    await ctx.send(embed=e)

@bot.command()
async def dbstatus(ctx):
    if ctx.author.id != ctx.guild.owner_id:
        return await ctx.send("Owner only.")
    db_type = "PostgreSQL" if IS_PG else "SQLite (local file)"
    e = discord.Embed(title="Database Status", color=discord.Color.green() if IS_PG else discord.Color.orange())
    e.add_field(name="Type", value=db_type, inline=False)
    try:
        conn = db(); c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM warns")
        n = c.fetchone()[0]
        conn.close()
        e.add_field(name="Connection", value="OK", inline=False)
        e.add_field(name="Total Warns in DB", value=str(n), inline=False)
    except Exception as err:
        e.add_field(name="Connection", value=f"FAILED: `{err}`", inline=False)
    await ctx.send(embed=e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(f"{error}")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission for that.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument provided.")
    elif isinstance(error, commands.CommandInvokeError):
        original = error.original
        await ctx.send(f"Command crashed: `{type(original).__name__}: {original}`")
    else:
        print(f"Error: {error}")

bot.run(TOKEN)
