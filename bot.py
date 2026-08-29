import os
import json
from datetime import datetime, timezone
from collections import defaultdict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ========== HARDCODED CONFIG ==========
TOKEN = "MTU0MjQ5NTA2OTY5NTY0MzcyOQ.GcOPja.q8vRcrIhhI6xAw1Bd03rgcgYmizTK0HlZnLMFI"
LEAGUE_ID = 1211030
ANNOUNCE_CHANNEL_ID = 1541831393620004946
GUILD_ID = 1541831392910901390
# ======================================

WEEKLY_PRIZE = 10
SEASON_1ST = 350
SEASON_2ND = 100
CUP_WINNER = 170
TOTAL_POT = 1000

DATA_FILE = "bot_data.json"
FPL_BASE = "https://fantasy.premierleague.com/api"

intents = discord.Intents.default()
intents.message_content = True  # needed for ! commands
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"last_announced_gw": 0}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def fetch_json(session, url):
    async with session.get(url) as resp:
        if resp.status != 200:
            raise Exception(f"FPL API error {resp.status} for {url}")
        return await resp.json()

async def get_bootstrap(session):
    return await fetch_json(session, f"{FPL_BASE}/bootstrap-static/")

async def get_league_standings(session):
    return await fetch_json(session, f"{FPL_BASE}/leagues-classic/{LEAGUE_ID}/standings/")

async def get_entry_history(session, entry_id):
    return await fetch_json(session, f"{FPL_BASE}/entry/{entry_id}/history/")

async def get_managers(session):
    data = await get_league_standings(session)
    results = data["standings"]["results"]
    return [{
        "entry": r["entry"],
        "name": r["entry_name"],
        "player_name": r["player_name"],
        "total": r["total"],
        "rank": r["rank"],
        "event_total": r["event_total"]
    } for r in results]

async def get_gw_scores(session, gw: int):
    managers = await get_managers(session)
    scores = []
    for m in managers:
        hist = await get_entry_history(session, m["entry"])
        points = 0
        for e in hist.get("current", []):
            if e["event"] == gw:
                points = e["points"]
                break
        scores.append((m["name"], m["player_name"], points))
    scores.sort(key=lambda x: x[2], reverse=True)
    high = scores[0][2] if scores else 0
    return scores, high

async def get_high_scorer_details(session):
    managers = await get_managers(session)
    all_hist = {}
    for m in managers:
        hist = await get_entry_history(session, m["entry"])
        all_hist[m["name"]] = {e["event"]: e["points"] for e in hist.get("current", [])}

    if not all_hist:
        return {}, {}, []

    max_gw = 0
    for h in all_hist.values():
        if h:
            max_gw = max(max_gw, max(h.keys()))

    wins = defaultdict(int)
    weeks_won = defaultdict(list)
    week_winners = []

    for gw in range(1, max_gw + 1):
        gw_scores = [(name, history.get(gw, 0)) for name, history in all_hist.items()]
        if not gw_scores:
            continue
        high = max(s[1] for s in gw_scores)
        if high <= 0:
            continue
        winners = [name for name, pts in gw_scores if pts == high]
        for name in winners:
            wins[name] += 1
            weeks_won[name].append(gw)
        week_winners.append((gw, winners, high))

    return dict(wins), dict(weeks_won), week_winners

# ========== SHARED EMBED BUILDERS ==========
async def make_rules_embed():
    embed = discord.Embed(
        title="💰 FPL_Suzhou_Seoul Prize Rules",
        color=0x00c853,
        timestamp=datetime.now(timezone.utc)
    )
    embed.description = (
        f"**Total Pot: {TOTAL_POT}¥**\n\n"
        f"• **Weekly High Scorer** → **{WEEKLY_PRIZE}¥** each Gameweek\n"
        f"  (38 GWs × {WEEKLY_PRIZE}¥ = **380¥** total)\n\n"
        f"• **Season 1st place** → **{SEASON_1ST}¥**\n"
        f"• **Season 2nd place** → **{SEASON_2ND}¥**\n"
        f"• **Cup Winner** → **{CUP_WINNER}¥**\n\n"
        f"380 + 350 + 100 + 170 = **1000¥**"
    )
    embed.set_footer(text="May the best manager win ⚽")
    return embed

async def make_standings_embed(session):
    data = await get_league_standings(session)
    league_name = data["league"]["name"]
    results = data["standings"]["results"]
    embed = discord.Embed(title=f"🏆 {league_name}", color=0x37003c, timestamp=datetime.now(timezone.utc))
    lines = []
    for r in results:
        medal = "🥇" if r["rank"] == 1 else "🥈" if r["rank"] == 2 else "🥉" if r["rank"] == 3 else f"**{r['rank']}.**"
        lines.append(f"{medal} {r['entry_name']} ({r['player_name']}) — **{r['total']}** pts  |  GW: {r['event_total']}")
    embed.description = "\n".join(lines)
    return embed

async def make_highscorers_embed(session):
    wins, weeks_won, week_winners = await get_high_scorer_details(session)
    if not wins:
        return None
    sorted_wins = sorted(wins.items(), key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="👑 Weekly High Scorer Leaderboard", color=0xffd700, timestamp=datetime.now(timezone.utc))
    lines = []
    for i, (name, count) in enumerate(sorted_wins, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "•"
        money = count * WEEKLY_PRIZE
        gws = ", ".join(f"GW{g}" for g in weeks_won.get(name, []))
        lines.append(f"{medal} **{name}** — **{count}** win{'s' if count != 1 else ''} ({money}¥)\n    └ Weeks: {gws}")
    embed.description = "\n".join(lines)
    if week_winners:
        history = "\n".join(f"**GW{gw}**: {', '.join(w)} ({pts} pts)" for gw, w, pts in week_winners)
        embed.add_field(name="Week-by-week winners", value=history, inline=False)
    return embed

async def make_money_embed(session):
    wins, _, _ = await get_high_scorer_details(session)
    managers = await get_managers(session)
    embed = discord.Embed(title="💵 Current Prize Money Tracker", color=0x00bcd4, timestamp=datetime.now(timezone.utc))
    weekly_lines = []
    total_weekly_paid = 0
    for name, count in sorted(wins.items(), key=lambda x: x[1], reverse=True):
        yen = count * WEEKLY_PRIZE
        total_weekly_paid += yen
        weekly_lines.append(f"**{name}**: {count} × {WEEKLY_PRIZE}¥ = **{yen}¥**")
    embed.add_field(name="Weekly High Scorer Money (so far)", value="\n".join(weekly_lines) or "None yet", inline=False)
    if managers:
        sorted_m = sorted(managers, key=lambda m: m["total"], reverse=True)
        first = sorted_m[0]["name"] if sorted_m else "—"
        second = sorted_m[1]["name"] if len(sorted_m) > 1 else "—"
        embed.add_field(
            name="Projected Season Prizes",
            value=f"🥇 1st ({SEASON_1ST}¥): **{first}**\n🥈 2nd ({SEASON_2ND}¥): **{second}**\n🏆 Cup ({CUP_WINNER}¥): *TBD*",
            inline=False
        )
    remaining = (38 * WEEKLY_PRIZE) - total_weekly_paid
    embed.set_footer(text=f"Weekly pot remaining: ~{remaining}¥ | Total pot: {TOTAL_POT}¥")
    return embed

# ========== SLASH COMMANDS ==========
@tree.command(name="rules", description="Prize pot and league rules")
async def slash_rules(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await make_rules_embed())

@tree.command(name="standings", description="Current league standings")
async def slash_standings(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        await interaction.followup.send(embed=await make_standings_embed(session))

@tree.command(name="gw", description="Scores for a specific gameweek + high scorer")
@app_commands.describe(gameweek="Gameweek number (empty = latest finished)")
async def slash_gw(interaction: discord.Interaction, gameweek: int = None):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        bootstrap = await get_bootstrap(session)
        if gameweek is None:
            finished = [e for e in bootstrap["events"] if e["finished"]]
            if not finished:
                await interaction.followup.send("No finished gameweeks yet.")
                return
            gameweek = finished[-1]["id"]
        scores, high = await get_gw_scores(session, gameweek)
        embed = discord.Embed(title=f"Gameweek {gameweek} Scores", color=0x00ff87, timestamp=datetime.now(timezone.utc))
        lines = [f"{'🥇 ' if pts == high and high > 0 else ''}**{name}** ({player}) — **{pts}** pts" for name, player, pts in scores]
        embed.description = "\n".join(lines)
        winners = [s[0] for s in scores if s[2] == high and high > 0]
        if winners:
            embed.set_footer(text=f"High scorer(s): {', '.join(winners)} ({high} pts) → +{WEEKLY_PRIZE}¥")
        await interaction.followup.send(embed=embed)

@tree.command(name="highscorers", description="Weekly high scorers + money")
async def slash_highscorers(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        embed = await make_highscorers_embed(session)
        if embed is None:
            await interaction.followup.send("No finished gameweeks yet.")
        else:
            await interaction.followup.send(embed=embed)

@tree.command(name="money", description="Prize money tracker")
async def slash_money(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        await interaction.followup.send(embed=await make_money_embed(session))

@tree.command(name="topscore", description="Highest single GW score")
async def slash_topscore(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        managers = await get_managers(session)
        best = (None, 0, 0)
        for m in managers:
            hist = await get_entry_history(session, m["entry"])
            for e in hist.get("current", []):
                if e["points"] > best[1]:
                    best = (m["name"], e["points"], e["event"])
        if best[0] is None:
            await interaction.followup.send("No scores yet.")
            return
        embed = discord.Embed(
            title="🚀 Highest Single GW Score",
            description=f"**{best[0]}** scored **{best[1]}** points in **GW{best[2]}**!",
            color=0xff5722,
            timestamp=datetime.now(timezone.utc)
        )
        await interaction.followup.send(embed=embed)

# ========== PREFIX COMMANDS (work immediately) ==========
@bot.command(name="rules")
async def prefix_rules(ctx):
    await ctx.send(embed=await make_rules_embed())

@bot.command(name="standings")
async def prefix_standings(ctx):
    async with aiohttp.ClientSession() as session:
        await ctx.send(embed=await make_standings_embed(session))

@bot.command(name="highscorers")
async def prefix_highscorers(ctx):
    async with aiohttp.ClientSession() as session:
        embed = await make_highscorers_embed(session)
        await ctx.send(embed=embed if embed else "No finished gameweeks yet.")

@bot.command(name="money")
async def prefix_money(ctx):
    async with aiohttp.ClientSession() as session:
        await ctx.send(embed=await make_money_embed(session))

@bot.command(name="gw")
async def prefix_gw(ctx, gameweek: int = None):
    async with aiohttp.ClientSession() as session:
        bootstrap = await get_bootstrap(session)
        if gameweek is None:
            finished = [e for e in bootstrap["events"] if e["finished"]]
            if not finished:
                await ctx.send("No finished gameweeks yet.")
                return
            gameweek = finished[-1]["id"]
        scores, high = await get_gw_scores(session, gameweek)
        embed = discord.Embed(title=f"Gameweek {gameweek} Scores", color=0x00ff87, timestamp=datetime.now(timezone.utc))
        lines = [f"{'🥇 ' if pts == high and high > 0 else ''}**{name}** ({player}) — **{pts}** pts" for name, player, pts in scores]
        embed.description = "\n".join(lines)
        winners = [s[0] for s in scores if s[2] == high and high > 0]
        if winners:
            embed.set_footer(text=f"High scorer(s): {', '.join(winners)} ({high} pts) → +{WEEKLY_PRIZE}¥")
        await ctx.send(embed=embed)

@bot.command(name="topscore")
async def prefix_topscore(ctx):
    async with aiohttp.ClientSession() as session:
        managers = await get_managers(session)
        best = (None, 0, 0)
        for m in managers:
            hist = await get_entry_history(session, m["entry"])
            for e in hist.get("current", []):
                if e["points"] > best[1]:
                    best = (m["name"], e["points"], e["event"])
        if best[0] is None:
            await ctx.send("No scores yet.")
            return
        embed = discord.Embed(
            title="🚀 Highest Single GW Score",
            description=f"**{best[0]}** scored **{best[1]}** points in **GW{best[2]}**!",
            color=0xff5722
        )
        await ctx.send(embed=embed)

@bot.command(name="help")
async def prefix_help(ctx):
    await ctx.send(
        "**FPL Bot Commands**\n"
        "`!rules` – Prize rules\n"
        "`!standings` – League table\n"
        "`!gw [number]` – Gameweek scores\n"
        "`!highscorers` – Weekly winners + money\n"
        "`!money` – Prize money tracker\n"
        "`!topscore` – Highest GW score\n"
        "(Slash commands `/rules` etc. also available)"
    )

# ========== AUTO ANNOUNCEMENT ==========
@tasks.loop(minutes=30)
async def check_new_gameweek():
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if channel is None:
        return
    data = load_data()
    last = data.get("last_announced_gw", 0)
    async with aiohttp.ClientSession() as session:
        bootstrap = await get_bootstrap(session)
        finished = [e for e in bootstrap["events"] if e["finished"]]
        if not finished:
            return
        latest = finished[-1]["id"]
        if latest > last:
            scores, high = await get_gw_scores(session, latest)
            winners = [s[0] for s in scores if s[2] == high and high > 0]
            embed = discord.Embed(title=f"🏁 Gameweek {latest} Final Results", color=0x37003c, timestamp=datetime.now(timezone.utc))
            lines = [f"{'🥇 ' if pts == high else ''}**{name}** — **{pts}** pts" for name, player, pts in scores]
            embed.description = "\n".join(lines)
            if winners:
                embed.add_field(name="Weekly High Scorer", value=f"🎉 **{', '.join(winners)}** ({high} pts)\n💰 **+{WEEKLY_PRIZE}¥**", inline=False)
            wins, _, _ = await get_high_scorer_details(session)
            table = "\n".join(f"**{n}**: {c} ({c * WEEKLY_PRIZE}¥)" for n, c in sorted(wins.items(), key=lambda x: -x[1]))
            embed.add_field(name="Season High Scorer Count", value=table or "—", inline=False)
            await channel.send(embed=embed)
            data["last_announced_gw"] = latest
            save_data(data)

@check_new_gameweek.before_loop
async def before_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Connected to {len(bot.guilds)} guild(s)")
    for g in bot.guilds:
        print(f"  - {g.name} ({g.id})")
    try:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"✅ Guild sync: {len(synced)} commands → {GUILD_ID}")
    except Exception as e:
        print(f"Guild sync failed: {e}")
        try:
            synced = await tree.sync()
            print(f"Global sync: {len(synced)} commands")
        except Exception as e2:
            print(f"Global sync failed: {e2}")
    if not check_new_gameweek.is_running():
        check_new_gameweek.start()

if __name__ == "__main__":
    print("Starting FPL bot...")
    bot.run(TOKEN)
