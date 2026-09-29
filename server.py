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

    summary_query = """
    query AnalysisSummary($code: String!) {
      reportData {
        report(code: $code) {
          code title startTime endTime
          fights(encounterID: 3445) {
            id name startTime endTime kill difficulty encounterID
            fightPercentage bossPercentage averageItemLevel
            friendlyPlayers friendlySpecs friendlyItemLevels
          }
          masterData(translate: false) {
            actors { id name type subType }
          }
        }
      }
      rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
    }
    """

    aggregate_query = """
    query AnalysisAggregate($code: String!, $fightIDs: [Int]) {
      reportData {
        report(code: $code) {
          damage: table(dataType: DamageDone, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          healing: table(dataType: Healing, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          casts: table(dataType: Casts, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          deaths: table(dataType: Deaths, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          taken: table(dataType: DamageTaken, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Target)
          debuffs: table(dataType: Debuffs, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Target)
          interrupts: table(dataType: Interrupts, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          dispels: table(dataType: Dispels, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          wdps: rankings(fightIDs: $fightIDs, playerMetric: wdps)
          dpsRank: rankings(fightIDs: $fightIDs, playerMetric: dps)
        }
      }
    }
    """

    fight_query = """
    query AnalysisFight($code: String!, $fightIDs: [Int]) {
      reportData {
        report(code: $code) {
          damage: table(dataType: DamageDone, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          healing: table(dataType: Healing, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
          deaths: table(dataType: Deaths, fightIDs: $fightIDs, hostilityType: Friendlies, translate: false, viewBy: Source)
        }
      }
    }
    """

    def table_entries(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        data = value.get("data")
        if not isinstance(data, dict):
            return []
        entries = data.get("entries")
        return entries if isinstance(entries, list) else []

    def simple_targets(entry: dict[str, Any]) -> list[dict[str, Any]]:
        values = entry.get("targets") or entry.get("sources") or []
        if not isinstance(values, list):
            return []
        return [
            {
                "name": x.get("name"),
                "total": x.get("total"),
                "totalReduced": x.get("totalReduced"),
                "type": x.get("type"),
            }
            for x in values
            if isinstance(x, dict)
        ]

    def simple_abilities(entry: dict[str, Any]) -> list[dict[str, Any]]:
        values = entry.get("abilities") or []
        if not isinstance(values, list):
            return []
        return [
            {
                "name": x.get("name"),
                "guid": x.get("guid"),
                "total": x.get("total"),
                "totalReduced": x.get("totalReduced"),
                "type": x.get("type"),
            }
            for x in values
            if isinstance(x, dict)
        ]

    def simple_death(entry: dict[str, Any]) -> dict[str, Any]:
        damage = entry.get("damage") if isinstance(entry.get("damage"), dict) else {}
        healing = entry.get("healing") if isinstance(entry.get("healing"), dict) else {}
        events = entry.get("events") if isinstance(entry.get("events"), list) else []
        return {
            "name": entry.get("name"),
            "id": entry.get("id"),
            "timestamp": entry.get("timestamp"),
            "fight": entry.get("fight"),
            "deathWindow": entry.get("deathWindow"),
            "overkill": entry.get("overkill"),
            "killingBlow": entry.get("killingBlow"),
            "damageTotal": damage.get("total"),
            "damageSources": simple_targets(damage),
            "damageAbilities": simple_abilities(damage),
            "healingTotal": healing.get("total"),
            "events": [
                {
                    "timestamp": e.get("timestamp"),
                    "type": e.get("type"),
                    "sourceID": e.get("sourceID"),
                    "targetID": e.get("targetID"),
                    "ability": e.get("ability"),
                    "amount": e.get("amount"),
                    "overkill": e.get("overkill"),
                    "absorbed": e.get("absorbed"),
                    "mitigated": e.get("mitigated"),
                    "unmitigatedAmount": e.get("unmitigatedAmount"),
                }
                for e in events[-5:]
                if isinstance(e, dict)
            ],
        }

    def compact_nested(value: Any, depth: int = 0) -> Any:
        if depth > 6:
            return None
        if isinstance(value, list):
            return [compact_nested(x, depth + 1) for x in value]
        if isinstance(value, dict):
            keep = {}
            for k, v in value.items():
                if k in {"gear", "talents"}:
                    continue
                keep[k] = compact_nested(v, depth + 1)
            return keep
        return value

    for code in codes:
        try:
            summary = _graphql(summary_query, {"code": code})
            rep = (((summary.get("data") or {}).get("reportData") or {}).get("report") or {})
            actors = rep.get("masterData", {}).get("actors", []) if isinstance(rep.get("masterData"), dict) else []
            actor_map = {a.get("id"): a for a in actors if isinstance(a, dict)}
            fights = rep.get("fights") or []
            fight_ids = [f.get("id") for f in fights if f.get("id") is not None]

            print("WCLDATA_META " + _json({
                "code": code,
                "title": rep.get("title"),
                "rateLimit": (summary.get("data") or {}).get("rateLimitData"),
                "actors": [
                    {"id": a.get("id"), "name": a.get("name"), "type": a.get("type"), "subType": a.get("subType")}
                    for a in actors
                    if a.get("type") == "Player"
                ],
                "fights": fights,
            }), flush=True)

            aggregate = _graphql(aggregate_query, {"code": code, "fightIDs": fight_ids})
            ar = (((aggregate.get("data") or {}).get("reportData") or {}).get("report") or {})

            for e in table_entries(ar.get("damage")):
                print("WCLDATA_AGG_DAMAGE " + _json({
                    "code": code, "name": e.get("name"), "id": e.get("id"), "type": e.get("type"),
                    "icon": e.get("icon"), "itemLevel": e.get("itemLevel"),
                    "total": e.get("total"), "activeTime": e.get("activeTime"),
                    "targets": simple_targets(e), "abilities": simple_abilities(e),
                }), flush=True)

            for e in table_entries(ar.get("healing")):
                print("WCLDATA_AGG_HEAL " + _json({
                    "code": code, "name": e.get("name"), "id": e.get("id"), "type": e.get("type"),
                    "icon": e.get("icon"), "itemLevel": e.get("itemLevel"),
                    "total": e.get("total"), "activeTime": e.get("activeTime"), "overheal": e.get("overheal"),
                    "targets": simple_targets(e), "abilities": simple_abilities(e),
                }), flush=True)

            for e in table_entries(ar.get("casts")):
                print("WCLDATA_AGG_CASTS " + _json({
                    "code": code, "name": e.get("name"), "id": e.get("id"), "type": e.get("type"),
                    "icon": e.get("icon"), "itemLevel": e.get("itemLevel"),
                    "total": e.get("total"), "activeTime": e.get("activeTime"),
                    "abilities": simple_abilities(e),
                }), flush=True)

            for e in table_entries(ar.get("taken")):
                print("WCLDATA_AGG_TAKEN " + _json({
                    "code": code, "name": e.get("name"), "id": e.get("id"), "type": e.get("type"),
                    "icon": e.get("icon"), "itemLevel": e.get("itemLevel"),
                    "total": e.get("total"), "totalReduced": e.get("totalReduced"),
                    "activeTime": e.get("activeTime"),
                    "sources": simple_targets(e), "abilities": simple_abilities(e),
                }), flush=True)

            for e in table_entries(ar.get("deaths")):
                print("WCLDATA_AGG_DEATH " + _json({"code": code, **simple_death(e)}), flush=True)

            print("WCLDATA_DEBUFFS " + _json({"code": code, "data": compact_nested(ar.get("debuffs"))}), flush=True)
            print("WCLDATA_INTERRUPTS " + _json({"code": code, "data": compact_nested(ar.get("interrupts"))}), flush=True)
            print("WCLDATA_DISPELS " + _json({"code": code, "data": compact_nested(ar.get("dispels"))}), flush=True)
            print("WCLDATA_WDPS " + _json({"code": code, "data": ar.get("wdps")}), flush=True)
            print("WCLDATA_DPSRANK " + _json({"code": code, "data": ar.get("dpsRank")}), flush=True)

            for f in fights:
                fid = f.get("id")
                if fid is None:
                    continue
                fq = _graphql(fight_query, {"code": code, "fightIDs": [fid]})
                fr = (((fq.get("data") or {}).get("reportData") or {}).get("report") or {})
                damage_rows = []
                for e in table_entries(fr.get("damage")):
                    damage_rows.append({
                        "name": e.get("name"), "id": e.get("id"), "type": e.get("type"),
                        "icon": e.get("icon"), "itemLevel": e.get("itemLevel"),
                        "total": e.get("total"), "activeTime": e.get("activeTime"),
                        "targets": simple_targets(e),
                    })
                healing_rows = []
                for e in table_entries(fr.get("healing")):
                    healing_rows.append({
                        "name": e.get("name"), "id": e.get("id"), "type": e.get("type"),
                        "icon": e.get("icon"), "itemLevel": e.get("itemLevel"),
                        "total": e.get("total"), "activeTime": e.get("activeTime"), "overheal": e.get("overheal"),
                    })
                death_rows = [simple_death(e) for e in table_entries(fr.get("deaths"))]
                roster = []
                pids = f.get("friendlyPlayers") or []
                specs = f.get("friendlySpecs") or []
                ilvls = f.get("friendlyItemLevels") or []
                for i, pid in enumerate(pids):
                    a = actor_map.get(pid, {})
                    roster.append({
                        "id": pid, "name": a.get("name"), "class": a.get("subType"),
                        "spec": specs[i] if i < len(specs) else None,
                        "itemLevel": ilvls[i] if i < len(ilvls) else None,
                    })
                print("WCLDATA_FIGHT " + _json({
                    "code": code, "fight": {
                        "id": fid, "startTime": f.get("startTime"), "endTime": f.get("endTime"),
                        "duration": (f.get("endTime") or 0) - (f.get("startTime") or 0),
                        "kill": f.get("kill"), "fightPercentage": f.get("fightPercentage"),
                        "bossPercentage": f.get("bossPercentage"), "averageItemLevel": f.get("averageItemLevel"),
                    },
                    "roster": roster, "damage": damage_rows, "healing": healing_rows, "deaths": death_rows,
                }), flush=True)

        except Exception as exc:
            print("WCLDATA_ERROR " + _json({"code": code, "error": repr(exc)}), flush=True)


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
