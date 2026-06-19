import discord
from discord.ext import commands
import os, sqlite3, datetime, asyncio, random, aiohttp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN or TOKEN == "your_bot_token_here":
    print("ERROR: BOT_TOKEN missing"); exit(1)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="%", intents=intents, help_command=None)
DB = "bot_data.db"

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS levels(user_id INT,guild_id INT,xp INT,level INT,PRIMARY KEY(user_id,guild_id))")
    c.execute("CREATE TABLE IF NOT EXISTS tags(name TEXT,guild_id INT,content TEXT,author_id INT,uses INT DEFAULT 0,created TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS warns(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,reason TEXT,mod_id INT,ts TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS strikes(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,reason TEXT,ts TEXT,mod_id INT,log_channel_id INT,log_message_id INT)")
    c.execute("CREATE TABLE IF NOT EXISTS tempbans(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,expiry_timestamp TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,user_id INT,guild_id INT,content TEXT,status TEXT DEFAULT 'pending',ts TEXT,message_id INT)")
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

def get_log_channel(guild, name): return discord.utils.get(guild.text_channels, name=name)

async def _delayed_unban(guild, user_id, expiry_str, delay):
    await asyncio.sleep(delay)
    conn = db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM tempbans WHERE user_id=? AND guild_id=? AND expiry_timestamp=?", (user_id, guild.id, expiry_str))
    if not c.fetchone():
        conn.close(); return
    try: await guild.unban(discord.Object(id=user_id), reason="Tempban expired")
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
            try: await guild.unban(discord.Object(id=user_id), reason="Tempban expired")
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

@bot.event
async def on_ready():
    init_db()
    await check_tempbans()
    print(f"Logged in as {bot.user} ({bot.user.id})")

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
    conn = db(); c = conn.cursor()
    c.execute("SELECT xp,level FROM levels WHERE user_id=? AND guild_id=?", (msg.author.id, msg.guild.id))
    row = c.fetchone(); add = random.randint(15,25)
    if row:
        xp, lvl = row["xp"]+add, row["level"]
        nl = get_lvl(xp)
        if nl > lvl:
            await msg.channel.send(f"{msg.author.mention} leveled up to **Level {nl}**!")
            lvl = nl
        c.execute("UPDATE levels SET xp=?,level=? WHERE user_id=? AND guild_id=?", (xp,lvl,msg.author.id,msg.guild.id))
    else:
        c.execute("INSERT INTO levels VALUES (?,?,?,?)", (msg.author.id,msg.guild.id,add,0))
    conn.commit(); conn.close()
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

def xp_for_level(l): return 5*(l**2)+50*l+100

def get_lvl(xp):
    l = 0
    while xp >= xp_for_level(l):
        xp -= xp_for_level(l); l += 1
    return l

# ─── MODERATION ───
@bot.command(aliases=["mban"])
@commands.has_permissions(ban_members=True)
@require_role("Lieutenant")
async def ban(ctx, member: discord.Member, *, reason="No reason"):
    await member.ban(reason=reason)
    await ctx.send(f"Banned {member.mention}. `{reason}`")

@bot.command(aliases=["mkick"])
@commands.has_permissions(kick_members=True)
@require_role("Head Admin")
async def kick(ctx, member: discord.Member, *, reason="No reason"):
    await member.kick(reason=reason)
    await ctx.send(f"Kicked {member.mention}. `{reason}`")

@bot.command(aliases=["mmute"])
@commands.has_permissions(moderate_members=True)
@require_role("Moderator")
async def mute(ctx, member: discord.Member, duration: str = "1h", *, reason="No reason"):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration. Use `1h`, `30m`, `1d`, etc.")
    if d > datetime.timedelta(days=28): return await ctx.send("Max 28 days.")
    await member.timeout(d, reason=reason)
    await ctx.send(f"Muted {member.mention} for `{duration}`. `{reason}`")

@bot.command(aliases=["munmute"])
@commands.has_permissions(moderate_members=True)
@require_role("Moderator")
async def unmute(ctx, member: discord.Member, *, reason="No reason"):
    await member.timeout(None, reason=reason)
    await ctx.send(f"Unmuted {member.mention}.")

@bot.command(aliases=["mtb"])
@commands.has_permissions(ban_members=True)
@require_role("Lieutenant")
async def tempban(ctx, member: discord.Member, duration: str, *, reason="No reason"):
    d = parse_dur(duration)
    if d is None: return await ctx.send("Invalid duration.")
    await member.ban(reason=f"Tempban ({duration}): {reason}")
    await ctx.send(f"Tempbanned {member.mention} for `{duration}`.")
    async def _unban():
        await asyncio.sleep(d.total_seconds())
        try: await ctx.guild.unban(member, reason="Tempban expired")
        except: pass
    asyncio.create_task(_unban())

@bot.command()
@commands.has_permissions(ban_members=True)
@require_role("Capo")
async def unban(ctx, user_id: int, *, reason="No reason"):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
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
    c.execute("DELETE FROM warns WHERE id=(SELECT id FROM warns WHERE user_id=? AND guild_id=? ORDER BY id DESC LIMIT 1)", (member.id,ctx.guild.id))
    conn.commit(); conn.close()
    await ctx.send(f"Removed 1 warning from {member.mention}")

@bot.command(aliases=["clearwarns"])
@commands.has_permissions(manage_messages=True)
@require_role("STAFF")
async def clearwarnings(ctx, member: discord.Member):
    conn = db(); c = conn.cursor()
    c.execute("DELETE FROM warns WHERE user_id=? AND guild_id=?", (member.id,ctx.guild.id))
    conn.commit(); conn.close()
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
        log_msg = await log_ch.send(f"user; <@{member.id}>\nstrike; {count}/3\nreason; {reason}")
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
            if log_ch: await log_ch.send(f"user; <@{member.id}>\naction; tempban (7 days)\nreason; 3 strikes")
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

# ─── LEVELING ───
@bot.command(aliases=["rankcard","lvl"])
async def rank(ctx, member: discord.Member = None):
    m = member or ctx.author
    conn = db(); c = conn.cursor()
    c.execute("SELECT xp,level FROM levels WHERE user_id=? AND guild_id=?", (m.id, ctx.guild.id))
    row = c.fetchone(); conn.close()
    if not row: return await ctx.send("No XP yet.")
    await ctx.send(f"{m.mention} is **Level {row['level']}** with **{row['xp']} XP**")

@bot.command(aliases=["lb","top"])
async def leaderboard(ctx):
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id,xp,level FROM levels WHERE guild_id=? ORDER BY xp DESC LIMIT 10", (ctx.guild.id,))
    rows = c.fetchall(); conn.close()
    if not rows: return await ctx.send("No data.")
    lines = ["**Leaderboard:**"]
    for i,r in enumerate(rows,1):
        u = ctx.guild.get_member(r["user_id"])
        lines.append(f"{i}. {u.name if u else 'Unknown'} — Level {r['level']} ({r['xp']} XP)")
    await ctx.send("\n".join(lines)[:2000])

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
    if len(t) < 5:
        return False
    if len(set(t)) <= 2:
        return False
    lazy = {"no", "n/a", "na", "none", "idk", "dont know", "don't know", "h", "hh", "hhh", "hhhh", "idkk", "nah", "nope", "pass", "skip", "...", "??", "???", "what", "dont know", "dk", "dunno"}
    if t in lazy:
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
        "• Reply in **ONE message** with all 9 answers\n"
        "• Number each answer like this:\n"
        "  1. YourDiscordName\n"
        "  2. YourRobloxName\n"
        "  3. 500\n"
        "• Each answer must be at least a few words — single letters like 'h' or 'idk' will be rejected\n"
        "• Be honest and detailed. Staff will review your answers carefully.\n\n"
        "[ APPLICATION FOR CÁRTEL NUEVA ALIANZA ]\n\n"
        "1. Discord Username:\n"
        "2. Roblox Username:\n"
        "3. Current In-Game Level:\n"
        "4. Owned Gamepasses / Spawns (List all that apply):\n"
        "5. Experience in 'No Mercy' (or similar hood games):\n"
        "6. Are you currently in any other factions? (If so, list them):\n"
        "7. What is your primary playstyle? (e.g., Tactical/Combat, Logistics/Grinding, Enforcement):\n"
        "8. Are you willing to prioritize the Cartel's objectives over solo play?\n"
        "9. Why Cártel Nueva Alianza?\n\n"
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
            if 1 <= n <= 9:
                rest = line[len(first):].strip()
                found[n] = rest
    if len(found) < 9:
        missing = [str(i) for i in range(1,10) if i not in found]
        await dm.send(f"Your application is incomplete. Missing answers for questions: {', '.join(missing)}. Please run `%apply` again and answer ALL 9 questions with the proper format.")
        return
    for n in range(1,10):
        if not _is_valid_answer(found[n]):
            bad.append(str(n))
    if bad:
        await dm.send(f"Your answers for question(s) {', '.join(bad)} are too short, lazy, or don't make sense. Please run `%apply` again and provide real, detailed answers. Single letters like 'h' or 'idk' are not accepted.")
        return
    formatted = "\n".join(f"**{n}.** {found[n]}" for n in range(1,10))
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

# ─── HELP ───
@bot.command()
async def help(ctx):
    e = discord.Embed(title="Command List", color=discord.Color.blurple())
    e.add_field(name="Moderation", value="**Capo**: unban\n**Senior Lieutenant+**: ban, tempban\n**Lieutenant+**: addrole, removerole\n**Head Admin+**: kick, nick\n**Admin+**: purge, slowmode, lock, unlock\n**Moderator+**: mute, unmute\n**STAFF+**: warn, warnlist, unwarn, clearwarnings, strike, removestrike", inline=False)
    e.add_field(name="Fun", value="8ball, coinflip, roll, rps, choose, rate, reverse, mock, cat, dog, meme", inline=False)
    e.add_field(name="Utility", value="avatar, userinfo, serverinfo, roleinfo, channelinfo, emojiinfo, ping, invite, say, embed, poll, remind, timer", inline=False)
    e.add_field(name="Leveling", value="rank, leaderboard", inline=False)
    e.add_field(name="Tags", value="tag, tagcreate, tagedit, tagdelete, taglist, taginfo", inline=False)
    e.add_field(name="Applications", value="apply, accept, deny", inline=False)
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
    else:
        print(f"Error: {error}")

bot.run(TOKEN)
