import os
import secrets
import time
import hashlib
import base64
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
import contextlib
from mcp_server import mcp

MCP_API_KEY = os.getenv("MCP_API_KEY", "").strip()

_auth_codes: dict = {}
AUTH_CODE_TTL = 300


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def mcp_middleware(request, call_next):
    path = request.scope["path"]

    if path == "/mcp":
        request.scope["path"] = "/mcp/"
        request.scope["raw_path"] = b"/mcp/"
        path = "/mcp/"

    if path.startswith("/mcp") and MCP_API_KEY:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {MCP_API_KEY}":
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

    return await call_next(request)


# OAuth 2.1 endpoints
@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "resource": base,
        "authorization_servers": [base],
    }

@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
    }

@app.post("/oauth/register")
async def oauth_register(request: Request):
    body = await request.json()
    client_id = secrets.token_urlsafe(16)
    return JSONResponse({
        "client_id": client_id,
        "client_name": body.get("client_name", "mcp-client"),
        "redirect_uris": body.get("redirect_uris", []),
        "grant_types": body.get("grant_types", ["authorization_code"]),
        "response_types": body.get("response_types", ["code"]),
        "token_endpoint_auth_method": body.get("token_endpoint_auth_method", "client_secret_post"),
    }, status_code=201)

@app.get("/oauth/authorize")
async def oauth_authorize_get(
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    scope: str = "",
):
    if not MCP_API_KEY:
        code = secrets.token_urlsafe(32)
        _auth_codes[code] = {
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "created_at": time.time(),
        }
        params = {"code": code}
        if state:
            params["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    html = f"""<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width,initial-scale=1"><title>NJ Reparations Council Report</title></head>
<body style="font-family:system-ui,sans-serif;max-width:380px;margin:80px auto;padding:0 20px">
<h2>NJ Reparations Council Report</h2>
<p>Enter the access password to connect:</p>
<form method="POST" action="/oauth/authorize">
  <input type="hidden" name="response_type" value="{response_type}">
  <input type="hidden" name="client_id" value="{client_id}">
  <input type="hidden" name="redirect_uri" value="{redirect_uri}">
  <input type="hidden" name="state" value="{state}">
  <input type="hidden" name="code_challenge" value="{code_challenge}">
  <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
  <input type="hidden" name="scope" value="{scope}">
  <input type="password" name="password" placeholder="Password" autofocus
         style="padding:10px;width:100%;box-sizing:border-box;margin-bottom:12px;font-size:16px;border:1px solid #ccc;border-radius:4px">
  <button type="submit"
          style="padding:10px 28px;font-size:16px;cursor:pointer;border:none;border-radius:4px;background:#7c2d12;color:#fff">
    Login</button>
</form>
</body></html>"""
    return HTMLResponse(html)

@app.post("/oauth/authorize")
async def oauth_authorize_post(request: Request):
    form = await request.form()
    password = form.get("password", "")
    redirect_uri = str(form.get("redirect_uri", ""))
    state = str(form.get("state", ""))
    code_challenge = str(form.get("code_challenge", ""))

    if password != MCP_API_KEY:
        return HTMLResponse(
            "<html><body style='font-family:system-ui,sans-serif;max-width:380px;margin:80px auto;padding:0 20px'>"
            "<h2>Wrong password</h2><p><a href='javascript:history.back()'>Try again</a></p>"
            "</body></html>",
            status_code=401,
        )

    now = time.time()
    expired = [k for k, v in _auth_codes.items() if now - v["created_at"] > AUTH_CODE_TTL]
    for k in expired:
        _auth_codes.pop(k, None)

    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "created_at": now,
    }

    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

@app.post("/oauth/token")
async def oauth_token(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "client_credentials":
        client_secret = form.get("client_secret", "")
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                _, client_secret = decoded.split(":", 1)
            except Exception:
                pass
        if client_secret != MCP_API_KEY:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        return {"access_token": MCP_API_KEY, "token_type": "bearer"}

    elif grant_type == "authorization_code":
        code = form.get("code", "")
        stored = _auth_codes.pop(code, None)
        if not stored:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if time.time() - stored["created_at"] > AUTH_CODE_TTL:
            return JSONResponse({"error": "invalid_grant", "error_description": "code expired"}, status_code=400)

        code_verifier = form.get("code_verifier", "")
        if stored.get("code_challenge") and code_verifier:
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if expected != stored["code_challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE failed"}, status_code=400)

        return {"access_token": MCP_API_KEY, "token_type": "bearer"}

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ---------------------------------------------------------------------------
# Public documentation page (served at root)
# ---------------------------------------------------------------------------
@app.get("/")
async def mcp_docs():
    import json
    from pathlib import Path

    report_path = Path(os.getenv(
        "NJRC_REPORT_JSON_PATH",
        str(Path(__file__).resolve().parent / "data" / "njrc-report.json"),
    ))
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}

    version = doc.get("semantic_version", "1.0.0")
    section_count = len(doc.get("sections", []))
    topic_count = sum(len(s.get("topics", [])) for s in doc.get("sections", []))

    # Count recommendations
    rec_count = 0
    for s in doc.get("sections", []):
        for t in s.get("topics", []):
            rec_count += len(t.get("recommendations", []))
            for sub in t.get("subtopics", []):
                rec_count += len(sub.get("recommendations", []))

    stat_count = len(doc.get("key_statistics", []))
    spotlight_count = sum(len(s.get("spotlights", [])) for s in doc.get("sections", []))
    committee_count = len(doc.get("council_committees", []))

    # Build sections overview
    sections_html = ""
    for s in doc.get("sections", []):
        topic_names = ", ".join(t.get("name", "") for t in s.get("topics", []))
        sections_html += f"""
        <div class="section-row">
            <div class="section-code">{s.get('code', '')}</div>
            <div class="section-info">
                <strong>{s.get('name', '')}</strong>
                <span class="section-topics">{topic_names}</span>
            </div>
        </div>"""

    # Build policy areas
    policy_html = ""
    blueprint = next((s for s in doc.get("sections", []) if s.get("id") == "blueprint_for_repair"), None)
    if blueprint:
        for t in blueprint.get("topics", []):
            recs = list(t.get("recommendations", []))
            for sub in t.get("subtopics", []):
                recs.extend(sub.get("recommendations", []))
            policy_html += f"""
            <div class="principle-card">
                <div class="principle-number">{len(recs)}</div>
                <div>
                    <strong>{t.get('name', '')}</strong>
                    <p class="principle-desc">{t.get('content', '')[:120]}...</p>
                </div>
            </div>"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NJ Reparations Council Report &mdash; MCP Documentation</title>
<style>
  :root {{
    --brown: #7c2d12;
    --brown-light: #9a3412;
    --brown-pale: #fef3c7;
    --brown-mist: #fffbeb;
    --gold: #b45309;
    --green: #16a34a;
    --green-pale: #dcfce7;
    --amber: #d97706;
    --amber-pale: #fef3c7;
    --red: #dc2626;
    --red-pale: #fee2e2;
    --gray-50: #f9fafb;
    --gray-100: #f3f4f6;
    --gray-200: #e5e7eb;
    --gray-400: #9ca3af;
    --gray-500: #6b7280;
    --gray-700: #374151;
    --gray-900: #111827;
    --radius: 12px;
    --radius-sm: 8px;
    --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.06);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: var(--gray-900);
    background: var(--gray-50);
    line-height: 1.6;
  }}
  .hero {{
    background: linear-gradient(135deg, var(--brown) 0%, var(--brown-light) 100%);
    color: #fff;
    padding: 60px 24px 48px;
    text-align: center;
  }}
  .hero-badge {{
    display: inline-block;
    background: rgba(255,255,255,.15);
    border: 1px solid rgba(255,255,255,.25);
    border-radius: 20px;
    padding: 4px 16px;
    font-size: 13px;
    letter-spacing: .5px;
    margin-bottom: 20px;
  }}
  .hero h1 {{ font-size: clamp(24px, 5vw, 36px); font-weight: 700; line-height: 1.2; margin-bottom: 12px; }}
  .hero p {{ font-size: 16px; opacity: .9; max-width: 700px; margin: 0 auto 28px; }}
  .hero-stats {{ display: flex; justify-content: center; gap: 32px; flex-wrap: wrap; }}
  .hero-stat {{ text-align: center; }}
  .hero-stat .num {{ font-size: 32px; font-weight: 700; display: block; }}
  .hero-stat .label {{ font-size: 13px; opacity: .75; text-transform: uppercase; letter-spacing: .5px; }}
  .container {{ max-width: 960px; margin: 0 auto; padding: 0 24px; }}
  .section-heading {{ margin-top: 56px; margin-bottom: 24px; padding-bottom: 12px; border-bottom: 3px solid var(--brown); }}
  .section-heading h2 {{ font-size: 26px; font-weight: 700; color: var(--brown); }}
  .section-heading p {{ margin-top: 4px; color: var(--gray-500); font-size: 15px; }}
  .card {{ background: #fff; border: 1px solid var(--gray-200); border-radius: var(--radius); padding: 28px; margin-bottom: 20px; box-shadow: var(--shadow); }}
  .card h3 {{ font-size: 18px; margin-bottom: 12px; color: var(--brown); }}
  .card p, .card li {{ color: var(--gray-700); font-size: 15px; }}
  .card ul {{ padding-left: 20px; margin-top: 8px; }}
  .card li {{ margin-bottom: 6px; }}
  .card li::marker {{ color: var(--brown-light); }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 700px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
  .ask-card {{ background: var(--brown-mist); border: 1px solid var(--brown-pale); border-radius: var(--radius); padding: 20px 24px; margin-bottom: 16px; }}
  .ask-card .prompt {{ font-size: 16px; font-style: italic; color: var(--brown); margin-bottom: 8px; padding-left: 24px; }}
  .ask-card .why {{ font-size: 13px; color: var(--gray-500); padding-left: 24px; }}
  .principle-card {{ display: flex; align-items: flex-start; gap: 16px; padding: 16px 0; border-bottom: 1px solid var(--gray-200); }}
  .principle-card:last-child {{ border-bottom: none; }}
  .principle-number {{ width: 36px; height: 36px; background: var(--brown); color: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; flex-shrink: 0; }}
  .principle-desc {{ font-size: 14px; color: var(--gray-500); margin-top: 4px; }}
  .section-row {{ display: flex; align-items: flex-start; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--gray-200); }}
  .section-row:last-child {{ border-bottom: none; }}
  .section-code {{ width: 40px; height: 40px; background: var(--brown-pale); color: var(--brown); border-radius: var(--radius-sm); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; flex-shrink: 0; }}
  .section-info strong {{ font-size: 15px; display: block; }}
  .section-topics {{ font-size: 13px; color: var(--gray-500); display: block; margin-top: 2px; }}
  .tool-item {{ padding: 14px 0; border-bottom: 1px solid var(--gray-200); }}
  .tool-item:last-child {{ border-bottom: none; }}
  .tool-name {{ font-family: "SF Mono", "Fira Code", monospace; font-size: 14px; font-weight: 600; color: var(--brown); }}
  .tool-desc {{ font-size: 13px; color: var(--gray-500); margin-top: 2px; }}
  .setup-nav {{ display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }}
  .setup-nav a {{ display: inline-block; padding: 10px 24px; background: #fff; border: 2px solid var(--gray-200); border-radius: var(--radius-sm); color: var(--gray-700); text-decoration: none; font-weight: 600; font-size: 15px; transition: all .15s ease; }}
  .setup-nav a:hover {{ border-color: var(--brown); color: var(--brown); box-shadow: var(--shadow); }}
  code {{ font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace; }}
  .footer {{ margin-top: 64px; padding: 32px 24px; background: var(--gray-100); border-top: 1px solid var(--gray-200); text-align: center; color: var(--gray-500); font-size: 13px; }}
  .footer a {{ color: var(--brown); text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>

<div class="hero">
  <div class="hero-badge">MCP Server &middot; Version {version}</div>
  <h1>NJ Reparations Council Report</h1>
  <p>An interactive, AI-accessible version of &ldquo;For Such a Time as This: The Nowness of Reparations for Black People in New Jersey&rdquo; &mdash; helping policymakers, educators, researchers, and communities understand and act on the report&rsquo;s findings.</p>
  <div class="hero-stats">
    <div class="hero-stat"><span class="num">{section_count}</span><span class="label">Sections</span></div>
    <div class="hero-stat"><span class="num">{topic_count}</span><span class="label">Topics</span></div>
    <div class="hero-stat"><span class="num">{rec_count}</span><span class="label">Policy Recommendations</span></div>
    <div class="hero-stat"><span class="num">{stat_count}</span><span class="label">Key Statistics</span></div>
    <div class="hero-stat"><span class="num">{spotlight_count}</span><span class="label">Spotlights</span></div>
  </div>
</div>

<div class="container">

<div class="section-heading">
  <h2>What Is This?</h2>
  <p>How this server works and what it makes possible</p>
</div>

<div class="card">
  <h3>The NJ Reparations Report, Made Interactive</h3>
  <p>This server takes the official <strong>NJ Reparations Council Report (June 2025)</strong> and makes it available through the <strong>Model Context Protocol (MCP)</strong> &mdash; an open standard that lets AI assistants like Claude, ChatGPT, and others access structured data in real time.</p>
  <p style="margin-top:12px">Instead of reading a 200+ page report, users can <strong>ask questions in plain language</strong> and get precise, sourced answers drawn directly from the Council&rsquo;s findings and recommendations. Every response includes attribution.</p>
</div>

<div class="grid-2">
  <div class="card">
    <h3>For Policymakers &amp; Advocates</h3>
    <ul>
      <li>Access all {rec_count} policy recommendations by area</li>
      <li>Get racial disparity data for briefings and proposals</li>
      <li>Understand the historical throughline from slavery to today</li>
      <li>Reference the wealth gap analysis and investment calculations</li>
      <li>Find successful reparations examples from other jurisdictions</li>
    </ul>
  </div>
  <div class="card">
    <h3>For Educators &amp; Researchers</h3>
    <ul>
      <li>Search the full report for specific topics and data</li>
      <li>Read historical spotlight stories for curriculum development</li>
      <li>Explore NJ&rsquo;s slavery history section by section</li>
      <li>Access Council committee and member information</li>
      <li>Find source statistics for research and citation</li>
    </ul>
  </div>
</div>

<div class="section-heading">
  <h2>Get Started</h2>
  <p>Connect this report to your AI assistant in under a minute &mdash; pick your platform</p>
</div>

<div class="card" style="text-align:center; padding:32px 28px;">
  <p style="font-size:15px; color:var(--gray-700); margin-bottom:16px">All platforms connect to the same address:</p>
  <div id="mcp-url-box" style="background: var(--gray-100); padding: 16px 20px; border-radius: var(--radius-sm); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 17px; color: var(--brown); border: 1px solid var(--gray-200); display:inline-block; cursor:pointer; user-select:all; position:relative;" onclick="navigator.clipboard.writeText('https://rwjf-mcp.workingpaper.co/mcp/');var el=document.getElementById('copy-toast');el.style.opacity=1;setTimeout(function(){{el.style.opacity=0}},1500);">
    https://rwjf-mcp.workingpaper.co/mcp/
    <span id="copy-toast" style="position:absolute;top:-32px;left:50%;transform:translateX(-50%);background:var(--brown);color:#fff;padding:4px 14px;border-radius:6px;font-size:12px;font-family:system-ui,sans-serif;opacity:0;transition:opacity .2s;pointer-events:none;">Copied!</span>
  </div>
  <p style="margin-top:10px; font-size:13px; color:var(--gray-400)">Click to copy</p>
</div>

<div class="setup-nav">
  <a href="#setup-claude">Claude</a>
  <a href="#setup-chatgpt">ChatGPT</a>
</div>

<!-- ---- Claude ---- -->
<div class="card" id="setup-claude">
  <h3>Claude</h3>
  <p style="margin-bottom:12px; font-size:14px; color:var(--gray-500)">Works on claude.ai (Pro, Team, or Enterprise) and the Claude desktop app</p>
  <ol style="padding-left:20px; font-size:14px; color:var(--gray-700)">
    <li style="margin-bottom:8px">Go to <a href="https://claude.ai" style="color:var(--brown)">claude.ai</a> and sign in</li>
    <li style="margin-bottom:8px">Click your name in the bottom left corner, then click <strong>Settings</strong></li>
    <li style="margin-bottom:8px">Click <strong>Integrations</strong> in the left sidebar</li>
    <li style="margin-bottom:8px">Click <strong>Add Integration</strong>, then choose <strong>MCP</strong></li>
    <li style="margin-bottom:8px">Paste this URL: <code style="background:var(--gray-100);padding:4px 8px;border-radius:4px;font-size:14px">https://rwjf-mcp.workingpaper.co/mcp/</code></li>
    <li style="margin-bottom:8px">Click <strong>Connect</strong> (if prompted for a password, ask your administrator)</li>
    <li>Start a new chat and ask any question about the report &mdash; Claude will automatically look up the answer</li>
  </ol>
  <details style="margin-top:16px; font-size:13px; color:var(--gray-500)">
    <summary style="cursor:pointer; font-weight:600; color:var(--gray-700)">Advanced: Claude Desktop app or Claude Code</summary>
    <div style="margin-top:10px">
      <p style="margin-bottom:8px"><strong>Desktop app</strong> &mdash; add to your config file (<code style="font-size:12px">~/Library/Application Support/Claude/claude_desktop_config.json</code>):</p>
      <div style="background:var(--gray-100); padding:14px 18px; border-radius:var(--radius-sm); font-family:'SF Mono','Fira Code',monospace; font-size:13px; color:var(--gray-900); border:1px solid var(--gray-200); overflow-x:auto; white-space:pre; line-height:1.5">{{
  "mcpServers": {{
    "njrc-report": {{
      "type": "streamable-http",
      "url": "https://rwjf-mcp.workingpaper.co/mcp/"
    }}
  }}
}}</div>
      <p style="margin-top:14px; margin-bottom:8px"><strong>Claude Code CLI</strong>:</p>
      <div style="background:var(--gray-100); padding:14px 18px; border-radius:var(--radius-sm); font-family:'SF Mono','Fira Code',monospace; font-size:13px; color:var(--gray-900); border:1px solid var(--gray-200); overflow-x:auto; white-space:pre; line-height:1.5">claude mcp add njrc-report \
  --transport streamable-http \
  https://rwjf-mcp.workingpaper.co/mcp/</div>
    </div>
  </details>
</div>

<!-- ---- ChatGPT ---- -->
<div class="card" id="setup-chatgpt">
  <h3>ChatGPT</h3>
  <p style="margin-bottom:12px; font-size:14px; color:var(--gray-500)">Requires ChatGPT Plus, Team, or Enterprise</p>
  <ol style="padding-left:20px; font-size:14px; color:var(--gray-700)">
    <li style="margin-bottom:8px">Go to <a href="https://chatgpt.com" style="color:var(--brown)">chatgpt.com</a> and sign in</li>
    <li style="margin-bottom:8px">Click your profile picture in the top right, then click <strong>Settings</strong></li>
    <li style="margin-bottom:8px">Click <strong>Connected apps</strong> in the left sidebar</li>
    <li style="margin-bottom:8px">Click <strong>Add connection</strong></li>
    <li style="margin-bottom:8px">Paste this URL: <code style="background:var(--gray-100);padding:4px 8px;border-radius:4px;font-size:14px">https://rwjf-mcp.workingpaper.co/mcp/</code></li>
    <li style="margin-bottom:8px">Complete any prompts that appear</li>
    <li>Start a new chat &mdash; the report tools will appear when you click the tools icon at the bottom of the chat</li>
  </ol>
  <details style="margin-top:16px; font-size:13px; color:var(--gray-500)">
    <summary style="cursor:pointer; font-weight:600; color:var(--gray-700)">Advanced: OpenAI Responses API</summary>
    <div style="background:var(--gray-100); padding:14px 18px; border-radius:var(--radius-sm); font-family:'SF Mono','Fira Code',monospace; font-size:13px; color:var(--gray-900); border:1px solid var(--gray-200); overflow-x:auto; white-space:pre; line-height:1.5; margin-top:10px">import openai

client = openai.OpenAI()
resp = client.responses.create(
    model="gpt-4.1",
    input="What is the racial wealth gap in New Jersey?",
    tools=[{{
        "type": "mcp",
        "server_label": "njrc-report",
        "server_url": "https://rwjf-mcp.workingpaper.co/mcp/",
        "require_approval": "never",
    }}],
)</div>
  </details>
</div>

<!-- ---- Gemini note ---- -->
<div class="card" style="border-left: 4px solid var(--gray-400);">
  <h3 style="color: var(--gray-500)">Google Gemini</h3>
  <p style="font-size:14px; color:var(--gray-500)">The consumer Gemini app and website do not currently support connecting to custom MCP servers. This feature is only available through Google&rsquo;s developer tools (Gemini CLI, AI Studio, and the Gemini API). We&rsquo;ll update this page if Google adds MCP support for everyday users.</p>
</div>

<div class="callout" style="margin-top:24px; background: var(--brown-mist); border: 1px solid var(--brown-pale); border-radius: var(--radius); padding: 20px 24px; font-size: 14px;">
  <strong style="color: var(--brown)">That&rsquo;s it!</strong> Once connected, just type a question in plain English like
  <em>&ldquo;What does the report say about the racial wealth gap?&rdquo;</em>
  &mdash; the AI will automatically look up the answer from the report.
</div>

<div class="section-heading">
  <h2>Example Questions</h2>
  <p>Try asking your AI assistant these questions</p>
</div>

<div class="ask-card">
  <div class="prompt">&ldquo;What is the racial wealth gap in New Jersey and what does the report recommend to close it?&rdquo;</div>
  <div class="why">Gets wealth data and economic justice policy recommendations</div>
</div>
<div class="ask-card">
  <div class="prompt">&ldquo;Tell me about Colonel Tye and his role in the Revolutionary War.&rdquo;</div>
  <div class="why">Retrieves the Colonel Tye spotlight from the slavery era section</div>
</div>
<div class="ask-card">
  <div class="prompt">&ldquo;What policy recommendations does the report make for criminal justice reform?&rdquo;</div>
  <div class="why">Returns the Public Safety and Justice policy area with all sub-recommendations</div>
</div>
<div class="ask-card">
  <div class="prompt">&ldquo;What are some examples of successful reparations programs around the world?&rdquo;</div>
  <div class="why">Lists reparations examples from Germany, Japan, California, Evanston, and more</div>
</div>
<div class="ask-card">
  <div class="prompt">&ldquo;How did New Jersey&rsquo;s gradual abolition differ from other northern states?&rdquo;</div>
  <div class="why">Explores the colonial era section on NJ&rsquo;s uniquely slow path to ending slavery</div>
</div>

<div class="section-heading">
  <h2>Report Structure</h2>
  <p>{section_count} sections covering NJ&rsquo;s history and path to reparative justice</p>
</div>

<div class="card">
  {sections_html}
</div>

<div class="section-heading">
  <h2>Policy Areas ({rec_count} Recommendations)</h2>
  <p>The Blueprint for Repair proposes transformative policies across 11 areas</p>
</div>

<div class="card">
  {policy_html}
</div>

<div class="section-heading">
  <h2>Available Tools</h2>
  <p>MCP tools for querying the report</p>
</div>

<div class="card">
  <div class="tool-item">
    <span class="tool-name">report.get_policy_recommendations</span>
    <div class="tool-desc">Get all policy recommendations, optionally filtered by area</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_key_statistics</span>
    <div class="tool-desc">Get key racial disparity statistics from the report</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_wealth_gap</span>
    <div class="tool-desc">Get detailed wealth gap data and closure calculations</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_spotlights</span>
    <div class="tool-desc">Get historical spotlight stories (Lockey White, Colonel Tye, Timbuctoo, etc.)</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_reparations_examples</span>
    <div class="tool-desc">Get examples of successful reparations programs worldwide</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_council_info</span>
    <div class="tool-desc">Get Council co-chairs, committees, and members</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.list_sections</span>
    <div class="tool-desc">List all major sections of the report</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.list_topics</span>
    <div class="tool-desc">List topics within a specific section</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_topic</span>
    <div class="tool-desc">Get full details for any topic by ID or code</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.search</span>
    <div class="tool-desc">Full-text search across the entire report</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_version_info</span>
    <div class="tool-desc">Document metadata and version information</div>
  </div>
  <div class="tool-item">
    <span class="tool-name">report.get_usage_guide</span>
    <div class="tool-desc">Navigation guide for the report</div>
  </div>
</div>

<div class="section-heading">
  <h2>Source &amp; Attribution</h2>
</div>

<div class="card">
  <p><strong>Source:</strong> &ldquo;For Such a Time as This: The Nowness of Reparations for Black People in New Jersey&rdquo; &mdash; A Report from the New Jersey Reparations Council (June 2025).</p>
  <p style="margin-top:8px"><strong>Convened by:</strong> New Jersey Institute for Social Justice in partnership with the Robert Wood Johnson Foundation.</p>
  <p style="margin-top:8px"><strong>Co-Chairs:</strong> Khalil Gibran Muhammad (Princeton University) and Taja-Nia Henderson (Rutgers University).</p>
  <p style="margin-top:8px"><strong>Committees:</strong> {committee_count} subject-matter committees with leading scholars, practitioners, and advocates.</p>
  <p style="margin-top:12px; font-size:13px; color:var(--gray-500)">All responses from this MCP server include attribution metadata. The full report is available at <a href="https://www.njisj.org" style="color:var(--brown)">njisj.org</a>.</p>
</div>

</div>

<div class="footer">
  <p>NJ Reparations Council Report MCP Server &middot; Version {version}</p>
  <p style="margin-top:4px"><a href="https://www.njisj.org">njisj.org</a></p>
</div>

</body>
</html>"""
    return HTMLResponse(page)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Mount MCP
# ---------------------------------------------------------------------------
app.mount("/mcp", mcp.streamable_http_app())

# ---------------------------------------------------------------------------
# Optional: embed dashboard at /reporting/
# ---------------------------------------------------------------------------
if os.getenv("DASH_EMBEDDED", "").lower() in ("1", "true", "yes"):
    try:
        from dashboard.app import app as dash_app
        from starlette.middleware.wsgi import WSGIMiddleware
        app.mount("/reporting", WSGIMiddleware(dash_app.server))
    except Exception:
        pass
