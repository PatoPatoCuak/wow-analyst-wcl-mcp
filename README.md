# WoW Analyst — Warcraft Logs MCP

Public Warcraft Logs API v2 bridge for WoW Analyst.

## MCP endpoint

After deployment, connect ChatGPT to:

```
https://<render-service>.onrender.com/mcp
```

## Required Render environment variables

- `WCL_CLIENT_ID`
- `WCL_CLIENT_SECRET`

The Warcraft Logs secret must only be stored in Render's environment settings.
Do not commit it to this repository.

## Analysis scope

The server exposes helpers for report parsing, fight/session metadata, players,
rankings and rate limits, plus a generic Warcraft Logs GraphQL tool. The generic
tool is intentional: it allows WoW Analyst to run focused `table()` and
`events()` queries for raid and Mythic+ analysis without redeploying the MCP
whenever a new metric or encounter mechanic is needed.
