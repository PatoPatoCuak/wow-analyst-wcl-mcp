import json
import os
import re
import time
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
