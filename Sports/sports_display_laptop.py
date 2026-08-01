import math
import threading
import pygame
import time
import requests
from io import BytesIO
import datetime
import zoneinfo
import json
import os
from flask import Flask, jsonify, request, render_template_string

# ─── Timezone & Refresh Constants ───────────────────────────────────────────────

ET = zoneinfo.ZoneInfo("America/New_York")
DATA_REFRESH = 30
LIVE_DATA_REFRESH = .5

# ─── Global State ────────────────────────────────────────────────────────────────

locked = False
last_data_update = 0
sports_data = []

# ─── Display Setup ───────────────────────────────────────────────────────────────

WIDTH, HEIGHT = 800, 480

# ─── ntfy app notification setup ─────────────────────────────────────────────────

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# ─── Invisible Exit Zone (top-right corner) ──────────────────────────────────────

EXIT_ZONE_SIZE = 60
exit_zone = pygame.Rect(WIDTH - EXIT_ZONE_SIZE, 0, EXIT_ZONE_SIZE, EXIT_ZONE_SIZE)

def check_exit(pos):
    if exit_zone.collidepoint(pos):
        pygame.quit()
        exit()

pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
#screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN)
#pygame.mouse.set_visible(False)

title_font = pygame.font.SysFont("Verdana", 32, bold=True)
sub_font   = pygame.font.SysFont("Verdana", 32)
score_font = pygame.font.SysFont("Verdana", 70, bold=True)
small_font = pygame.font.SysFont("Verdana", 22)

# ─── League Logo URLs ────────────────────────────────────────────────────────────

league_logos = {
    "hockey/nhl":              "https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png",
    "baseball/mlb":            "https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png",
    "basketball/nba":          "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
    "football/nfl":            "https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png",
    "football/college-football": "https://a2.espncdn.com/combiner/i?img=%2Fi%2Fespn%2Fmisc_logos%2F500%2Fncaa.png",
    "soccer/usa.1":            "https://a.espncdn.com/i/teamlogos/leagues/500/mls.png",
    "soccer/eng.1":            "https://a.espncdn.com/combiner/i?img=/i/leaguelogos/soccer/500-dark/23.png"
}

SUPPORTED_LEAGUES = [
    ("basketball/nba",           "NBA"),
    ("baseball/mlb",             "MLB"),
    ("hockey/nhl",               "NHL"),
    ("football/nfl",             "NFL"),
    ("football/college-football","NCAAF"),
    ("soccer/usa.1",             "MLS"),
    ("soccer/eng.1",             "Premier League"),
]

# ─── Persistence Config ──────────────────────────────────────────────────────────

TEAMS_FILE = os.path.join(os.path.dirname(__file__), "my_teams.json")

DEFAULT_TEAMS = [
    ("basketball/nba",           "nyk"),
    ("baseball/mlb",             "nyy"),
    ("hockey/nhl",               "nj"),
    ("football/nfl",             "pit"),
    ("football/college-football","psu"),
]


# ─── Persistence ────────────────────────────────────────────────────────────────

def load_my_teams():
    if os.path.exists(TEAMS_FILE):
        try:
            with open(TEAMS_FILE) as f:
                data = json.load(f)
            return [tuple(t) for t in data]
        except Exception as e:
            print("Could not load teams file:", e)
    return list(DEFAULT_TEAMS)


def save_my_teams(teams):
    try:
        with open(TEAMS_FILE, "w") as f:
            json.dump(teams, f, indent=2)
    except Exception as e:
        print("Could not save teams file:", e)


MY_TEAMS = load_my_teams()

# ─── ESPN Helpers ────────────────────────────────────────────────────────────────

session = requests.Session()
clock   = pygame.time.Clock()

TEAM_IDS = {}

logo_cache      = {}
team_logo_cache = {}
_teams_cache    = {}

FALLBACK_LOGO = "https://img.icons8.com/color/1200/espn.jpg"

# Leagues whose "current season" flips over around a specific point in the
# calendar year rather than at Jan 1 (college football, NFL). ESPN's schedule
# endpoint silently defaults to a season based on today's date, and right
# around the season rollover (roughly July 1 for CFB) it can still hand back
# last year's (already-finished) schedule instead of the upcoming one. We
# pass an explicit ?season= to sidestep that.
SEASON_YEAR_LEAGUES = {"football/college-football", "football/nfl", "soccer/eng.1", "soccer/usa.1"}

def season_year_for(league):
    now = datetime.datetime.now(ET)
    if league in ("football/college-football", "football/nfl", "soccer/eng.1"):
        # Seasons that start in the back half of the year and cross into the next
        # (e.g. EPL Aug–May) — season "year" is the year it started in.
        return now.year if now.month >= 6 else now.year - 1
    if league == "soccer/usa.1":
        # MLS is roughly calendar-aligned (Feb–Dec).
        return now.year
    return now.year


team_info_cache = {}


def fetch_team_info(league, abbr):
    """
    Plain team-detail lookup (id, displayName, logo) via /teams/{abbr}.
    Unlike /teams/{abbr}/schedule, this works reliably for national teams
    under the fifa.world tournament league.
    """
    key = (league, abbr)
    if key in team_info_cache:
        return team_info_cache[key]
    url = f"https://site.api.espn.com/apis/site/v2/sports/{league}/teams/{abbr}"
    try:
        data  = session.get(url, timeout=5).json()
        t     = data.get("team", {})
        logos = t.get("logos", [])
        info = {
            "id":          t.get("id"),
            "displayName": t.get("displayName", abbr.upper()),
            "logo":        logos[0].get("href") if logos else FALLBACK_LOGO,
        }
    except Exception as e:
        print(f"fetch_team_info failed for {league}/{abbr}:", e)
        info = {"id": None, "displayName": abbr.upper(), "logo": FALLBACK_LOGO}
    team_info_cache[key] = info
    return info


def rebuild_team_ids():
    global TEAM_IDS
    TEAM_IDS = {}
    for team_league, identifier in MY_TEAMS:
        try:
            # If a soccer club was configured with an abbreviation instead of a numeric ID,
            # look up its numeric ID via fetch_league_teams
            tid = identifier
            if team_league.startswith("soccer/") and team_league != "soccer/fifa.world":
                if not str(identifier).isdigit():
                    teams = fetch_league_teams(team_league)
                    match = next((t for t in teams if t["abbreviation"] == str(identifier).lower()), None)
                    if match:
                        tid = str(match["id"])

            if team_league == "soccer/fifa.world":
                tid = fetch_team_info(team_league, identifier).get("id")
            elif not team_league.startswith("soccer/"):
                url = f"https://site.api.espn.com/apis/site/v2/sports/{team_league}/teams/{identifier}/schedule"
                data = session.get(url, timeout=5).json()
                tid = data.get("team", {}).get("id")

            if team_league not in TEAM_IDS:
                TEAM_IDS[team_league] = []
            TEAM_IDS[team_league].append(tid)
        except Exception as e:
            print(f"Could not get team_id - {team_league}/{identifier}:", e)


rebuild_team_ids()


def fetch_league_teams(league):
    if league in _teams_cache:
        return _teams_cache[league]

    try:
        if league == "football/college-football":
            url = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=500&groups=80"
        else:
            url = f"https://site.api.espn.com/apis/site/v2/sports/{league}/teams?limit=500"

        data  = session.get(url, timeout=8).json()
        items = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])

        teams = []
        for item in items:
            t     = item.get("team", {})
            logos = t.get("logos", [])
            logo  = logos[0].get("href", FALLBACK_LOGO) if logos else FALLBACK_LOGO
            teams.append({
                "id":          t.get("id"),
                "abbreviation": t.get("abbreviation", "").lower(),
                "displayName": t.get("displayName", ""),
                "logo":        logo,
            })

        teams.sort(key=lambda x: x["displayName"])
        _teams_cache[league] = teams
        return teams
    except Exception as e:
        print("fetch_league_teams failed:", e)
        return []


def get_cached_logo(url, size):
    key = (url, size)
    if key not in logo_cache:
        logo_cache[key] = load_oled_logo(url, size)
    return logo_cache[key]


def load_oled_logo(url, size):
    try:
        response = session.get(url, timeout=5)
        img_str  = BytesIO(response.content)
        image    = pygame.image.load(img_str).convert_alpha()
        return pygame.transform.smoothscale(image, (size, size))
    except Exception as e:
        print(f"Logo fail: {e}")
        surf = pygame.Surface((size, size))
        surf.fill((30, 30, 30))
        return surf


def fetch_team_logo(team, sport_league):
    key = (sport_league, team)
    if key in team_logo_cache:
        return team_logo_cache[key]
    try:
        url   = f"https://site.api.espn.com/apis/site/v2/sports/{sport_league}/teams/{team}"
        tdata = session.get(url, timeout=5).json()
        logos = tdata.get("team", {}).get("logos", [])
        logo  = logos[0].get("href") if logos else FALLBACK_LOGO
        team_logo_cache[key] = logo
        return logo
    except:
        return FALLBACK_LOGO


def fetch_team_record(team_id, sport_league):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_league}/teams/{team_id}"
    try:
        data    = session.get(url, timeout=5).json()
        records = data.get("team", {}).get("record", {}).get("items", [])
        return next((r["summary"] for r in records if r.get("type") in ("total", "overall")), None)
    except:
        return None


def get_center(a, b):
    return (a - b) / 2


# ─── Game Data ───────────────────────────────────────────────────────────────────

def get_next_event(events):
    upcoming = []
    now      = datetime.datetime.now(datetime.UTC)
    for e in events:
        try:
            comp  = e.get("competitions", [{}])[0]
            state = comp.get("status", {}).get("type", {}).get("state")
            dt    = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            if state == "in":
                return e
            if dt > now:
                upcoming.append((dt, e))
        except:
            continue
    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[0])
    return upcoming[0][1]


def get_last_finished_event(events):
    """
    Return the most recently completed ('post') game from a team's schedule.
    Scans all events and picks the one with the latest date that has already finished.
    Returns None if no completed games are found.
    """
    finished = []
    now = datetime.datetime.now(datetime.UTC)
    for e in events:
        try:
            comp  = e.get("competitions", [{}])[0]
            state = comp.get("status", {}).get("type", {}).get("state")
            dt    = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            if state == "post" and dt < now:
                finished.append((dt, e))
        except:
            continue
    if not finished:
        return None
    finished.sort(key=lambda x: x[0], reverse=True)
    return finished[0][1]


# 2026 World Cup runs June 11 – July 19; padded a bit on either side.
WORLD_CUP_DATE_RANGE = "20260601-20260731"


def get_world_cup_events(abbr):
    """
    National-team schedules aren't reliably exposed via the per-team
    /schedule endpoint under the fifa.world tournament league (it comes back
    empty) — ESPN only really backs that league with the scoreboard. Pull
    the whole tournament scoreboard for the date range instead and filter
    down to games involving this team.
    """
    url    = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
    params = {"dates": WORLD_CUP_DATE_RANGE, "limit": 300}
    try:
        data   = session.get(url, params=params, timeout=8).json()
        events = data.get("events", [])
        abbr   = abbr.lower()
        return [
            e for e in events
            if any(
                c.get("team", {}).get("abbreviation", "").lower() == abbr
                for c in e.get("competitions", [{}])[0].get("competitors", [])
            )
        ]
    except Exception as e:
        print(f"World Cup scoreboard fetch failed for {abbr}:", e)
        return []


def get_world_cup_team_game(abbr):
    events    = get_world_cup_events(abbr)
    info      = fetch_team_info("soccer/fifa.world", abbr)
    event     = get_next_event(events)
    last_evt  = get_last_finished_event(events)
    last_game = _build_last_game_dict(last_evt, "soccer/fifa.world") if last_evt else None

    if not event:
        return {
            "title":         info["displayName"],
            "subtitle":      "No upcoming games",
            "home_logo_url": info["logo"],
            "away_logo_url": None,
            "state":         None,
            "league":        "soccer/fifa.world",
            "team_id":       abbr,
            "home_record":   None,
            "away_record":   None,
            "last_game":     last_game,
        }

    comp        = event["competitions"][0]
    comp_status = comp.get("status", {}).get("type", {})
    state       = comp_status.get("state")
    competitors = comp["competitors"]
    home_comp   = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away_comp   = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

    title = event.get("name", info["displayName"])
    short = (comp_status.get("shortDetail") or comp_status.get("detail") or "TBD")
    if state == "pre":
        raw_date = event.get("date", "")
        try:
            dt_utc   = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(ET)
            short    = dt_local.strftime("%A %B %d, %I:%M %p")
        except:
            pass

    return {
        "title":         title,
        "subtitle":      short,
        "home_logo_url": fetch_team_logo(home_comp["team"]["id"], "soccer/fifa.world"),
        "away_logo_url": fetch_team_logo(away_comp["team"]["id"], "soccer/fifa.world"),
        "state":         state,
        "league":        "soccer/fifa.world",
        "team_id":       abbr,
        "home_record":   None,
        "away_record":   None,
        "last_game":     last_game,
    }


def get_team_game(sport_league, team_id):
    if sport_league == "soccer/fifa.world":
        return get_world_cup_team_game(team_id)

    # Convert soccer club abbreviations to numeric IDs if needed
    if sport_league.startswith("soccer/") and not str(team_id).isdigit():
        teams = fetch_league_teams(sport_league)
        match = next((t for t in teams if t["abbreviation"] == str(team_id).lower()), None)
        if match:
            team_id = str(match["id"])

    # ESPN's club-soccer team schedule broke on site.api.espn.com — it now
    # silently returns events: [] no matter what season/seasontype params are
    # passed. The working replacement lives on a different host entirely and
    # uses a league-agnostic "all" path plus a required fixture=true flag.
    if sport_league.startswith("soccer/"):
        url = f"https://site.web.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule"
        params = {"fixture": "true"}
    else:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_league}/teams/{team_id}/schedule"
        params = {}
        if sport_league in SEASON_YEAR_LEAGUES:
            params["season"] = season_year_for(sport_league)
            params["seasontype"] = 2  # 2 = regular season (1 = preseason, 3 = postseason)
    try:
        data      = session.get(url, params=params, timeout=5).json()
        team_info = data.get("team", {})
        events    = data.get("events", [])

        # If parameterized search yielded no games, try without parameters
        if not events and params:
            data   = session.get(url, timeout=5).json()
            team_info = data.get("team", team_info)
            events = data.get("events", [])

        event     = get_next_event(events)
        logo_url  = fetch_team_logo(team_id, sport_league)

        last_event = get_last_finished_event(events)
        last_game  = None
        if last_event:
            last_game = _build_last_game_dict(last_event, sport_league)

        if not event:
            return {
                "title":        team_info.get("displayName"),
                "subtitle":     "No upcoming games",
                "home_logo_url": logo_url,
                "away_logo_url": None,
                "state":        None,
                "league":       sport_league,
                "team_id":      team_id,
                "home_record":  None,
                "away_record":  None,
                "last_game":    last_game,
            }

        comp        = event["competitions"][0]
        comp_status = comp.get("status", {}).get("type", {})
        state       = comp_status.get("state")

        competitors = comp["competitors"]
        home_comp   = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away_comp   = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        home_team   = home_comp["team"]["id"]
        away_team   = away_comp["team"]["id"]

        title = event.get("name", team_info.get("displayName", "Unknown Game"))
        short = (comp_status.get("shortDetail") or comp_status.get("detail") or "TBD")

        if comp_status.get("state") == "pre":
            raw_date = event.get("date", "")
            try:
                dt_utc  = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                dt_local = dt_utc.astimezone(ET)
                short   = dt_local.strftime("%A %B %d, %I:%M %p")
            except:
                pass

        return {
            "title":         title,
            "subtitle":      short,
            "home_logo_url": fetch_team_logo(home_team, sport_league),
            "away_logo_url": fetch_team_logo(away_team, sport_league),
            "state":         state,
            "league":        sport_league,
            "team_id":       team_id,
            "home_record":   fetch_team_record(home_team, sport_league),
            "away_record":   fetch_team_record(away_team, sport_league),
            "last_game":     last_game,
        }
    except Exception as e:
        print(f"Team fetch failed for {sport_league}/{team_id}:", e)
        return None


def _build_last_game_dict(event, sport_league):
    """
    Build a display dict for a completed game containing:
      title, date string, away/home team abbreviations, logos, scores, winner flag,
      and the raw event + league so final_display() can use the full competition object.

    Score is read from score.displayValue (ESPN's post-game structure).
    Winner is taken from the competitor's own 'winner' boolean rather than
    comparing scores, so OT/SO results are handled correctly.
    """
    try:
        comp        = event["competitions"][0]
        competitors = comp["competitors"]
        home_comp   = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away_comp   = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        # ESPN wraps the score in a dict post-game: {"displayValue": "4", "value": 4.0}
        # Fall back to a plain string/int for any legacy paths that return it directly.
        def _score(comp):
            s = comp.get("score", "0")
            if isinstance(s, dict):
                return s.get("displayValue", "0")
            return str(s)

        home_score = _score(home_comp)
        away_score = _score(away_comp)

        # Use ESPN's own winner flag — more reliable than score comparison for OT/SO
        home_won = home_comp.get("winner")   # True / False / None
        if home_won is None:
            # Fallback: compare numeric scores
            try:
                home_won = int(home_score) > int(away_score)
            except:
                home_won = None

        # Status detail: "Final", "Final/OT", "Final/SO", etc.
        status_detail = (comp.get("status", {}).get("type", {}).get("shortDetail") or "Final")

        raw_date = event.get("date", "")
        try:
            dt_utc   = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(ET)
            date_str = dt_local.strftime("%b %d, %Y")
        except:
            date_str = ""

        return {
            "title":          event.get("name", ""),
            "date_str":       date_str,
            "status_detail":  status_detail,
            "away_abbr":      away_comp["team"].get("abbreviation", ""),
            "home_abbr":      home_comp["team"].get("abbreviation", ""),
            "away_logo_url":  fetch_team_logo(away_comp["team"]["id"], sport_league),
            "home_logo_url":  fetch_team_logo(home_comp["team"]["id"], sport_league),
            "away_score":     away_score,
            "home_score":     home_score,
            "home_won":       home_won,
            # Raw data kept so final_display() can call sport-specific renderers
            "_event":         event,
            "_league":        sport_league,
        }
    except Exception as e:
        print("Could not build last game dict:", e)
        return None


def get_mma_fight():
    url = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
    try:
        data   = session.get(url, timeout=5).json()
        events = data.get("events", [])
        if not events:
            return None
        event    = events[0]
        raw_date = event.get("date", "")
        dt       = datetime.datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        return {
            "title":         event["name"],
            "subtitle":      dt.strftime("%A %B %d, %I:%M %p"),
            "home_logo_url": "https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png",
            "away_logo_url": None,
            "state":         None,
            "league":        "UFC",
            "team_id":       None,
            "home_record":   None,
            "away_record":   None,
            "last_game":     None,
        }
    except Exception as e:
        print("Could not find UFC fight", e)
        return None


def fetch_all_data():
    results = []
    for league, team in MY_TEAMS:
        res = get_team_game(league, team)
        if res:
            results.append(res)
    mma = get_mma_fight()
    if mma:
        results.append(mma)
    return results


def refresh_data():
    global sports_data, last_data_update
    if time.time() - last_data_update > DATA_REFRESH:
        sports_data      = fetch_all_data()
        last_data_update = time.time()


# ─── Flask Web Interface ─────────────────────────────────────────────────────────

WEB_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sports Display Manager</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600&display=swap');
  :root {
    --bg: #0a0a0f;
    --surface: #13131a;
    --border: #1e1e2e;
    --accent: #e8ff00;
    --accent2: #ff3c3c;
    --text: #f0f0f0;
    --muted: #666;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif; min-height: 100vh; }
  header {
    padding: 24px 20px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 14px;
  }
  header h1 { font-family: 'Bebas Neue', sans-serif; font-size: 2rem; letter-spacing: 2px; color: var(--accent); }
  header span { color: var(--muted); font-size: .85rem; }
  .container { max-width: 640px; margin: 0 auto; padding: 24px 16px 80px; }
  .section-label { font-family: 'Bebas Neue', sans-serif; font-size: 1.1rem; letter-spacing: 2px; color: var(--muted); margin-bottom: 12px; }
  .team-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px;
    display: flex; align-items: center; gap: 14px; margin-bottom: 10px;
  }
  .team-card img { width: 44px; height: 44px; object-fit: contain; border-radius: 6px; background:#1a1a26; }
  .team-card .info { flex: 1; }
  .team-card .info .name { font-weight: 600; font-size: .95rem; }
  .team-card .info .league { font-size: .78rem; color: var(--muted); margin-top: 2px; }
  .btn-delete {
    background: none; border: 1px solid #333; color: var(--accent2);
    padding: 7px 14px; border-radius: 6px; cursor: pointer; font-size: .82rem; font-family: inherit;
    transition: all .15s;
  }
  .btn-delete:hover { background: var(--accent2); color: #fff; border-color: var(--accent2); }
  .add-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-top: 28px; }
  .add-panel h2 { font-family: 'Bebas Neue', sans-serif; font-size: 1.4rem; letter-spacing: 2px; margin-bottom: 16px; }
  select, .search-input {
    width: 100%; background: #0d0d14; border: 1px solid #2a2a3a; color: var(--text);
    border-radius: 8px; padding: 12px 14px; font-size: .93rem; font-family: inherit;
    margin-bottom: 14px; outline: none; appearance: none;
  }
  select:focus, .search-input:focus { border-color: var(--accent); }
  .team-list { max-height: 320px; overflow-y: auto; }
  .team-list::-webkit-scrollbar { width: 4px; }
  .team-list::-webkit-scrollbar-thumb { background: #2a2a3a; border-radius: 2px; }
  .team-row {
    display: flex; align-items: center; gap: 12px; padding: 11px 12px;
    border-radius: 8px; cursor: pointer; transition: background .12s;
  }
  .team-row:hover { background: #1a1a26; }
  .team-row img { width: 36px; height: 36px; object-fit: contain; background: #1a1a26; border-radius: 5px; }
  .team-row .tname { font-size: .9rem; font-weight: 500; flex: 1; }
  .team-row .tadd {
    background: var(--accent); color: #000; border: none;
    padding: 6px 14px; border-radius: 6px; font-size: .8rem; font-weight: 600; cursor: pointer;
    font-family: inherit; transition: opacity .15s;
  }
  .team-row .tadd:hover { opacity: .8; }
  .team-row .tadd.added { background: #2a2a3a; color: var(--muted); cursor: default; }
  .msg { padding: 12px; border-radius: 8px; font-size: .9rem; margin-bottom: 14px; display: none; }
  .msg.ok  { background: #0d2e0d; border: 1px solid #1a5c1a; color: #6dff6d; display: block; }
  .msg.err { background: #2e0d0d; border: 1px solid #5c1a1a; color: #ff6d6d; display: block; }
  .loading { color: var(--muted); font-size: .9rem; padding: 20px 0; text-align: center; }
</style>
</head>
<body>
<header>
  <div>
    <h1>⚡ Sports Display</h1>
    <span>Manage your tracked teams</span>
  </div>
</header>
<div class="container">
  <div id="msg" class="msg"></div>
  <div class="section-label">YOUR TEAMS</div>
  <div id="current-teams"><div class="loading">Loading…</div></div>
  <div class="add-panel">
    <h2>Add a Team</h2>
    <select id="league-select" onchange="loadLeagueTeams()">
      <option value="">— Pick a league —</option>
      <option value="basketball/nba">NBA</option>
      <option value="baseball/mlb">MLB</option>
      <option value="hockey/nhl">NHL</option>
      <option value="football/nfl">NFL</option>
      <option value="football/college-football">NCAAF</option>
      <option value="soccer/usa.1">MLS</option>
      <option value="soccer/eng.1">Premier League</option>
    </select>
    <input id="search-box" class="search-input" placeholder="Search teams…" oninput="filterTeams()" style="display:none">
    <div id="team-list" class="team-list"></div>
  </div>
</div>
<script>
let allLeagueTeams = [];
let currentTeams   = [];

async function api(path, opts={}) {
  const r = await fetch(path, opts);
  return r.json();
}

function showMsg(text, type='ok') {
  const el = document.getElementById('msg');
  el.textContent = text;
  el.className   = 'msg ' + type;
  setTimeout(() => el.className = 'msg', 3000);
}

async function loadCurrentTeams() {
  const data = await api('/api/teams');
  currentTeams = data.teams;
  const el = document.getElementById('current-teams');
  if (!currentTeams.length) { el.innerHTML = '<div class="loading">No teams added yet.</div>'; return; }
  el.innerHTML = currentTeams.map((t,i) => `
    <div class="team-card">
      <img src="${t.logo}" onerror="this.src='https://img.icons8.com/color/1200/espn.jpg'">
      <div class="info">
        <div class="name">${t.displayName}</div>
        <div class="league">${t.leagueName}</div>
      </div>
      <button class="btn-delete" onclick="deleteTeam(${i})">Remove</button>
    </div>
  `).join('');
}

async function deleteTeam(idx) {
  const res = await api('/api/teams/' + idx, {method:'DELETE'});
  if (res.ok) { showMsg('Team removed.'); loadCurrentTeams(); renderLeagueList(); }
  else showMsg(res.error || 'Error removing team.', 'err');
}

async function loadLeagueTeams() {
  const league  = document.getElementById('league-select').value;
  const listEl  = document.getElementById('team-list');
  const searchEl = document.getElementById('search-box');
  if (!league) { listEl.innerHTML=''; searchEl.style.display='none'; return; }
  listEl.innerHTML = '<div class="loading">Loading teams…</div>';
  searchEl.style.display = 'block';
  searchEl.value = '';
  const data = await api('/api/league_teams?league=' + encodeURIComponent(league));
  allLeagueTeams = data.teams || [];
  renderLeagueList();
}

function filterTeams() {
  renderLeagueList(document.getElementById('search-box').value.toLowerCase());
}

function renderLeagueList(filter='') {
  const listEl     = document.getElementById('team-list');
  const filtered   = allLeagueTeams.filter(t => !filter || t.displayName.toLowerCase().includes(filter));
  const currentAbbrs = currentTeams.map(t => t.abbreviation + '|' + t.league);
  listEl.innerHTML = filtered.map(t => {
    const key     = t.abbreviation + '|' + document.getElementById('league-select').value;
    const already = currentAbbrs.includes(key);
    return `<div class="team-row">
      <img src="${t.logo}" onerror="this.src='https://img.icons8.com/color/1200/espn.jpg'">
      <span class="tname">${t.displayName}</span>
      <button class="tadd ${already?'added':''}" onclick="addTeam('${t.abbreviation}')" ${already?'disabled':''}>
        ${already ? 'Added' : '+ Add'}
      </button>
    </div>`;
  }).join('') || '<div class="loading">No teams found.</div>';
}

async function addTeam(abbr) {
  const league = document.getElementById('league-select').value;
  const res = await api('/api/teams', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({league, abbreviation: abbr})
  });
  if (res.ok) { showMsg(res.message || 'Team added!'); loadCurrentTeams(); renderLeagueList(); }
  else showMsg(res.error || 'Error adding team.', 'err');
}

loadCurrentTeams();
</script>
</body>
</html>
"""

flask_app = Flask(__name__)
LEAGUE_LABELS = {l: n for l, n in SUPPORTED_LEAGUES}


@flask_app.route("/")
def index():
    return render_template_string(WEB_HTML)


@flask_app.route("/api/teams", methods=["GET"])
def api_get_teams():
    result = []
    for league, abbr in MY_TEAMS:
        teams = fetch_league_teams(league)
        info = next((t for t in teams if t["abbreviation"] == abbr.lower() or str(t.get("id")) == str(abbr)), None)
        result.append({
            "league":      league,
            "leagueName":  LEAGUE_LABELS.get(league, league),
            "abbreviation": abbr,
            "displayName": info["displayName"] if info else abbr.upper(),
            "logo":        info["logo"] if info else FALLBACK_LOGO,
        })
    return jsonify({"teams": result})


@flask_app.route("/api/teams", methods=["POST"])
def api_add_team():
    body   = request.get_json() or {}
    league = body.get("league", "").strip()
    abbr   = body.get("abbreviation", "").strip().lower()
    if not league or not abbr:
        return jsonify({"ok": False, "error": "Missing league or abbreviation"}), 400
    teams = fetch_league_teams(league)
    match = next((t for t in teams if t["abbreviation"] == abbr), None)
    if not match:
        return jsonify({"ok": False, "error": "Team not found in league"}), 404
    # ESPN's soccer club endpoints only resolve teams by numeric ID, not
    # abbreviation (unlike NFL/NBA/MLB/NHL/NCAAF) — store the ID for those.
    is_soccer_club = league.startswith("soccer/") and league != "soccer/fifa.world"
    identifier = str(match["id"]) if is_soccer_club else abbr
    if (league, identifier) in [(l, a.lower()) for l, a in MY_TEAMS]:
        return jsonify({"ok": False, "error": "Team already added"}), 409
    MY_TEAMS.append((league, identifier))
    save_my_teams(MY_TEAMS)
    rebuild_team_ids()
    return jsonify({"ok": True, "message": "Team added!"})


@flask_app.route("/api/teams/<int:idx>", methods=["DELETE"])
def api_delete_team(idx):
    if idx < 0 or idx >= len(MY_TEAMS):
        return jsonify({"ok": False, "error": "Invalid index"}), 404
    MY_TEAMS.pop(idx)
    save_my_teams(MY_TEAMS)
    rebuild_team_ids()
    return jsonify({"ok": True})


@flask_app.route("/api/league_teams")
def api_league_teams():
    league = request.args.get("league", "")
    if not league:
        return jsonify({"teams": []})
    teams = fetch_league_teams(league)
    return jsonify({"teams": teams})


def run_flask():
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


# ─── Live Sport Renderers ────────────────────────────────────────────────────────

def get_nhl_sog(game_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={game_id}"
    try:
        data = session.get(url, timeout=5).json()
        sog  = {"home": 0, "away": 0}
        for team in data.get("boxscore", {}).get("teams", []):
            ha = team.get("homeAway")
            for stat in team.get("statistics", []):
                if stat.get("name") == "shotsTotal":
                    sog[ha] = int(stat.get("displayValue", 0))
        return sog
    except:
        return {"home": 0, "away": 0}


def render_hockey(screen, game, home, away, league):
    league_logo = get_cached_logo(league_logos[league], 200)
    screen.blit(league_logo, (get_center(WIDTH, league_logo.get_width()), 0))

    period_time = game["status"]["type"]["shortDetail"]
    pt = period_time
    if "-" in period_time:
        game_clock, period = period_time.split("-")
        pt = f"{period}: {game_clock}"

    pt_surf = sub_font.render(pt, True, (255, 255, 255))
    screen.blit(pt_surf, (get_center(WIDTH, pt_surf.get_width()), get_center(HEIGHT, pt_surf.get_height())))

    sog     = get_nhl_sog(game["id"])
    sog_surf = sub_font.render(f"SOG: {sog['away']}-{sog['home']}", True, (255, 255, 255))
    screen.blit(sog_surf, (get_center(WIDTH, sog_surf.get_width()), get_center(4 * HEIGHT / 3, sog_surf.get_height())))


def get_nba_bonus(game_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={game_id}"
    try:
        data  = session.get(url, timeout=5).json()
        bonus = {"home": False, "away": False}
        for comp in data.get("header", {}).get("competitions", [{}])[0].get("competitors", []):
            ha    = comp.get("homeAway")
            state = comp.get("fouls", {}).get("bonusState", "")
            if state in ("BONUS", "DOUBLE"):
                bonus[ha] = True
        return bonus
    except:
        return {"home": False, "away": False}


def render_basketball(screen, game, home, away, league):
    league_logo = get_cached_logo(league_logos[league], 200)
    screen.blit(league_logo, (get_center(WIDTH, league_logo.get_width()), 0))

    game_id = game["id"]
    bonus   = get_nba_bonus(game_id)

    bonus_surf = sub_font.render("BONUS", True, (255, 255, 255))
    bx = get_center(WIDTH, bonus_surf.get_width())
    by = get_center(4 * HEIGHT / 3, bonus_surf.get_height())
    screen.blit(bonus_surf, (bx, by))

    tri_h = bonus_surf.get_height()
    tri_w = int(tri_h / math.tan(math.radians(45)))
    at, ab = by, by + tri_h
    am     = by + tri_h / 2

    if bonus["away"]:
        ax_r = int(bx - 10)
        ax_l = int(ax_r - tri_w)
        pygame.draw.polygon(screen, (255, 255, 255), [(ax_r, at), (ax_r, ab), (ax_l, am)], 0)

    if bonus["home"]:
        hx_l = int(bx + bonus_surf.get_width() + 10)
        hx_r = int(hx_l + tri_w)
        pygame.draw.polygon(screen, (255, 255, 255), [(hx_l, at), (hx_l, ab), (hx_r, am)], 0)

    qt = game["status"]["type"]["shortDetail"]
    if "-" in qt:
        c, p = qt.split("-")
        qt = f"{p}: {c}"
    qt_surf = sub_font.render(qt, True, (255, 255, 255))
    screen.blit(qt_surf, (get_center(WIDTH, qt_surf.get_width()), get_center(HEIGHT, qt_surf.get_height())))


def draw_diamond(surface, color, cx, cy, radius, width=0):
    pts = [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)]
    pygame.draw.polygon(surface, color, pts, width)


def render_baseball(screen, game, home, away, league):
    x_mid = WIDTH / 2
    y_mid = HEIGHT / 2

    sit     = game.get("competitions", [{}])[0].get("situation", {})
    balls   = sit.get("balls",   0)
    strikes = sit.get("strikes", 0)
    outs    = sit.get("outs",    0)
    r1      = sit.get("onFirst",  False)
    r2      = sit.get("onSecond", False)
    r3      = sit.get("onThird",  False)

    inning_str      = game["status"]["type"]["shortDetail"]
    top_bot, inning = inning_str.split(" ", 1)

    radius = 50
    gap    = 10
    base_y = 3 * HEIGHT / 10
    draw_diamond(screen, (255, 255, 255), x_mid + radius + gap, base_y, radius, 0 if r1 else 1)
    draw_diamond(screen, (255, 255, 255), x_mid,                base_y - radius - gap, radius, 0 if r2 else 1)
    draw_diamond(screen, (255, 255, 255), x_mid - radius - gap, base_y, radius, 0 if r3 else 1)

    for i in range(3):
        pygame.draw.circle(screen, (255, 255, 255),
                           (int(x_mid - 45 + i * 45), int(y_mid)), 15, 0 if i < outs else 1)

    in_surf = sub_font.render(inning, True, (255, 255, 255))
    is_x    = get_center(WIDTH, in_surf.get_width())
    is_y    = 3 * HEIGHT / 5
    screen.blit(in_surf, (is_x, is_y))

    ty  = is_y
    by2 = is_y + in_surf.get_height()
    tw  = in_surf.get_height() / math.tan(math.radians(45))
    xr  = is_x - 10
    xl  = xr - tw
    xm  = xr - tw / 2
    if top_bot == "Bot":
        pts = [(xr, ty), (xl, ty), (xm, by2)]
    else:
        pts = [(xr, by2), (xl, by2), (xm, ty)]
    pygame.draw.polygon(screen, (255, 255, 255), pts, 0)

    cnt_surf = sub_font.render(f"{balls}-{strikes}", True, (255, 255, 255))
    screen.blit(cnt_surf, (x_mid + in_surf.get_width() / 2 + 10, is_y))

    league_logo = get_cached_logo(league_logos[league], 250)
    screen.blit(league_logo, (get_center(WIDTH, league_logo.get_width()), HEIGHT - league_logo.get_height() + 50))


def render_football(screen, game, home, away, league):
    try:
        league_logo = get_cached_logo(league_logos[league], 200)
    except Exception as e:
        league_logo = get_cached_logo(FALLBACK_LOGO, 200)
    screen.blit(league_logo, (get_center(WIDTH, league_logo.get_width()), 0))

    football = pygame.image.load("images/Football.png").convert_alpha()
    football = pygame.transform.smoothscale(football, (40, 40))

    status = game.get("status", {})
    competitions = game.get("competitions", [])
    comp = competitions[0] if competitions else {}
    situation = comp.get("situation", {})

    # ----- Quarter + Time -----
    period = status.get("period", 0)
    clock = status.get("displayClock", "")

    quarter_map = {
        1: "1st",
        2: "2nd",
        3: "3rd",
        4: "4th",
        5: "OT"
    }
    quarter = quarter_map.get(period, f"Q{period}")

    quarter_time = f"{quarter} {clock}" if clock else quarter # "3rd 15:00"
    down_distance = situation.get("downDistanceText", "")     # "1st & 10"
    possession = situation.get("possession", "")              # "LAR"
    yard_line = situation.get("yardLine", None)               # "20"
    ball_spot = ""
    if possession and yard_line is not None:
        ball_spot = f"{possession} {yard_line}"               # "LAR 20"


    quarter_time_surf = title_font.render(quarter_time, True, (255, 255, 255))
    screen.blit(quarter_time_surf,
                (get_center(WIDTH, quarter_time_surf.get_width()), get_center(HEIGHT, quarter_time_surf.get_height())))

    if possession == away:
        screen.blit(football, (1 * WIDTH / 3, get_center(HEIGHT, football.get_height())))
    else:
        screen.blit(football, (18 * WIDTH / 29, get_center(HEIGHT, football.get_height())))

    down_distance_surf = sub_font.render(down_distance, True, (255, 255, 255))
    screen.blit(down_distance_surf, (get_center(WIDTH, down_distance_surf.get_width()),
                                     get_center(4 * HEIGHT / 3, down_distance_surf.get_height())))

    ball_spot_surf = sub_font.render(ball_spot, True, (255, 255, 255))
    screen.blit(ball_spot_surf, (get_center(WIDTH, ball_spot_surf.get_width()),
                                 get_center(5 * HEIGHT / 3, ball_spot_surf.get_height())))

def render_soccer(screen, game, home, away, league):
    try:
        league_logo = get_cached_logo(league_logos[league],200)
    except Exception as e:
        league_logo=get_cached_logo(FALLBACK_LOGO,200)
    screen.blit(league_logo, (get_center(WIDTH, league_logo.get_width()), 0))

    detail=game["status"]["type"]["description"]
    time=game["status"]["displayClock"]


    time_surf = title_font.render(time, True, (255, 255, 255))
    screen.blit(time_surf, (get_center(WIDTH, time_surf.get_width()), get_center(HEIGHT, time_surf.get_height())))

    detail_surf = sub_font.render(detail, True, (255, 255, 255))
    screen.blit(detail_surf, (get_center(WIDTH, detail_surf.get_width()), get_center(4 * HEIGHT / 3, detail_surf.get_height())))

# ─── Last Game Display ───────────────────────────────────────────────────────────

def final_display(last_game):
    """
    Static scoreboard for a completed game. Mirrors the live_display() layout exactly
    (same logo positions, score positions, sport-specific renderer) but does not poll —
    the event dict is fixed. Shows a BACK button (bottom-left) to return to the cycle.

    The sport-specific renderer is called with the same (screen, game, home, away)
    signature as in live_display(). Post-game the status detail reads "Final" / "Final/OT"
    and situation blocks are absent; each renderer handles these cases gracefully already.

    Winning team's score and abbreviation are highlighted in yellow.
    """
    back_button = pygame.Rect(0, 0, 0, 0)
    pad, border = 6, 2

    event  = last_game["_event"]
    league = last_game["_league"]

    comp = event.get("competitions", [{}])[0]
    home = away = None
    for t in comp.get("competitors", []):
        if t.get("homeAway") == "home":
            home = t
        else:
            away = t

    if not home or not away:
        return  # Malformed event — bail out silently

    away_logo_url = fetch_team_logo(away["team"]["id"], league)
    home_logo_url = fetch_team_logo(home["team"]["id"], league)

    # Winner colours: gold for the winner, dim white for the loser
    home_won  = last_game.get("home_won")
    away_col  = (255, 220, 0) if home_won is False else (220, 220, 220)
    home_col  = (255, 220, 0) if home_won is True  else (220, 220, 220)

    # Score strings — already extracted correctly by _build_last_game_dict
    away_score_str = last_game["away_score"]
    home_score_str = last_game["home_score"]

    # Status label: "FINAL", "FINAL/OT", "FINAL/SO", etc. — uppercased for display
    status_label = last_game.get("status_detail", "Final").upper()

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); exit()
            if event.type == pygame.MOUSEBUTTONDOWN:
                check_exit(event.pos)
                if back_button.collidepoint(event.pos):
                    pygame.event.clear()
                    return

        screen.fill((0, 0, 0))

        # ── Away team — left third (identical geometry to live_display) ──
        al_surf = get_cached_logo(away_logo_url, 250)
        al_y    = get_center(HEIGHT, al_surf.get_height())
        al_x    = get_center(WIDTH / 3, al_surf.get_width())
        screen.blit(al_surf, (al_x, al_y))
        aa_surf = title_font.render(away["team"]["abbreviation"], True, away_col)
        screen.blit(aa_surf, (get_center(WIDTH / 3, aa_surf.get_width()),
                               (HEIGHT + al_surf.get_height()) / 2))

        # ── Home team — right third ──
        hl_surf = get_cached_logo(home_logo_url, 250)
        hl_x    = al_x + 2 * WIDTH / 3
        screen.blit(hl_surf, (hl_x, al_y))
        ha_surf = title_font.render(home["team"]["abbreviation"], True, home_col)
        screen.blit(ha_surf, (get_center(WIDTH / 3, ha_surf.get_width()) + 2 * WIDTH / 3,
                               (HEIGHT + hl_surf.get_height()) / 2))

        # ── Final scores above each logo ──
        as_surf = score_font.render(away_score_str, True, away_col)
        screen.blit(as_surf, (get_center(WIDTH / 3, as_surf.get_width()),
                               al_y - as_surf.get_height() - 10))
        hs_surf = score_font.render(home_score_str, True, home_col)
        screen.blit(hs_surf, (get_center(WIDTH / 3, hs_surf.get_width()) + 2 * WIDTH / 3,
                               al_y - hs_surf.get_height() - 10))

        # ── FINAL / FINAL/OT label centred (no live renderer — status strings are
        #    in live format only and situation blocks are absent post-game) ──
        st_surf = sub_font.render(status_label, True, (180, 180, 180))
        screen.blit(st_surf, (get_center(WIDTH, st_surf.get_width()),
                               get_center(HEIGHT, st_surf.get_height())))

        # ── Date line below the status label ──
        dt_surf = small_font.render(last_game["date_str"], True, (120, 120, 120))
        screen.blit(dt_surf, (get_center(WIDTH, dt_surf.get_width()),
                               get_center(HEIGHT, st_surf.get_height()) + st_surf.get_height() + 4))

        # ── BACK button — bottom-left ──
        bk_s = sub_font.render("CYCLE", True, (255, 255, 255))
        bx   = 0
        by   = HEIGHT - (bk_s.get_height() + pad * 2 + border)
        back_button = pygame.Rect(bx, by, bk_s.get_width() + pad * 2, bk_s.get_height() + pad * 2)
        pygame.draw.rect(screen, (0, 0, 0), back_button)
        pygame.draw.rect(screen, (255, 255, 255), back_button, border)
        screen.blit(bk_s, (bx + pad, by + pad))

        pygame.display.update()
        clock.tick(30)


# ─── Live Display ────────────────────────────────────────────────────────────────

def live_display(league, extra_renderer=None):
    """
    Full-screen live scoreboard for in-progress games. If the game(s) being
    watched end while this is on screen, transitions straight into
    final_display() with that game's final score instead of silently
    dropping back to the normal cycle.
    """
    global locked
    cycle_button     = pygame.Rect(0, 0, 0, 0)
    next_game_button = pygame.Rect(0, 0, 0, 0)
    game_idx = 0
    watched_game_id = None   # id of the game currently on screen, tracked across polls

    while True:
        scores_url = f"https://site.api.espn.com/apis/site/v2/sports/{league}/scoreboard"
        try:
            data       = session.get(scores_url, timeout=5).json()
            all_events = data.get("events", [])
            games      = []

            for event in all_events:
                state = event.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state")
                if state != "in":
                    continue
                for c in event["competitions"][0]["competitors"]:
                    if c["team"]["id"] in TEAM_IDS.get(league, []):
                        games.append(event)
                        break

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); exit()
                if event.type == pygame.MOUSEBUTTONDOWN:
                    check_exit(event.pos)
                    if cycle_button.collidepoint(event.pos):
                        locked = False
                        pygame.event.clear()
                        return
                    if next_game_button.collidepoint(event.pos) and games:
                        game_idx = (game_idx + 1) % len(games)

            if not games:
                # No live games left for this league among our teams. If we were
                # actively watching one, see if it just went final and, if so,
                # show its final score instead of silently returning to cycle.
                finished_event = None
                if watched_game_id:
                    finished_event = next((e for e in all_events if e.get("id") == watched_game_id), None)

                if not finished_event:
                    # Fallback: any tracked team's game that just finished
                    for event in all_events:
                        state = event.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state")
                        if state != "post":
                            continue
                        for c in event["competitions"][0]["competitors"]:
                            if c["team"]["id"] in TEAM_IDS.get(league, []):
                                finished_event = event
                                break
                        if finished_event:
                            break

                if finished_event:
                    fstate = finished_event.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state")
                    if fstate == "post":
                        last_game = _build_last_game_dict(finished_event, league)
                        if last_game:
                            final_display(last_game)

                return

            game_idx = game_idx % len(games)
            game     = games[game_idx]
            watched_game_id = game.get("id")
            comp     = game["competitions"][0]
            home = away = None
            for t in comp["competitors"]:
                if t["homeAway"] == "home":
                    home = t
                else:
                    away = t

            screen.fill((0, 0, 0))

            al_surf = get_cached_logo(away["team"]["logo"], 250)
            al_y    = get_center(HEIGHT, al_surf.get_height())
            al_x    = get_center(WIDTH / 3, al_surf.get_width())
            screen.blit(al_surf, (al_x, al_y))
            aa_surf = title_font.render(away["team"]["abbreviation"], True, (255, 255, 255))
            screen.blit(aa_surf, (get_center(WIDTH / 3, aa_surf.get_width()), (HEIGHT + al_surf.get_height()) / 2))

            hl_surf = get_cached_logo(home["team"]["logo"], 250)
            hl_x    = al_x + 2 * WIDTH / 3
            screen.blit(hl_surf, (hl_x, al_y))
            ha_surf = title_font.render(home["team"]["abbreviation"], True, (255, 255, 255))
            screen.blit(ha_surf, (get_center(WIDTH / 3, ha_surf.get_width()) + 2 * WIDTH / 3,
                                  (HEIGHT + hl_surf.get_height()) / 2))

            as_surf = score_font.render(away.get("score", "0"), True, (255, 255, 255))
            screen.blit(as_surf, (get_center(WIDTH / 3, as_surf.get_width()), al_y - as_surf.get_height() - 10))
            hs_surf = score_font.render(home.get("score", "0"), True, (255, 255, 255))
            screen.blit(hs_surf, (get_center(WIDTH / 3, hs_surf.get_width()) + 2 * WIDTH / 3,
                                  al_y - hs_surf.get_height() - 10))

            if extra_renderer:
                extra_renderer(screen, game, home, away, league)

            pad, border = 6, 2
            cy_s = sub_font.render("CYCLE", True, (255, 255, 255))
            cx, cy = 0, HEIGHT - (cy_s.get_height() + pad * 2 + border)
            cycle_button = pygame.Rect(cx, cy, cy_s.get_width() + pad * 2, cy_s.get_height() + pad * 2)
            pygame.draw.rect(screen, (0, 0, 0), cycle_button)
            pygame.draw.rect(screen, (255, 255, 255), cycle_button, border)
            screen.blit(cy_s, (cx + pad, cy + pad))

            if len(games) > 1:
                nx_s = sub_font.render(f"NEXT ({game_idx + 1}/{len(games)})", True, (255, 255, 255))
                nx   = WIDTH - (nx_s.get_width() + 2 * pad + border)
                ny   = HEIGHT - (nx_s.get_height() + pad * 2 + border)
                next_game_button = pygame.Rect(nx, ny, nx_s.get_width() + pad * 2, nx_s.get_height() + pad * 2)
                pygame.draw.rect(screen, (0, 0, 0), next_game_button)
                pygame.draw.rect(screen, (255, 255, 255), next_game_button, border)
                screen.blit(nx_s, (nx + pad, ny + pad))
            else:
                next_game_button = pygame.Rect(0, 0, 0, 0)

            pygame.display.update()
            clock.tick(1 / LIVE_DATA_REFRESH)

        except Exception as e:
            print(f"Display failed for {league}:", e)
            return


# ─── Main Cycle ──────────────────────────────────────────────────────────────────

def cycle():
    global locked
    live_button  = pygame.Rect(0, 0, 0, 0)
    lock_button  = pygame.Rect(0, 0, 0, 0)
    last_button  = pygame.Rect(0, 0, 0, 0)   # "LAST" button — shown only when a finished game exists
    left_button  = pygame.Rect(0, 0, 0, 0)
    right_button = pygame.Rect(0, 0, 0, 0)

    current_idx     = 0
    last_rotation   = time.time()
    ROTATION_SPEED  = 5
    scroll_x        = 0
    last_known_state = None

    while True:
        refresh_data()

        if not sports_data:
            screen.fill((0, 0, 0))
            pygame.display.update()
            clock.tick(DATA_REFRESH)
            continue

        if time.time() - last_rotation > ROTATION_SPEED and not locked:
            current_idx      = (current_idx + 1) % len(sports_data)
            scroll_x         = 0
            last_rotation    = time.time()
            last_known_state = None

        item          = sports_data[current_idx]
        league        = item["league"]
        current_state = item["state"]
        last_game     = item.get("last_game")   # None for UFC or teams with no completed games

        # ── Input handling ──
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); exit()
            if event.type == pygame.MOUSEBUTTONDOWN:
                check_exit(event.pos)
                if live_button.collidepoint(event.pos):
                    locked = True
                    if league == "hockey/nhl":
                        live_display(league, render_hockey)
                    elif league == "baseball/mlb":
                        live_display(league, render_baseball)
                    elif league == "basketball/nba":
                        live_display(league, render_basketball)
                    elif league in ("football/nfl", "football/college-football"):
                        live_display(league, render_football)
                    elif league in ("soccer/usa.1", "soccer/eng.1"):
                        live_display(league, render_soccer)
                    last_rotation = time.time()
                if lock_button.collidepoint(event.pos):
                    locked = not locked
                if last_button.collidepoint(event.pos) and last_game:
                    final_display(last_game) # Enter the static final score screen
                    last_rotation = time.time()
                if left_button.collidepoint(event.pos):
                    current_idx   = (current_idx - 1) % len(sports_data)
                    scroll_x      = 0
                    last_rotation = time.time()
                if right_button.collidepoint(event.pos):
                    current_idx   = (current_idx + 1) % len(sports_data)
                    scroll_x      = 0
                    last_rotation = time.time()

        if locked and last_known_state != "in" and current_state == "in":
            last_known_state = current_state
            if league == "hockey/nhl":
                live_display(league, render_hockey)
            elif league == "baseball/mlb":
                live_display(league, render_baseball)
            elif league == "basketball/nba":
                live_display(league, render_basketball)
            elif league in ("football/nfl", "football/college-football"):
                live_display(league, render_football)
            elif league in ("soccer/usa.1", "soccer/eng.1"):
                live_display(league, render_soccer)
            last_rotation = time.time()
            continue

        last_known_state = current_state
        screen.fill((5, 5, 5))

        pad    = 6
        border = 2

        # ── LIVE / LOCK button (bottom-left) ──
        if item["state"] == "in":
            lock_button = pygame.Rect(0, 0, 0, 0)
            lv = sub_font.render("LIVE", True, (255, 255, 255))
            x  = 0
            y  = HEIGHT - (lv.get_height() + pad * 2 + border)
            live_button = pygame.Rect(x, y, lv.get_width() + pad * 2, lv.get_height() + pad * 2)
            pygame.draw.rect(screen, (0, 0, 0), live_button)
            pygame.draw.rect(screen, (255, 255, 255), live_button, border)
            screen.blit(lv, (x + pad, y + pad))
        else:
            live_button = pygame.Rect(0, 0, 0, 0)
            lbl = "UNLOCK" if locked else "LOCK"
            lk  = sub_font.render(lbl, True, (255, 255, 255))
            x   = 0
            y   = HEIGHT - (lk.get_height() + pad * 2 + border)
            lock_button = pygame.Rect(x, y, lk.get_width() + pad * 2, lk.get_height() + pad * 2)
            pygame.draw.rect(screen, (0, 0, 0), lock_button)
            pygame.draw.rect(screen, (255, 255, 255), lock_button, border)
            screen.blit(lk, (x + pad, y + pad))

        # ── LAST button (bottom-left, above LIVE/LOCK) — only when a finished game exists ──
        if last_game:
            lt_s = sub_font.render("LAST", True, (255, 255, 255))
            # Stack it directly above the bottom-left button with a small gap
            lt_y = y - (lt_s.get_height() + pad * 2 + border) - 8
            lt_x = 0
            last_button = pygame.Rect(lt_x, lt_y, lt_s.get_width() + pad * 2, lt_s.get_height() + pad * 2)
            pygame.draw.rect(screen, (0, 0, 0), last_button)
            pygame.draw.rect(screen, (255, 255, 255), last_button, border)
            screen.blit(lt_s, (lt_x + pad, lt_y + pad))
        else:
            last_button = pygame.Rect(0, 0, 0, 0)

        # ── < > navigation buttons (bottom-right) ──
        left_surf  = sub_font.render("<", True, (255, 255, 255))
        right_surf = sub_font.render(">", True, (255, 255, 255))
        right_button = pygame.Rect(WIDTH - (right_surf.get_width() + pad * 2 + border),
                                   HEIGHT - (right_surf.get_height() + pad * 2 + border),
                                   right_surf.get_width() + pad * 2, right_surf.get_height() + pad * 2)
        left_button  = pygame.Rect(right_button.x - (left_surf.get_width() + pad * 2 + border) - 6,
                                   right_button.y,
                                   left_surf.get_width() + pad * 2, left_surf.get_height() + pad * 2)
        for btn, surf in [(left_button, left_surf), (right_button, right_surf)]:
            pygame.draw.rect(screen, (0, 0, 0), btn)
            pygame.draw.rect(screen, (255, 255, 255), btn, border)
            screen.blit(surf, (btn.x + pad, btn.y + pad))

            # ── Title ──
            title_surf = title_font.render(item["title"], True, (255, 255, 255))
            tw = title_surf.get_width()
            if tw > WIDTH:
                gap = 60  # space between the tail of one copy and the head of the next
                cycle_w = tw + gap
                offset = scroll_x % cycle_w
                x = -offset  # first copy starts flush at the left edge (x=0) when scroll_x=0
                while x < WIDTH:
                    screen.blit(title_surf, (x, 2))
                    x += cycle_w
                scroll_x += 1.5
            else:
                x_start = get_center(WIDTH, tw)
                screen.blit(title_surf, (x_start, 2))

        # ── Subtitle ──
        sub_surf = sub_font.render(item["subtitle"], True, (255, 255, 255))
        screen.blit(sub_surf, (get_center(WIDTH, sub_surf.get_width()), 70))

        # ── Logos and records ──
        h = HEIGHT + sub_surf.get_height() - 20

        if item["away_logo_url"] is None:
            h+=100
            ls = get_cached_logo(item["home_logo_url"], 350)
            screen.blit(ls, (get_center(WIDTH, ls.get_width()), get_center(h, ls.get_height())))
        else:
            ls  = get_cached_logo(item["away_logo_url"], 250)
            y0  = get_center(h, ls.get_height())
            x0  = get_center(WIDTH / 2, ls.get_width())
            screen.blit(ls, (x0, y0))

            ar  = title_font.render(item.get("away_record") or "0-0", True, (255, 255, 255))
            rx0 = get_center(WIDTH / 2, ar.get_width())
            ry0 = ((HEIGHT - ar.get_height()) - 2 * pad)-left_button.height
            screen.blit(ar, (rx0, ry0))

            ls1 = get_cached_logo(item["home_logo_url"], 250)
            screen.blit(ls1, (x0 + WIDTH / 2, y0))

            hr  = title_font.render(item.get("home_record") or "0-0", True, (255, 255, 255))
            rx1 = rx0 + WIDTH / 2
            screen.blit(hr, (rx1, ry0))

        pygame.display.update()
        clock.tick(30)


# ─── Utilities ───────────────────────────────────────────────────────────────────

def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "unknown"


def send_startup_notification():
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set — skipping startup notification")
        return
    ip  = get_local_ip()
    url = f"http://{ip}:5000"
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=f"Sports Display is live! Manage your teams at {url}",
            headers={"Title": "Sports Display Started"},
            timeout=5
        )
        print(f"Startup notification sent — web UI at {url}")
    except Exception as e:
        print(f"Could not send startup notification: {e}")


# ─── Entry Point ─────────────────────────────────────────────────────────────────

def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    send_startup_notification()

    refresh_data()
    while True:
        cycle()


if __name__ == "__main__":
    main()