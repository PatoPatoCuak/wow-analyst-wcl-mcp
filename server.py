import json
import os
import re
import time
import statistics
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

WCL_CLIENT_ID = os.getenv("WCL_CLIENT_ID", "")
WCL_CLIENT_SECRET = os.getenv("WCL_CLIENT_SECRET", "")
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"
OPENAI_APPS_CHALLENGE = os.getenv("OPENAI_APPS_CHALLENGE", "").strip()
PUBLIC_BASE_URL = "https://wow-analyst-wcl-mcp.onrender.com"
GITHUB_REPO_URL = "https://github.com/PatoPatoCuak/wow-analyst-wcl-mcp"

mcp = MCPServer(
    "WoW Analyst - Warcraft Logs",
    instructions=(
        "Read-only Warcraft Logs analysis server for competitive World of Warcraft raid "
        "and Mythic+ analysis. Use report-specific tools first, then the GraphQL tool only "
        "for focused follow-up queries needed to explain mechanics, deaths, useful damage, "
        "cooldowns, utility, composition, routes, and consistency."
    ),
    website_url=PUBLIC_BASE_URL,
)

LOCAL_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=False,
)
EXTERNAL_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    open_world_hint=True,
)

_token: str | None = None
_token_expires_at = 0.0


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:760px;margin:48px auto;padding:0 20px;line-height:1.55;color:#111}"
        "h1{font-size:2rem}a{color:inherit}code{background:#f3f3f3;padding:2px 5px;"
        "border-radius:4px}</style></head><body>"
        f"<h1>{title}</h1>{body}</body></html>"
    )


def _require_credentials() -> None:
    if not WCL_CLIENT_ID or not WCL_CLIENT_SECRET:
        raise RuntimeError(
            "Warcraft Logs credentials are not configured. "
            "Set WCL_CLIENT_ID and WCL_CLIENT_SECRET on the server."
        )


def _get_access_token() -> str:
    global _token, _token_expires_at
    _require_credentials()

    now = time.time()
    if _token and now < _token_expires_at - 60:
        return _token

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            WCL_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(WCL_CLIENT_ID, WCL_CLIENT_SECRET),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()

    _token = payload["access_token"]
    _token_expires_at = now + int(payload.get("expires_in", 3600))
    return _token


def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    global _token, _token_expires_at

    body = {"query": query, "variables": variables or {}}

    for attempt in range(2):
        token = _get_access_token()
        with httpx.Client(timeout=90.0) as client:
            response = client.post(
                WCL_GRAPHQL_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )

        if response.status_code == 401 and attempt == 0:
            _token = None
            _token_expires_at = 0.0
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError("Warcraft Logs authentication failed")


@mcp.custom_route("/", methods=["GET"])
async def landing(_: Request) -> Response:
    return _page(
        "WoW Analyst",
        "<p>Competitive World of Warcraft raid and Mythic+ analysis powered by "
        "Warcraft Logs.</p>"
        "<p>This service exposes a read-only MCP endpoint at <code>/mcp</code>.</p>"
        "<p><a href='/privacy'>Privacy</a> · <a href='/terms'>Terms</a> · "
        "<a href='/support'>Support</a></p>",
    )


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])
async def openai_apps_challenge(_: Request) -> Response:
    if not OPENAI_APPS_CHALLENGE:
        return PlainTextResponse("", status_code=404)
    return PlainTextResponse(OPENAI_APPS_CHALLENGE, media_type="text/plain")


@mcp.custom_route("/privacy", methods=["GET"])
async def privacy(_: Request) -> Response:
    return _page(
        "WoW Analyst Privacy Policy",
        "<p>WoW Analyst processes Warcraft Logs report identifiers, report URLs, and "
        "analysis parameters only to retrieve public Warcraft Logs data and return the "
        "requested analysis.</p>"
        "<p>The service does not ask users for Warcraft Logs passwords, access tokens, or "
        "private account credentials. Warcraft Logs API credentials are stored only on the "
        "server and are not returned to users.</p>"
        "<p>WoW Analyst does not intentionally persist Warcraft Logs report contents or "
        "analysis results as a user profile. The hosting provider may retain ordinary "
        "operational logs for security, reliability, and debugging.</p>"
        "<p>Data retrieved from Warcraft Logs remains subject to Warcraft Logs' own terms "
        "and privacy practices.</p>"
        f"<p>Support: <a href='{GITHUB_REPO_URL}'>{GITHUB_REPO_URL}</a></p>",
    )


@mcp.custom_route("/terms", methods=["GET"])
async def terms(_: Request) -> Response:
    return _page(
        "WoW Analyst Terms of Use",
        "<p>WoW Analyst is provided for informational analysis of World of Warcraft "
        "performance data. Results may be incomplete or affected by log quality, encounter "
        "strategy, assignments, patches, API availability, or missing data.</p>"
        "<p>Users are responsible for interpreting recommendations in context. The service "
        "does not guarantee raid, Mythic+, ranking, or progression outcomes.</p>"
        "<p>World of Warcraft, Warcraft Logs, and related names and trademarks belong to "
        "their respective owners. WoW Analyst is an independent analysis tool and is not "
        "affiliated with or endorsed by Blizzard Entertainment or Warcraft Logs.</p>"
        f"<p>Support: <a href='{GITHUB_REPO_URL}'>{GITHUB_REPO_URL}</a></p>",
    )


@mcp.custom_route("/support", methods=["GET"])
async def support(_: Request) -> Response:
    return _page(
        "WoW Analyst Support",
        "<p>For bug reports, feature requests, and reproducible issues, use the GitHub "
        f"repository: <a href='{GITHUB_REPO_URL}'>{GITHUB_REPO_URL}</a>.</p>"
        "<p>Do not include passwords, API secrets, access tokens, or other credentials in "
        "support reports.</p>",
    )


@mcp.tool(
    title="Parse Warcraft Logs report URL",
    annotations=LOCAL_READ_ONLY,
)
def parse_report_url(url: str) -> str:
    """Extract report code and optional fight from a Warcraft Logs report URL."""
    parsed = urlparse(url.strip())
    match = re.search(r"/reports/([A-Za-z0-9]+)", parsed.path)
    if not match:
        return _json({"error": "No Warcraft Logs report code found in URL."})

    fight = parse_qs(parsed.query).get("fight", [None])[0]
    return _json({"code": match.group(1), "fight": fight})


@mcp.tool(
    title="Get Warcraft Logs report summary",
    annotations=EXTERNAL_READ_ONLY,
)
def get_report_summary(code: str) -> str:
    """
    Return report metadata and all fights.

    Use this first to understand raid/M+ session structure, encounters, kills,
    durations, revision/segments, zone and participant/spec IDs.
    """
    query = """
    query ReportSummary($code: String!) {
      reportData {
        report(code: $code) {
          code
          title
          startTime
          endTime
          visibility
          revision
          segments
          zone { id name }
          fights {
            id
            name
            startTime
            endTime
            kill
            difficulty
            encounterID
            friendlyPlayers
            friendlySpecs
          }
        }
      }
    }
    """
    return _json(_graphql(query, {"code": code}))


@mcp.tool(
    title="Get Warcraft Logs players",
    annotations=EXTERNAL_READ_ONLY,
)
def get_report_players(
    code: str,
    fight_ids_json: str = "[]",
    include_combatant_info: bool = True,
) -> str:
    """
    Return actors and playerDetails for selected fights.

    Includes specs and combatant info when available, useful for gear/talents,
    role normalization, assignments and player-to-player comparisons.
    fight_ids_json must be a JSON array such as [28] or [1,2,3].
    """
    try:
        fight_ids = json.loads(fight_ids_json)
        if not isinstance(fight_ids, list):
            raise ValueError
    except Exception:
        return _json({"error": "fight_ids_json must be a JSON array."})

    query = """
    query ReportPlayers($code: String!, $fightIDs: [Int], $combat: Boolean!) {
      reportData {
        report(code: $code) {
          masterData(translate: false) {
            logVersion
            gameVersion
            lang
            actors { id name type subType }
          }
          playerDetails(
            fightIDs: $fightIDs
            translate: false
            includeCombatantInfo: $combat
          )
        }
      }
    }
    """

    return _json(
        _graphql(
            query,
            {
                "code": code,
                "fightIDs": fight_ids or None,
                "combat": include_combatant_info,
            },
        )
    )


@mcp.tool(
    title="Get Warcraft Logs rankings",
    annotations=EXTERNAL_READ_ONLY,
)
def get_report_rankings(code: str, fight_ids_json: str = "[]") -> str:
    """
    Return Warcraft Logs rankings JSON for selected fights.

    Rankings are context only; pair them with execution data rather than using
    percentile as a verdict.
    """
    try:
        fight_ids = json.loads(fight_ids_json)
        if not isinstance(fight_ids, list):
            raise ValueError
    except Exception:
        return _json({"error": "fight_ids_json must be a JSON array."})

    query = """
    query ReportRankings($code: String!, $fightIDs: [Int]) {
      reportData {
        report(code: $code) {
          rankings(fightIDs: $fightIDs)
        }
      }
    }
    """

    return _json(
        _graphql(
            query,
            {"code": code, "fightIDs": fight_ids or None},
        )
    )


@mcp.tool(
    title="Get Warcraft Logs API rate limit",
    annotations=EXTERNAL_READ_ONLY,
)
def get_rate_limit() -> str:
    """Return Warcraft Logs API rate-limit data before expensive analysis."""
    query = """
    query RateLimit {
      rateLimitData {
        limitPerHour
        pointsSpentThisHour
        pointsResetIn
      }
    }
    """
    return _json(_graphql(query))


@mcp.tool(
    title="Query Warcraft Logs GraphQL",
    annotations=EXTERNAL_READ_ONLY,
)
def warcraftlogs_graphql(query: str, variables_json: str = "{}") -> str:
    """
    Run a read-only GraphQL query against Warcraft Logs public API v2.

    Use this for focused report.table() and report.events() queries covering
    damage, priority/boss/add damage, healing, casts, resources, buffs/debuffs,
    deaths, damage taken, interrupts, dispels, CC/stops, cooldowns, defensives,
    potions/healthstones, mechanics, timelines, dungeonPulls, M+ route/pacing,
    rankings, world data and other public schema fields. Filter aggressively by
    fightIDs/sourceID/targetID/abilityID/time and paginate events when needed.

    variables_json must be a JSON object.
    """
    try:
        variables = json.loads(variables_json)
        if not isinstance(variables, dict):
            raise ValueError
    except Exception:
        return _json({"error": "variables_json must be a JSON object."})

    return _json(_graphql(query, variables))


# ---------------------------------------------------------------------------
# High-level analysis tools
# No Warcraft Logs report/result cache is used. Every tool invocation fetches
# fresh report data. Only the short-lived OAuth access token is retained in
# process memory to authenticate API requests.
# ---------------------------------------------------------------------------

def _table_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    data = value.get("data")
    if not isinstance(data, dict):
        return []
    entries = data.get("entries")
    return [x for x in entries if isinstance(x, dict)] if isinstance(entries, list) else []


def _as_int_list(raw: str, field_name: str) -> list[int]:
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError
        return [int(x) for x in values]
    except Exception as exc:
        raise ValueError(f"{field_name} must be a JSON array of integers.") from exc


def _median(values: list[float]) -> float | None:
    clean = [float(x) for x in values if x is not None]
    return round(float(statistics.median(clean)), 2) if clean else None


def _cv_pct(values: list[float]) -> float | None:
    clean = [float(x) for x in values if x is not None]
    if len(clean) < 2:
        return None
    mean = statistics.fmean(clean)
    if mean == 0:
        return None
    return round(statistics.pstdev(clean) / mean * 100.0, 2)


def _event_ability_id(event: dict[str, Any]) -> int | None:
    for key in ("abilityGameID", "abilityID", "guid"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    ability = event.get("ability")
    if isinstance(ability, dict):
        for key in ("guid", "id", "gameID"):
            value = ability.get(key)
            if isinstance(value, (int, float)):
                return int(value)
    return None


def _compact_targets(entry: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    rows = entry.get("targets") or entry.get("sources") or []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "type": row.get("type"),
            "total": row.get("total"),
            "totalReduced": row.get("totalReduced"),
        })
    out.sort(key=lambda x: -(float(x.get("total") or 0)))
    return out[:limit]


def _compact_abilities(entry: dict[str, Any], limit: int = 15) -> list[dict[str, Any]]:
    rows = entry.get("abilities") or []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "id": row.get("id") or row.get("guid"),
            "guid": row.get("guid"),
            "name": row.get("name"),
            "type": row.get("type"),
            "total": row.get("total"),
            "uses": row.get("uses"),
            "uptime": row.get("uptime"),
        })
    out.sort(key=lambda x: -(float(x.get("total") or x.get("uses") or 0)))
    return out[:limit]


def _compact_death(entry: dict[str, Any], fight_start: float | None = None) -> dict[str, Any]:
    ts = entry.get("timestamp")
    seconds = None
    if isinstance(ts, (int, float)) and isinstance(fight_start, (int, float)):
        seconds = round((float(ts) - float(fight_start)) / 1000.0, 2)
    events = entry.get("events")
    recent_events = []
    if isinstance(events, list):
        for event in events[-6:]:
            if not isinstance(event, dict):
                continue
            recent_events.append({
                "timestamp": event.get("timestamp"),
                "type": event.get("type"),
                "sourceID": event.get("sourceID"),
                "targetID": event.get("targetID"),
                "abilityID": _event_ability_id(event),
                "ability": event.get("ability"),
                "amount": event.get("amount"),
                "overkill": event.get("overkill"),
                "absorbed": event.get("absorbed"),
                "mitigated": event.get("mitigated"),
            })
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "timestamp": ts,
        "secondsFromPull": seconds,
        "fight": entry.get("fight"),
        "killingBlow": entry.get("killingBlow"),
        "overkill": entry.get("overkill"),
        "deathWindow": entry.get("deathWindow"),
        "events": recent_events,
    }


def _fetch_fight_tables(code: str, fight_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Fetch per-fight damage/healing/deaths in compact batches."""
    out: dict[int, dict[str, Any]] = {}
    for offset in range(0, len(fight_ids), 18):
        chunk = fight_ids[offset: offset + 18]
        fields = []
        for fid in chunk:
            fields.extend([
                f'f{fid}d: table(dataType: DamageDone, fightIDs: [{fid}], hostilityType: Friendlies, translate: false, viewBy: Source)',
                f'f{fid}h: table(dataType: Healing, fightIDs: [{fid}], hostilityType: Friendlies, translate: false, viewBy: Source)',
                f'f{fid}x: table(dataType: Deaths, fightIDs: [{fid}], hostilityType: Friendlies, translate: false, viewBy: Source)',
            ])
        query = (
            "query PerFight($code:String!){reportData{report(code:$code){"
            + " ".join(fields)
            + "}}}"
        )
        payload = _graphql(query, {"code": code})
        report = (((payload.get("data") or {}).get("reportData") or {}).get("report") or {})
        for fid in chunk:
            out[fid] = {
                "damage": _table_entries(report.get(f"f{fid}d")),
                "healing": _table_entries(report.get(f"f{fid}h")),
                "deaths": _table_entries(report.get(f"f{fid}x")),
            }
    return out


def _build_raid_snapshot(code: str, encounter_id: int = 0) -> dict[str, Any]:
    meta_query = """
    query RaidMeta($code:String!) {
      reportData {
        report(code:$code) {
          code title startTime endTime revision segments visibility
          zone { id name }
          fights {
            id name startTime endTime kill difficulty encounterID
            fightPercentage bossPercentage averageItemLevel
            friendlyPlayers friendlySpecs friendlyItemLevels wipeCalledTime
          }
          masterData(translate:false) {
            actors { id name type subType }
          }
        }
      }
      rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
    }
    """
    meta = _graphql(meta_query, {"code": code})
    report = (((meta.get("data") or {}).get("reportData") or {}).get("report") or {})
    fights = [x for x in (report.get("fights") or []) if isinstance(x, dict)]
    if encounter_id:
        fights = [x for x in fights if int(x.get("encounterID") or 0) == int(encounter_id)]
    else:
        fights = [x for x in fights if int(x.get("encounterID") or 0) != 0]

    actors = ((report.get("masterData") or {}).get("actors") or [])
    actor_map = {
        int(x["id"]): x
        for x in actors
        if isinstance(x, dict) and isinstance(x.get("id"), (int, float))
    }
    fight_ids = [int(x["id"]) for x in fights if isinstance(x.get("id"), (int, float))]
    if not fight_ids:
        return {
            "report": {
                "code": code,
                "title": report.get("title"),
                "zone": report.get("zone"),
                "encounterID": encounter_id or None,
            },
            "fights": [],
            "players": [],
            "wipes": [],
            "warning": "No matching encounter fights were found.",
        }

    aggregate_query = """
    query RaidAggregate($code:String!, $fightIDs:[Int]) {
      reportData {
        report(code:$code) {
          damage: table(dataType: DamageDone, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          healing: table(dataType: Healing, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          taken: table(dataType: DamageTaken, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Target)
          casts: table(dataType: Casts, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          interrupts: table(dataType: Interrupts, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          dispels: table(dataType: Dispels, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
        }
      }
    }
    """
    aggregate = _graphql(aggregate_query, {"code": code, "fightIDs": fight_ids})
    ar = (((aggregate.get("data") or {}).get("reportData") or {}).get("report") or {})
    aggregate_rows = {
        key: _table_entries(ar.get(key))
        for key in ("damage", "healing", "taken", "casts", "interrupts", "dispels")
    }
    per_fight = _fetch_fight_tables(code, fight_ids)

    attendance: dict[int, int] = {}
    specs: dict[int, dict[str, int]] = {}
    ilvls: dict[int, list[float]] = {}
    for fight in fights:
        pids = fight.get("friendlyPlayers") or []
        pspecs = fight.get("friendlySpecs") or []
        pilvls = fight.get("friendlyItemLevels") or []
        for idx, pid_raw in enumerate(pids):
            try:
                pid = int(pid_raw)
            except Exception:
                continue
            attendance[pid] = attendance.get(pid, 0) + 1
            spec = pspecs[idx] if idx < len(pspecs) else "Unknown"
            specs.setdefault(pid, {})[str(spec)] = specs.setdefault(pid, {}).get(str(spec), 0) + 1
            if idx < len(pilvls) and pilvls[idx] is not None:
                ilvls.setdefault(pid, []).append(float(pilvls[idx]))

    def row_map(kind: str) -> dict[int, dict[str, Any]]:
        mapped = {}
        for row in aggregate_rows[kind]:
            rid = row.get("id")
            if isinstance(rid, (int, float)):
                mapped[int(rid)] = row
        return mapped

    damage_map = row_map("damage")
    healing_map = row_map("healing")
    taken_map = row_map("taken")
    casts_map = row_map("casts")
    interrupts_map = row_map("interrupts")
    dispels_map = row_map("dispels")

    dps_samples: dict[int, list[float]] = {pid: [] for pid in attendance}
    hps_samples: dict[int, list[float]] = {pid: [] for pid in attendance}
    deaths: dict[int, int] = {pid: 0 for pid in attendance}
    first_deaths: dict[int, int] = {pid: 0 for pid in attendance}
    wipe_rows = []

    fight_by_id = {int(f["id"]): f for f in fights if isinstance(f.get("id"), (int, float))}
    for fid in fight_ids:
        fight = fight_by_id[fid]
        duration_s = max(0.001, (float(fight.get("endTime") or 0) - float(fight.get("startTime") or 0)) / 1000.0)
        pdata = per_fight.get(fid, {})

        for row in pdata.get("damage", []):
            rid = row.get("id")
            if isinstance(rid, (int, float)) and int(rid) in attendance:
                dps_samples.setdefault(int(rid), []).append(float(row.get("total") or 0) / duration_s)

        for row in pdata.get("healing", []):
            rid = row.get("id")
            if isinstance(rid, (int, float)) and int(rid) in attendance:
                hps_samples.setdefault(int(rid), []).append(float(row.get("total") or 0) / duration_s)

        drows = [
            _compact_death(row, fight.get("startTime"))
            for row in pdata.get("deaths", [])
        ]
        drows.sort(key=lambda x: float(x.get("timestamp") or 1e30))
        for death in drows:
            rid = death.get("id")
            if isinstance(rid, (int, float)) and int(rid) in deaths:
                deaths[int(rid)] += 1
        if drows:
            first = drows[0]
            rid = first.get("id")
            if isinstance(rid, (int, float)) and int(rid) in first_deaths:
                first_deaths[int(rid)] += 1
        if not fight.get("kill"):
            wipe_rows.append({
                "fightID": fid,
                "durationSeconds": round(duration_s, 2),
                "fightPercentage": fight.get("fightPercentage"),
                "bossPercentage": fight.get("bossPercentage"),
                "wipeCalledTime": fight.get("wipeCalledTime"),
                "deathCount": len(drows),
                "firstDeath": drows[0] if drows else None,
                "deathChain": drows[:8],
            })

    players = []
    for pid, pulls in attendance.items():
        actor = actor_map.get(pid, {})
        if actor.get("type") != "Player":
            continue
        drow = damage_map.get(pid, {})
        hrow = healing_map.get(pid, {})
        trow = taken_map.get(pid, {})
        crow = casts_map.get(pid, {})
        irow = interrupts_map.get(pid, {})
        srow = dispels_map.get(pid, {})
        spec_counts = specs.get(pid, {})
        primary_spec = max(spec_counts, key=spec_counts.get) if spec_counts else None
        player = {
            "id": pid,
            "name": actor.get("name"),
            "class": actor.get("subType"),
            "spec": primary_spec,
            "pulls": pulls,
            "itemLevelMedian": _median(ilvls.get(pid, [])),
            "dpsMedian": _median(dps_samples.get(pid, [])),
            "dpsCVPct": _cv_pct(dps_samples.get(pid, [])),
            "hpsMedian": _median(hps_samples.get(pid, [])),
            "hpsCVPct": _cv_pct(hps_samples.get(pid, [])),
            "deaths": deaths.get(pid, 0),
            "firstDeaths": first_deaths.get(pid, 0),
            "damageTotal": drow.get("total"),
            "healingTotal": hrow.get("total"),
            "damageTaken": trow.get("total"),
            "interrupts": irow.get("total"),
            "dispels": srow.get("total"),
            "targets": _compact_targets(drow),
            "castsTop": _compact_abilities(crow),
            "dpsSeries": [round(x, 1) for x in dps_samples.get(pid, [])],
            "hpsSeries": [round(x, 1) for x in hps_samples.get(pid, [])],
        }
        players.append(player)

    players.sort(key=lambda p: (-(p.get("pulls") or 0), p.get("name") or ""))

    fight_rows = []
    for fight in fights:
        fight_rows.append({
            "id": fight.get("id"),
            "name": fight.get("name"),
            "kill": fight.get("kill"),
            "difficulty": fight.get("difficulty"),
            "encounterID": fight.get("encounterID"),
            "durationSeconds": round(
                (float(fight.get("endTime") or 0) - float(fight.get("startTime") or 0)) / 1000.0,
                2,
            ),
            "fightPercentage": fight.get("fightPercentage"),
            "bossPercentage": fight.get("bossPercentage"),
            "averageItemLevel": fight.get("averageItemLevel"),
        })

    return {
        "report": {
            "code": code,
            "title": report.get("title"),
            "zone": report.get("zone"),
            "revision": report.get("revision"),
            "segments": report.get("segments"),
            "encounterID": encounter_id or (fight_rows[0].get("encounterID") if fight_rows else None),
            "fightCount": len(fight_rows),
            "kills": sum(1 for x in fight_rows if x.get("kill")),
            "wipes": sum(1 for x in fight_rows if not x.get("kill")),
        },
        "fights": fight_rows,
        "players": players,
        "wipes": wipe_rows,
        "rateLimit": (meta.get("data") or {}).get("rateLimitData"),
        "fresh": True,
        "resultCacheUsed": False,
    }


@mcp.tool(
    title="Analyze raid session",
    annotations=EXTERNAL_READ_ONLY,
)
def analyze_raid_session(code: str, encounter_id: int = 0) -> str:
    """
    Build a fresh, structured raid-analysis dataset for one report and encounter.

    Returns pull-by-pull DPS/HPS series, attendance, median throughput, consistency
    (CV), deaths, first deaths, damage taken, interrupts, dispels, target damage,
    top casts, fight progress and wipe death chains. Prefer this over ad-hoc GraphQL
    for the first pass of a competitive raid analysis.

    encounter_id=0 analyzes all boss encounters in the report; for fair ranking,
    passing one encounter ID is strongly preferred.
    """
    try:
        return _json(_build_raid_snapshot(code, int(encounter_id or 0)))
    except Exception as exc:
        return _json({"error": repr(exc), "code": code, "encounterID": encounter_id})


@mcp.tool(
    title="Compare raid reports",
    annotations=EXTERNAL_READ_ONLY,
)
def compare_raid_reports(codes_json: str, encounter_id: int) -> str:
    """
    Compare the same raid encounter across multiple Warcraft Logs reports.

    Fetches every report fresh, merges players by normalized character name, and
    returns attendance, first deaths, total deaths, median DPS/HPS per report and
    combined pull-series consistency. Use this for progression across raid nights.
    """
    try:
        codes = json.loads(codes_json)
        if not isinstance(codes, list) or not codes:
            raise ValueError
        codes = [str(x).strip() for x in codes if str(x).strip()]
        if len(codes) > 6:
            return _json({"error": "Maximum 6 reports per comparison."})
    except Exception:
        return _json({"error": "codes_json must be a JSON array of report codes."})

    snapshots = [_build_raid_snapshot(code, int(encounter_id)) for code in codes]
    merged: dict[str, dict[str, Any]] = {}
    for snap in snapshots:
        report_code = (snap.get("report") or {}).get("code")
        for player in snap.get("players") or []:
            key = str(player.get("name") or player.get("id")).casefold()
            row = merged.setdefault(key, {
                "name": player.get("name"),
                "class": player.get("class"),
                "specs": {},
                "pullsTotal": 0,
                "deathsTotal": 0,
                "firstDeathsTotal": 0,
                "dpsSeriesCombined": [],
                "hpsSeriesCombined": [],
                "reports": [],
            })
            if player.get("spec"):
                row["specs"][player["spec"]] = row["specs"].get(player["spec"], 0) + int(player.get("pulls") or 0)
            row["pullsTotal"] += int(player.get("pulls") or 0)
            row["deathsTotal"] += int(player.get("deaths") or 0)
            row["firstDeathsTotal"] += int(player.get("firstDeaths") or 0)
            row["dpsSeriesCombined"].extend(player.get("dpsSeries") or [])
            row["hpsSeriesCombined"].extend(player.get("hpsSeries") or [])
            row["reports"].append({
                "code": report_code,
                "pulls": player.get("pulls"),
                "dpsMedian": player.get("dpsMedian"),
                "dpsCVPct": player.get("dpsCVPct"),
                "hpsMedian": player.get("hpsMedian"),
                "hpsCVPct": player.get("hpsCVPct"),
                "deaths": player.get("deaths"),
                "firstDeaths": player.get("firstDeaths"),
            })

    players = []
    for row in merged.values():
        row["dpsMedianCombined"] = _median(row.pop("dpsSeriesCombined"))
        row["hpsMedianCombined"] = _median(row.pop("hpsSeriesCombined"))
        # Rebuild combined CV from compact report series is intentionally omitted here;
        # per-report CVs remain directly reproducible and avoid fabricating a score.
        players.append(row)
    players.sort(key=lambda x: (-int(x.get("pullsTotal") or 0), x.get("name") or ""))

    return _json({
        "encounterID": encounter_id,
        "reports": [x.get("report") for x in snapshots],
        "players": players,
        "fresh": True,
        "resultCacheUsed": False,
    })


@mcp.tool(
    title="Analyze raid wipes",
    annotations=EXTERNAL_READ_ONLY,
)
def analyze_raid_wipes(code: str, encounter_id: int) -> str:
    """
    Return fresh wipe timelines for one raid encounter.

    Focuses on the first death, death chain, pull duration and boss/fight progress.
    Use it to derive Trigger -> Cascade -> Wipe without blaming the final death.
    """
    try:
        snap = _build_raid_snapshot(code, int(encounter_id))
        return _json({
            "report": snap.get("report"),
            "wipes": snap.get("wipes"),
            "fresh": True,
            "resultCacheUsed": False,
        })
    except Exception as exc:
        return _json({"error": repr(exc), "code": code, "encounterID": encounter_id})


@mcp.tool(
    title="Analyze one player",
    annotations=EXTERNAL_READ_ONLY,
)
def analyze_player(
    code: str,
    player_id: int,
    fight_ids_json: str = "[]",
) -> str:
    """
    Return a fresh focused profile for one player across selected fights.

    Includes ability-level casts, damage done, damage taken, debuffs, interrupts,
    dispels, healing and deaths. Use after the session-level tool when explaining
    exactly why a player ranks high/low or what to fix next.
    """
    try:
        fight_ids = _as_int_list(fight_ids_json, "fight_ids_json")
    except ValueError as exc:
        return _json({"error": str(exc)})

    query = """
    query PlayerAnalysis($code:String!, $fightIDs:[Int], $playerID:Int!) {
      reportData {
        report(code:$code) {
          masterData(translate:false) { actors { id name type subType } }
          damage: table(dataType: DamageDone, fightIDs:$fightIDs, sourceID:$playerID, translate:false, viewBy: Ability)
          healing: table(dataType: Healing, fightIDs:$fightIDs, sourceID:$playerID, translate:false, viewBy: Ability)
          casts: table(dataType: Casts, fightIDs:$fightIDs, sourceID:$playerID, translate:false, viewBy: Ability)
          taken: table(dataType: DamageTaken, fightIDs:$fightIDs, targetID:$playerID, translate:false, viewBy: Ability)
          debuffs: table(dataType: Debuffs, fightIDs:$fightIDs, targetID:$playerID, translate:false, viewBy: Ability)
          interrupts: table(dataType: Interrupts, fightIDs:$fightIDs, sourceID:$playerID, translate:false, viewBy: Ability)
          dispels: table(dataType: Dispels, fightIDs:$fightIDs, sourceID:$playerID, translate:false, viewBy: Ability)
          deaths: table(dataType: Deaths, fightIDs:$fightIDs, translate:false, viewBy: Source)
        }
      }
    }
    """
    try:
        payload = _graphql(query, {
            "code": code,
            "fightIDs": fight_ids or None,
            "playerID": int(player_id),
        })
        report = (((payload.get("data") or {}).get("reportData") or {}).get("report") or {})
        actor = None
        for row in ((report.get("masterData") or {}).get("actors") or []):
            if isinstance(row, dict) and int(row.get("id") or -1) == int(player_id):
                actor = row
                break

        result = {
            "code": code,
            "player": actor or {"id": player_id},
            "fightIDs": fight_ids,
            "damage": [_compact_death(x) if False else {
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"), "uses": x.get("uses"), "uptime": x.get("uptime"),
            } for x in _table_entries(report.get("damage"))[:80]],
            "healing": [{
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"), "uses": x.get("uses"), "uptime": x.get("uptime"),
                "overheal": x.get("overheal"),
            } for x in _table_entries(report.get("healing"))[:80]],
            "casts": [{
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"), "uses": x.get("uses"), "uptime": x.get("uptime"),
            } for x in _table_entries(report.get("casts"))[:100]],
            "damageTaken": [{
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"), "totalReduced": x.get("totalReduced"),
            } for x in _table_entries(report.get("taken"))[:100]],
            "debuffs": [{
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"), "uptime": x.get("uptime"),
            } for x in _table_entries(report.get("debuffs"))[:100]],
            "interrupts": [{
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"),
            } for x in _table_entries(report.get("interrupts"))[:80]],
            "dispels": [{
                "name": x.get("name"), "id": x.get("id"), "guid": x.get("guid"),
                "total": x.get("total"),
            } for x in _table_entries(report.get("dispels"))[:80]],
            "deaths": [
                _compact_death(x)
                for x in _table_entries(report.get("deaths"))
                if int(x.get("id") or -1) == int(player_id)
            ],
            "fresh": True,
            "resultCacheUsed": False,
        }
        return _json(result)
    except Exception as exc:
        return _json({"error": repr(exc), "code": code, "playerID": player_id})


def _fetch_cast_pages(code: str, fight_ids: list[int]) -> list[dict[str, Any]]:
    query = """
    query CastPage($code:String!, $fightIDs:[Int], $start:Float) {
      reportData {
        report(code:$code) {
          events(
            dataType:Casts
            fightIDs:$fightIDs
            hostilityType:Friendlies
            startTime:$start
            limit:10000
            translate:false
            useActorIDs:true
            useAbilityIDs:true
          ) { data nextPageTimestamp }
        }
      }
    }
    """
    events: list[dict[str, Any]] = []
    start = None
    for _ in range(50):
        payload = _graphql(query, {"code": code, "fightIDs": fight_ids, "start": start})
        paginator = (((payload.get("data") or {}).get("reportData") or {}).get("report") or {}).get("events") or {}
        page = paginator.get("data") or []
        if isinstance(page, list):
            events.extend(x for x in page if isinstance(x, dict))
        nxt = paginator.get("nextPageTimestamp")
        if not nxt or nxt == start:
            break
        start = nxt
    return events


@mcp.tool(
    title="Analyze cooldown timeline",
    annotations=EXTERNAL_READ_ONLY,
)
def analyze_cooldown_timeline(
    code: str,
    fight_ids_json: str,
    ability_ids_json: str,
    player_ids_json: str = "[]",
) -> str:
    """
    Return exact cast timestamps for selected cooldown abilities, paginating events
    automatically. Use this to judge alignment, missed windows and availability
    instead of relying on aggregate cast counts.

    Supply Warcraft Logs fight IDs and ability game IDs as JSON arrays. Optional
    player_ids_json filters the timeline to specific players.
    """
    try:
        fight_ids = _as_int_list(fight_ids_json, "fight_ids_json")
        ability_ids = set(_as_int_list(ability_ids_json, "ability_ids_json"))
        player_ids = set(_as_int_list(player_ids_json, "player_ids_json"))
        if not fight_ids:
            return _json({"error": "fight_ids_json cannot be empty."})
        if not ability_ids:
            return _json({"error": "ability_ids_json cannot be empty."})
    except ValueError as exc:
        return _json({"error": str(exc)})

    try:
        events = _fetch_cast_pages(code, fight_ids)
        filtered = []
        for event in events:
            aid = _event_ability_id(event)
            sid = event.get("sourceID")
            if aid not in ability_ids:
                continue
            if player_ids and (not isinstance(sid, (int, float)) or int(sid) not in player_ids):
                continue
            filtered.append({
                "timestamp": event.get("timestamp"),
                "fight": event.get("fight"),
                "sourceID": sid,
                "targetID": event.get("targetID"),
                "abilityID": aid,
                "type": event.get("type"),
            })
        return _json({
            "code": code,
            "fightIDs": fight_ids,
            "abilityIDs": sorted(ability_ids),
            "playerIDs": sorted(player_ids),
            "events": filtered,
            "fresh": True,
            "resultCacheUsed": False,
        })
    except Exception as exc:
        return _json({"error": repr(exc), "code": code})


@mcp.tool(
    title="Analyze Mythic Plus run",
    annotations=EXTERNAL_READ_ONLY,
)
def analyze_mplus_run(code: str, fight_id: int = 0) -> str:
    """
    Build a fresh, pull-by-pull Mythic+ dataset.

    Returns key level, affixes, official time, enemy forces, route pulls, enemy
    NPCs, pull durations/gaps, player damage, interrupts and death chains. Prefer
    this tool for M+ before issuing custom GraphQL.
    """
    meta_query = """
    query MPlusMeta($code:String!) {
      reportData {
        report(code:$code) {
          code title startTime endTime
          zone { id name }
          masterData(translate:false) { actors { id name type subType } }
          fights {
            id name startTime endTime kill difficulty encounterID
            keystoneLevel keystoneBonus keystoneTime keystoneAffixes rating
            countReached countRequired
            friendlyPlayers friendlySpecs friendlyItemLevels
            dungeonPulls {
              id name startTime endTime kill encounterID x y
              enemyNPCs {
                id gameID minimumInstanceID maximumInstanceID
                minimumInstanceGroupID maximumInstanceGroupID
              }
            }
          }
        }
      }
    }
    """
    try:
        meta = _graphql(meta_query, {"code": code})
        report = (((meta.get("data") or {}).get("reportData") or {}).get("report") or {})
        candidates = [
            x for x in (report.get("fights") or [])
            if isinstance(x, dict) and x.get("keystoneLevel") is not None
        ]
        if fight_id:
            candidates = [x for x in candidates if int(x.get("id") or -1) == int(fight_id)]
        if not candidates:
            return _json({"error": "No matching Mythic+ run found.", "code": code, "fightID": fight_id or None})
        run = candidates[-1]
        pulls = [x for x in (run.get("dungeonPulls") or []) if isinstance(x, dict)]
        pull_ids = [int(x["id"]) for x in pulls if isinstance(x.get("id"), (int, float))]

        actors = ((report.get("masterData") or {}).get("actors") or [])
        actor_map = {
            int(x["id"]): x for x in actors
            if isinstance(x, dict) and isinstance(x.get("id"), (int, float))
        }

        pull_tables: dict[int, dict[str, Any]] = {}
        for offset in range(0, len(pull_ids), 16):
            chunk = pull_ids[offset: offset + 16]
            fields = []
            for pid in chunk:
                fields.extend([
                    f'p{pid}d: table(dataType: DamageDone, fightIDs: [{pid}], hostilityType: Friendlies, translate:false, viewBy: Source)',
                    f'p{pid}x: table(dataType: Deaths, fightIDs: [{pid}], hostilityType: Friendlies, translate:false, viewBy: Source)',
                    f'p{pid}i: table(dataType: Interrupts, fightIDs: [{pid}], hostilityType: Friendlies, translate:false, viewBy: Source)',
                ])
            query = "query MPPulls($code:String!){reportData{report(code:$code){" + " ".join(fields) + "}}}"
            payload = _graphql(query, {"code": code})
            rr = (((payload.get("data") or {}).get("reportData") or {}).get("report") or {})
            for pid in chunk:
                pull_tables[pid] = {
                    "damage": _table_entries(rr.get(f"p{pid}d")),
                    "deaths": _table_entries(rr.get(f"p{pid}x")),
                    "interrupts": _table_entries(rr.get(f"p{pid}i")),
                }

        result_pulls = []
        previous_end = None
        for index, pull in enumerate(pulls, start=1):
            pid = int(pull.get("id"))
            pdata = pull_tables.get(pid, {})
            duration = max(0.0, (float(pull.get("endTime") or 0) - float(pull.get("startTime") or 0)) / 1000.0)
            gap = None
            if previous_end is not None:
                gap = max(0.0, (float(pull.get("startTime") or 0) - float(previous_end)) / 1000.0)
            previous_end = pull.get("endTime")

            damage_rows = []
            for row in pdata.get("damage", []):
                rid = row.get("id")
                actor = actor_map.get(int(rid)) if isinstance(rid, (int, float)) else None
                if actor and actor.get("type") == "Player":
                    damage_rows.append({
                        "id": int(rid),
                        "name": actor.get("name"),
                        "class": actor.get("subType"),
                        "damage": row.get("total"),
                        "dps": round(float(row.get("total") or 0) / max(duration, 0.001), 1),
                    })
            damage_rows.sort(key=lambda x: -(float(x.get("damage") or 0)))

            deaths_rows = [_compact_death(x, pull.get("startTime")) for x in pdata.get("deaths", [])]
            deaths_rows.sort(key=lambda x: float(x.get("timestamp") or 1e30))

            interrupt_total = 0
            interrupts_by_player = []
            for row in pdata.get("interrupts", []):
                rid = row.get("id")
                actor = actor_map.get(int(rid)) if isinstance(rid, (int, float)) else None
                if actor and actor.get("type") == "Player":
                    count = int(row.get("total") or 0)
                    interrupt_total += count
                    interrupts_by_player.append({
                        "id": int(rid),
                        "name": actor.get("name"),
                        "count": count,
                    })

            result_pulls.append({
                "index": index,
                "id": pid,
                "name": pull.get("name"),
                "encounterID": pull.get("encounterID"),
                "kill": pull.get("kill"),
                "durationSeconds": round(duration, 2),
                "gapBeforeSeconds": round(gap, 2) if gap is not None else None,
                "x": pull.get("x"),
                "y": pull.get("y"),
                "enemyNPCs": pull.get("enemyNPCs") or [],
                "playerDamage": damage_rows,
                "deathCount": len(deaths_rows),
                "firstDeath": deaths_rows[0] if deaths_rows else None,
                "deathChain": deaths_rows,
                "interruptsTotal": interrupt_total,
                "interruptsByPlayer": interrupts_by_player,
            })

        pids = run.get("friendlyPlayers") or []
        specs = run.get("friendlySpecs") or []
        ilvls = run.get("friendlyItemLevels") or []
        roster = []
        for idx, pid_raw in enumerate(pids):
            pid = int(pid_raw)
            actor = actor_map.get(pid, {})
            roster.append({
                "id": pid,
                "name": actor.get("name"),
                "class": actor.get("subType"),
                "spec": specs[idx] if idx < len(specs) else None,
                "itemLevel": ilvls[idx] if idx < len(ilvls) else None,
            })

        return _json({
            "report": {"code": code, "title": report.get("title"), "zone": report.get("zone")},
            "run": {
                "fightID": run.get("id"),
                "name": run.get("name"),
                "keystoneLevel": run.get("keystoneLevel"),
                "affixes": run.get("keystoneAffixes"),
                "officialTimeMs": run.get("keystoneTime"),
                "bonus": run.get("keystoneBonus"),
                "rating": run.get("rating"),
                "countReached": run.get("countReached"),
                "countRequired": run.get("countRequired"),
                "durationSeconds": round(
                    (float(run.get("endTime") or 0) - float(run.get("startTime") or 0)) / 1000.0,
                    2,
                ),
            },
            "roster": roster,
            "pulls": result_pulls,
            "fresh": True,
            "resultCacheUsed": False,
        })
    except Exception as exc:
        return _json({"error": repr(exc), "code": code, "fightID": fight_id or None})


if __name__ == "__main__":
    # Fail fast if the configured Warcraft Logs credentials cannot authenticate.
    _graphql("""
    query StartupAuthCheck {
      rateLimitData {
        limitPerHour
        pointsSpentThisHour
        pointsResetIn
      }
    }
    """)
    print("Warcraft Logs API authentication OK", flush=True)

    port = int(os.getenv("PORT", "10000"))
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )
