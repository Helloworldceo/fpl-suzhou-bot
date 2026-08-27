# FPL Suzhou Seoul Discord Bot

Tracks weekly high scorers for the **FPL_Suzhou_Seoul** mini-league (ID 1211030).

## Commands
- `/standings` – Current league table
- `/gw [number]` – Gameweek scores + high scorer
- `/highscorers` – Season high-scorer win counts

## Auto announcements
Posts final GW results + high scorer to the configured channel every time a gameweek finishes.

## Setup (Railway – recommended)

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. New Project → Deploy from GitHub repo → select this repo
3. Add Variables:
   - `DISCORD_TOKEN` = your bot token
   - `LEAGUE_ID` = `1211030`
   - `ANNOUNCE_CHANNEL_ID` = `1541831393620004946`
4. Deploy. The bot will stay online 24/7.

## Local run
```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your token
python bot.py
```

## Invite the bot
Use the OAuth2 URL generator in the Discord Developer Portal with scopes `bot` + `applications.commands` and permissions Send Messages + Embed Links.
