# NJ Reparations Council Report — MCP Server

An interactive, AI-accessible version of **"For Such a Time as This: The Nowness of Reparations for Black People in New Jersey"** — a report from the New Jersey Reparations Council, convened by the [New Jersey Institute for Social Justice](https://www.njisj.org) in partnership with the [Robert Wood Johnson Foundation](https://www.rwjf.org).

This project takes the 200+ page report and makes it queryable through the **Model Context Protocol (MCP)** — an open standard that lets AI assistants like Claude, ChatGPT, and Gemini access structured data in real time.

## What It Does

Instead of reading the full report, users can ask questions in plain language and get precise, sourced answers drawn directly from the Council's findings and recommendations. Every response includes attribution.

### Available Tools (15+)

| Tool | Purpose |
|------|---------|
| `report.get_policy_recommendations()` | Get all 100+ policy recommendations by area |
| `report.get_key_statistics()` | Racial disparity data (wealth, health, incarceration, etc.) |
| `report.get_wealth_gap()` | Detailed racial wealth gap analysis and closure calculations |
| `report.get_spotlights()` | Historical spotlight stories (Lockey White, Colonel Tye, Timbuctoo, etc.) |
| `report.get_reparations_examples()` | Successful reparations programs worldwide |
| `report.get_council_info()` | Council co-chairs, committees, and members |
| `report.list_sections()` | List all 10 major sections |
| `report.list_topics(section_id)` | List topics within a section |
| `report.get_topic(topic_id)` | Get full content for any topic |
| `report.search(query)` | Full-text search across the entire report |
| `report.get_version_info()` | Document metadata |
| `report.get_usage_guide()` | Navigation guide |
| `report.log_activity()` | Usage logging for analytics |

### Policy Recommendation Areas

The Blueprint for Repair includes recommendations across 11 areas:
- **Democracy** (18 recommendations)
- **Economic Justice** (10 recommendations, including direct payments)
- **Social Programs and Well-Being** (6 recommendations)
- **Health Equity** (9 recommendations)
- **Desegregation** (18 recommendations: schools + housing)
- **Higher Education** (3 recommendations)
- **Environmental Justice** (7 recommendations)
- **Public Safety and Justice** (18 recommendations)
- **Public Education and Narrative** (5 recommendations)
- **Faith Institutions** (4 recommendations)
- **Accountability** (2 recommendations)

## Quick Start

### Local Development

```bash
cd report
pip install -r requirements.txt
export NJRC_REPORT_JSON_PATH=./data/njrc-report.json
gunicorn server:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080
```

### Docker

```bash
cd report
docker compose up --build -d
```

### Connect from Claude Desktop

```json
{
  "mcpServers": {
    "njrc-report": {
      "url": "http://localhost:8080/mcp/"
    }
  }
}
```

### Connect from Claude Code

```json
{
  "mcpServers": {
    "njrc-report": {
      "type": "streamableHttp",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

## Architecture

```
report/
├── data/
│   ├── njrc-report.json     ← Structured report content (~900 lines)
│   └── logs/                 ← NDJSON activity logs
├── dashboard/
│   └── app.py                ← Plotly Dash analytics dashboard
├── tools.py                  ← MCP tool definitions (~550 lines)
├── mcp_server.py             ← FastMCP configuration + tool registration
├── server.py                 ← FastAPI app, OAuth 2.1, HTML docs page
├── activity_logger.py        ← Structured NDJSON logging (buffered, rotated)
├── costing.py                ← Token estimation + cost tracking
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── Caddyfile                 ← Caddy reverse proxy config
```

### Key Design Decisions

- **No database** — entire content is a single JSON file loaded once at startup
- **Stateless HTTP** — MCP over HTTP with SSE streaming
- **Tier 1 + Tier 3 logging** — automatic tool call logging + LLM self-reporting
- **OAuth 2.1** — built-in PKCE-compliant auth (optional)
- **Embedded dashboard** — analytics at `/reporting/` (Plotly Dash)
- **Pi-friendly** — runs on Raspberry Pi 4+ (~250 MB RAM)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NJRC_REPORT_JSON_PATH` | `./data/njrc-report.json` | Path to report JSON |
| `MCP_LOG_DIR` | `/var/log/mcp` | Log directory |
| `MCP_API_KEY` | _(empty)_ | Optional Bearer token for auth |
| `PUBLIC_HOST` | _(empty)_ | Domain for reverse proxy |
| `DASH_EMBEDDED` | `1` | Embed dashboard in server |
| `PORT` | `8080` | Server port |

## Source & Attribution

**Report:** "For Such a Time as This: The Nowness of Reparations for Black People in New Jersey" (June 2025)

**Convened by:** New Jersey Institute for Social Justice in partnership with the Robert Wood Johnson Foundation

**Co-Chairs:** Khalil Gibran Muhammad (Princeton University) and Taja-Nia Henderson (Rutgers University)

**9 Subject-Matter Committees** with leading scholars, practitioners, faith leaders, and advocates

All responses from this MCP server include attribution metadata linking back to the original report.
