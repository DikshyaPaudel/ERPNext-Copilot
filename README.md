# erpnext_copilot

> Ask your ERPNext site to do things, in plain English. A self-built Gemini-powered agent for ERPNext, running as a real installed Frappe app.

!
## What you get

Type things you'd normally click through ERPNext's UI for:

- "Show me sales invoices grouped by customer."
- "Create a doctype called Vehicle Maintenance Log with fields Vehicle, Date, Cost."
- "Build a bar chart of sales invoices by customer."
- "Preview the file I just uploaded before I import it."

Gemini decides which tool to call and with what arguments; the tool runs
as **your actual logged-in ERPNext user**, so permissions are enforced the
same way they would be if you clicked through the UI yourself — no shared
API key, no bypassing ERPNext's own access rules.

Anything that changes data (creating a DocType, creating a chart) stops
and asks for confirmation first — in the browser as a real dialog, or in
the console as a y/n prompt — showing exactly what it's about to do
before it does it.

## Screenshots


![Chat interface](docs/screenshots/1st.png)
![Chat interface asking about doctype generation](docs/screenshots/1st.png)

![Browser confirmation dialog before creating a DocType](docs/screenshots/confirm.png)
![Successful creation](docs/screenshots/4th.png)
## Quick start

```bash
# from your bench directory
bench get-app <this-repo-url>
bench --site your-site install-app erpnext_copilot

# get a free key from https://aistudio.google.com/apikey
bench --site your-site set-config gemini_api_key "your-key"
```

**Console:**
```bash
bench --site your-site console
```
```python
from erpnext_copilot.custom_code.gemini_agent import run
run()
```

**Web chat UI:** visit `/app/copilot_chat` on your site.

## Tools at a glance

| Category | Tools |
|---|---|
| Search & fetch | `search_documents`, `search_doctype`, `fetch`, `search` |
| Aggregation | `aggregate_documents`, `list_invoices` |
| Schema | `get_doctype_fields` |
| Files | `read_uploaded_file` |
| Create (confirmed before running) | `create_doctype`, `create_dashboard_chart` |

Search and aggregation tools work against **any DocType** — they take a
`doctype` parameter rather than being hardcoded per document type, and
validate it against Frappe's real metadata before building a query.

## Architecture

```
User message (console or web chat)
        │
        ▼
Gemini — decides which tool to call, in a loop, so a single
message like "show X, then create Y" can chain multiple calls
        │
        ▼
Tool dispatch → api.py (@frappe.whitelist() methods)
        │
        ▼
frappe.get_all() / frappe.get_doc() / frappe.db.sql()
        │   runs as the real logged-in user — permissions
        │   enforced natively by Frappe, not by this app
        ▼
ERPNext database
```

Two front ends share the same tool layer: `gemini_agent.py` (console
REPL) and `agent_api.py` (whitelisted HTTP endpoints for the web chat
page, with conversation state kept server-side per user since HTTP has no
persistent process the way a console session does).

<!-- ## Relationship to prior art

ERPNext + LLM agents isn't a new idea. Before building this, I looked at
what already exists — most notably
[**Frappe_Assistant_Core**](https://github.com/buildswithpaul/Frappe_Assistant_Core),
a production-grade project with OAuth2/PKCE per-user auth, 24 tools, a
plugin system, and commercial support. This project doesn't try to
compete with that — it's smaller, self-built, and exists so I'd have
hands-on experience with the actual pattern (tool-calling against a real
ERP's permission model) rather than just being able to
describe it.

Where the design overlaps with FAC — generic, DocType-agnostic tools
(`search_documents`, `aggregate_documents`) instead of one function per
document type — I arrived at it independently and only confirmed the
overlap afterward by reading their code. Where it differs: FAC
authenticates external clients (Claude Desktop, ChatGPT) via OAuth so
*they* can act as a real user over the network; this project runs
in-process inside Frappe, so permission enforcement comes from already
being the logged-in session user, which is a different, considerably
smaller problem to solve.

<!-- For the full list of real bugs hit and fixed while building this — SDK
quirks, ERPNext validation surprises, a Gemini 3.x API requirement that
only showed up in the web interface — see
[`DEBUGGING.md`](./DEBUGGING.md). Kept deliberately, not smoothed over. --> -->

## Known limitations

- `read_uploaded_file` previews CSV/Excel files but doesn't import them
  yet — column mapping and cleaning are a planned next step.
- Only tested as Administrator so far — multi-user/role-based permission
  behavior hasn't been verified end-to-end.
- No automated test suite yet; verification has been manual, tool-by-tool.
- For a user who already knows ERPNext's Report Builder and filters,
  typing a sentence to reproduce a filtered list is often *slower* than
  just applying the filter — this tool's real value is in write and
  multi-step operations, not simple reads.

## License

MIT (or update to whatever you prefer)