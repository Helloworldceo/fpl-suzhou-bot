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

CHIP_NAMES = {
    "wildcard": "Wildcard",
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
    "freehit": "Free Hit",
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
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

async def get_finished_gw_ids(session):
    bootstrap = await get_bootstrap(session)
    return sorted(e["id"] for e in bootstrap["events"] if e.get("finished"))

async def get_current_gw(session):
    bootstrap = await get_bootstrap(session)
    current = next((e for e in bootstrap["events"] if e.get("is_current")), None)
    if current:
        return current["id"]
    finished = [e for e in bootstrap["events"] if e["finished"]]
    return finished[-1]["id"] if finished else 1

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
            weeks_won[name].append((gw, high))
        week_winners.append((gw, winners, high))

    return dict(wins), dict(weeks_won), week_winners

async def get_form_data(session, last_n=5):
    managers = await get_managers(session)
    rows = []
    for m in managers:
        hist = await get_entry_history(session, m["entry"])
        events = sorted(hist.get("current", []), key=lambda e: e["event"])
        recent = events[-last_n:] if events else []
        form_pts = sum(e["points"] for e in recent)
        detail = ", ".join(f"GW{e['event']}:{e['points']}" for e in recent)
        rows.append((m["name"], form_pts, detail, len(recent)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows

async def get_chips_data(session):
    managers = await get_managers(session)
    rows = []
    for m in managers:
        hist = await get_entry_history(session, m["entry"])
        used = hist.get("chips", []) or []
        used_map = {}
        for c in used:
            name = CHIP_NAMES.get(c.get("name", ""), c.get("name", "?"))
            used_map[name] = c.get("event")
        all_chips = ["Wildcard", "Bench Boost", "Triple Captain", "Free Hit"]
        status = []
        for chip in all_chips:
            if chip in used_map:
                status.append(f"{chip} (GW{used_map[chip]})")
            else:
                status.append(f"{chip} ✅")
        remaining = 4 - len(used_map)
        rows.append((m["name"], status, remaining, used_map))
    return rows

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

async def make_help_embed():
    embed = discord.Embed(
        title="📖 FPL Bot – Commands",
        color=0x9c27b0,
        timestamp=datetime.now(timezone.utc)
    )
    embed.description = (
        "Use `!` commands (most reliable) or `/` slash commands.\n\n"
        "**League & scores**\n"
        "`!standings` / `/standings` – League table (points only)\n"
        "`!gw [n]` – Scores for a gameweek\n"
        "`!live` – Live scores for the current gameweek\n"
        "`!form` – Form table (last 5 gameweeks)\n\n"
        "**Prizes**\n"
        "`!money` / `/money` – Prize money + which GWs each manager won\n"
        "`!highscorers` – Weekly high scorer count\n"
        "`!topscore` – Highest single GW score\n"
        "`!rules` – Prize pot rules (1000¥)\n\n"
        "**Team info**\n"
        "`!chips` – Chip usage\n\n"
        "**Help**\n"
        "`!help` – This list"
    )
    embed.set_footer(text="FPL_Suzhou_Seoul tracker")
    return embed

async def make_standings_embed(session):
    data = await get_league_standings(session)
    league_name = data["league"]["name"]
    results = data["standings"]["results"]
    embed = discord.Embed(
        title=f"🏆 {league_name} – Standings",
        color=0x37003c,
        timestamp=datetime.now(timezone.utc)
    )
    lines = []
    for r in results:
        medal = "🥇" if r["rank"] == 1 else "🥈" if r["rank"] == 2 else "🥉" if r["rank"] == 3 else f"**{r['rank']}.**"
        lines.append(
            f"{medal} {r['entry_name']} ({r['player_name']}) — "
            f"**{r['total']}** pts  |  GW: {r['event_total']}"
        )
    embed.description = "\n".join(lines)
    embed.set_footer(text="Points only. Use /money for prizes and GW wins.")
    return embed

async def make_highscorers_embed(session):
    wins, weeks_won, week_winners = await get_high_scorer_details(session)
    if not wins:
        return None
    finished = set(await get_finished_gw_ids(session))
    sorted_wins = sorted(wins.items(), key=lambda x: x[1], reverse=True)
    embed = discord.Embed(
        title="👑 Weekly High Scorer Leaderboard",
        color=0xffd700,
        timestamp=datetime.now(timezone.utc)
    )
    lines = []
    for i, (name, count) in enumerate(sorted_wins, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "•"
        money = count * WEEKLY_PRIZE
        details = []
        for gw, pts in weeks_won.get(name, []):
            tag = f"GW{gw}" if gw in finished else f"GW{gw}*"
            details.append(f"{tag} ({pts} pts)")
        detail_str = ", ".join(details) if details else "—"
        lines.append(
            f"{medal} **{name}** — **{count}** win{'s' if count != 1 else ''} ({money}¥)\n"
            f"    └ {detail_str}"
        )
    embed.description = "\n".join(lines)
    if week_winners:
        history = []
        for gw, w, pts in week_winners:
            live = " *(live)*" if gw not in finished else ""
            history.append(f"**GW{gw}**{live}: {', '.join(w)} ({pts} pts)")
        history_text = "\n".join(history)
        if len(history_text) > 1000:
            history_text = history_text[:997] + "..."
        embed.add_field(name="Week-by-week winners", value=history_text, inline=False)
    embed.set_footer(text="* = live / not finished yet  ·  Use /money for prize totals")
    return embed

async def make_money_embed(session):
    wins, weeks_won, week_winners = await get_high_scorer_details(session)
    managers = await get_managers(session)
    finished = set(await get_finished_gw_ids(session))
    embed = discord.Embed(
        title="💵 Prize Money Tracker",
        color=0x00bcd4,
        timestamp=datetime.now(timezone.utc)
    )

    names = [m["name"] for m in managers]
    for n in weeks_won:
        if n not in names:
            names.append(n)
    names.sort(key=lambda n: wins.get(n, 0), reverse=True)

    lines = []
    total_confirmed = 0
    for name in names:
        pairs = weeks_won.get(name, [])
        conf = sum(1 for gw, _pts in pairs if gw in finished)
        prov = len(pairs) - conf
        yen_conf = conf * WEEKLY_PRIZE
        yen_live = prov * WEEKLY_PRIZE
        total_confirmed += yen_conf
        extra = f" + **{yen_live}¥** live" if prov else ""
        details = []
        for gw, pts in pairs:
            if gw in finished:
                details.append(f"GW{gw} ({pts} pts) = {WEEKLY_PRIZE}¥")
            else:
                details.append(f"GW{gw}* ({pts} pts) live")
        detail_str = ", ".join(details) if details else "no weekly wins yet"
        lines.append(f"**{name}** — **{yen_conf}¥**{extra}\n    └ {detail_str}")

    embed.description = "\n".join(lines) if lines else "No weekly prizes yet."

    if week_winners:
        history = []
        for gw, w, pts in week_winners:
            live = " *(live)*" if gw not in finished else ""
            history.append(f"**GW{gw}**{live}: {', '.join(w)} ({pts} pts)")
        history_text = "\n".join(history)
        if len(history_text) > 1000:
            history_text = history_text[:997] + "..."
        embed.add_field(name="Who won which GW", value=history_text, inline=False)

    if managers:
        sorted_m = sorted(managers, key=lambda m: m["total"], reverse=True)
        first = sorted_m[0]["name"] if sorted_m else "—"
        second = sorted_m[1]["name"] if len(sorted_m) > 1 else "—"
        embed.add_field(
            name="Projected Season Prizes",
            value=(
                f"🥇 1st ({SEASON_1ST}¥): **{first}**\n"
                f"🥈 2nd ({SEASON_2ND}¥): **{second}**\n"
                f"🏆 Cup ({CUP_WINNER}¥): *TBD*"
            ),
            inline=False,
        )

    remaining = (38 * WEEKLY_PRIZE) - total_confirmed
    embed.set_footer(
        text=f"Confirmed weekly: {total_confirmed}¥ | Remaining weekly pot: ~{remaining}¥ | * = live"
    )
    return embed

async def make_live_embed(session):
    gw = await get_current_gw(session)
    scores, high = await get_gw_scores(session, gw)
    bootstrap = await get_bootstrap(session)
    ev = next((e for e in bootstrap["events"] if e["id"] == gw), None)
    status = "LIVE" if ev and not ev.get("finished") else "Finished"
    embed = discord.Embed(
        title=f"📡 Gameweek {gw} – {status}",
        color=0xe91e63 if status == "LIVE" else 0x00ff87,
        timestamp=datetime.now(timezone.utc)
    )
    lines = []
    for name, player, pts in scores:
        medal = "🥇 " if pts == high and high > 0 else ""
        lines.append(f"{medal}**{name}** — **{pts}** pts")
    embed.description = "\n".join(lines)
    winners = [s[0] for s in scores if s[2] == high and high > 0]
    if winners and high > 0:
        embed.set_footer(text=f"Current high: {', '.join(winners)} ({high} pts)")
    return embed

async def make_form_embed(session):
    rows = await get_form_data(session, last_n=5)
    embed = discord.Embed(title="🔥 Form Table (Last 5 GWs)", color=0xff9800, timestamp=datetime.now(timezone.utc))
    lines = []
    for i, (name, pts, detail, n) in enumerate(rows, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"**{i}.**"
        lines.append(f"{medal} **{name}** — **{pts}** pts ({n} GWs)\n    └ {detail}")
    embed.description = "\n".join(lines) if lines else "No data yet."
    return embed

async def make_chips_embed(session):
    rows = await get_chips_data(session)
    embed = discord.Embed(title="🎴 Chip Tracker", color=0x3f51b5, timestamp=datetime.now(timezone.utc))
    lines = []
    for name, status, remaining, _ in rows:
        used_txt = ", ".join(s for s in status if "✅" not in s) or "None used"
        left = ", ".join(s.replace(" ✅", "") for s in status if "✅" in s) or "None"
        lines.append(f"**{name}** — {remaining}/4 left\n    Used: {used_txt}\n    Left: {left}")
    embed.description = "\n\n".join(lines) if lines else "No data."
    embed.set_footer(text="✅ = still available")
    return embed

async def make_gw_final_embed(session, gw, scores, high, winners, wins, weeks_won):
    embed = discord.Embed(
        title=f"🏁 Gameweek {gw} Final Results",
        color=0x37003c,
        timestamp=datetime.now(timezone.utc),
    )
    lines = [f"{'🥇 ' if pts == high else ''}**{name}** — **{pts}** pts" for name, player, pts in scores]
    embed.description = "\n".join(lines)
    if winners:
        embed.add_field(
            name="Weekly High Scorer",
            value=f"🎉 **{', '.join(winners)}** ({high} pts)\n💰 **+{WEEKLY_PRIZE}¥** each",
            inline=False,
        )
    table_lines = []
    for n, c in sorted(wins.items(), key=lambda x: -x[1]):
        details = ", ".join(f"GW{g} ({p})" for g, p in weeks_won.get(n, []))
        table_lines.append(f"**{n}**: {c} ({c * WEEKLY_PRIZE}¥) — {details}")
    table = "\n".join(table_lines) or "—"
    if len(table) > 1000:
        table = table[:997] + "..."
    embed.add_field(name="Season High Scorer Count", value=table, inline=False)
    return embed

@tree.command(name="help", description="List all bot commands and what they do")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await make_help_embed())

@tree.command(name="rules", description="Prize pot and league rules")
async def slash_rules(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await make_rules_embed())

@tree.command(name="standings", description="League table (points only)")
async def slash_standings(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        await interaction.followup.send(embed=await make_standings_embed(session))

@tree.command(name="gw", description="Scores for a gameweek")
@app_commands.describe(gameweek="Gameweek number (empty = current)")
async def slash_gw(interaction: discord.Interaction, gameweek: int = None):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        if gameweek is None:
            gameweek = await get_current_gw(session)
        scores, high = await get_gw_scores(session, gameweek)
        embed = discord.Embed(title=f"Gameweek {gameweek} Scores", color=0x00ff87, timestamp=datetime.now(timezone.utc))
        lines = [f"{'🥇 ' if pts == high and high > 0 else ''}**{name}** ({player}) — **{pts}** pts" for name, player, pts in scores]
        embed.description = "\n".join(lines)
        winners = [s[0] for s in scores if s[2] == high and high > 0]
        if winners:
            embed.set_footer(text=f"High scorer(s): {', '.join(winners)} ({high} pts) → +{WEEKLY_PRIZE}¥")
        await interaction.followup.send(embed=embed)

@tree.command(name="live", description="Live scores for the current gameweek")
async def slash_live(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        await interaction.followup.send(embed=await make_live_embed(session))

@tree.command(name="form", description="Form table – last 5 gameweeks")
async def slash_form(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        await interaction.followup.send(embed=await make_form_embed(session))

@tree.command(name="chips", description="Chip usage tracker")
async def slash_chips(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        await interaction.followup.send(embed=await make_chips_embed(session))

@tree.command(name="highscorers", description="Weekly high scorers + which GWs + points")
async def slash_highscorers(interaction: discord.Interaction):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        embed = await make_highscorers_embed(session)
        await interaction.followup.send(embed=embed if embed else "No data yet.")

@tree.command(name="money", description="Prize money + which GWs each manager won")
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

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "Something went wrong. Try `!help` instead."
    if isinstance(error, app_commands.CommandNotFound):
        msg = "Slash command out of date. Use `!money` or `!help`."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass
    print(f"App command error: {error}")

@bot.command(name="help")
async def prefix_help(ctx):
    await ctx.send(embed=await make_help_embed())

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
        await ctx.send(embed=embed if embed else "No data yet.")

@bot.command(name="money")
async def prefix_money(ctx):
    async with aiohttp.ClientSession() as session:
        await ctx.send(embed=await make_money_embed(session))

@bot.command(name="gw")
async def prefix_gw(ctx, gameweek: int = None):
    async with aiohttp.ClientSession() as session:
        if gameweek is None:
            gameweek = await get_current_gw(session)
        scores, high = await get_gw_scores(session, gameweek)
        embed = discord.Embed(title=f"Gameweek {gameweek} Scores", color=0x00ff87, timestamp=datetime.now(timezone.utc))
        lines = [f"{'🥇 ' if pts == high and high > 0 else ''}**{name}** ({player}) — **{pts}** pts" for name, player, pts in scores]
        embed.description = "\n".join(lines)
        winners = [s[0] for s in scores if s[2] == high and high > 0]
        if winners:
            embed.set_footer(text=f"High scorer(s): {', '.join(winners)} ({high} pts) → +{WEEKLY_PRIZE}¥")
        await ctx.send(embed=embed)

@bot.command(name="live")
async def prefix_live(ctx):
    async with aiohttp.ClientSession() as session:
        await ctx.send(embed=await make_live_embed(session))

@bot.command(name="form")
async def prefix_form(ctx):
    async with aiohttp.ClientSession() as session:
        await ctx.send(embed=await make_form_embed(session))

@bot.command(name="chips")
async def prefix_chips(ctx):
    async with aiohttp.ClientSession() as session:
        await ctx.send(embed=await make_chips_embed(session))

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

@tasks.loop(minutes=30)
async def check_new_gameweek():
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if channel is None:
        return
    data = load_data()
    last = data.get("last_announced_gw", 0)
    async with aiohttp.ClientSession() as session:
        finished_gws = await get_finished_gw_ids(session)
        missed = [gw for gw in finished_gws if gw > last]
        if not missed:
            return
        wins, weeks_won, _ = await get_high_scorer_details(session)
        for gw in missed:
            scores, high = await get_gw_scores(session, gw)
            winners = [s[0] for s in scores if s[2] == high and high > 0]
            embed = await make_gw_final_embed(session, gw, scores, high, winners, wins, weeks_won)
            await channel.send(embed=embed)
            data["last_announced_gw"] = gw
            save_data(data)
            print(f"Announced GW{gw}")

@check_new_gameweek.before_loop
async def before_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    for g in bot.guilds:
        print(f"  - {g.name} ({g.id})")
    try:
        guild = discord.Object(id=GUILD_ID)
        tree.clear_commands(guild=guild)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"✅ Guild sync: {len(synced)} commands → {GUILD_ID}")
        for c in synced:
            print(f"   /{c.name}")
    except Exception as e:
        print(f"Guild sync error: {e}")
        try:
            synced = await tree.sync()
            print(f"Global sync fallback: {len(synced)} commands")
        except Exception as e2:
            print(f"Global sync failed: {e2}")
    if not check_new_gameweek.is_running():
        check_new_gameweek.start()

if __name__ == "__main__":
    print("Starting FPL bot...")
    bot.run(TOKEN)
