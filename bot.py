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
        # PostgreSQL: CREATE IF NOT EXISTS to preserve data on restarts
        pg_tables = [
            "CREATE TABLE IF NOT EXISTS levels(user_id BIGINT,guild_id BIGINT,xp BIGINT,level BIGINT,PRIMARY KEY(user_id,guild_id))",
            "CREATE TABLE IF NOT EXISTS tags(name TEXT,guild_id BIGINT,content TEXT,author_id BIGINT,uses BIGINT DEFAULT 0,created TEXT)",
            "CREATE TABLE IF NOT EXISTS warns(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,reason TEXT,mod_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS strikes(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,reason TEXT,ts TEXT,mod_id BIGINT,log_channel_id BIGINT,log_message_id BIGINT)",
            "CREATE TABLE IF NOT EXISTS tempbans(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,expiry_timestamp TEXT)",
            "CREATE TABLE IF NOT EXISTS applications(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,content TEXT,status TEXT DEFAULT 'pending',ts TEXT,message_id BIGINT)",
            "CREATE TABLE IF NOT EXISTS invites(id SERIAL PRIMARY KEY,inviter_id BIGINT,invitee_id BIGINT,guild_id BIGINT,code TEXT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS roblox_verify(user_id BIGINT PRIMARY KEY,roblox_id BIGINT,roblox_username TEXT,verified_ts TEXT)",
            "CREATE TABLE IF NOT EXISTS tickets(id SERIAL PRIMARY KEY,guild_id BIGINT,channel_id BIGINT UNIQUE,user_id BIGINT,claimer_id BIGINT,status TEXT DEFAULT 'open',created_ts TEXT)",
            "CREATE TABLE IF NOT EXISTS activity_checks(id SERIAL PRIMARY KEY,guild_id BIGINT,message_id BIGINT,channel_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS event_logs(id SERIAL PRIMARY KEY,num BIGINT,event_type TEXT,name TEXT,game_name TEXT,host_id BIGINT,guild_id BIGINT,channel_id BIGINT,message_id BIGINT,start_ts TEXT,end_ts TEXT,attendees TEXT,no_shows TEXT)",
            "CREATE TABLE IF NOT EXISTS war_logs(id SERIAL PRIMARY KEY,result TEXT,opponent TEXT,score TEXT,mvps TEXT,image_url TEXT,guild_id BIGINT,ts TEXT,mod_id BIGINT)",
            "CREATE TABLE IF NOT EXISTS staff_notes(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,note TEXT,author_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS quotes(id SERIAL PRIMARY KEY,guild_id BIGINT,user_id BIGINT,content TEXT,author_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS giveaways(id SERIAL PRIMARY KEY,channel_id BIGINT,message_id BIGINT,prize TEXT,end_ts TEXT,winner_id BIGINT,guild_id BIGINT)",
            "CREATE TABLE IF NOT EXISTS sticky_messages(id SERIAL PRIMARY KEY,channel_id BIGINT,message_id BIGINT,content TEXT,guild_id BIGINT)",
            "CREATE TABLE IF NOT EXISTS activity(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,message_count BIGINT DEFAULT 0,week_start TEXT)",
            "CREATE TABLE IF NOT EXISTS mod_actions(id SERIAL PRIMARY KEY,mod_id BIGINT,action_type TEXT,target_id BIGINT,guild_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS pending_approvals(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS app_votes(id SERIAL PRIMARY KEY,app_id BIGINT,voter_id BIGINT,guild_id BIGINT,vote TEXT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS last_seen(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS afk(id SERIAL PRIMARY KEY,user_id BIGINT,guild_id BIGINT,reason TEXT,ts TEXT)",
        ]
        for sql in pg_tables:
            c.execute(sql)
        # Add new columns safely if tables already exist
        try:
            c.execute("ALTER TABLE event_logs ADD COLUMN IF NOT EXISTS num BIGINT")
        except Exception:
            pass
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
            "CREATE TABLE IF NOT EXISTS war_logs(id INTEGER PRIMARY KEY,result TEXT,opponent TEXT,score TEXT,mvps TEXT,image_url TEXT,guild_id INT,ts TEXT,mod_id INT)",
            "CREATE TABLE IF NOT EXISTS staff_notes(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,note TEXT,author_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY,guild_id INT,user_id INT,content TEXT,author_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS giveaways(id INTEGER PRIMARY KEY,channel_id INT,message_id INT,prize TEXT,end_ts TEXT,winner_id INT,guild_id INT)",
            "CREATE TABLE IF NOT EXISTS sticky_messages(id INTEGER PRIMARY KEY,channel_id INT,message_id INT,content TEXT,guild_id INT)",
            "CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,message_count INT DEFAULT 0,week_start TEXT)",
            "CREATE TABLE IF NOT EXISTS mod_actions(id INTEGER PRIMARY KEY,mod_id INT,action_type TEXT,target_id INT,guild_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS pending_approvals(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS app_votes(id INTEGER PRIMARY KEY,app_id INT,voter_id INT,guild_id INT,vote TEXT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS last_seen(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,ts TEXT)",
            "CREATE TABLE IF NOT EXISTS afk(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,reason TEXT,ts TEXT)",
        ]
        for t in sqlite_tables:
            c.execute(t)
        for col in ("log_channel_id", "log_message_id"):
            try: c.execute(f"ALTER TABLE strikes ADD COLUMN {col} INTEGER")
            except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE event_logs ADD COLUMN num INTEGER")
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
SLUR_PATTERNS = ["nigger", "nigga", "faggot", "chink", "kike", "spic", "wetback", "coon", "gook", "raghead", "sandnigger", "tranny"]
active_events = {}  # {message_id: {type, name, game, host_id, guild_id, reactors:set(), channel_id, num}}
# Helper to find active event by guild+number
def _find_event_by_num(guild_id, num):
    for msg_id, ev in active_events.items():
        if ev.get("guild_id") == guild_id and ev.get("num") == num:
            return msg_id, ev
    return None, None

ticket_warned = {}  # {channel_id: warned_timestamp}
lockdown_state = {}  # {guild_id: {channel_id: discord.PermissionOverwrite or None}}

milestone_sent = {}  # {guild_id: {member_count: True}}

# Nuke protection tracking
nuke_channel_deletes = {}  # {guild_id: [(timestamp, executor_id)]}
nuke_ban_tracker = {}  # {guild_id: [(timestamp, mod_id)]}

# Command cooldowns (3s global)
cmd_cooldowns = {}  # {user_id: last_cmd_ts}

# AFK tracking
afk_users = {}  # {user_id: {"reason": str, "ts": str}}

# Bot start time for uptime
bot_start_time = datetime.datetime.utcnow()

# @everyone/@here cooldown
everyone_cooldown = {}  # {guild_id: last_ping_ts}
stats_vc_ids = {}  # {guild_id: {"members": channel_id, "bots": channel_id}}
giveaway_tasks = {}  # {message_id: asyncio.Task}
vc_lobbies = {}  # {message_id: guild_id}
pending_app_msgs = {}  # {app_id: message_id} cache for vote tracking

def contains_slur(text):
    t = text.lower()
    return any(slur in t for slur in SLUR_PATTERNS)

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

async def auto_close_tickets():
    await bot.wait_until_ready()
    while True:
        await asyncio.sleep(600)  # check every 10 minutes
        now = datetime.datetime.utcnow()
        conn = db(); c = conn.cursor()
        c.execute("SELECT channel_id, guild_id FROM tickets WHERE status='open'")
        rows = c.fetchall(); conn.close()
        for channel_id, guild_id in rows:
            ch = bot.get_channel(channel_id)
            if not ch:
                continue
            try:
                msgs = [m async for m in ch.history(limit=1)]
                if not msgs:
                    continue
                last_msg = msgs[0]
                inactivity = (now - last_msg.created_at.replace(tzinfo=None)).total_seconds()
                if inactivity >= 24 * 3600:
                    # Close ticket
                    transcript = []
                    async for m in ch.history(limit=500, oldest_first=True):
                        ts = m.created_at.strftime("%Y-%m-%d %H:%M")
                        transcript.append(f"[{ts}] {m.author.name}: {m.content}")
                    transcript_text = "\n".join(transcript) or "No messages."
                    log_ch = get_log_channel(ch.guild, "ticket-logs")
                    if log_ch:
                        e = discord.Embed(title=f"Ticket Closed (Inactivity) — #{ch.name}", color=discord.Color.orange())
                        e.add_field(name="Transcript", value=transcript_text[:1000] + ("..." if len(transcript_text) > 1000 else ""), inline=False)
                        await log_ch.send(embed=e)
                    await ch.delete(reason="Auto-closed after 24h inactivity")
                    ticket_warned.pop(channel_id, None)
                    conn = db(); c = conn.cursor()
                    c.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (channel_id,))
                    conn.commit(); conn.close()
                elif inactivity >= 23 * 3600 and channel_id not in ticket_warned:
                    await ch.send("⚠️ This ticket will be closed in 1 hour due to inactivity.")
                    ticket_warned[channel_id] = now.isoformat()
            except Exception:
                pass

async def warn_decay():
    await bot.wait_until_ready()
    while True:
        await asyncio.sleep(86400)  # run daily
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).isoformat()
        conn = db(); c = conn.cursor()
        c.execute("DELETE FROM warns WHERE ts <= ?", (cutoff,))
        deleted = c.rowcount
        conn.commit(); conn.close()
        if deleted:
            for guild in bot.guilds:
                log_ch = get_log_channel(guild, "punishment-logs")
                if log_ch:
                    await log_ch.send(f"[{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}] Auto-decay: {deleted} warn(s) older than 14 days removed.")

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
    bot.loop.create_task(auto_close_tickets())
    bot.loop.create_task(warn_decay())
    bot.loop.create_task(status_cycle())
    bot.loop.create_task(inactivity_purge())
    print(f"Logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_member_join(member):
    guild = member.guild
    # Alt account detection
    acc_age = (datetime.datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
    if acc_age < 7:
        conn = db(); c = conn.cursor()
        c.execute("INSERT INTO pending_approvals (user_id,guild_id,ts) VALUES (?,?,?)", (member.id, guild.id, datetime.datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
        mod_ch = get_log_channel(guild, "moderation")
        if not mod_ch:
            mod_ch = get_log_channel(guild, "punishment-logs")
        if mod_ch:
            e = discord.Embed(title="Pending Manual Approval", description=f"{member.mention} joined with an account only **{acc_age} days** old. Staff must approve.", color=discord.Color.orange())
            e.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"), inline=False)
            e.add_field(name="Action", value="Use `%approve @user` or `%reject @user`", inline=False)
            await mod_ch.send(embed=e)
        await member.send("Your account is too new. Staff must manually approve your join. Please wait.")
        return
    # Auto-role
    auto_role = get_role_by_name(guild, "OUTSIDER & UNRANKED")
    if auto_role:
        try:
            await member.add_roles(auto_role)
        except Exception:
            pass
    # Nickname slur filter
    if member.nick and contains_slur(member.nick):
        try:
            await member.edit(nick="Moderated Nickname")
        except Exception:
            pass
    # Welcome message + milestones
    welcome_ch = discord.utils.get(guild.text_channels, name="『👋🏽』welcome-and-fairwell")
    if welcome_ch:
        e = discord.Embed(title="Welcome!", description=f"{member.mention} has joined the server.", color=discord.Color.green())
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"Member count: {guild.member_count}")
        await welcome_ch.send(embed=e)
    # Member milestones
    milestones = [100, 500, 1000, 2500, 5000]
    for m in milestones:
        if guild.member_count >= m and not milestone_sent.get(guild.id, {}).get(m):
            if guild.id not in milestone_sent:
                milestone_sent[guild.id] = {}
            milestone_sent[guild.id][m] = True
            if welcome_ch:
                await welcome_ch.send(f"🎉 **Server milestone reached!** {m} members! 🎉")
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
async def on_member_remove(member):
    guild = member.guild
    welcome_ch = discord.utils.get(guild.text_channels, name="『👋🏽』welcome-and-fairwell")
    if welcome_ch:
        e = discord.Embed(title="Goodbye", description=f"{member.mention} ({member.name}) has left the server.", color=discord.Color.red())
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"Member count: {guild.member_count}")
        await welcome_ch.send(embed=e)

@bot.event
async def on_member_update(before, after):
    guild = after.guild
    # Nickname change logging
    if before.nick != after.nick:
        if after.nick and contains_slur(after.nick):
            try:
                await after.edit(nick="Moderated Nickname")
            except Exception:
                pass
        log_ch = get_log_channel(guild, "message-logs")
        if log_ch:
            e = discord.Embed(title="Nickname Changed", color=discord.Color.blurple())
            e.add_field(name="User", value=after.mention, inline=False)
            e.add_field(name="Before", value=before.nick or before.name, inline=True)
            e.add_field(name="After", value=after.nick or after.name, inline=True)
            await log_ch.send(embed=e)
    # Role change logging
    if before.roles != after.roles:
        removed = set(before.roles) - set(after.roles)
        added = set(after.roles) - set(before.roles)
        log_ch = get_log_channel(guild, "promotion-logs")
        if log_ch and (removed or added):
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            if added:
                for role in added:
                    await log_ch.send(f"[{ts}] ROLE ADDED | {after.mention} → **{role.name}**")
            if removed:
                for role in removed:
                    await log_ch.send(f"[{ts}] ROLE REMOVED | {after.mention} ← **{role.name}**")
    # Boost logging
    if not before.premium_since and after.premium_since:
        welcome_ch = discord.utils.get(guild.text_channels, name="『👋🏽』welcome-and-fairwell")
        if welcome_ch:
            await welcome_ch.send(f"🎉 {after.mention} just boosted the server! Thank you for the support! 🚀")
    # Staff welcome DM
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
async def on_guild_channel_delete(channel):
    if not channel.guild:
        return
    guild = channel.guild
    now = datetime.datetime.utcnow()
    # Fetch audit log to find who deleted
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            if entry.target and entry.target.id == channel.id:
                executor = entry.user
                if executor and executor.id == bot.user.id:
                    return
                if guild.id not in nuke_channel_deletes:
                    nuke_channel_deletes[guild.id] = []
                nuke_channel_deletes[guild.id].append((now, executor.id if executor else None))
                # Remove entries older than 60 seconds
                nuke_channel_deletes[guild.id] = [(ts, eid) for ts, eid in nuke_channel_deletes[guild.id] if (now - ts).total_seconds() <= 60]
                if len(nuke_channel_deletes[guild.id]) >= 3:
                    mod_ch = get_log_channel(guild, "moderation")
                    if not mod_ch:
                        mod_ch = get_log_channel(guild, "punishment-logs")
                    if mod_ch:
                        await mod_ch.send(f"@here 🚨 **NUKE ALERT**: {len(nuke_channel_deletes[guild.id])} channels deleted within 60 seconds! Potential raid in progress.")
                    # Auto-lockdown all channels
                    for ch in guild.text_channels:
                        if ch.id == (mod_ch.id if mod_ch else 0):
                            continue
                        try:
                            perms = ch.overwrites_for(guild.default_role)
                            if perms.send_messages is not False:
                                lockdown_state.setdefault(guild.id, {})[ch.id] = perms
                                await ch.set_permissions(guild.default_role, send_messages=False)
                        except Exception:
                            pass
                    if mod_ch:
                        await mod_ch.send("🔒 Auto-lockdown activated due to suspected nuke. Use `%unlockdown` to restore.")
                    nuke_channel_deletes[guild.id] = []
                break
    except Exception:
        pass

@bot.event
async def on_member_ban(guild, user):
    now = datetime.datetime.utcnow()
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
            if entry.target and entry.target.id == user.id:
                mod = entry.user
                if mod and mod.id == bot.user.id:
                    return
                if guild.id not in nuke_ban_tracker:
                    nuke_ban_tracker[guild.id] = []
                nuke_ban_tracker[guild.id].append((now, mod.id if mod else None))
                nuke_ban_tracker[guild.id] = [(ts, mid) for ts, mid in nuke_ban_tracker[guild.id] if (now - ts).total_seconds() <= 60]
                if len(nuke_ban_tracker[guild.id]) >= 3:
                    mod_ch = get_log_channel(guild, "moderation")
                    if not mod_ch:
                        mod_ch = get_log_channel(guild, "punishment-logs")
                    if mod_ch:
                        await mod_ch.send(f"@here 🚨 **MASS BAN ALERT**: {len(nuke_ban_tracker[guild.id])} users banned within 60 seconds! Potential compromised account.")
                    nuke_ban_tracker[guild.id] = []
                break
    except Exception:
        pass

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    log_ch = get_log_channel(message.guild, "message-logs")
    if log_ch:
        e = discord.Embed(title="Message Deleted", color=discord.Color.red())
        e.add_field(name="Author", value=message.author.mention, inline=False)
        e.add_field(name="Channel", value=message.channel.mention, inline=False)
        e.add_field(name="Content", value=message.content[:1024] or "(empty/attachment)", inline=False)
        await log_ch.send(embed=e)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    log_ch = get_log_channel(before.guild, "message-logs")
    if log_ch:
        e = discord.Embed(title="Message Edited", color=discord.Color.orange())
        e.add_field(name="Author", value=before.author.mention, inline=False)
        e.add_field(name="Channel", value=before.channel.mention, inline=False)
        e.add_field(name="Before", value=before.content[:1024] or "(empty)", inline=False)
        e.add_field(name="After", value=after.content[:1024] or "(empty)", inline=False)
        e.add_field(name="Jump", value=f"[Jump to message]({after.jump_url})", inline=False)
        await log_ch.send(embed=e)

CHEAT_TERMS = ["matcha", "cookie", "cookies", "potassium", "matrix", "esp"]
BLACKLIST_WORDS = set(SLUR_PATTERNS + CHEAT_TERMS)

def contains_blacklist(text):
    t = text.lower()
    return any(word in t for word in BLACKLIST_WORDS)

@bot.event
async def on_message(msg):
    if msg.author.bot: return
    if not msg.guild:
        return
    # Command cooldown (3s, high rank bypass)
    now = datetime.datetime.utcnow()
    last = cmd_cooldowns.get(msg.author.id)
    if last and (now - last).total_seconds() < 3:
        if not is_lieutenant(msg.author):
            return
    if msg.content.startswith('%'):
        cmd_cooldowns[msg.author.id] = now
    # Last seen tracking
    conn = db(); c = conn.cursor()
    c.execute("SELECT id FROM last_seen WHERE user_id=? AND guild_id=?", (msg.author.id, msg.guild.id))
    row = c.fetchone()
    if row:
        c.execute("UPDATE last_seen SET ts=? WHERE id=?", (now.isoformat(), row["id"]))
    else:
        c.execute("INSERT INTO last_seen (user_id,guild_id,ts) VALUES (?,?,?)", (msg.author.id, msg.guild.id, now.isoformat()))
    conn.commit(); conn.close()
    # Activity tracking
    if msg.content and len(msg.content) > 3:
        week_start = now.strftime("%Y-%m-%d")
        conn = db(); c = conn.cursor()
        c.execute("SELECT id, message_count FROM activity WHERE user_id=? AND guild_id=? AND week_start=?", (msg.author.id, msg.guild.id, week_start))
        row = c.fetchone()
        if row:
            c.execute("UPDATE activity SET message_count = message_count + 1 WHERE id=?", (row["id"],))
        else:
            c.execute("INSERT INTO activity (user_id, guild_id, message_count, week_start) VALUES (?,?,?,?)", (msg.author.id, msg.guild.id, 1, week_start))
        conn.commit(); conn.close()
    # AFK auto-reply
    if msg.mentions:
        for u in msg.mentions:
            if u.id in afk_users:
                try:
                    await msg.channel.send(f"{u.mention} is AFK: {afk_users[u.id]['reason']}", delete_after=8)
                except Exception:
                    pass
    # Remove AFK if user sends a message
    if msg.author.id in afk_users:
        afk_users.pop(msg.author.id, None)
        conn = db(); c = conn.cursor()
        c.execute("DELETE FROM afk WHERE user_id=? AND guild_id=?", (msg.author.id, msg.guild.id))
        conn.commit(); conn.close()
        try:
            await msg.channel.send(f"Welcome back {msg.author.mention}, your AFK has been removed.", delete_after=3)
        except Exception:
            pass
    # @everyone/@here cooldown (30m, high rank bypass)
    if "@everyone" in msg.content or "@here" in msg.content:
        if not is_lieutenant(msg.author):
            last_ping = everyone_cooldown.get(msg.guild.id)
            if last_ping and (now - last_ping).total_seconds() < 1800:
                remaining = int(1800 - (now - last_ping).total_seconds())
                try:
                    await msg.delete()
                except Exception:
                    pass
                try:
                    await msg.channel.send(f"{msg.author.mention} `@everyone`/`@here` is on cooldown. Wait {remaining//60}m {remaining%60}s.", delete_after=5)
                except Exception:
                    pass
                return
            everyone_cooldown[msg.guild.id] = now
    # Message blacklist
    if contains_blacklist(msg.content):
        try:
            await msg.delete()
            await msg.channel.send(f"{msg.author.mention} That language is not allowed here.", delete_after=5)
        except Exception:
            pass
        return
    # Clear ticket inactivity warning if message sent in ticket channel
    if msg.channel.name.startswith("ticket-") and msg.channel.id in ticket_warned:
        ticket_warned.pop(msg.channel.id, None)
    # Sticky messages
    conn = db(); c = conn.cursor()
    c.execute("SELECT message_id, content FROM sticky_messages WHERE channel_id=?", (msg.channel.id,))
    sticky = c.fetchone(); conn.close()
    if sticky:
        try:
            old = await msg.channel.fetch_message(sticky["message_id"])
            await old.delete()
        except Exception:
            pass
        try:
            new_msg = await msg.channel.send(sticky["content"])
            conn = db(); c = conn.cursor()
            c.execute("UPDATE sticky_messages SET message_id=? WHERE channel_id=?", (new_msg.id, msg.channel.id))
            conn.commit(); conn.close()
        except Exception:
            pass
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
    ts = datetime.datetime.utcnow().isoformat()
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO mod_actions (mod_id,action_type,target_id,guild_id,ts) VALUES (?,?,?,?,?)",
              (mod.id, action, member.id if isinstance(member, discord.Member) else member, guild.id, ts))
    conn.commit(); conn.close()
    if not log_ch:
        return
    ts_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    r = f" | reason; {reason}" if reason else ""
    await log_ch.send(f"[{ts_str}] action; {action} | user; {member.mention if isinstance(member, discord.Member) else member} | mod; {mod.mention}{r}")

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
async def purge(ctx, amount: int, member: discord.Member = None):
    try:
        await ctx.message.delete()
    except Exception:
        pass
    if member:
        def check(m):
            return m.author.id == member.id
        deleted = await ctx.channel.purge(limit=amount, check=check)
        m = await ctx.send(f"Cleared {len(deleted)} messages from {member.mention}")
    else:
        deleted = await ctx.channel.purge(limit=amount)
        m = await ctx.send(f"Cleared {len(deleted)} messages")
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

@bot.command()
@require_role("STAFF")
async def add(ctx, member: discord.Member):
    if not ctx.channel.name.startswith("ticket-"):
        return await ctx.send("This command only works in ticket channels.")
    try:
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await ctx.send(f"Added {member.mention} to this ticket.")
    except discord.Forbidden:
        await ctx.send("Missing permissions to modify channel permissions.")

@bot.command()
@require_role("STAFF")
async def remove(ctx, member: discord.Member):
    if not ctx.channel.name.startswith("ticket-"):
        return await ctx.send("This command only works in ticket channels.")
    try:
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(f"Removed {member.mention} from this ticket.")
    except discord.Forbidden:
        await ctx.send("Missing permissions to modify channel permissions.")

@bot.command()
@require_role("STAFF")
async def close(ctx, *, reason="No reason provided"):
    if not ctx.channel.name.startswith("ticket-"):
        return await ctx.send("This command only works in ticket channels.")
    ch = ctx.channel
    transcript = []
    async for m in ch.history(limit=500, oldest_first=True):
        ts = m.created_at.strftime("%Y-%m-%d %H:%M")
        transcript.append(f"[{ts}] {m.author.name}: {m.content}")
    transcript_text = "\n".join(transcript) or "No messages."
    log_ch = get_log_channel(ch.guild, "ticket-logs")
    if log_ch:
        e = discord.Embed(title=f"Ticket Closed — #{ch.name}", color=discord.Color.orange())
        e.add_field(name="Closed by", value=ctx.author.mention, inline=False)
        e.add_field(name="Reason", value=reason, inline=False)
        e.add_field(name="Transcript", value=transcript_text[:1000] + ("..." if len(transcript_text) > 1000 else ""), inline=False)
        await log_ch.send(embed=e)
    conn = db(); c = conn.cursor()
    c.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (ch.id,))
    conn.commit(); conn.close()
    ticket_warned.pop(ch.id, None)
    await ch.delete(reason=f"Ticket closed by {ctx.author}: {reason}")

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

# ─── LOCKDOWN ───
@bot.command()
@require_role("STAFF")
async def lockdown(ctx):
    guild = ctx.guild
    locked = []
    for ch in guild.text_channels:
        if ch.id == ctx.channel.id:
            continue
        overwrite = ch.overwrites_for(guild.default_role)
        prev = discord.PermissionOverwrite()
        prev.send_messages = overwrite.send_messages
        if lockdown_state.get(guild.id) is None:
            lockdown_state[guild.id] = {}
        lockdown_state[guild.id][ch.id] = prev
        try:
            await ch.set_permissions(guild.default_role, send_messages=False)
            locked.append(ch.mention)
        except Exception:
            pass
    await ctx.send(f"Lockdown activated in {len(locked)} channel(s). Use `%unlockdown` to restore.")

@bot.command()
@require_role("STAFF")
async def unlockdown(ctx):
    guild = ctx.guild
    restored = []
    if guild.id not in lockdown_state:
        return await ctx.send("No active lockdown to restore.")
    for ch_id, prev in lockdown_state[guild.id].items():
        ch = guild.get_channel(ch_id)
        if ch:
            try:
                await ch.set_permissions(guild.default_role, overwrite=prev if prev.send_messages is not None else None)
                restored.append(ch.mention)
            except Exception:
                pass
    lockdown_state.pop(guild.id, None)
    await ctx.send(f"Lockdown lifted in {len(restored)} channel(s).")

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
            await ch.send(f"{' '.join(pings[:75])}\nStarted {link}")
        else:
            await ch.send(f"Started {link}")
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
    e.add_field(name="Moderation", value="**Capo**: unban\n**Senior Lieutenant+**: ban, tempban\n**Lieutenant+**: addrole, removerole, promote, demote\n**Head Admin+**: kick, nick\n**Admin+**: purge, slowmode, lock, unlock, lockdown, unlockdown\n**Moderator+**: mute, unmute, tempmute\n**STAFF+**: warn, warnlist, unwarn, clearwarnings, strike, removestrike, approve, reject, modstats, note, notes (warns auto-decay after 14 days)", inline=False)
    e.add_field(name="Fun", value="8ball, coinflip, roll, rps, choose, rate, reverse, mock, cat, dog, meme", inline=False)
    e.add_field(name="Utility", value="avatar, userinfo, serverinfo, roleinfo, channelinfo, emojiinfo, ping, invite, say, embed, poll, remind, timer, inviteleaderboard, activity, attendance, topattendees, warstats, quote, quotes, seen, uptime", inline=False)
    e.add_field(name="Auto Features", value="Auto-role: OUTSIDER & UNRANKED | Welcome/Leave: 『👋🏽』welcome-and-fairwell | Message Logging: #message-logs | Warn Decay: 14 days | Alt Detection: <7 days | Nuke Protection | Inactivity Purge | Cmd Cooldown (3s) | @everyone cooldown (30m)", inline=False)
    e.add_field(name="Tags", value="tag, tagcreate, tagedit, tagdelete, taglist, taginfo", inline=False)
    e.add_field(name="Invites", value="invites, inviteleaderboard", inline=False)
    e.add_field(name="Applications", value="apply, staffapply, pendingapps, review (STAFF+)", inline=False)
    e.add_field(name="Tickets", value="ticket, claim, rename, add, remove, close (STAFF+)", inline=False)
    e.add_field(name="War Ranks", value="warrank (Lieutenant+), warwin, warloss (STAFF+)", inline=False)
    e.add_field(name="Events", value="`%event <name> <time>` → `%eventstart #N` → `%eventend #N` / `%eventcancel #N <reason>` (STAFF+)", inline=False)
    e.add_field(name="Deployments", value="`%deployment <game> <time>` → `%deploymentstart #N` → `%deploymentend #N` / `%deploymentcancel #N <reason>` (STAFF+)", inline=False)
    e.add_field(name="Raids", value="`%raid <game> <time>` → `%raidstart #N` → `%raidend #N` / `%raidcancel #N <reason>` (STAFF+)", inline=False)
    e.add_field(name="Engagement", value="suggest, giveaway (STAFF+), sticky (STAFF+), archive (STAFF+), vclobby (STAFF+), afk", inline=False)
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

@bot.command()
@require_role("STAFF")
async def approve(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT id FROM pending_approvals WHERE user_id=? AND guild_id=?", (member.id, ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send(f"{member.mention} is not in the pending approval list.")
    c.execute("DELETE FROM pending_approvals WHERE id=?", (row["id"],))
    conn.commit(); conn.close()
    auto_role = get_role_by_name(ctx.guild, "OUTSIDER & UNRANKED")
    if auto_role:
        try: await member.add_roles(auto_role)
        except Exception: pass
    welcome_ch = discord.utils.get(ctx.guild.text_channels, name="『👋🏽』welcome-and-fairwell")
    if welcome_ch:
        await welcome_ch.send(f"Welcome {member.mention}! Approved by {ctx.author.mention}.")
    await ctx.send(f"Approved {member.mention}.")

@bot.command()
@require_role("STAFF")
async def reject(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT id FROM pending_approvals WHERE user_id=? AND guild_id=?", (member.id, ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send(f"{member.mention} is not in the pending approval list.")
    c.execute("DELETE FROM pending_approvals WHERE id=?", (row["id"],))
    conn.commit(); conn.close()
    try:
        await member.send("Your join request was rejected by server staff.")
    except Exception:
        pass
    try:
        await member.kick(reason="Rejected by staff (alt account)")
    except Exception:
        await ctx.send(f"Rejected {member.mention}. Could not kick — remove manually if needed.")
        return
    await ctx.send(f"Rejected and kicked {member.mention}.")

@bot.command()
@require_role("STAFF")
async def modstats(ctx, member: discord.Member = None):
    target = member or ctx.author
    conn = db(); c = conn.cursor()
    c.execute("SELECT action_type, COUNT(*) as cnt FROM mod_actions WHERE mod_id=? AND guild_id=? GROUP BY action_type", (target.id, ctx.guild.id))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return await ctx.send(f"No mod actions found for {target.mention}.")
    e = discord.Embed(title=f"Mod Stats — {target.name}", color=discord.Color.blurple())
    total = 0
    for r in rows:
        e.add_field(name=r["action_type"].capitalize(), value=str(r["cnt"]), inline=True)
        total += r["cnt"]
    e.set_footer(text=f"Total actions: {total}")
    await ctx.send(embed=e)

# ─── WAR TRACKER ───
@bot.command()
@require_role("STAFF")
async def warwin(ctx, opponent: str, score: str, mvp1: discord.Member, mvp2: discord.Member = None, mvp3: discord.Member = None, *, image_url: str = ""):
    mvps = [m for m in (mvp1, mvp2, mvp3) if m]
    mvp_names = ", ".join(m.display_name for m in mvps)
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO war_logs (result,opponent,score,mvps,image_url,guild_id,ts,mod_id) VALUES (?,?,?,?,?,?,?,?)",
              ("win", opponent, score, mvp_names, image_url or None, ctx.guild.id, datetime.datetime.utcnow().isoformat(), ctx.author.id))
    conn.commit(); conn.close()
    log_ch = get_log_channel(ctx.guild, "『🏆』war-logs")
    if log_ch:
        e = discord.Embed(title="War Victory", color=discord.Color.gold())
        e.add_field(name="Opponent", value=opponent, inline=True)
        e.add_field(name="Score", value=score, inline=True)
        e.add_field(name="MVPs", value=mvp_names, inline=False)
        e.set_footer(text=f"Logged by {ctx.author.name}")
        if image_url:
            e.set_image(url=image_url)
        await log_ch.send(embed=e)
    await ctx.send(f"War win logged vs **{opponent}** — Score: `{score}` — MVPs: {mvp_names}")

@bot.command()
@require_role("STAFF")
async def warloss(ctx, opponent: str, score: str):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO war_logs (result,opponent,score,mvps,image_url,guild_id,ts,mod_id) VALUES (?,?,?,?,?,?,?,?)",
              ("loss", opponent, score, None, None, ctx.guild.id, datetime.datetime.utcnow().isoformat(), ctx.author.id))
    conn.commit(); conn.close()
    log_ch = get_log_channel(ctx.guild, "『🏆』war-logs")
    if log_ch:
        e = discord.Embed(title="War Defeat", color=discord.Color.red())
        e.add_field(name="Opponent", value=opponent, inline=True)
        e.add_field(name="Score", value=score, inline=True)
        e.set_footer(text=f"Logged by {ctx.author.name}")
        await log_ch.send(embed=e)
    await ctx.send(f"War loss logged vs **{opponent}** — Score: `{score}`")

@bot.command()
async def warstats(ctx):
    conn = db(); c = conn.cursor()
    c.execute("SELECT result, COUNT(*) FROM war_logs WHERE guild_id=? GROUP BY result", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    wins = 0; losses = 0
    for r in rows:
        if r["result"] == "win": wins = r[1]
        elif r["result"] == "loss": losses = r[1]
    total = wins + losses
    ratio = f"{wins}/{total}" if total else "N/A"
    e = discord.Embed(title="War Stats", color=discord.Color.blurple())
    e.add_field(name="Wins", value=str(wins), inline=True)
    e.add_field(name="Losses", value=str(losses), inline=True)
    e.add_field(name="Record", value=ratio, inline=True)
    await ctx.send(embed=e)

# ─── STAFF NOTES ───
@bot.command()
@require_role("STAFF")
async def note(ctx, member: discord.Member, *, text: str):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO staff_notes (user_id,guild_id,note,author_id,ts) VALUES (?,?,?,?,?)",
              (member.id, ctx.guild.id, text, ctx.author.id, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await ctx.send(f"Note added for {member.mention}.")

@bot.command()
@require_role("STAFF")
async def notes(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT note, author_id, ts FROM staff_notes WHERE user_id=? AND guild_id=? ORDER BY id DESC", (member.id, ctx.guild.id))
    rows = c.fetchall(); conn.close()
    if not rows:
        return await ctx.send(f"No notes found for {member.mention}.")
    e = discord.Embed(title=f"Staff Notes — {member.name}", color=discord.Color.blurple())
    for i, r in enumerate(rows[:10]):
        author = ctx.guild.get_member(r["author_id"])
        a_name = author.mention if author else f"<@{r['author_id']}>"
        e.add_field(name=f"#{i+1} — {r['ts'][:10]}", value=f"By: {a_name}\n{r['note'][:500]}", inline=False)
    await ctx.send(embed=e)

# ─── PROMOTION / DEMOTION ───
@bot.command()
@require_role("Lieutenant")
async def promote(ctx, member: discord.Member, *, role_name: str):
    role = get_role_by_name(ctx.guild, role_name)
    if not role:
        return await ctx.send(f"Role `{role_name}` not found.")
    try:
        await member.add_roles(role)
        log_ch = get_log_channel(ctx.guild, "promotion-logs")
        if log_ch:
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            await log_ch.send(f"[{ts}] PROMOTION | {member.mention} → **{role.name}** by {ctx.author.mention}")
        await ctx.send(f"Promoted {member.mention} to **{role.name}**.")
    except discord.Forbidden:
        await ctx.send("Missing permissions.")

@bot.command()
@require_role("Lieutenant")
async def demote(ctx, member: discord.Member, *, role_name: str):
    role = get_role_by_name(ctx.guild, role_name)
    if not role:
        return await ctx.send(f"Role `{role_name}` not found.")
    if role not in member.roles:
        return await ctx.send(f"{member.mention} does not have that role.")
    try:
        await member.remove_roles(role)
        log_ch = get_log_channel(ctx.guild, "promotion-logs")
        if log_ch:
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            await log_ch.send(f"[{ts}] DEMOTION | {member.mention} ← **{role.name}** by {ctx.author.mention}")
        await ctx.send(f"Demoted {member.mention} from **{role.name}**.")
    except discord.Forbidden:
        await ctx.send("Missing permissions.")

# ─── ACTIVITY TRACKER ───
@bot.command()
async def activity(ctx, member: discord.Member = None):
    target = member or ctx.author
    week_start = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    conn = db(); c = conn.cursor()
    c.execute("SELECT message_count FROM activity WHERE user_id=? AND guild_id=? AND week_start=?", (target.id, ctx.guild.id, week_start))
    row = c.fetchone(); conn.close()
    count = row["message_count"] if row else 0
    await ctx.send(f"{target.mention} has sent **{count}** messages this week.")

# ─── ATTENDANCE STREAKS ───
@bot.command()
async def attendance(ctx, member: discord.Member = None):
    target = member or ctx.author
    conn = db(); c = conn.cursor()
    c.execute("SELECT attendees FROM event_logs WHERE guild_id=?", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    attended = 0
    streak = 0
    max_streak = 0
    for r in rows:
        atts = r["attendees"] or ""
        uids = set(int(x) for x in atts.split(",") if x.strip().isdigit())
        if target.id in uids:
            attended += 1
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    await ctx.send(f"{target.mention} attended **{attended}** event(s). Best streak: **{max_streak}**.")

@bot.command()
async def topattendees(ctx):
    conn = db(); c = conn.cursor()
    c.execute("SELECT attendees FROM event_logs WHERE guild_id=?", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    counts = {}
    for r in rows:
        atts = r["attendees"] or ""
        for uid in atts.split(","):
            uid = uid.strip()
            if uid.isdigit():
                counts[int(uid)] = counts.get(int(uid), 0) + 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    if not top:
        return await ctx.send("No attendance data yet.")
    lines = []
    for i, (uid, cnt) in enumerate(top, 1):
        u = ctx.guild.get_member(uid)
        name = u.mention if u else f"<@{uid}>"
        lines.append(f"**{i}.** {name} — {cnt} events")
    e = discord.Embed(title="Top Attendees", color=discord.Color.gold())
    e.description = "\n".join(lines)
    await ctx.send(embed=e)

# ─── SUGGESTIONS ───
@bot.command()
async def suggest(ctx, *, idea: str):
    ch = get_log_channel(ctx.guild, "『🤔』suggestions")
    if not ch:
        return await ctx.send("Suggestions channel not found.")
    e = discord.Embed(title="Suggestion", description=idea[:4000], color=discord.Color.blurple())
    e.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
    msg = await ch.send(embed=e)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await ctx.send(f"Suggestion posted in {ch.mention}.")

# ─── QUOTES ───
@bot.command()
async def quote(ctx, member: discord.Member, *, text: str):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO quotes (guild_id,user_id,content,author_id,ts) VALUES (?,?,?,?,?)",
              (ctx.guild.id, member.id, text, ctx.author.id, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await ctx.send(f"Quote saved from {member.mention}!")

@bot.command()
async def quotes(ctx, member: discord.Member = None):
    conn = db(); c = conn.cursor()
    if member:
        c.execute("SELECT content, author_id, ts FROM quotes WHERE guild_id=? AND user_id=? ORDER BY id DESC", (ctx.guild.id, member.id))
    else:
        c.execute("SELECT content, user_id, author_id, ts FROM quotes WHERE guild_id=? ORDER BY id DESC LIMIT 20", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    if not rows:
        return await ctx.send("No quotes found.")
    e = discord.Embed(title="Quotes", color=discord.Color.blurple())
    for i, r in enumerate(rows[:10]):
        q = r["content"][:200]
        uid = r.get("user_id", member.id if member else None)
        u = ctx.guild.get_member(uid) if uid else None
        name = u.mention if u else f"<@{uid}>"
        e.add_field(name=f"#{i+1} — {name}", value=q, inline=False)
    await ctx.send(embed=e)

# ─── STICKY MESSAGES ───
@bot.command()
@require_role("STAFF")
async def sticky(ctx, *, text: str):
    conn = db(); c = conn.cursor()
    c.execute("SELECT message_id FROM sticky_messages WHERE channel_id=?", (ctx.channel.id,))
    old = c.fetchone()
    if old:
        try:
            msg = await ctx.channel.fetch_message(old["message_id"])
            await msg.delete()
        except Exception:
            pass
    msg = await ctx.channel.send(text)
    if old:
        c.execute("UPDATE sticky_messages SET message_id=?, content=? WHERE channel_id=?", (msg.id, text, ctx.channel.id))
    else:
        c.execute("INSERT INTO sticky_messages (channel_id,message_id,content,guild_id) VALUES (?,?,?,?)", (ctx.channel.id, msg.id, text, ctx.guild.id))
    conn.commit(); conn.close()
    await ctx.send("Sticky message set.", delete_after=3)

# ─── TIMED MUTE ───
@bot.command(aliases=["mtempmute"])
@commands.has_permissions(moderate_members=True)
@require_role("Moderator")
async def tempmute(ctx, member: discord.Member, duration: str, *, reason="No reason"):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration. Use `1h`, `30m`, `1d`, etc.")
    if d > datetime.timedelta(days=28): return await ctx.send("Max 28 days.")
    try:
        until = datetime.datetime.utcnow() + d
        await member.timeout(until, reason=f"Tempmute ({duration}): {reason}")
        await _log_punishment(ctx.guild, "tempmute", member, ctx.author, reason)
        await ctx.send(f"Tempmuted {member.mention} for `{duration}`. `{reason}`")
    except discord.Forbidden:
        await ctx.send("Missing permissions.")

# ─── CHANNEL ARCHIVE ───
@bot.command()
@require_role("STAFF")
async def archive(ctx):
    cat = discord.utils.get(ctx.guild.categories, name="Archives")
    if not cat:
        try:
            cat = await ctx.guild.create_category("Archives")
        except Exception:
            return await ctx.send("Could not create 'Archives' category.")
    try:
        await ctx.channel.edit(category=cat)
        await ctx.send(f"Archived {ctx.channel.mention}.")
    except Exception:
        await ctx.send("Could not archive channel.")

# ─── CUSTOM VC CREATOR ───
@bot.command()
@require_role("STAFF")
async def vclobby(ctx):
    e = discord.Embed(title="Create a Voice Channel", description="React with 🎤 to create your own temporary voice channel!", color=discord.Color.blurple())
    msg = await ctx.send(embed=e)
    await msg.add_reaction("🎤")
    vc_lobbies[msg.id] = ctx.guild.id

# ─── BULK ROLE MANAGEMENT ───
@bot.command()
@require_role("Lieutenant")
async def addrole(ctx, role: discord.Role, *members: discord.Member):
    if not members:
        return await ctx.send("Mention at least one user.")
    added = 0
    for m in members:
        try:
            await m.add_roles(role)
            added += 1
        except Exception:
            pass
    await ctx.send(f"Added **{role.name}** to {added}/{len(members)} user(s).")

@bot.command()
@require_role("Lieutenant")
async def removerole(ctx, role: discord.Role, *members: discord.Member):
    if not members:
        return await ctx.send("Mention at least one user.")
    removed = 0
    for m in members:
        try:
            await m.remove_roles(role)
            removed += 1
        except Exception:
            pass
    await ctx.send(f"Removed **{role.name}** from {removed}/{len(members)} user(s).")

# ─── GIVEAWAYS ───
@bot.command()
@require_role("STAFF")
async def giveaway(ctx, duration: str, *, prize: str):
    d = parse_dur(duration)
    if d is None:
        return await ctx.send("Invalid duration. Use `1h`, `30m`, `1d`, etc.")
    end = datetime.datetime.utcnow() + d
    e = discord.Embed(title="🎉 Giveaway", description=f"**Prize:** {prize}\n**Ends:** <t:{int(end.timestamp())}:R>\nReact with 🎉 to enter!", color=discord.Color.gold())
    msg = await ctx.send(embed=e)
    await msg.add_reaction("🎉")
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO giveaways (channel_id,message_id,prize,end_ts,guild_id) VALUES (?,?,?,?,?)",
              (ctx.channel.id, msg.id, prize, end.isoformat(), ctx.guild.id))
    conn.commit(); conn.close()
    task = bot.loop.create_task(_end_giveaway(msg.id, end))
    giveaway_tasks[msg.id] = task

async def _end_giveaway(msg_id, end_time):
    delay = (end_time - datetime.datetime.utcnow()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    conn = db(); c = conn.cursor()
    c.execute("SELECT channel_id, prize FROM giveaways WHERE message_id=?", (msg_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return
    ch = bot.get_channel(row["channel_id"])
    if not ch:
        c.execute("DELETE FROM giveaways WHERE message_id=?", (msg_id,))
        conn.commit(); conn.close(); return
    try:
        msg = await ch.fetch_message(msg_id)
        users = []
        for reaction in msg.reactions:
            if str(reaction.emoji) == "🎉":
                async for u in reaction.users():
                    if not u.bot:
                        users.append(u)
        if users:
            winner = random.choice(users)
            c.execute("UPDATE giveaways SET winner_id=? WHERE message_id=?", (winner.id, msg_id))
            conn.commit(); conn.close()
            await ch.send(f"🎉 Giveaway ended! **{winner.mention}** won **{row['prize']}**!")
        else:
            c.execute("DELETE FROM giveaways WHERE message_id=?", (msg_id,))
            conn.commit(); conn.close()
            await ch.send("Giveaway ended — no participants.")
    except Exception:
        c.execute("DELETE FROM giveaways WHERE message_id=?", (msg_id,))
        conn.commit(); conn.close()

# ─── APPLICATIONS RESTRUCTURE ───
async def _run_application(ctx, app_type, questions):
    if ctx.author.id in app_sessions:
        return await ctx.send("You already have an application in progress. Check your DMs.")
    conn = db(); c = conn.cursor()
    c.execute("SELECT 1 FROM applications WHERE user_id=? AND guild_id=? AND status='pending'", (ctx.author.id, ctx.guild.id))
    if c.fetchone():
        conn.close()
        return await ctx.send("You already have a pending application. Please wait for staff to review.")
    conn.close()
    try:
        await ctx.message.delete()
    except Exception:
        pass
    try:
        dm = await ctx.author.create_dm()
    except Exception:
        return await ctx.send("I couldn't open a DM with you. Please enable DMs from server members.")
    header = f"─── {'STAFF' if app_type == 'staff' else 'MEMBER'} APPLICATION ───\n\nReply in **ONE message** with all {len(questions)} answers numbered.\n\n"
    q_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    await dm.send(header + q_text + "\n\nBe detailed. Short or lazy answers will be rejected.")
    app_sessions[ctx.author.id] = ctx.guild.id
    def check(m):
        return m.author.id == ctx.author.id and isinstance(m.channel, discord.DMChannel)
    try:
        msg = await bot.wait_for('message', check=check, timeout=600)
    except asyncio.TimeoutError:
        app_sessions.pop(ctx.author.id, None)
        return await dm.send("Application timed out after 10 minutes.")
    app_sessions.pop(ctx.author.id, None)
    lines = [l.strip() for l in msg.content.split('\n') if l.strip()]
    found = {}
    bad = []
    for line in lines:
        parts = line.split(None, 1)
        if not parts:
            continue
        num = parts[0].rstrip('.):')
        if num.isdigit():
            n = int(num)
            if 1 <= n <= len(questions):
                rest = parts[1] if len(parts) > 1 else ""
                found[n] = rest
    if len(found) < len(questions):
        missing = [str(i) for i in range(1, len(questions)+1) if i not in found]
        await dm.send(f"Incomplete. Missing answers: {', '.join(missing)}. Run the command again.")
        return
    for n in range(1, len(questions)+1):
        if not _is_valid_answer(found[n]):
            bad.append(str(n))
    if bad:
        await dm.send(f"Answers for question(s) {', '.join(bad)} are too short or lazy. Run the command again with real answers.")
        return
    formatted = "\n".join(f"**{n}.** {found[n]}" for n in range(1, len(questions)+1))
    pending_ch = discord.utils.get(ctx.guild.text_channels, name="pending-applications")
    if not pending_ch:
        await dm.send("Error: the #pending-applications channel doesn't exist. Contact staff.")
        return
    e = discord.Embed(title=f"New {'Staff' if app_type == 'staff' else 'Member'} Application", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
    e.set_author(name=f"{ctx.author} ({ctx.author.id})", icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None)
    e.description = formatted[:4000]
    e.set_footer(text=f"Type: {app_type} | Use %review #N to vote")
    staff_role = discord.utils.get(ctx.guild.roles, name="STAFF")
    mention = staff_role.mention if staff_role else "@STAFF"
    try:
        app_msg = await pending_ch.send(f"{mention} New Application {ctx.author.mention}", embed=e)
        await app_msg.add_reaction("✅")
    except Exception:
        await dm.send("Error submitting your application. Contact staff.")
        return
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO applications (user_id,guild_id,content,status,ts,message_id) VALUES (?,?,?,?,?,?)",
              (ctx.author.id, ctx.guild.id, msg.content, "pending", datetime.datetime.utcnow().isoformat(), app_msg.id))
    app_id = c.lastrowid if not IS_PG else None
    if IS_PG:
        c.execute("SELECT id FROM applications WHERE user_id=? AND guild_id=? AND message_id=?", (ctx.author.id, ctx.guild.id, app_msg.id))
        app_id = c.fetchone()["id"]
    conn.commit(); conn.close()
    pending_app_msgs[app_id] = app_msg.id
    await dm.send("Your application has been submitted successfully! A staff member will review it shortly.")

MEMBER_QUESTIONS = [
    "Discord Username:",
    "Roblox Username:",
    "Which game are you applying for? (Chicblocko / No Mercy / Both):",
    "Current In-Game Level:",
    "Owned Gamepasses / Spawns (List all that apply):",
    "Experience in hood games like CB or NM / Similar?",
    "Are you currently in any other factions? (If so, list them):",
    "What is your primary playstyle? (e.g., Tactical/Combat, Logistics/Grinding, Enforcement):",
    "Are you willing to prioritize the Cartel's objectives over solo play?",
    "Why Cártel Nueva Alianza?"
]

STAFF_QUESTIONS = [
    "Discord Username:",
    "Roblox Username:",
    "What position are you applying for? (Moderator / Admin / etc.):",
    "How long have you been in the server?",
    "What experience do you have with moderation or leadership?",
    "Why do you want to join the staff team?",
    "How would you handle a rule-breaking member?",
    "What timezone are you in?"
]

@bot.command()
async def apply(ctx):
    if not ctx.guild:
        return await ctx.send("Use this command in the server.")
    await _run_application(ctx, "member", MEMBER_QUESTIONS)

@bot.command()
async def staffapply(ctx):
    if not ctx.guild:
        return await ctx.send("Use this command in the server.")
    await _run_application(ctx, "staff", STAFF_QUESTIONS)

@bot.command()
@require_role("STAFF")
async def pendingapps(ctx):
    conn = db(); c = conn.cursor()
    c.execute("SELECT id, user_id, ts, message_id FROM applications WHERE guild_id=? AND status='pending'", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    if not rows:
        return await ctx.send("No pending applications.")
    lines = []
    for r in rows:
        u = ctx.guild.get_member(r["user_id"])
        name = u.mention if u else f"<@{r['user_id']}>"
        lines.append(f"**#{r['id']}** — {name} — {r['ts'][:10]}")
    e = discord.Embed(title="Pending Applications", description="\n".join(lines), color=discord.Color.orange())
    e.set_footer(text="Use %review #<id> to view details and vote.")
    await ctx.send(embed=e)

@bot.command()
@require_role("STAFF")
async def review(ctx, app_id: int):
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id, content, status, message_id FROM applications WHERE id=? AND guild_id=?", (app_id, ctx.guild.id))
    row = c.fetchone()
    if not row:
        conn.close()
        return await ctx.send("Application not found.")
    if row["status"] != "pending":
        conn.close()
        return await ctx.send(f"This application is already **{row['status']}**.")
    c.execute("SELECT voter_id, vote FROM app_votes WHERE app_id=?", (app_id,))
    votes = c.fetchall(); conn.close()
    yes = sum(1 for v in votes if v["vote"] == "yes")
    no = sum(1 for v in votes if v["vote"] == "no")
    e = discord.Embed(title=f"Application #{app_id}", color=discord.Color.blurple())
    u = ctx.guild.get_member(row["user_id"])
    e.set_author(name=u.name if u else f"User {row['user_id']}", icon_url=u.display_avatar.url if u else None)
    e.description = row["content"][:4000] if row["content"] else "(no content)"
    e.add_field(name="Votes", value=f"✅ {yes} | ❌ {no}", inline=False)
    e.set_footer(text="React with ✅ to approve or ❌ to deny this application.")
    msg = await ctx.send(embed=e)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

@bot.event
async def on_raw_reaction_add(payload):
    # Custom VC lobby
    if str(payload.emoji) == "🎤" and payload.message_id in vc_lobbies:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        cat = discord.utils.get(guild.categories, name="Custom VCs")
        if not cat:
            try:
                cat = await guild.create_category("Custom VCs")
            except Exception:
                return
        try:
            vc = await guild.create_voice_channel(f"{member.display_name}'s VC", category=cat)
            await member.move_to(vc)
        except Exception:
            pass
        return
    # Application votes
    if str(payload.emoji) == "✅" and payload.guild_id:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot or not is_staff(member):
            return
        conn = db(); c = conn.cursor()
        c.execute("SELECT id, user_id, status FROM applications WHERE message_id=? AND guild_id=?", (payload.message_id, payload.guild_id))
        row = c.fetchone()
        if row and row["status"] == "pending":
            app_id = row["id"]
            c.execute("SELECT 1 FROM app_votes WHERE app_id=? AND voter_id=?", (app_id, payload.user_id))
            if not c.fetchone():
                c.execute("INSERT INTO app_votes (app_id,voter_id,guild_id,vote,ts) VALUES (?,?,?,?,?)",
                          (app_id, payload.user_id, payload.guild_id, "yes", datetime.datetime.utcnow().isoformat()))
                conn.commit()
                c.execute("SELECT COUNT(*) FROM app_votes WHERE app_id=? AND vote='yes'", (app_id,))
                yes_count = c.fetchone()[0]
                if yes_count >= 3:
                    c.execute("UPDATE applications SET status='accepted' WHERE id=?", (app_id,))
                    conn.commit()
                    target = guild.get_member(row["user_id"])
                    if target:
                        member_role = discord.utils.get(guild.roles, name="Member")
                        outsider_role = discord.utils.get(guild.roles, name="OUTSIDER & UNRANKED")
                        if member_role:
                            try: await target.add_roles(member_role)
                            except Exception: pass
                        if outsider_role:
                            try: await target.remove_roles(outsider_role)
                            except Exception: pass
                        general = discord.utils.get(guild.text_channels, name="『💬』general-chat")
                        if general:
                            await general.send(f"Welcome {target.mention} enjoy your stay")
        conn.close()
        return
    if str(payload.emoji) == "❌" and payload.guild_id:
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot or not is_staff(member):
            return
        conn = db(); c = conn.cursor()
        c.execute("SELECT id, user_id, status FROM applications WHERE message_id=? AND guild_id=?", (payload.message_id, payload.guild_id))
        row = c.fetchone()
        if row and row["status"] == "pending":
            app_id = row["id"]
            c.execute("SELECT 1 FROM app_votes WHERE app_id=? AND voter_id=?", (app_id, payload.user_id))
            if not c.fetchone():
                c.execute("INSERT INTO app_votes (app_id,voter_id,guild_id,vote,ts) VALUES (?,?,?,?,?)",
                          (app_id, payload.user_id, payload.guild_id, "no", datetime.datetime.utcnow().isoformat()))
                conn.commit()
                c.execute("SELECT COUNT(*) FROM app_votes WHERE app_id=? AND vote='no'", (app_id,))
                no_count = c.fetchone()[0]
                if no_count >= 3:
                    c.execute("UPDATE applications SET status='denied' WHERE id=?", (app_id,))
                    conn.commit()
        conn.close()
        return
    if str(payload.emoji) != "✅":
        return
    if payload.message_id in active_events:
        active_events[payload.message_id]["reactors"].add(payload.user_id)

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.category and before.channel.category.name == "Custom VCs":
        if len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Empty custom VC")
            except Exception:
                pass

@bot.command()
async def afk(ctx, *, reason: str = "AFK"):
    afk_users[ctx.author.id] = {"reason": reason, "ts": datetime.datetime.utcnow().isoformat()}
    conn = db(); c = conn.cursor()
    c.execute("DELETE FROM afk WHERE user_id=? AND guild_id=?", (ctx.author.id, ctx.guild.id))
    c.execute("INSERT INTO afk (user_id,guild_id,reason,ts) VALUES (?,?,?,?)", (ctx.author.id, ctx.guild.id, reason, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await ctx.send(f"{ctx.author.mention} is now AFK: {reason}")

@bot.command()
async def seen(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("SELECT ts FROM last_seen WHERE user_id=? AND guild_id=?", (member.id, ctx.guild.id))
    row = c.fetchone(); conn.close()
    if not row:
        return await ctx.send(f"No activity data for {member.mention}.")
    ts = datetime.datetime.fromisoformat(row["ts"])
    delta = datetime.datetime.utcnow() - ts
    hours = int(delta.total_seconds() // 3600)
    mins = int((delta.total_seconds() % 3600) // 60)
    ago = f"{hours}h {mins}m ago" if hours else f"{mins}m ago" if mins else "just now"
    await ctx.send(f"{member.mention} was last seen **{ago}** ({ts.strftime('%Y-%m-%d %H:%M UTC')}).")

@bot.command()
async def uptime(ctx):
    delta = datetime.datetime.utcnow() - bot_start_time
    hours = int(delta.total_seconds() // 3600)
    mins = int((delta.total_seconds() % 3600) // 60)
    secs = int(delta.total_seconds() % 60)
    await ctx.send(f"🤖 Uptime: **{hours}h {mins}m {secs}s**")

# ─── BACKGROUND TASKS ───
async def status_cycle():
    await bot.wait_until_ready()
    while True:
        for guild in bot.guilds:
            await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{guild.member_count} members | %help"))
            await asyncio.sleep(30)
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="%help"))
        await asyncio.sleep(30)

async def inactivity_purge():
    await bot.wait_until_ready()
    while True:
        await asyncio.sleep(86400)  # run daily
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).isoformat()
        warn_cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=23)).isoformat()
        for guild in bot.guilds:
            conn = db(); c = conn.cursor()
            # Get all members with last seen
            c.execute("SELECT user_id, ts FROM last_seen WHERE guild_id=?", (guild.id,))
            rows = c.fetchall(); conn.close()
            inactive = []
            warned = []
            for r in rows:
                member = guild.get_member(r["user_id"])
                if not member or member.bot:
                    continue
                # Skip staff+
                if is_lieutenant(member):
                    continue
                if r["ts"] < cutoff:
                    inactive.append(member)
                elif r["ts"] < warn_cutoff:
                    warned.append(member)
            # Send warnings
            for m in warned:
                try:
                    await m.send("⚠️ You have been inactive in the server for 23 days. You will be purged in 7 days if you remain inactive.")
                except Exception:
                    pass
            # Kick inactive
            for m in inactive:
                try:
                    await m.kick(reason="Inactivity purge (0 messages in 30 days)")
                except Exception:
                    pass
            if inactive or warned:
                log_ch = get_log_channel(guild, "moderation")
                if not log_ch:
                    log_ch = get_log_channel(guild, "punishment-logs")
                if log_ch:
                    await log_ch.send(f"[Inactivity Purge] Warned: {len(warned)} | Purged: {len(inactive)}")

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
