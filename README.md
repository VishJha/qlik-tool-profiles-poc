# Tool Profiles and Skills for the Qlik MCP Server

A working proof of concept that accompanies the presentation *Tool Profiles and Skills for the Qlik MCP Server*. If you have seen the slides, this repo lets you run the solution yourself and see where each idea lives in the code.

---

The Qlik MCP server exposes 100+ tools. An AI choosing from that many tools picks the wrong one more often, and permissions alone don't fix it, because a power user is allowed almost everything. This PoC shows a different approach: the AI starts with a small set of read-only tools, loads a **tested skill** when a task needs more, falls back to a **task-based profile** when no skill fits, and never sees the full catalog. The server filters every tool list by the user's existing permissions, checks every call again, and holds any change to data until the user approves it.

---

## Quick start (about 5 minutes)

**Requirements:** Python 3.10 or newer. Tested on macOS with Python 3.13 and 3.15.

```bash
git clone https://github.com/VishJha/qlik-tool-profiles-poc.git
cd qlik-tool-profiles-poc
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Run the 2-minute demo** (opens your browser):

```bash
python demo_client.py story --view
```

Add `--step` to advance one step at a time by pressing Enter, which is useful when you want to explain as you go.

**No install at all?** Double-click `viewer/index.html`. It replays a recorded run of every scenario, with play, pause, step and scene buttons.

> **macOS note:** the `python3` that ships with macOS is 3.9, and pip will report *"No matching distribution found for fastmcp."* Install a newer Python from [python.org](https://www.python.org/downloads/) or with `brew install python@3.12`, then create the environment with it (for example `python3.12 -m venv .venv`).

---

## What you will see

The browser shows three panels:

| Panel | What it shows |
| --- | --- |
| **Left: decision graph** | The path each request takes. Circles are what the user sees, squares are the AI's choices, hexagons are checks the server enforces. Green means passed, amber means limited, red means stopped. |
| **Right: conversation** | The user's messages, the AI's tool calls and the server's replies, plus a meter of how many tools the AI can currently see. |
| **Bottom right: server log** | Every decision the server makes: permission checks, tool swaps, held changes, approvals. |

### The Demo Sceanrio

One user, **Rachelle**, a business analyst who can read apps and edit the glossary but can't run custom calculations or reloads. In one session she touches every path:

| # | Rachelle asks | What happens | Path |
| --- | --- | --- | --- |
| 1 | *(signs in)* | The AI sees 11 of 25 tools: Core tools plus the loaders. | Starting kit |
| 2 | "Can you look into sales?" | Too vague, so the AI asks one clarifying question. | Clarify |
| 3 | "Why did North revenue drop last month?" | A tested skill fits, but one optional step needs a permission she lacks. The skill runs in reduced form from an existing chart and says the answer is limited. | Reduced skill |
| 4 | "Reload the sales app." | A required step needs reload permission and the task changes data, so it stops and explains. | Stop |
| 5 | "Add a glossary term for ARR." | No skill fits, so it falls back to the Govern Data profile, with the delete tool filtered out. The request is logged for review. | Profile fallback |
| 6 | "Yes, create it." | The change was held as a preview; after her approval it runs. | Approved change |

### Other scenarios

```bash
python demo_client.py --view      # all five scenes in the browser
python demo_client.py 3           # one scene in the terminal
```

| Scene | User | Shows |
| --- | --- | --- |
| 1 | Tom, analyst | A skill running with full access |
| 2 | Tom, analyst | Profile fallback, a rejected fake approval, a confirmed change |
| 3 | Sam, junior viewer | A blocked request: the reload tools are never visible |
| 4 | Arjun, developer | A reload that fails, with the error line returned |
| 5 | Sam, junior viewer | A skill running in reduced form |

The AI's **choices are scripted** so every run is identical. Everything the **server** does in response is real.

---

## How the solution works

### The building blocks

| Piece | What it is |
| --- | --- |
| **Tool registry** | One list of every tool, with its owner, required permission, and whether it reads or changes data. |
| **Core tools** | About eight read-only tools every user gets. The same for everyone. |
| **Skills** | Tested recipes for common tasks: which tools, in what order, which checks, and what the answer looks like. Steps are marked required or optional. |
| **Profiles** | Groups of related tools for one kind of work, used when no skill fits. A profile never grants access. |
| **`load_skill` / `load_profile`** | Tools that list what's available to this user and, when called, swap the matching tools in. |
| **Permissions** | The user's existing Qlik roles. Nothing new is created or stored per user. |

### What happens, step by step

1. **Before anyone signs in,** the server loads Qlik's configuration: the registry, Core list, profiles and skills.
2. **At sign-in,** the server looks up the user's permissions once, builds their personal skill menu, and gives the AI the Core tools plus the loaders.
3. **On each request,** the AI loads a skill (or a profile). The server checks permissions, swaps in only the tools the user may use, and tells the AI app the list changed. No reconnect is needed.
4. **On every tool call,** the server checks permission again. Anything that changes data is held as a preview until the user approves it.
5. **When the session ends,** nothing about the user is kept.

### The fallback order

1. **A tested skill fits, with full access:** run it. Most reliable.
2. **A skill fits, but an optional step is missing:** run a reduced version and say what's limited.
3. **A skill fits, but a required step is missing:** if the task only reads data, fall back to a profile; if it changes data, stop and explain.
4. **No skill, and the request is vague:** ask one clarifying question.
5. **No skill, but a profile fits:** load it, filtered to the user's permissions. Marked less certain and logged.
6. **Nothing fits:** answer with read-only Core tools, or explain. Logged.

The AI never falls back to the full catalog, and nothing can exceed the user's own permissions.

---

## Governance checks

```bash
python validate.py            # the real configuration passes
python validate.py --broken   # adds real issues found in Qlik's published tool docs
```

The validator checks naming (`qlik_verb_object` with a fixed verb list), required metadata and owners, descriptions that contradict their names, references to tools that don't exist, profile size limits, and tools that sit in no profile. The `--broken` mode adds three real examples from Qlik's docs (a typo in a tool name, a name with two verbs, and a description that looks swapped) and shows each one being caught. In a real setup, this would run automatically on every change.

---

## Where each idea lives in the code

| Idea | File |
| --- | --- |
| Tools, owners, permissions | `config/registry.yaml` |
| Core list, profiles, skills (with required and optional steps) | `config/catalog.yaml` |
| Demo users and their permissions | `config/users.yaml` |
| Filtering by permission, loading, swapping, re-checks, approval for changes | `server.py` |
| Canned Qlik responses | `mocks.py` |
| The scripted scenarios and the live view server | `demo_client.py` |
| Governance checks | `validate.py` |
| The decision graph and replay | `viewer/index.html` |

---

## What is real and what is mocked

| Real | Mocked |
| --- | --- |
| The MCP server and protocol (built on [FastMCP](https://gofastmcp.com)) | Qlik's API responses (`mocks.py`) |
| Per-session tool lists and mid-session swaps (`tools/list_changed`) | Users and permissions (`config/users.yaml`) |
| Permission filtering, feasibility checks, per-call re-checks | Sign-in (the user is set by the demo) |
| Holding changes until the user approves | The AI model (`demo_client.py` plays its role) |
| Governance validator and the log of unmatched requests | |

Because the Qlik backend sits behind one function, this layer could run in front of Qlik's real MCP server: replace the mock with real calls and nothing in the profile and skill layer changes.

---

## Prior art

On-demand tool loading is already proven: [GitHub's MCP server](https://github.com/github/github-mcp-server) offers "dynamic toolsets." This PoC builds on that pattern and adds what Qlik needs: loading that respects each user's permissions, tested skills with required and optional steps, server-enforced approval for changes, governance as code, and a log that turns unmatched requests into future skills.

---

## Assumptions and open questions

- **Mid-session tool swaps:** we assume the server can change a user's tools during a session. Qlik's docs suggest new tools currently appear only after signing in again, so this needs confirming.
- **Tool-to-permission mapping:** each tool must be mapped to the Qlik permission it needs; only the tool owners can confirm this.
- **App-level permissions:** access can differ by app, so the up-front filter is approximate and the per-call check remains the real safeguard.
- **Client support:** AI apps that don't support the "tool list changed" signal would need to reconnect after loading a skill.
- **Session state:** this PoC keeps state per server process, which fits a local connection. Over HTTP it would be keyed on the MCP session ID.

## Next step

The riskiest assumption is the core one: that a smaller set of tools and tested skills actually makes the AI more accurate and consistent on real Qlik tasks. The next step is to run the same set of realistic tasks with a real model three ways (full catalog, profiles, profiles plus skills) and compare task success and consistency.
