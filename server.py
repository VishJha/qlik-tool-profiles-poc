"""Qlik tool profiles + skills: proof of concept MCP server.

FastMCP provides the protocol plumbing. Everything that is the *design* lives here:
  - shared read-only Core tool set
  - load_skill / load_profile: feasibility check, then swap the session's tool list
  - entitlement filter on every tools/list, permission re-check on every tools/call
  - server-enforced preview -> confirm for write and destructive tools
  - live log of every decision (for the side-by-side demo) + unmatched-request log

Run:  QLIK_USER=analyst python server.py      (stdio transport)
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

import mcp.types as mt
import yaml
from fastmcp import Context, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool, ToolResult

import mocks

ROOT = Path(__file__).parent
REGISTRY = {t["name"]: t for t in yaml.safe_load((ROOT / "config/registry.yaml").read_text())["tools"]}
CATALOG = yaml.safe_load((ROOT / "config/catalog.yaml").read_text())
USERS = yaml.safe_load((ROOT / "config/users.yaml").read_text())["users"]

USER_ID = os.environ.get("QLIK_USER", "analyst")
USER = USERS[USER_ID]
ENTITLEMENTS = set(USER["entitlements"])
META_TOOLS = {"load_skill", "load_profile", "confirm_action"}

# ---------------------------------------------------------------- live log
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
COLORS = {"SESSION": "36", "LIST": "34", "LOAD": "35", "PASS": "32", "BLOCK": "31",
          "CALL": "37", "PREVIEW": "33", "CONFIRM": "32", "UNMATCHED": "33"}


def log(kind: str, msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {kind:<9} {msg}"
    sys.stderr.write(f"\033[{COLORS.get(kind, '0')}m{line}\033[0m\n")  # stdout is the MCP channel
    with open(LOG_DIR / "server.log", "a") as f:
        f.write(line + "\n")


TRACE_FILE = Path(os.environ.get("QLIK_TRACE", LOG_DIR / "trace.jsonl"))


def trace(node: str | None, kind: str, **data) -> None:
    """Structured event for the graph viewer. node = which box on the decision graph lights up."""
    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps({"ts": time.time(), "src": "server", "node": node, "kind": kind, **data}) + "\n")


# ---------------------------------------------------------------- session state
SESSIONS: dict[str, dict] = {}


def session(ctx: Context | None) -> dict:
    # stdio runs one client per server process, so the process is the session.
    # Over HTTP, key this on the Mcp-Session-Id header instead.
    return SESSIONS.setdefault("stdio", {"active": None, "active_tools": [], "pending": {}})


def allowed(tool: str) -> bool:
    return REGISTRY[tool]["permission"] in ENTITLEMENTS


def visible_tools(s: dict) -> list[str]:
    """profile/skill tools ∩ entitlements. The LLM never sees anything outside this list."""
    names = list(dict.fromkeys(CATALOG["core"] + s["active_tools"]))
    return [n for n in names if allowed(n)]


def missing_permissions(tools: list[str]) -> set[str]:
    return {REGISTRY[t]["permission"] for t in tools} - ENTITLEMENTS


def required_tools(skill: dict) -> list[str]:
    return [t for t in skill["tools"] if t not in skill.get("optional", [])]


# A skill is offered if the user may use all of its REQUIRED tools (optional steps may be missing).
ENTITLED_SKILLS = {k: v for k, v in CATALOG["skills"].items() if not missing_permissions(required_tools(v))}


async def swap_tools(ctx: Context, s: dict, label: str, tools: list[str]) -> None:
    s["active"], s["active_tools"] = label, tools
    await ctx.send_notification(mt.ToolListChangedNotification())


# ---------------------------------------------------------------- registry tools (mock backend)
class RegistryTool(Tool):
    async def run(self, arguments: dict) -> ToolResult:
        return ToolResult(structured_content=mocks.respond(self.name, arguments))


def to_schema(params: dict) -> dict:
    return {"type": "object", "properties": {k: {"type": v} for k, v in params.items()}, "required": list(params)}


mcp = FastMCP("qlik-tool-profiles-poc")
for t in REGISTRY.values():
    mcp.add_tool(RegistryTool(name=t["name"], description=t["description"], parameters=to_schema(t["params"]),
                              tags={t["side_effect"]}))


# ---------------------------------------------------------------- the loader (menu + switch)
skill_menu = "\n".join(f"- {k}: {v['description']}" for k, v in ENTITLED_SKILLS.items()) or "- (none for this user)"
profile_menu = "\n".join(f"- {k}: {v['description']}" for k, v in CATALOG["profiles"].items())


@mcp.tool(description="Load a tested skill for the user's task. Prefer this over load_profile. "
                      f"Skills available to this user:\n{skill_menu}")
async def load_skill(name: str, ctx: Context) -> dict:
    s = session(ctx)
    skill = CATALOG["skills"].get(name)
    if not skill:
        return {"error": f"Unknown skill '{name}'."}
    # Feasibility check with three outcomes: full, reduced (optional steps missing), or blocked.
    req = required_tools(skill)
    missing_req = missing_permissions(req)
    if missing_req:
        changes_data = any(REGISTRY[t]["side_effect"] != "read" for t in req)
        if changes_data:  # never improvise a workaround for an action that changes data
            log("BLOCK", f"load_skill({name}) feasibility FAIL, missing {sorted(missing_req)}")
            trace("feasibility", "fail", text=f"Feasibility FAIL: user lacks {', '.join(sorted(missing_req))}; task changes data, so stop")
            return {"loaded": False, "reason": f"User lacks {sorted(missing_req)}; explain this limitation to the user."}
        log("BLOCK", f"load_skill({name}) required step missing {sorted(missing_req)}; read-only task -> suggest profile")
        trace("feasibility", "fail", text=f"Required step missing ({', '.join(sorted(missing_req))}); read-only task, so try profile {skill['profile']}")
        return {"loaded": False, "reason": f"User lacks {sorted(missing_req)} for a required step.",
                "suggest_profile": skill["profile"], "next": "Call load_profile with the suggested profile; tell the user the answer may be limited."}
    usable = [t for t in skill["tools"] if allowed(t)]
    skipped = [t for t in skill["tools"] if t not in usable]
    for t in skipped:  # swap in each missing optional step's fallback, if the user may use it
        alt = skill.get("fallback", {}).get(t)
        if alt and allowed(alt) and alt not in usable:
            usable.append(alt)
    if skipped:
        trace("feasibility", "partial", text=f"Feasibility PARTIAL: optional step {', '.join(skipped)} not permitted; running reduced skill")
    else:
        trace("feasibility", "pass", text=f"Feasibility PASS: user may use all {len(skill['tools'])} tools of {name}")
    trace("entitle", "pass", text=f"Entitlement filter: {len(skipped)} tools removed")
    await swap_tools(ctx, s, f"skill:{name}" + (" (reduced)" if skipped else ""), usable)
    trace("swap", "pass", text=f"Tool list swapped to Core + {name}; client notified (list_changed)")
    log("LOAD", f"load_skill({name}) feasibility {'PARTIAL (reduced)' if skipped else 'PASS'} -> {len(visible_tools(s)) + len(META_TOOLS)} tools visible")
    result = {"loaded": name, "instructions": skill["instructions"], "required_inputs": skill["inputs"],
              "output_format": skill["output"], "confidence": "high (tested skill)"}
    if skipped:
        alts = {t: skill.get("fallback", {}).get(t) for t in skipped}
        result["confidence"] = "medium (reduced skill)"
        result["limitations"] = (f"User may not use {', '.join(skipped)}. Use instead: "
                                 f"{', '.join(f'{k} -> {v}' for k, v in alts.items() if v) or 'nothing'}. "
                                 "Tell the user the answer is limited and why.")
    return result


@mcp.tool(description="Fallback when NO skill fits: load a task-based profile and plan within its tools. "
                      f"Results are lower confidence. Profiles:\n{profile_menu}")
async def load_profile(name: str, user_request: str, ctx: Context) -> dict:
    s = session(ctx)
    profile = CATALOG["profiles"].get(name)
    if not profile:
        return {"error": f"Unknown profile '{name}'."}
    usable = [t for t in profile["tools"] if allowed(t)]
    with open(LOG_DIR / "unmatched.jsonl", "a") as f:  # Level 3: review recurring requests for new skills
        f.write(json.dumps({"ts": time.time(), "user": USER_ID, "profile": name, "request": user_request}) + "\n")
    log("UNMATCHED", f"no skill for '{user_request}' -> logged for skill review")
    trace("log_unmatched", "pass", text=f"Logged for skill review: \"{user_request}\"")
    if not usable:
        log("BLOCK", f"load_profile({name}) has no tools this user may use")
        trace("entitle", "fail", text="Entitlement filter: no usable tools in this profile")
        return {"loaded": False, "reason": "User has no permissions in this profile; explain the limitation."}
    trace("entitle", "pass", text=f"Entitlement filter: {len(profile['tools']) - len(usable)} of {len(profile['tools'])} profile tools removed")
    await swap_tools(ctx, s, f"profile:{name}", usable)
    trace("swap", "pass", text=f"Tool list swapped to Core + profile {name}; client notified (list_changed)")
    log("LOAD", f"load_profile({name}) -> {len(visible_tools(s)) + len(META_TOOLS)} tools visible ({len(profile['tools']) - len(usable)} filtered by entitlements)")
    return {"loaded": name, "confidence": "lower (no tested skill); writes still need confirmation"}


@mcp.tool(description="Execute a write that was previewed, ONLY after the user explicitly said yes.")
async def confirm_action(token: str, ctx: Context) -> dict:
    pending = session(ctx)["pending"].pop(token, None)
    if not pending:
        log("BLOCK", f"confirm_action({token}) rejected: no matching preview")
        trace("token", "fail", text=f"Token '{token}' rejected: no matching preview")
        return {"error": "No pending action for this token. Writes must be previewed first."}
    log("CONFIRM", f"{pending['tool']} executed after user confirmation")
    trace("token", "pass", text=f"Token '{token}' matches the held preview")
    trace("execute", "pass", text=f"{pending['tool']} executed on Qlik API (mock)")
    return {"executed": pending["tool"], "result": mocks.respond(pending["tool"], pending["args"])}


# ---------------------------------------------------------------- enforcement (the server, not the LLM)
class Enforcement(Middleware):
    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        s = session(context.fastmcp_context)
        keep = set(visible_tools(s)) | META_TOOLS
        result = [t for t in tools if t.name in keep]
        log("LIST", f"tools/list -> {len(result)} of {len(tools)} registered  [{s['active'] or 'core only'}]")
        trace(None, "tools", visible=[t.name for t in result], total=len(REGISTRY), active=s["active"] or "Core only")
        return result

    async def on_call_tool(self, context, call_next):
        name, args = context.message.name, context.message.arguments or {}
        if name in META_TOOLS:
            return await call_next(context)
        s = session(context.fastmcp_context)
        if name not in visible_tools(s):
            log("BLOCK", f"{name} rejected: not in this session's tool list")
            trace("recheck", "fail", text=f"{name} rejected: not loaded in this session")
            return ToolResult(structured_content={"error": f"{name} is not loaded for this session."})
        if not allowed(name):  # per-call permission re-check
            log("BLOCK", f"{name} rejected: no access")
            trace("recheck", "fail", text=f"{name} rejected: no access")
            return ToolResult(structured_content={"error": "no access"})
        if REGISTRY[name]["side_effect"] != "read":  # preview -> confirm, enforced server-side
            token = uuid.uuid4().hex[:6]
            s["pending"][token] = {"tool": name, "args": args}
            trace("recheck", "pass", text=f"{name}: loaded and permitted")
            log("PREVIEW", f"{name} ({REGISTRY[name]['side_effect']}) held for confirmation, token={token}")
            trace("hold", "pass", text=f"{name} is a {REGISTRY[name]['side_effect']}: held as preview, token {token}")
            return ToolResult(structured_content={"preview": {"tool": name, "args": args}, "token": token,
                                                  "next": "Show this to the user. Call confirm_action only if they say yes."})
        log("CALL", f"{name}({json.dumps(args)[:70]})")
        trace("recheck", "pass", text=f"{name}: loaded and permitted")
        trace("execute", "pass", text=f"{name} executed on Qlik API (mock)")
        return await call_next(context)


mcp.add_middleware(Enforcement())
trace("signin", "pass", user=USER["display"], text=f"Signed in as {USER['display']}: Core tools + loader; "
      f"skills offered: {', '.join(ENTITLED_SKILLS) or 'none'}")
log("SESSION", f"server up for {USER['display']}; entitlements={sorted(ENTITLEMENTS)}; "
               f"skills offered={list(ENTITLED_SKILLS) or 'none'}")

if __name__ == "__main__":
    mcp.run(show_banner=False)
