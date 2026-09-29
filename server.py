import json
import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

WCL_CLIENT_ID = os.getenv("WCL_CLIENT_ID", "")
WCL_CLIENT_SECRET = os.getenv("WCL_CLIENT_SECRET", "")
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_GRAPHQL_URL = "https://www.warcraftlogs.com/api/v2/client"

mcp = MCPServer("WoW Analyst - Warcraft Logs")

_token: str | None = None
_token_expires_at = 0.0


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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


@mcp.tool()
def parse_report_url(url: str) -> str:
    """Extract report code and optional fight from a Warcraft Logs report URL."""
    parsed = urlparse(url.strip())
    match = re.search(r"/reports/([A-Za-z0-9]+)", parsed.path)
    if not match:
        return _json({"error": "No Warcraft Logs report code found in URL."})

    fight = parse_qs(parsed.query).get("fight", [None])[0]
    return _json({"code": match.group(1), "fight": fight})


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def warcraftlogs_graphql(query: str, variables_json: str = "{}") -> str:
    """
    Run an arbitrary GraphQL query against Warcraft Logs public API v2.

    This is the main high-ELO analysis tool. Use it for report.table() and
    report.events() queries covering damage, priority/boss/add damage, healing,
    casts, resources, buffs/debuffs, deaths, damage taken, interrupts, dispels,
    CC/stops, cooldowns, defensives, potions/healthstones, mechanics, timelines,
    dungeonPulls, M+ route/pacing, rankings, world data and other public schema
    fields. Filter aggressively by fightIDs/sourceID/targetID/abilityID/time and
    paginate events when needed.

    variables_json must be a JSON object.
    """
    try:
        variables = json.loads(variables_json)
        if not isinstance(variables, dict):
            raise ValueError
    except Exception:
        return _json({"error": "variables_json must be a JSON object."})

    return _json(_graphql(query, variables))

def _run_analysis_probe() -> None:
    codes = ["Cq2Lm6ZJQ8pFKY91", "McHnAzfxQPKqg9j8"]
    meta_query = """
    query PlayerProbeMeta($code: String!) {
      reportData {
        report(code: $code) {
          fights(encounterID: 3445) { id friendlyPlayers friendlySpecs }
          masterData(translate: false) { actors { id name type subType } }
        }
      }
    }
    """
    player_query = """
    query PlayerTables($code: String!, $fightIDs: [Int], $playerID: Int!) {
      reportData {
        report(code: $code) {
          casts: table(dataType: Casts, fightIDs: $fightIDs, sourceID: $playerID, translate: false, viewBy: Ability)
          taken: table(dataType: DamageTaken, fightIDs: $fightIDs, targetID: $playerID, translate: false, viewBy: Ability)
          debuffs: table(dataType: Debuffs, fightIDs: $fightIDs, targetID: $playerID, translate: false, viewBy: Ability)
        }
      }
    }
    """

    def entries(v: Any) -> list[dict[str, Any]]:
        if not isinstance(v, dict):
            return []
        d = v.get("data")
        if not isinstance(d, dict):
            return []
        e = d.get("entries")
        return e if isinstance(e, list) else []

    def simplify(e: dict[str, Any]) -> dict[str, Any]:
        out = {
            "name": e.get("name"), "id": e.get("id"), "guid": e.get("guid"),
            "type": e.get("type"), "total": e.get("total"),
            "totalReduced": e.get("totalReduced"), "activeTime": e.get("activeTime"),
            "uptime": e.get("uptime"), "uses": e.get("uses"),
        }
        for key in ("actors", "sources", "targets", "details"):
            vals = e.get(key)
            if isinstance(vals, list):
                out[key] = [
                    {"name": x.get("name"), "id": x.get("id"), "total": x.get("total"), "type": x.get("type")}
                    for x in vals if isinstance(x, dict)
                ]
        return out

    for code in codes:
        try:
            meta = _graphql(meta_query, {"code": code})
            rep = (((meta.get("data") or {}).get("reportData") or {}).get("report") or {})
            fights = rep.get("fights") or []
            fight_ids = [int(f.get("id")) for f in fights if f.get("id") is not None]
            actors = (rep.get("masterData") or {}).get("actors") or []
            actor_map = {int(a.get("id")): a for a in actors if isinstance(a, dict) and isinstance(a.get("id"), (int, float))}
            attendance: dict[int, int] = {}
            specs: dict[int, dict[str, int]] = {}
            for f in fights:
                pids = f.get("friendlyPlayers") or []
                ss = f.get("friendlySpecs") or []
                for i, pid in enumerate(pids):
                    pid = int(pid)
                    attendance[pid] = attendance.get(pid, 0) + 1
                    sp = ss[i] if i < len(ss) else "Unknown"
                    specs.setdefault(pid, {})[sp] = specs.setdefault(pid, {}).get(sp, 0) + 1

            for pid, pulls in sorted(attendance.items(), key=lambda x: actor_map.get(x[0], {}).get("name") or ""):
                actor = actor_map.get(pid, {})
                if actor.get("type") != "Player":
                    continue
                result = _graphql(player_query, {"code": code, "fightIDs": fight_ids, "playerID": pid})
                rr = (((result.get("data") or {}).get("reportData") or {}).get("report") or {})
                print("WCLPLAYER_TABLES " + _json({
                    "code": code, "id": pid, "name": actor.get("name"), "class": actor.get("subType"),
                    "pulls": pulls, "specs": specs.get(pid, {}),
                    "casts": [simplify(x) for x in entries(rr.get("casts"))],
                    "taken": [simplify(x) for x in entries(rr.get("taken"))],
                    "debuffs": [simplify(x) for x in entries(rr.get("debuffs"))],
                }), flush=True)
        except Exception as exc:
            print("WCLPLAYER_ERROR " + _json({"code": code, "error": repr(exc)}), flush=True)


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
    _run_analysis_probe()

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
