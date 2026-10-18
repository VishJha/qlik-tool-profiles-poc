"""Canned responses standing in for Qlik's APIs. Swap this module for real API calls
(or a proxy to Qlik's MCP server) and nothing in the profile/skill layer changes."""

APPS = [{"id": "sales_app", "name": "Sales Performance"}, {"id": "inventory_app", "name": "Inventory"}]


def respond(tool: str, args: dict) -> dict:
    app = args.get("app_id", "sales_app")
    match tool:
        case "qlik_search":
            return {"results": APPS}
        case "qlik_describe_app":
            return {"id": app, "owner": "finance-team", "published": True, "fields": 14}
        case "qlik_get_fields":
            return {"fields": ["Region", "ProductCategory", "Month", "Revenue", "Margin"]}
        case "qlik_list_sheets":
            return {"sheets": [{"id": "s1", "title": "Revenue Overview"}, {"id": "s2", "title": "Regional Detail"}]}
        case "qlik_get_sheet_details":
            return {"charts": [{"id": "c1", "type": "bar", "title": "Revenue by Region"}]}
        case "qlik_get_field_values":
            return {"field": args.get("field"), "values": ["NORTH", "SOUTH", "EAST", "WEST"]}
        case "qlik_search_field_values":
            s = args.get("search", "").upper()
            hits = [v for v in ["NORTH", "SOUTH", "EAST", "WEST"] if s and s in v]
            return {"field": args.get("field"), "search": args.get("search"), "matches": hits}
        case "qlik_get_chart_data":
            return {"rows": [["NORTH", 1.82e6], ["SOUTH", 2.10e6], ["EAST", 1.55e6], ["WEST", 1.97e6]]}
        case "qlik_create_data_object":
            if "ProductCategory" in args.get("dimensions", ""):
                return {"rows": [["Cloud Services", -184000], ["Hardware", -41000], ["Support", 12000]]}
            return {"rows": [["2026-08", 2.03e6], ["2026-09", 1.82e6]], "change_pct": -10.3}
        case "qlik_select_values" | "qlik_clear_selections":
            return {"current_selections": [] if tool.endswith("clear_selections") else [{args.get("field"): args.get("values")}]}
        case "qlik_get_script":
            return {"script": "LOAD * FROM [lib://data/orders.qvd] (qvd);"}
        case "qlik_start_app_reload":
            return {"reload_id": f"rl_{app}", "status": "queued"}
        case "qlik_get_reload_status":
            failed = "inventory" in args.get("reload_id", "")
            return {"reload_id": args.get("reload_id"), "status": "FAILED" if failed else "SUCCEEDED"}
        case "qlik_get_reload_log":
            return {"error": "Field 'SKU_Code' not found in table Stock (line 42)"}
        case "qlik_search_glossary_terms":
            return {"matches": []}
        case "qlik_get_lineage":
            return {"parents": [{"id": "orders.qvd", "type": "dataset"}]}
        case _:
            return {"ok": True, "tool": tool, "args": args}
