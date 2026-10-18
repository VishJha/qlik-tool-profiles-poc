"""Scripted MCP client that plays the role of the LLM, so the PoC runs anywhere with Python.

It speaks real MCP to server.py over stdio. The model's *choices* are scripted (so every run is the
same); everything the *server* does in response is real.

  python demo_client.py            all scenes in the terminal
  python demo_client.py 2          one scene
  python demo_client.py story --view   the 2-minute demo: one user, every path, ending in success
  python demo_client.py --view     opens a browser: decision graph + conversation, live as it runs
  python demo_client.py --view --step   same, but press Enter in the terminal to advance each step

Every run is saved to viewer/last_run.js, so viewer/index.html can replay it offline (double-click it).
"""
import asyncio
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastmcp import Client
from fastmcp.client.messages import MessageHandler
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).parent
TRACE = ROOT / "logs/trace.jsonl"
B, DIM, GRN, RED, YEL, OFF = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"
VIEW = "--view" in sys.argv
STEP = "--step" in sys.argv
PACE = float(os.environ.get("DEMO_PACE", "3.2")) if VIEW else 0.0
PORT = int(os.environ.get("DEMO_PORT", "8765"))


# ---------------------------------------------------------------- trace + pacing
def trace(node, kind, **data):
    with open(TRACE, "a") as f:
        f.write(json.dumps({"ts": time.time(), "src": "client", "node": node, "kind": kind, **data}) + "\n")


async def pause(factor: float = 1.0):
    if STEP:
        await asyncio.to_thread(input, f"   {DIM}[Enter for next step]{OFF}")
    elif PACE:
        await asyncio.sleep(PACE * factor)


class Notifications(MessageHandler):
    async def on_tool_list_changed(self, message):
        print(f"   {YEL}<- server notification: tools/list_changed (client re-fetches){OFF}")


def connect(user: str) -> Client:
    env = {**os.environ, "QLIK_USER": user, "QLIK_TRACE": str(TRACE)}
    transport = StdioTransport(sys.executable, [str(ROOT / "server.py")], env=env, cwd=str(ROOT),
                               log_file=ROOT / "logs/server.stderr")
    return Client(transport, message_handler=Notifications(), cache=False)


async def scene(n, title, user):
    print(f"\n{B}=== {'Story' if n == 'S' else f'Scene {n}'}: {title} ==={OFF}")
    trace(None, "scene", n=n, title=title, user=user)
    await pause(0.8)


async def say(text, node="request"):
    print(f"\n{B}USER:{OFF} {text}")
    trace(node, "user", text=text)
    await pause()


async def decide(text):
    print(f"   {DIM}model ->{OFF} {text}")
    trace("pick", "decide", text=text)
    await pause()


async def note(text):
    print(f"   {DIM}model ->{OFF} {text}")
    trace(None, "note", text=text)
    await pause(0.6)


async def finish(text, ok=True):
    print(f"   {GRN if ok else RED}{'answer' if ok else 'explains'}: {text}{OFF}")
    trace("answer" if ok else "blocked", "answer", text=text, ok=ok)
    await pause()


async def show_tools(c: Client, note_text=""):
    tools = [t.name for t in await c.list_tools()]
    print(f"   {B}model sees {len(tools)} tools{OFF} {note_text}")
    print(f"   {DIM}{', '.join(tools)}{OFF}")


async def call(c: Client, tool: str, **args):
    node = {"load_skill": "load_skill", "load_profile": "load_profile", "confirm_action": None}.get(tool, "call")
    print(f"   {DIM}model ->{OFF} {tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
    trace(node, "call", tool=tool, args=args)
    await pause(0.7)
    res = await c.call_tool(tool, args, raise_on_error=False)
    data = res.structured_content or {}
    ok = not ("error" in data or data.get("loaded") is False)
    print(f"   {GRN if ok else RED}<- {data}{OFF}")
    trace(None, "result", tool=tool, data=data, ok=ok)
    await pause()
    return data


# ---------------------------------------------------------------- scenes
async def scene_1():
    await scene(1, "A request a tested skill covers", "Tom, Business Analyst")
    async with connect("analyst") as c:
        await show_tools(c, "(shared Core read-only kit + loader; the other 14 tools stay on the server)")
        await say("Why did North revenue drop last month?")
        await decide("a tested skill fits: investigate_metric_change")
        await call(c, "load_skill", name="investigate_metric_change")
        await show_tools(c, "(Core + skill tools)")
        await call(c, "qlik_search_field_values", app_id="sales_app", field="Region", search="North")
        await note("value is stored as 'NORTH': verified before filtering, as the skill requires")
        await call(c, "qlik_create_data_object", app_id="sales_app", dimensions="Month",
                   measures="Sum({<Region={'NORTH'}>} Revenue)")
        await call(c, "qlik_create_data_object", app_id="sales_app", dimensions="ProductCategory",
                   measures="Sum({<Region={'NORTH'},Month={'2026-09'}>} Revenue)-Sum({<Region={'NORTH'},Month={'2026-08'}>} Revenue)")
        await finish("North revenue fell 10.3% (2.03M to 1.82M). Top driver: Cloud Services, down 184k.")


async def scene_2():
    await scene(2, "No skill matches: profile fallback, and a write needs confirmation", "Tom, Business Analyst")
    async with connect("analyst") as c:
        await say("Add a glossary term for Annual Recurring Revenue.")
        await decide("tries a glossary tool directly, without loading anything")
        await call(c, "qlik_create_glossary_term", glossary_id="sales", term="ARR", definition="...")
        await decide("no glossary skill exists: fall back to the closest profile")
        await call(c, "load_profile", name="govern_data", user_request="Add a glossary term for ARR")
        await show_tools(c, "(delete_data_product filtered out: analyst lacks dataproducts.manage)")
        data = await call(c, "qlik_create_glossary_term", glossary_id="sales", term="ARR",
                          definition="Yearly value of recurring subscriptions")
        await note("tries to skip the user and confirm with a made-up token")
        await call(c, "confirm_action", token="abc123")
        await say("Yes, create it.", node="confirm")
        await call(c, "confirm_action", token=data["token"])
        await finish("Created glossary term 'ARR' as a draft (lower confidence: profile path, no tested skill).")


async def scene_3():
    await scene(3, "A user asks for something they are not entitled to", "Sam, Junior Viewer")
    async with connect("viewer") as c:
        await show_tools(c, "(load_skill offers only skills whose required tools Sam may use)")
        await say("Reload the inventory app.")
        await decide("the reload skill fits the request")
        await call(c, "load_skill", name="reload_and_verify")
        await finish("You don't have permission to run reloads (reload.run). Ask an app developer or admin.", ok=False)


async def scene_4():
    await scene(4, "Developer runs the reload skill; the failure path returns the log", "Arjun, App Developer")
    async with connect("developer") as c:
        await say("Reload the inventory app and tell me if it worked.")
        await decide("a tested skill fits: reload_and_verify")
        await call(c, "load_skill", name="reload_and_verify")
        await show_tools(c)
        data = await call(c, "qlik_start_app_reload", app_id="inventory_app")
        await say("Yes, go ahead.", node="confirm")
        await call(c, "confirm_action", token=data["token"])
        await call(c, "qlik_get_reload_status", reload_id="rl_inventory_app")
        await call(c, "qlik_get_reload_log", reload_id="rl_inventory_app")
        await finish("Reload FAILED at line 42: field 'SKU_Code' is missing from table Stock. Rename or add the field.")


async def scene_5():
    await scene(5, "A skill is partly blocked: it runs in reduced form", "Sam, Junior Viewer")
    async with connect("viewer") as c:
        await show_tools(c, "(the metric skill is offered: Sam has its required tools, not its optional one)")
        await say("Why did North revenue drop last month?")
        await decide("a tested skill fits: investigate_metric_change")
        await call(c, "load_skill", name="investigate_metric_change")
        await show_tools(c, "(no create_data_object; an existing chart is used instead)")
        await call(c, "qlik_search_field_values", app_id="sales_app", field="Region", search="North")
        await call(c, "qlik_get_chart_data", app_id="sales_app", chart_id="c1")
        await finish("From the existing Revenue by Region chart, North is at 1.82M, the lowest region. "
                     "You can't run custom calculations, so I can't break down why; ask an admin for query access.")


async def scene_story():
    """One user, one session, every path, ending in success. Built for a 2-minute demo."""
    await scene("S", "One session, every path: clarify, reduced skill, stop, profile fallback, approved change",
                "Rachelle, Business Analyst")
    async with connect("rachelle") as c:
        await show_tools(c, "(Core + loaders; her skill menu only lists skills she can run)")

        await say("Can you look into sales?")
        await note("the request is vague: ask one clarifying question instead of guessing")
        print(f"   {B}AI:{OFF} What would you like to know about sales: a trend, a region, a product?")
        trace(None, "note", text="AI asks: What would you like to know about sales: a trend, a region, a product?")
        await pause()

        await say("Why did North revenue drop last month?")
        await decide("a tested skill fits: investigate_metric_change")
        await call(c, "load_skill", name="investigate_metric_change")
        await show_tools(c, "(reduced: no custom calculation tool; an existing chart is used instead)")
        await call(c, "qlik_search_field_values", app_id="sales_app", field="Region", search="North")
        await call(c, "qlik_get_chart_data", app_id="sales_app", chart_id="c1")
        await finish("From the existing Revenue by Region chart, North is at 1.82M, the lowest region. "
                     "You can't run custom calculations, so this answer is limited.")

        await say("Then reload the sales app so the numbers are fresh.")
        await decide("the reload skill fits the request")
        await call(c, "load_skill", name="reload_and_verify")
        await finish("You don't have permission to run reloads, so I haven't attempted it. "
                     "An app developer or admin can run it for you.", ok=False)

        await say("OK, add a glossary term for ARR so the team uses one definition.")
        await decide("no tested skill for glossary work: fall back to the Govern Data profile")
        await call(c, "load_profile", name="govern_data", user_request="Add a glossary term for ARR")
        await show_tools(c, "(delete_data_product filtered out: Rachelle lacks that permission)")
        data = await call(c, "qlik_create_glossary_term", glossary_id="sales", term="ARR",
                          definition="Annual Recurring Revenue: yearly value of active subscriptions")
        await say("Yes, create it.", node="confirm")
        await call(c, "confirm_action", token=data["token"])
        await finish("Done: the glossary term 'ARR' is created as a draft, ready for a steward to verify.")


SCENES = {"1": scene_1, "2": scene_2, "3": scene_3, "4": scene_4, "5": scene_5, "story": scene_story}


# ---------------------------------------------------------------- live viewer (local page + event stream)
CONNECTED = threading.Event()


class ViewerHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the terminal clean
        pass

    def do_GET(self):
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            CONNECTED.set()
            try:
                with open(TRACE) as f:
                    while True:
                        line = f.readline()
                        if line:
                            self.wfile.write(f"data: {line.strip()}\n\n".encode())
                            self.wfile.flush()
                        else:
                            time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                return
        path = ROOT / "viewer" / ("index.html" if self.path in ("/", "/index.html") else self.path.lstrip("/"))
        if path.is_file() and path.parent == ROOT / "viewer":
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if path.suffix == ".html" else "text/javascript")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def start_viewer():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), ViewerHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{PORT}/"
    print(f"{B}Live view:{OFF} {url}  (opening your browser...)")
    webbrowser.open(url)
    if not CONNECTED.wait(timeout=20):
        print(f"{YEL}Browser did not connect yet; open {url} manually. Continuing.{OFF}")
    time.sleep(1.0)


def export_replay():
    events = [json.loads(l) for l in TRACE.read_text().splitlines() if l.strip()]
    (ROOT / "viewer/last_run.js").write_text("window.TRACE = " + json.dumps(events) + ";\n")


if __name__ == "__main__":
    logging.disable(logging.WARNING)  # hide client-side schema warnings; the server log is the real record
    (ROOT / "logs").mkdir(exist_ok=True)
    TRACE.write_text("")
    if VIEW:
        start_viewer()
    chosen = [a for a in sys.argv[1:] if not a.startswith("--")] or [k for k in SCENES if k != "story"]
    for k in chosen:
        asyncio.run(SCENES[k]())
    trace(None, "end")
    export_replay()
    print(f"\n{DIM}Saved this run for replay: open viewer/index.html{OFF}")
    if VIEW:
        input(f"{B}Done. Press Enter to stop the live view.{OFF}")
