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
# ======================================

DATA_FILE = "bot_data.json"
FPL_BASE = "https://fantasy.premierleague.com/api"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------- helpers ----------
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
    """Return list of {entry, name, player_name}"""
    data = await get_league_standings(session)
    results = data["standings"]["results"]
    managers = []
    for r in results:
        managers.append({
            "entry": r["entry"],
            "name": r["entry_name"],
            "player_name": r["player_name"]
        })
    return managers

async def get_gw_scores(session, gw: int):
    """Returns list of (name, player_name, points) sorted by points desc + high score value"""
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

async def get_high_scorer_counts(session):
    """Returns dict name -> number of weeks they had the highest score"""
    managers = await get_managers(session)
    all_hist = {}
    for m in managers:
        hist = await get_entry_history(session, m["entry"])
        all_hist[m["name"]] = {e["event"]: e["points"] for e in hist.get("current", [])}

    if not all_hist:
        return {}

    max_gw = 0
    for h in all_hist.values():
        if h:
            max_gw = max(max_gw, max(h.keys()))

    wins = defaultdict(int)
    for gw in range(1, max_gw + 1):
        gw_scores = []
        for name, history in all_hist.items():
            pts = history.get(gw, 0)
            gw_scores.append((name, pts))
        if not gw_scores:
            continue
        high = max(s[1] for s in gw_scores)
        for name, pts in gw_scores:
            if pts == high and high > 0:
                wins[name] += 1
    return dict(wins)

# ---------- slash commands ----------
@tree.command(name="standings", description="Current league standings")
async def standings(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        data = await get_league_standings(session)
        league_name = data["league"]["name"]
        results = data["standings"]["results"]

        embed = discord.Embed(
            title=f"🏆 {league_name}",
            color=0x37003c,
            timestamp=datetime.now(timezone.utc)
        )
        lines = []
        for r in results:
            lines.append(
                f"**{r['rank']}.** {r['entry_name']} ({r['player_name']}) — "
                f"**{r['total']}** pts  |  GW: {r['event_total']}"
            )
        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

@tree.command(name="gw", description="Scores for a specific gameweek + high scorer")
@app_commands.describe(gameweek="Gameweek number (leave empty for latest finished GW)")
async def gw_command(interaction: discord.Interaction, gameweek: int = None):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        bootstrap = await get_bootstrap(session)
        events = bootstrap["events"]

        if gameweek is None:
            finished = [e for e in events if e["finished"]]
            if not finished:
                await interaction.followup.send("No finished gameweeks yet.")
                return
            gameweek = finished[-1]["id"]

        scores, high = await get_gw_scores(session, gameweek)

        embed = discord.Embed(
            title=f"Gameweek {gameweek} Scores",
            color=0x00ff87,
            timestamp=datetime.now(timezone.utc)
        )
        lines = []
        for name, player, pts in scores:
            medal = "🥇 " if pts == high and high > 0 else ""
            lines.append(f"{medal}**{name}** ({player}) — **{pts}** pts")
        embed.description = "\n".join(lines)

        winners = [s[0] for s in scores if s[2] == high and high > 0]
        if winners:
            embed.set_footer(text=f"High scorer(s): {', '.join(winners)} ({high} pts)")

        await interaction.followup.send(embed=embed)

@tree.command(name="highscorers", description="How many times each friend has been the weekly high scorer")
async def highscorers(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        wins = await get_high_scorer_counts(session)
        if not wins:
            await interaction.followup.send("No data yet.")
            return

        sorted_wins = sorted(wins.items(), key=lambda x: x[1], reverse=True)
        embed = discord.Embed(
            title="👑 Weekly High Scorer Leaderboard",
            color=0xffd700,
            timestamp=datetime.now(timezone.utc)
        )
        lines = []
        for i, (name, count) in enumerate(sorted_wins, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "•"
            lines.append(f"{medal} **{name}** — {count} time{'s' if count != 1 else ''}")
        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

# ---------- auto announcement ----------
@tasks.loop(minutes=30)
async def check_new_gameweek():
    if ANNOUNCE_CHANNEL_ID == 0:
        return
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

            embed = discord.Embed(
                title=f"🏁 Gameweek {latest} Final Results",
                color=0x37003c,
                timestamp=datetime.now(timezone.utc)
            )
            lines = []
            for name, player, pts in scores:
                medal = "🥇 " if pts == high else ""
                lines.append(f"{medal}**{name}** — **{pts}** pts")
            embed.description = "\n".join(lines)

            if winners:
                embed.add_field(
                    name="Weekly High Scorer",
                    value=f"🎉 **{', '.join(winners)}** with **{high}** points!",
                    inline=False
                )

            wins = await get_high_scorer_counts(session)
            sorted_wins = sorted(wins.items(), key=lambda x: x[1], reverse=True)
            table = "\n".join(f"**{n}**: {c}" for n, c in sorted_wins)
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
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    if not check_new_gameweek.is_running():
        check_new_gameweek.start()

if __name__ == "__main__":
    print("Starting FPL bot...")
    bot.run(TOKEN)
