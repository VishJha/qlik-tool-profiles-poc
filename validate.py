"""Governance checks, meant to run in CI on every change to the registry, profiles or skills.

  python validate.py               check the real config (should pass)
  python validate.py --broken      inject entries modelled on real issues in Qlik's published tool docs
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
VERBS = {"search", "describe", "get", "list", "create", "update", "delete", "start", "cancel", "select", "clear", "add"}
NAME = re.compile(r"^qlik_([a-z]+)_[a-z_]+$|^qlik_search$")
READ_VERBS = {"search", "describe", "get", "list"}
ACTION_WORDS = {"stop", "delete", "create", "update", "submit", "start", "cancel", "activate", "deactivate", "remove", "replace"}
SIDE_EFFECTS = {"read", "write", "destructive"}
STATUSES = {"preview", "ga", "deprecated"}


def check(registry: list[dict], catalog: dict) -> list[str]:
    errors, tools = [], {t["name"]: t for t in registry}
    descriptions = {}
    for t in registry:
        n = t["name"]
        m = NAME.match(n)
        if not m or (m.group(1) and m.group(1) not in VERBS):
            errors.append(f"naming: '{n}' must be lowercase qlik_<verb>_<object> with a verb from {sorted(VERBS)}")
        parts = n.split("_")
        if len(parts) > 2 and parts[1] in VERBS and parts[2] in ACTION_WORDS:
            errors.append(f"naming: '{n}' has two verbs; use one, e.g. 'qlik_{parts[2]}_{'_'.join(parts[3:])}'")
        first = (t.get("description") or "").split(" ")[0].lower()
        if len(parts) > 1 and parts[1] in READ_VERBS and first in ACTION_WORDS:
            errors.append(f"description: '{n}' is named as a read but described as '{first}...' (swapped description?)")
        for field in ("description", "owner", "permission", "side_effect", "status"):
            if not t.get(field):
                errors.append(f"metadata: '{n}' is missing '{field}'")
        if t.get("side_effect") not in SIDE_EFFECTS:
            errors.append(f"metadata: '{n}' has invalid side_effect '{t.get('side_effect')}'")
        if t.get("status") not in STATUSES:
            errors.append(f"metadata: '{n}' has invalid status '{t.get('status')}'")
        d = (t.get("description") or "").strip().lower()
        if len(d) < 25:
            errors.append(f"description: '{n}' is too short to guide tool selection")
        if d in descriptions:
            errors.append(f"description: '{n}' duplicates '{descriptions[d]}' (the model cannot tell them apart)")
        descriptions[d] = n

    def refs(where: str, names: list[str]):
        for n in names:
            if n not in tools:
                errors.append(f"reference: {where} lists unknown tool '{n}'")
            elif tools[n].get("status") == "deprecated":
                errors.append(f"reference: {where} still lists deprecated tool '{n}'")

    refs("core", catalog["core"])
    if any(tools.get(n, {}).get("side_effect") != "read" for n in catalog["core"]):
        errors.append("core: must contain read-only tools only")
    placed = set(catalog["core"])
    for p, spec in catalog["profiles"].items():
        refs(f"profile '{p}'", spec["tools"])
        placed |= set(spec["tools"])
        if len(spec["tools"]) + len(catalog["core"]) > catalog["profile_size_cap"]:
            errors.append(f"size: profile '{p}' + core exceeds cap of {catalog['profile_size_cap']}")
        if not spec.get("owner"):
            errors.append(f"ownership: profile '{p}' has no owner")
    for k, spec in catalog["skills"].items():
        refs(f"skill '{k}'", spec["tools"])
        for field in ("owner", "instructions", "output"):
            if not spec.get(field):
                errors.append(f"skill: '{k}' is missing '{field}'")
    for n, t in tools.items():
        if n not in placed and t.get("status") != "deprecated":
            errors.append(f"orphan: '{n}' is in no profile or core, so no user can ever reach it")
    return errors


if __name__ == "__main__":
    registry = yaml.safe_load((ROOT / "config/registry.yaml").read_text())["tools"]
    catalog = yaml.safe_load((ROOT / "config/catalog.yaml").read_text())
    if "--broken" in sys.argv:
        # Modelled on issues visible in Qlik's published MCP tool docs.
        registry += [
            {"name": "Qlik_list_dimensionst", "description": "List all library dimensions available in the app.",
             "side_effect": "read", "permission": "apps.read", "owner": "analytics-engine", "status": "ga"},
            {"name": "qlik_update_activate_data_product", "description": "Activate a data product in a specific space.",
             "side_effect": "write", "permission": "dataproducts.manage", "owner": "", "status": "ga"},
            {"name": "qlik_get_automation_run", "description": "Stop a running automation run immediately.",
             "side_effect": "read", "permission": "automations.run", "owner": "automation", "status": "ga"},
        ]
        catalog["skills"]["investigate_metric_change"]["tools"].append("qlik_get_measure_list")
    errors = check(registry, catalog)
    print(f"Checked {len(registry)} tools, {len(catalog['profiles'])} profiles, {len(catalog['skills'])} skills.")
    for e in errors:
        print(f"  \033[31mFAIL\033[0m {e}")
    if errors:
        sys.exit(1)
    print("  \033[32mPASS\033[0m all governance checks")
