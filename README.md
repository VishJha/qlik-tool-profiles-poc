# Qlik Tool Profiles + Skills: Proof of Concept

A working MCP server that shows how to stop handing an LLM 100+ tools at once. Each session starts with a small, shared, read-only **Core** set. When a task needs more, the model loads a **tested skill** (or, if none fits, a **task-based profile**), and the server swaps the tool list mid-session after checking the user's **entitlements**.

The Qlik backend is mocked. The design is real: protocol, sessions, tool swapping, permission checks, write confirmation and governance checks all run.

## Run it

Requires **Python 3.10 or newer** (tested on 3.13 and on 3.15 on macOS).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python demo_client.py story --view   # the 2-minute demo: one user, every path, ending in success
python demo_client.py --view   # all five scenes, live in the browser
python demo_client.py          # same four scenes in the terminal (or: python demo_client.py 2)
python validate.py             # governance checks pass
python validate.py --broken    # ...and catch real-world mistakes
```

> **macOS note:** the `python3` that ships with macOS is 3.9, and pip will report *"No matching distribution found for fastmcp"*. Install a newer Python from [python.org](https://www.python.org/downloads/) or with `brew install python@3.12`, then create the venv with that version (e.g. `python3.12 -m venv .venv`).

### The live view

`--view` serves a local page at `http://localhost:8765` and streams every event from the real run into it:

- **Left: the decision graph.** One graph for the whole design. Circles are what the user sees, squares are the model's choices, hexagons are checks the server enforces. Each scene lights its own path in green; blocked steps turn red.
- **Right: the conversation**, with a meter of which tools the model can currently see (newly loaded tools are highlighted).
- **Bottom right: the server's decisions** (feasibility, entitlement filter, tool swap, permission re-check, held writes, token checks).

Options: `--step` advances one step each time you press Enter (good for narrating a recording); `DEMO_PACE=5 python demo_client.py --view` slows it down further (seconds per step; default 3.2), and `DEMO_PACE=1.5` speeds it up.

Every run is saved to `viewer/last_run.js`. **Double-click `viewer/index.html` to replay it offline** with play, pause, step, speed and scene buttons. The repo includes a recorded run, so the replay works before you install anything.

## The 2-minute story

`python demo_client.py story --view` follows one user, Rachelle, through a single session that touches every path and ends in success:

1. **Sign in:** she sees 11 of 25 tools (Core plus the loaders).
2. **"Can you look into sales?"** The request is vague, so the AI asks one clarifying question.
3. **"Why did North revenue drop?"** The metric skill runs in **reduced** form: she can't run custom calculations, so it uses an existing chart and says the answer is limited.
4. **"Reload the sales app."** **Stopped:** a required step needs reload permission and the task changes data, so nothing is attempted.
5. **"Add a glossary term for ARR."** No skill fits, so it **falls back** to the Govern Data profile, with "delete data product" filtered out. The request is logged for review.
6. **The write is held** as a preview until she says yes, then the term is created. **Success.**

## What the demo shows

| Scene | User | What happens |
| --- | --- | --- |
| 1 | Analyst | Signs in and sees 11 of 25 tools. Asks why North revenue dropped; the model loads `investigate_metric_change`, the list swaps, and the skill's guardrail (verify `North` vs `NORTH` before filtering) is followed. |
| 2 | Analyst | No glossary skill exists, so the model falls back to the `govern_data` profile. The request is logged for skill review. The write is held as a preview; a made-up confirmation token is rejected; the real one executes. |
| 3 | Junior viewer | The skill menu is empty for this user. Asking for a reload fails the feasibility check with a clear reason; reload tools were never visible. |
| 4 | Developer | Runs `reload_and_verify`: preview, confirm, reload fails, and the log line is returned. |
| 5 | Junior viewer | Asks why North revenue dropped. Sam has the skill's required tools but not its optional calculation step, so the skill runs in reduced form from an existing chart and says the answer is limited. |

The model's *choices* are scripted so every run is identical; everything the *server* does in response is real.

## How it maps to the proposal

| Proposal section | Where it lives |
| --- | --- |
| Profiles (task-based, size-capped) | `config/catalog.yaml` → `profiles` |
| Effective tools = (Core + active skill or profile) ∩ entitlements | `server.py` → `visible_tools`, `Enforcement.on_list_tools` |
| Loader (menu + switch), feasibility check, mid-session swap | `server.py` → `load_skill`, `load_profile`, `swap_tools` (sends `notifications/tools/list_changed`) |
| Skills constrain tools, order, guardrails, inputs, output | `config/catalog.yaml` → `skills` |
| Required vs optional steps: full, reduced or blocked skill; read-only tasks fall back to a profile, data-changing tasks stop | `server.py` → `load_skill`; `optional`, `fallback`, `profile` in each skill |
| Server enforces, LLM chooses | `Enforcement.on_call_tool`: per-call permission re-check, preview then `confirm_action` for writes |
| Unmatched tasks (Level 2 to Level 3) | `load_profile` writes `logs/unmatched.jsonl` |
| Governance (registry, naming, ownership, orphans, size cap) | `config/registry.yaml`, `validate.py` |

## Real vs mocked

| Real | Mocked |
| --- | --- |
| MCP server and protocol (via [FastMCP](https://gofastmcp.com)) | Qlik API responses (`mocks.py`) |
| Session tool list, swap, `list_changed` | Users and entitlements (`config/users.yaml`, standing in for Qlik roles) |
| Feasibility, permission re-check, preview/confirm | Sign-in (user chosen with `QLIK_USER`) |
| Governance validator, unmatched-request log | The LLM (`demo_client.py` plays its role, so the demo is deterministic) |
| Live view and replay (`viewer/index.html`, fed by `logs/trace.jsonl`) | |

Because the backend sits behind one function (`mocks.respond`), this layer could run as a **gateway in front of Qlik's MCP server**: replace the mock with real calls and nothing in the profile/skill layer changes.

## Prior art, and what this adds

The on-demand loading pattern is proven: [GitHub's MCP server](https://github.com/github/github-mcp-server) ships "dynamic toolsets" (`list_available_toolsets`, `enable_toolset`) and static `--toolsets`. FastMCP provides per-session visibility as plumbing. This PoC builds on that pattern and adds what Qlik needs:

- **Entitlement-aware loading:** the skill menu only lists skills the user can fully run, and loading checks feasibility first.
- **Skills, not just tool groups:** each skill carries instructions, guardrails, required inputs and an output shape.
- **Server-enforced write safety:** writes return a preview; only a matching token executes them.
- **Governance as code:** naming, ownership, orphan, reference and description checks, runnable in CI.
- **A feedback loop:** profile fallbacks are logged as candidates for new skills.

## Known limitations

- Session state is per server process, which matches stdio (one client per process). Over HTTP it would key on the `Mcp-Session-Id` header.
- Clients must handle `notifications/tools/list_changed`; clients that do not would need to reconnect after a load.
- Qlik's docs say new tools appear after re-authenticating. We assume this refers to catalog additions, not per-session swaps; to be confirmed.
- No evals yet. Next step: run the same golden tasks with a real model against the full catalog, profiles, and skills, and compare task success and consistency.
