"""
Main application.

Exposes:
  GET  /                 health check
  GET  /connect          starts Xero OAuth (open this in a browser once)
  GET  /callback         OAuth redirect target; exchanges code for tokens
  POST /mcp              MCP endpoint (JSON-RPC) used by Claude

The MCP layer here implements the core methods Claude's custom-connector
client needs: initialize, tools/list, tools/call.
"""
import os
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse

from token_store import init_db, load_tokens, DATABASE_URL, TOKEN_STORE_PATH
from xero_client import build_authorize_url, exchange_code
from xero_client import missing_env_vars
from tools import TOOLS, call_tool

app = FastAPI(title="Xero MCP Connector")

# Simple in-memory state token for CSRF protection on the OAuth round-trip.
_oauth_state = {"value": None}


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/")
def health():
    authorized = load_tokens() is not None
    missing = missing_env_vars()
    storage = "postgres" if DATABASE_URL else "file"
    return {
        "status": "ok",
        "authorized": authorized,
        "xeroConfigured": len(missing) == 0,
        "missingEnv": missing,
        "tokenStorage": storage,
        "tokenStorePath": TOKEN_STORE_PATH if storage == "file" else None,
    }


@app.get("/connect")
def connect():
    state = secrets.token_urlsafe(16)
    _oauth_state["value"] = state
    return RedirectResponse(build_authorize_url(state))


@app.get("/callback", response_class=HTMLResponse)
async def callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Authorization failed: {error}</h3>", status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Missing authorization code.</h3>", status_code=400)
    if state != _oauth_state["value"]:
        return HTMLResponse("<h3>State mismatch — possible CSRF. Try /connect again.</h3>", status_code=400)

    result = await exchange_code(code)
    return HTMLResponse(
        f"<h3>Connected to Xero ✓</h3>"
        f"<p>Tenant: {result['tenant_id']}</p>"
        f"<p>You can close this tab and use the connector in Claude.</p>"
    )


# ---- MCP JSON-RPC endpoint --------------------------------------------------

def _rpc_result(req_id, result):
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code, message):
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


@app.post("/mcp")
async def mcp(request: Request):
    return await _handle_mcp(request)


@app.post("/")
async def mcp_root(request: Request):
    # Some clients expect the MCP endpoint to be the server root URL.
    return await _handle_mcp(request)


async def _handle_mcp(request: Request):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return _rpc_result(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "xero-mcp-connector", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        try:
            text = await call_tool(tool_name, args)
            return _rpc_result(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:  # surface the error back to Claude as tool output
            return _rpc_result(req_id, {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            })

    return _rpc_error(req_id, -32601, f"Method not found: {method}")
