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
    encounter_id = 3445
    mechanic_ids = {
        "Living Venom": 1284209,
        "Toxic Droplets": 1284451,
        "Noxious Blast": 1284452,
        "Protovenom Eruption": 1296962,
        "Shifting Protovenom": 1296882,
        "Cultivated Burst": 1284948,
        "Blood Venom": 1284210,
        "Clinging Murk": 1303097,
        "Unstable Miasma": 1288282,
        "Helical Toxins": 1284813,
        "Contaminate": 1284258,
        "Blighted Blood": 1284471,
    }

    meta_query = """
    query EventProbeMeta($code: String!) {
      reportData {
        report(code: $code) {
          fights(encounterID: 3445) { id startTime endTime kill friendlyPlayers friendlySpecs }
          masterData(translate: false) {
            actors { id name type subType }
            abilities { id name type }
          }
        }
      }
    }
    """
    casts_query = """
    query CastPage($code: String!, $fightIDs: [Int], $start: Float) {
      reportData {
        report(code: $code) {
          events(
            dataType: Casts
            fightIDs: $fightIDs
            hostilityType: Friendlies
            startTime: $start
            limit: 10000
            translate: false
            useActorIDs: true
            useAbilityIDs: true
          ) { data nextPageTimestamp }
        }
      }
    }
    """
    dispel_query = """
    query DispelPage($code: String!, $fightIDs: [Int], $start: Float) {
      reportData {
        report(code: $code) {
          events(
            dataType: Dispels
            fightIDs: $fightIDs
            hostilityType: Friendlies
            abilityID: 1284471
            startTime: $start
            limit: 10000
            translate: false
            useActorIDs: true
            useAbilityIDs: true
          ) { data nextPageTimestamp }
        }
      }
    }
    """

    def event_ability_id(e: dict[str, Any]) -> int | None:
        for k in ("abilityGameID", "abilityID", "guid"):
            v = e.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        ability = e.get("ability")
        if isinstance(ability, dict):
            for k in ("guid", "id", "gameID"):
                v = ability.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
        return None

    def fetch_pages(query: str, code: str, fight_ids: list[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start_time: float | None = None
        for _ in range(30):
            result = _graphql(query, {"code": code, "fightIDs": fight_ids, "start": start_time})
            paginator = (((result.get("data") or {}).get("reportData") or {}).get("report") or {}).get("events") or {}
            data = paginator.get("data") or []
            if isinstance(data, list):
                out.extend(x for x in data if isinstance(x, dict))
            nxt = paginator.get("nextPageTimestamp")
            if not nxt or nxt == start_time:
                break
            start_time = nxt
        return out

    for code in codes:
        try:
            meta = _graphql(meta_query, {"code": code})
            rep = (((meta.get("data") or {}).get("reportData") or {}).get("report") or {})
            fights = rep.get("fights") or []
            fight_ids = [f.get("id") for f in fights if f.get("id") is not None]
            actors = (rep.get("masterData") or {}).get("actors") or []
            abilities = (rep.get("masterData") or {}).get("abilities") or []
            actor_map = {int(a.get("id")): a for a in actors if isinstance(a, dict) and isinstance(a.get("id"), (int, float))}
            ability_map = {int(a.get("id")): a for a in abilities if isinstance(a, dict) and isinstance(a.get("id"), (int, float))}
            attendance: dict[int, int] = {}
            for f in fights:
                for pid in f.get("friendlyPlayers") or []:
                    attendance[int(pid)] = attendance.get(int(pid), 0) + 1

            cast_events = fetch_pages(casts_query, code, fight_ids)
            by_player: dict[int, dict[int, dict[str, Any]]] = {}
            samples: list[dict[str, Any]] = []
            for e in cast_events:
                sid = e.get("sourceID")
                aid = event_ability_id(e)
                if sid is None or aid is None:
                    if len(samples) < 5:
                        samples.append(e)
                    continue
                sid, aid = int(sid), int(aid)
                fight = e.get("fight")
                slot = by_player.setdefault(sid, {}).setdefault(aid, {"total": 0, "fights": {}})
                slot["total"] += 1
                if fight is not None:
                    fs = slot["fights"]
                    fs[str(int(fight))] = fs.get(str(int(fight)), 0) + 1

            for sid, spells in by_player.items():
                actor = actor_map.get(sid, {})
                pulls = attendance.get(sid, 0)
                if actor.get("type") != "Player":
                    continue
                items = []
                for aid, info in spells.items():
                    ab = ability_map.get(aid, {})
                    items.append({
                        "id": aid,
                        "name": ab.get("name") or str(aid),
                        "total": info["total"],
                        "fights": info["fights"],
                    })
                items.sort(key=lambda x: (-x["total"], x["name"]))
                interesting = [
                    x for x in items
                    if x["total"] <= max(4, pulls * 3) and x["total"] >= max(1, int(pulls * 0.10))
                ]
                print("WCLCAST_PLAYER " + _json({
                    "code": code, "id": sid, "name": actor.get("name"), "class": actor.get("subType"),
                    "pulls": pulls, "eventCount": sum(x["total"] for x in items),
                    "interesting": interesting, "allAbilityCount": len(items),
                }), flush=True)

            if samples:
                print("WCLCAST_SAMPLE " + _json({"code": code, "samples": samples}), flush=True)

            mechanic_aliases = []
            for i, (name, aid) in enumerate(mechanic_ids.items()):
                mechanic_aliases.append(
                    f'm{i}: events(dataType: DamageTaken, fightIDs: $fightIDs, hostilityType: Friendlies, '
                    f'abilityID: {aid}, limit: 10000, translate: false, useActorIDs: true, useAbilityIDs: true) '
                    '{ data nextPageTimestamp }'
                )
            mech_query = "query Mechanics($code:String!,$fightIDs:[Int]){reportData{report(code:$code){" + " ".join(mechanic_aliases) + "}}}"
            mech_result = _graphql(mech_query, {"code": code, "fightIDs": fight_ids})
            mr = (((mech_result.get("data") or {}).get("reportData") or {}).get("report") or {})
            for i, (name, aid) in enumerate(mechanic_ids.items()):
                paginator = mr.get(f"m{i}") or {}
                events = paginator.get("data") or []
                targets: dict[int, dict[str, Any]] = {}
                for e in events:
                    if not isinstance(e, dict):
                        continue
                    tid = e.get("targetID")
                    if tid is None:
                        continue
                    tid = int(tid)
                    t = targets.setdefault(tid, {"hits": 0, "amount": 0, "fights": {}})
                    t["hits"] += 1
                    t["amount"] += int(e.get("amount") or 0) + int(e.get("absorbed") or 0)
                    fight = e.get("fight")
                    if fight is not None:
                        fs = t["fights"]
                        fs[str(int(fight))] = fs.get(str(int(fight)), 0) + 1
                print("WCLMECH " + _json({
                    "code": code, "mechanic": name, "abilityID": aid,
                    "nextPageTimestamp": paginator.get("nextPageTimestamp"),
                    "targets": [
                        {"id": tid, "name": actor_map.get(tid, {}).get("name"), **vals}
                        for tid, vals in targets.items()
                        if actor_map.get(tid, {}).get("type") == "Player"
                    ],
                }), flush=True)

            dispels = fetch_pages(dispel_query, code, fight_ids)
            dispelers: dict[int, int] = {}
            for e in dispels:
                sid = e.get("sourceID")
                if sid is not None:
                    dispelers[int(sid)] = dispelers.get(int(sid), 0) + 1
            print("WCLDISPEL_PLAYERS " + _json({
                "code": code,
                "players": [
                    {"id": sid, "name": actor_map.get(sid, {}).get("name"), "count": count}
                    for sid, count in sorted(dispelers.items(), key=lambda x: -x[1])
                ],
            }), flush=True)

        except Exception as exc:
            print("WCLEVENT_ERROR " + _json({"code": code, "error": repr(exc)}), flush=True)


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
