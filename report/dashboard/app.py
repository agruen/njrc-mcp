"""
NJ Reparations Council Report MCP Analytics Dashboard

Reads NDJSON log files written by the MCP server and displays
real-time insights about tool usage and stakeholder activity.

Runs as a sidecar container sharing the /var/log/mcp volume.
"""

import gzip
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

# ---------------------
# Configuration
# ---------------------
LOG_DIR = Path(os.getenv("MCP_LOG_DIR", "/var/log/mcp"))
TOOL_LOG = LOG_DIR / "tool_calls.jsonl"
ACTIVITY_LOG = LOG_DIR / "activities.jsonl"
REFRESH_INTERVAL_MS = 30_000
MAX_LOG_LINES = int(os.getenv("MAX_LOG_LINES", "10000"))

COLORS = {
    "brown": "#7c2d12",
    "green": "#16a34a",
    "orange": "#ea580c",
    "purple": "#9333ea",
    "red": "#dc2626",
    "teal": "#0d9488",
    "pink": "#db2777",
    "gray": "#6b7280",
    "light_bg": "#f8fafc",
    "border": "#e5e7eb",
}

STAKEHOLDER_COLORS = {
    "policymakers": "#16a34a",
    "educators": "#2563eb",
    "community_members": "#ea580c",
    "researchers": "#9333ea",
    "faith_leaders": "#0d9488",
    "advocates": "#db2777",
}

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=40, r=20, t=30, b=40),
    height=300,
    font=dict(family="system-ui, -apple-system, sans-serif"),
)

TALL_CHART_LAYOUT = {**CHART_LAYOUT, "height": 400}


# ---------------------
# Data loading
# ---------------------
def read_jsonl_tail(filepath: Path, max_lines: int = MAX_LOG_LINES,
                    chunk_size: int = 65536) -> list[dict]:
    if not filepath.exists():
        return []
    try:
        file_size = filepath.stat().st_size
    except OSError:
        return []
    if file_size == 0:
        return []

    if file_size <= chunk_size:
        with open(filepath, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return _parse_lines(all_lines[-max_lines:])

    lines: list[str] = []
    with open(filepath, "rb") as f:
        remaining = file_size
        fragment = b""
        while remaining > 0 and len(lines) < max_lines:
            read_size = min(chunk_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            chunk = f.read(read_size) + fragment
            parts = chunk.split(b"\n")
            fragment = parts[0]
            for part in reversed(parts[1:]):
                decoded = part.decode("utf-8", errors="replace").strip()
                if decoded:
                    lines.append(decoded)
                if len(lines) >= max_lines:
                    break
        if remaining == 0 and fragment:
            decoded = fragment.decode("utf-8", errors="replace").strip()
            if decoded and len(lines) < max_lines:
                lines.append(decoded)

    lines.reverse()
    return _parse_lines(lines)


def _parse_lines(lines: list[str]) -> list[dict]:
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _discover_archives(base_filepath: Path) -> list[Path]:
    stem = base_filepath.stem
    return sorted(base_filepath.parent.glob(f"{stem}.*.jsonl.gz"))


def _read_jsonl_gz(filepath: Path) -> list[dict]:
    records = []
    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def read_all_history(base_filepath: Path) -> list[dict]:
    all_records: list[dict] = []
    for archive in _discover_archives(base_filepath):
        all_records.extend(_read_jsonl_gz(archive))
    if base_filepath.exists():
        with open(base_filepath, "r", encoding="utf-8") as f:
            all_records.extend(_parse_lines(f.readlines()))
    return all_records


def safe_df(records: list[dict], date_col: str = "timestamp") -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    return df


def _no_data(msg: str = "No data yet") -> go.Figure:
    return go.Figure().update_layout(
        **CHART_LAYOUT,
        annotations=[dict(text=msg, showarrow=False, font=dict(size=14, color=COLORS["gray"]))],
    )


# ---------------------
# Derived data helpers
# ---------------------
def extract_stakeholder_from_args(args: dict) -> str:
    for key in ("stakeholder", "stakeholder_type"):
        val = args.get(key, "")
        if val:
            return str(val).lower()
    return ""


def extract_search_queries(tool_df: pd.DataFrame) -> pd.DataFrame:
    if tool_df.empty or "arguments" not in tool_df.columns:
        return pd.DataFrame()
    search_tools = ["report__search"]
    mask = tool_df["tool_name"].isin(search_tools)
    if not mask.any():
        return pd.DataFrame()
    searches = tool_df[mask].copy()
    searches["query"] = searches["arguments"].apply(lambda a: a.get("query", "") if isinstance(a, dict) else "")
    searches = searches[searches["query"].str.strip() != ""]
    return searches


def extract_policy_queries(tool_df: pd.DataFrame) -> pd.DataFrame:
    if tool_df.empty or "arguments" not in tool_df.columns:
        return pd.DataFrame()
    policy_tools = ["report__get_policy_recommendations"]
    mask = tool_df["tool_name"].isin(policy_tools)
    if not mask.any():
        return pd.DataFrame()
    queries = tool_df[mask].copy()
    queries["policy_area"] = queries["arguments"].apply(
        lambda a: a.get("policy_area", "all") if isinstance(a, dict) else "all"
    )
    return queries


def classify_exploration_depth(tool_df: pd.DataFrame) -> dict:
    if tool_df.empty or "tool_name" not in tool_df.columns:
        return {"Browsing": 0, "Targeted lookup": 0, "Deep exploration": 0}

    shallow = {
        "report__list_sections", "report__list_topics",
        "report__list_tools", "report__get_usage_guide",
        "report__get_version_info",
    }
    medium = {
        "report__search", "report__get_topic",
        "report__get_council_info",
    }
    deep = {
        "report__get_policy_recommendations", "report__get_key_statistics",
        "report__get_wealth_gap", "report__get_spotlights",
        "report__get_reparations_examples",
    }

    counts = {"Browsing": 0, "Targeted lookup": 0, "Deep exploration": 0}
    counts["Deep exploration"] = len(tool_df[tool_df["tool_name"].isin(deep)])
    counts["Targeted lookup"] = len(tool_df[tool_df["tool_name"].isin(medium)])
    counts["Browsing"] = len(tool_df[tool_df["tool_name"].isin(shallow)])
    return counts


def compute_tier3_compliance(tool_df: pd.DataFrame, act_df: pd.DataFrame) -> float:
    if tool_df.empty:
        return 0.0
    data_tools = tool_df[tool_df["tool_name"] != "report__log_activity"].copy()
    if data_tools.empty:
        return 0.0
    data_tools["window"] = data_tools["timestamp"].dt.floor("5min")
    data_windows = set(data_tools["window"].unique())
    log_tools = tool_df[tool_df["tool_name"] == "report__log_activity"].copy()
    if log_tools.empty:
        return 0.0
    log_tools["window"] = log_tools["timestamp"].dt.floor("5min")
    log_windows = set(log_tools["window"].unique())
    if not data_windows:
        return 0.0
    return len(data_windows & log_windows) / len(data_windows) * 100


# ---------------------
# Layout helpers
# ---------------------
def _card(title: str, value: str, subtitle: str = "", color: str = COLORS["brown"]) -> html.Div:
    children = [
        html.Div(title, style={"fontSize": "13px", "color": COLORS["gray"], "marginBottom": "4px"}),
        html.Div(value, style={"fontSize": "28px", "fontWeight": "bold", "color": color}),
    ]
    if subtitle:
        children.append(html.Div(subtitle, style={"fontSize": "11px", "color": COLORS["gray"], "marginTop": "4px"}))
    return html.Div(
        style={
            "background": COLORS["light_bg"],
            "border": f"2px solid {color}",
            "borderRadius": "12px",
            "padding": "16px 20px",
            "minWidth": "140px",
            "flex": "1",
        },
        children=children,
    )


def _section(title: str, children) -> html.Div:
    return html.Div(
        style={"marginBottom": "40px"},
        children=[
            html.H2(title, style={"fontSize": "20px", "marginBottom": "8px", "borderBottom": f"2px solid {COLORS['brown']}", "paddingBottom": "6px"}),
            *children,
        ],
    )


# ---------------------
# Dashboard Layout
# ---------------------
EMBEDDED = os.getenv("DASH_EMBEDDED", "").lower() in ("1", "true", "yes")

if EMBEDDED:
    app = Dash(
        __name__,
        routes_pathname_prefix="/",
        requests_pathname_prefix="/reporting/",
    )
else:
    URL_BASE = os.getenv("DASH_URL_BASE", "/reporting/")
    if not URL_BASE.startswith("/"):
        URL_BASE = "/" + URL_BASE
    if not URL_BASE.endswith("/"):
        URL_BASE = URL_BASE + "/"
    app = Dash(__name__, url_base_pathname=URL_BASE)
app.title = "NJ Reparations Report Analytics"

app.layout = html.Div(
    style={"fontFamily": "system-ui, -apple-system, sans-serif", "margin": "0 auto", "maxWidth": "1200px", "padding": "20px"},
    children=[
        html.H1("NJ Reparations Report Analytics", style={"borderBottom": f"3px solid {COLORS['brown']}", "paddingBottom": "10px", "marginBottom": "4px"}),
        html.P("Usage insights for the NJ Reparations Council Report MCP server.", style={"color": COLORS["gray"], "marginBottom": "16px"}),

        html.Div(
            style={
                "display": "flex", "alignItems": "center", "gap": "12px",
                "marginBottom": "20px", "padding": "10px 16px",
                "background": COLORS["light_bg"], "borderRadius": "8px",
                "border": f"1px solid {COLORS['border']}",
            },
            children=[
                dcc.Checklist(
                    id="history-toggle",
                    options=[{"label": " Include all archived history", "value": "all"}],
                    value=[],
                    style={"fontSize": "14px"},
                ),
            ],
        ),

        dcc.Interval(id="refresh", interval=REFRESH_INTERVAL_MS, n_intervals=0),
        html.Div(id="report-output"),
    ],
)


# ---------------------
# Main callback
# ---------------------
@app.callback(
    Output("report-output", "children"),
    Input("refresh", "n_intervals"),
    Input("history-toggle", "value"),
    prevent_initial_call=False,
)
def update_dashboard(_n, history_toggle):
    include_all = "all" in (history_toggle or [])

    if include_all:
        tool_records = read_all_history(TOOL_LOG)
        act_records = read_all_history(ACTIVITY_LOG)
    else:
        tool_records = read_jsonl_tail(TOOL_LOG)
        act_records = read_jsonl_tail(ACTIVITY_LOG)

    tool_df = safe_df(tool_records)
    act_df = safe_df(act_records)

    sections = []

    # Overview cards
    total_calls = len(tool_df)
    data_calls = tool_df[tool_df["tool_name"] != "report__log_activity"] if not tool_df.empty and "tool_name" in tool_df.columns else tool_df
    avg_latency = f"{data_calls['latency_ms'].mean():.0f}ms" if not data_calls.empty and "latency_ms" in data_calls.columns else "N/A"
    compliance = compute_tier3_compliance(tool_df, act_df) if not tool_df.empty else 0

    # Count policy-focused tool usage
    policy_tools = {"report__get_policy_recommendations", "report__get_wealth_gap", "report__get_key_statistics"}
    policy_calls = len(tool_df[tool_df["tool_name"].isin(policy_tools)]) if not tool_df.empty and "tool_name" in tool_df.columns else 0

    cards = html.Div(
        style={"display": "flex", "gap": "16px", "marginBottom": "30px", "flexWrap": "wrap"},
        children=[
            _card("Tool Calls", str(len(data_calls)), "excludes log_activity"),
            _card("Policy Queries", str(policy_calls), "policy-focused tool calls", COLORS["green"]),
            _card("Activities Logged", str(len(act_df)), "", COLORS["orange"]),
            _card("Avg Latency", avg_latency, "per tool call", COLORS["purple"]),
            _card("Tier 3 Compliance", f"{compliance:.0f}%", "sessions with self-report", COLORS["teal"]),
        ],
    )
    sections.append(cards)

    # Search queries
    report2_children = []
    searches = extract_search_queries(tool_df)
    if not searches.empty:
        query_counts = searches["query"].value_counts().head(15).reset_index()
        query_counts.columns = ["query", "count"]
        fig_queries = px.bar(
            query_counts, x="count", y="query", orientation="h",
            color_discrete_sequence=[COLORS["brown"]],
        )
        fig_queries.update_layout(**CHART_LAYOUT, yaxis=dict(autorange="reversed"))
        report2_children.append(html.Div([
            html.H3("Most Common Search Queries", style={"fontSize": "16px", "marginBottom": "4px"}),
            dcc.Graph(figure=fig_queries, config={"displayModeBar": False}),
        ]))
    else:
        report2_children.append(html.P("No search queries logged yet.", style={"color": COLORS["gray"]}))

    sections.append(_section("What Are People Asking About?", report2_children))

    # Tool usage breakdown
    report3_children = []
    if not tool_df.empty and "tool_name" in tool_df.columns:
        tool_counts = data_calls["tool_name"].value_counts().head(10).reset_index()
        tool_counts.columns = ["tool", "count"]
        tool_counts["tool"] = tool_counts["tool"].str.replace("report__", "")
        fig_tools = px.bar(
            tool_counts, x="count", y="tool", orientation="h",
            color_discrete_sequence=[COLORS["brown"]],
        )
        fig_tools.update_layout(**CHART_LAYOUT, yaxis=dict(autorange="reversed"))
        report3_children.append(dcc.Graph(figure=fig_tools, config={"displayModeBar": False}))

        # Exploration depth
        depths = classify_exploration_depth(data_calls)
        if sum(depths.values()) > 0:
            fig_depth = px.pie(
                values=list(depths.values()), names=list(depths.keys()),
                color_discrete_sequence=[COLORS["teal"], COLORS["orange"], COLORS["brown"]],
                hole=0.4,
            )
            fig_depth.update_layout(**CHART_LAYOUT)
            report3_children.append(dcc.Graph(figure=fig_depth, config={"displayModeBar": False}))
    else:
        report3_children.append(html.P("No tool usage data yet.", style={"color": COLORS["gray"]}))

    sections.append(_section("How Are People Using the Report?", report3_children))

    # Server health
    health_children = []
    if not data_calls.empty and "latency_ms" in data_calls.columns:
        fig_latency = px.box(
            data_calls, x="tool_name", y="latency_ms",
            color_discrete_sequence=[COLORS["brown"]],
        )
        fig_latency.update_layout(**CHART_LAYOUT, xaxis_tickangle=45)
        health_children.append(dcc.Graph(figure=fig_latency, config={"displayModeBar": False}))
    else:
        health_children.append(html.P("No latency data yet.", style={"color": COLORS["gray"]}))

    sections.append(_section("Server Health", health_children))

    return sections
