"""
gemini_agent.py — wires ERPNext-facing functions (api.py) to Gemini's
function calling using explicit schemas (not automatic introspection from
Python type hints, which crashed on this SDK version — see project plan
for details).

Meant to be run from inside `bench console`, so it runs with a real Frappe
request context and functions in api.py execute as the logged-in site
user — same permission model as the rest of ERPNext.

Conversation history is managed manually (not via the SDK's Chat object)
because combining `tools=` with a function-response Part in the same call,
via Chat.send_message(), triggers a SDK serialization bug on this version.
Two separate GenerateContentConfig objects are used instead: one with
tools (for the initial/tool-deciding call), one without (for the
follow-up call that turns a tool result into a natural-language reply).
"""

import json
import frappe
from google import genai
from google.genai import types
import erpnext_copilot.custom_code.api as api

# ---------------------------------------------------------------------------
# Tool schemas — defined explicitly rather than auto-inferred from Python
# function signatures.
# ---------------------------------------------------------------------------

LIST_INVOICES_DECLARATION = types.FunctionDeclaration(
    name="list_invoices",
    description="List Sales Invoices from the last N days, respecting the calling user's ERPNext permissions.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "days": types.Schema(type=types.Type.INTEGER, description="How many days back to look. Defaults to 20."),
            "customer": types.Schema(type=types.Type.STRING, description="Optional customer name to filter by."),
            "only_unpaid": types.Schema(type=types.Type.BOOLEAN, description="If true, only return invoices with an outstanding balance."),
        },
        required=[],
    ),
)

AGGREGATE_DOCUMENTS_DECLARATION = types.FunctionDeclaration(
    name="aggregate_documents",
    description="Count (and sum, if a total/amount field exists) any DocType's records, grouped by any field. Works for any DocType — e.g. Sales Invoice grouped by customer, Purchase Order grouped by supplier.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "doctype": types.Schema(type=types.Type.STRING, description="The DocType to aggregate, e.g. 'Sales Invoice'."),
            "group_by": types.Schema(type=types.Type.STRING, description="The field to group by, e.g. 'customer' or 'status'."),
            "days": types.Schema(type=types.Type.INTEGER, description="Optional: only include records from the last N days."),
            "date_field": types.Schema(type=types.Type.STRING, description="Which date field to filter on if 'days' is given. Defaults to 'creation'."),
        },
        required=["doctype", "group_by"],
    ),
)
SEARCH_DOCUMENTS_DECLARATION = types.FunctionDeclaration(
    name="search_documents",
    description="Search any DocType with structured filters, e.g. find Sales Orders for a specific customer.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "doctype": types.Schema(type=types.Type.STRING),
            "filters": types.Schema(type=types.Type.OBJECT, description="Field-value filters, e.g. {'customer': 'Acme'}."),
            "fields": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
            "limit": types.Schema(type=types.Type.INTEGER),
        },
        required=["doctype"],
    ),
)

SEARCH_DOCTYPE_DECLARATION = types.FunctionDeclaration(
    name="search_doctype",
    description="Text search within one DocType by name/title, e.g. find a customer by partial name.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "doctype": types.Schema(type=types.Type.STRING),
            "query": types.Schema(type=types.Type.STRING),
            "limit": types.Schema(type=types.Type.INTEGER),
        },
        required=["doctype", "query"],
    ),
)

FETCH_DECLARATION = types.FunctionDeclaration(
    name="fetch",
    description="Get the full details of one specific document by its ID.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "doctype": types.Schema(type=types.Type.STRING),
            "name": types.Schema(type=types.Type.STRING),
        },
        required=["doctype", "name"],
    ),
)

SEARCH_DECLARATION = types.FunctionDeclaration(
    name="search",
    description="Global search across all DocTypes when you don't know which DocType to look in.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(type=types.Type.STRING),
            "limit": types.Schema(type=types.Type.INTEGER),
        },
        required=["query"],
    ),
)
GET_DOCTYPE_FIELDS_DECLARATION = types.FunctionDeclaration(
    name="get_doctype_fields",
    description="Return the field schema (fieldname, label, fieldtype, required) for a DocType. Use this to check what fields already exist before proposing a new field or DocType, or before querying unfamiliar data.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "doctype": types.Schema(type=types.Type.STRING, description="The DocType to inspect."),
        },
        required=["doctype"],
    ),
)

READ_UPLOADED_FILE_DECLARATION = types.FunctionDeclaration(
    name="read_uploaded_file",
    description="Preview a CSV or Excel file already uploaded to ERPNext (as a File record). Returns columns and sample rows. Does not import anything.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_name": types.Schema(type=types.Type.STRING, description="The File record's name or file_url in ERPNext."),
            "preview_rows": types.Schema(type=types.Type.INTEGER, description="How many rows to preview. Defaults to 10."),
        },
        required=["file_name"],
    ),
)

CREATE_DOCTYPE_DECLARATION = types.FunctionDeclaration(
    name="create_doctype",
    description="Create a brand-new custom DocType with the given fields. Every field needs an explicit fieldtype — never guess it, ask the user if it wasn't specified.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "doctype_name": types.Schema(type=types.Type.STRING, description="Name of the new DocType."),
            "fields": types.Schema(
                type=types.Type.ARRAY,
                description="List of field definitions.",
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "fieldname": types.Schema(type=types.Type.STRING),
                        "label": types.Schema(type=types.Type.STRING),
                        "fieldtype": types.Schema(type=types.Type.STRING, description="e.g. Data, Int, Date, Select, Link, Check"),
                        "reqd": types.Schema(type=types.Type.INTEGER, description="1 if required, 0 otherwise."),
                    },
                ),
            ),
            "module": types.Schema(type=types.Type.STRING, description="Defaults to 'Custom'."),
        },
        required=["doctype_name", "fields"],
    ),
)

CREATE_DASHBOARD_CHART_DECLARATION = types.FunctionDeclaration(
    name="create_dashboard_chart",
    description="Create a native ERPNext Dashboard Chart for any DocType, grouped by any field. Optionally attach it directly to a Dashboard.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "chart_name": types.Schema(type=types.Type.STRING, description="Name for the chart."),
            "document_type": types.Schema(type=types.Type.STRING, description="The DocType to chart, e.g. 'Sales Invoice'."),
            "group_by_based_on": types.Schema(type=types.Type.STRING, description="Field to group by, e.g. 'customer'."),
            "chart_type": types.Schema(type=types.Type.STRING, description="Bar, Line, Pie, Percentage, or Donut. Defaults to Bar."),
            "dashboard_name": types.Schema(type=types.Type.STRING, description="Optional Dashboard name to attach this chart to after creation."),
        },
        required=["chart_name", "document_type", "group_by_based_on"],
    ),
)

LIST_DASHBOARDS_DECLARATION = types.FunctionDeclaration(
    name="list_dashboards",
    description="List all available Dashboards in ERPNext (e.g. Selling, Buying, Accounts) so you can show the user available choices for adding a chart.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={},
        required=[],
    ),
)

ADD_CHART_TO_DASHBOARD_DECLARATION = types.FunctionDeclaration(
    name="add_chart_to_dashboard",
    description="Add an existing Dashboard Chart to an ERPNext Dashboard (e.g. Selling, Buying, Accounts).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "chart_name": types.Schema(type=types.Type.STRING, description="Name of the existing Dashboard Chart."),
            "dashboard_name": types.Schema(type=types.Type.STRING, description="Name of the Dashboard to add the chart to."),
        },
        required=["chart_name", "dashboard_name"],
    ),
)

TOOLS = types.Tool(function_declarations=[
    LIST_INVOICES_DECLARATION,
    AGGREGATE_DOCUMENTS_DECLARATION,
    GET_DOCTYPE_FIELDS_DECLARATION,
    READ_UPLOADED_FILE_DECLARATION,
    CREATE_DOCTYPE_DECLARATION,
    CREATE_DASHBOARD_CHART_DECLARATION,
    LIST_DASHBOARDS_DECLARATION,
    ADD_CHART_TO_DASHBOARD_DECLARATION,
    SEARCH_DOCUMENTS_DECLARATION,
    SEARCH_DOCTYPE_DECLARATION,
    FETCH_DECLARATION,
    SEARCH_DECLARATION,
])

TOOL_DISPATCH = {
    "list_invoices": api.list_invoices,
    "aggregate_documents": api.aggregate_documents,
    "get_doctype_fields": api.get_doctype_fields,
    "read_uploaded_file": api.read_uploaded_file,
    "create_doctype": api.create_doctype,
    "create_dashboard_chart": api.create_dashboard_chart,
    "list_dashboards": api.list_dashboards,
    "add_chart_to_dashboard": api.add_chart_to_dashboard,
    "search_documents": api.search_documents,
    "search_doctype": api.search_doctype,
    "fetch": api.fetch,
    "search": api.search,
}

# Tools that change data — gated by an explicit human confirmation in
# Python, not by trusting the model to self-police.
WRITE_TOOLS = {"create_doctype", "create_dashboard_chart", "add_chart_to_dashboard"}
SYSTEM_INSTRUCTION = """You are an ERPNext operations assistant running inside a real
ERPNext site, acting with the current user's own permissions.

CRITICAL: Never claim an action was completed (created, updated, deleted) unless
you actually called the corresponding tool and received a successful result back.
If you intended to take an action but didn't call the tool, say so explicitly
rather than describing it as done. Only report what a tool result actually confirms.

When the user asks for multiple things in one request (e.g. 'show X, then create Y'),
handle them as separate tool calls, one at a time — do not skip a step and only
describe it in your final answer.

When the user wants to create a new DocType:
- Ask for the fieldtype of any field they didn't specify — never guess it.
  Valid types include Data, Int, Float, Currency, Date, Datetime, Select, Link, Check, Text.
- Once you have all fields with types, summarize the full planned schema in
  plain text and ask the user to confirm before calling create_doctype.

When the user wants to import data from a file:
- Call read_uploaded_file first to preview it. Never assume column names or content.

When the user wants a chart or dashboard:
- Use aggregate_documents first if you need to check the data shape, then create_dashboard_chart to create a persistent chart in ERPNext.
- If the user did NOT specify a target dashboard when asking to create a chart:
  1. Once create_dashboard_chart completes, immediately call list_dashboards to fetch available Dashboards in ERPNext.
  2. Inform the user that the chart was created successfully.
  3. Ask: "Would you like to display this chart on a Dashboard?" and present the list of available dashboards as bullet options.
  4. Prompt the user that they can reply with the name of any Dashboard to add it there.
- If the user specifies or selects a Dashboard (e.g. "Buying"), YOU MUST EXECUTE the add_chart_to_dashboard function call. NEVER send a text reply claiming the chart was added to a dashboard without actually calling add_chart_to_dashboard first!

If you're unsure whether a DocType or field exists, use get_doctype_fields
to check rather than guessing.

Report results in plain language, but only ever report what tool results actually
confirm happened."""

def _sanitize_tool_result(result):
    """Ensure the tool result is JSON-serializable before it goes back to
    Gemini (e.g. dates, Decimal types from frappe.db.sql)."""
    return json.loads(json.dumps(result, default=str))


# def run():
#     gemini_api_key = frappe.conf.get("gemini_api_key")
#     if not gemini_api_key:
#         print("No Gemini API key found. Set it with:")
#         print('  bench --site your-site-name set-config gemini_api_key "your-key"')
#         return

#     client = genai.Client(api_key=gemini_api_key)

#     config_with_tools = types.GenerateContentConfig(
#         system_instruction=SYSTEM_INSTRUCTION,
#         tools=[TOOLS],
#     )
#     config_no_tools = types.GenerateContentConfig(
#         system_instruction=SYSTEM_INSTRUCTION,
#     )

#     history = []

#     print(f"erpnext_copilot ready, running as user: {frappe.session.user}")
#     print("Type 'exit' to quit.\n")

#     while True:
#         try:
#             user_input = input("You: ").strip()
#         except (EOFError, KeyboardInterrupt):
#             break
#         if user_input.lower() in ("exit", "quit"):
#             break
#         if not user_input:
#             continue

#         history.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

#         response = client.models.generate_content(model="gemini-3.6-flash", contents=history, config=config_with_tools)
#         candidate_content = response.candidates[0].content
#         history.append(candidate_content)

#         function_call = None
#         for part in candidate_content.parts:
#             if part.function_call:
#                 function_call = part.function_call
#                 break

#         if function_call:
#             fn_name = function_call.name
#             fn_args = dict(function_call.args)
#             print(f"[calling tool: {fn_name}({fn_args})]")

#             fn = TOOL_DISPATCH.get(fn_name)

#             if fn_name in WRITE_TOOLS:
#                 print("\n[PLANNED ACTION]")
#                 for k, v in fn_args.items():
#                     print(f"  {k}: {v}")
#                 proceed = input("\nProceed with this action? (y/n): ").strip().lower()
#                 if proceed != "y":
#                     result = {"cancelled": True, "message": "Action cancelled by user."}
#                 else:
#                     result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
#             else:
#                 result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}

#             clean_result = _sanitize_tool_result(result)

#             history.append(
#                 types.Content(
#                     role="user",
#                     parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})],
#                 )
#             )

#             # No tools on this call — combining tools with a function-response
#             # Part triggered a serialization bug in this SDK version.
#             follow_up = client.models.generate_content(model="gemini-3.6-flash", contents=history, config=config_no_tools)
#             follow_up_content = follow_up.candidates[0].content
#             history.append(follow_up_content)
#             print(f"\nAgent: {follow_up.text}\n")
#         else:
#             print(f"\nAgent: {response.text}\n")


def _extract_text(content) -> str:
    """response.text can be None when the response has non-text parts
    (e.g. a function_call). Pull whatever text parts exist instead."""
    texts = [p.text for p in content.parts if getattr(p, "text", None)]
    return "\n".join(texts) if texts else "(no text response)"


def run():
    gemini_api_key = frappe.conf.get("gemini_api_key")
    if not gemini_api_key:
        print("No Gemini API key found. Set it with:")
        print('  bench --site your-site-name set-config gemini_api_key "your-key"')
        return

    client = genai.Client(api_key=gemini_api_key)

    config_with_tools = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[TOOLS],
    )

    history = []
    MAX_TOOL_STEPS = 5  # safety cap against runaway tool-call loops

    print(f"erpnext_copilot ready, running as user: {frappe.session.user}")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue

        history.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

        for step in range(MAX_TOOL_STEPS):
            response = client.models.generate_content(model="gemini-3.6-flash", contents=history, config=config_with_tools)
            candidate_content = response.candidates[0].content
            history.append(candidate_content)

            function_call = next((p.function_call for p in candidate_content.parts if p.function_call), None)

            if not function_call:
                print(f"\nAgent: {_extract_text(candidate_content)}\n")
                break

            fn_name = function_call.name
            fn_args = dict(function_call.args)
            print(f"[calling tool: {fn_name}({fn_args})]")

            fn = TOOL_DISPATCH.get(fn_name)

            if fn_name in WRITE_TOOLS:
                print("\n[PLANNED ACTION]")
                for k, v in fn_args.items():
                    print(f"  {k}: {v}")
                proceed = input("\nProceed with this action? (y/n): ").strip().lower()
                if proceed != "y":
                    result = {"cancelled": True, "message": "Action cancelled by user."}
                else:
                    result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
            else:
                result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}

            clean_result = _sanitize_tool_result(result)

            history.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})],
                )
            )
        else:
            print("\nAgent: (stopped after reaching the tool-call safety limit)\n")