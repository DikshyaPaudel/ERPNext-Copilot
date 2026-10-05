"""
api.py — whitelisted methods exposed to the erpnext_copilot Gemini agent.

Every function here is decorated with @frappe.whitelist(), meaning it runs
as whichever Frappe user is currently authenticated (e.g. the bench console
session user) and is subject to that user's real ERPNext permissions via
frappe.get_all() / frappe.get_doc() / frappe.get_meta(). No separate API key
or manual permission logic is used — this is the main advantage of running
inside the Frappe app rather than as an external script.
"""
import json
from typing import Optional
import frappe
from frappe import _
import frappe.utils

from erpnext_copilot.erpnext_copilot.query_engine import execute as execute_query

VALID_FIELDTYPES = {
    "Data", "Int", "Float", "Currency", "Date", "Datetime", "Select",
    "Link", "Check", "Text", "Small Text", "Long Text", "Attach",
}

# Frappe already caches frappe.get_meta() for the lifetime of a single HTTP
# request (frappe.local.meta_cache), so repeated calls within one agent turn
# were already cheap. What wasn't cached is the same doctype being checked
# again on a LATER turn — a fresh request — which re-triggers a full meta
# load. This cache closes that gap with a short TTL (schema changes are rare
# enough that a 3-minute staleness window is an acceptable trade for fewer
# meta loads across a session).
_DEFAULT_DOCFIELDS = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
_META_CACHE_TTL = 180


def _cached_doctype_fieldnames(doctype: str) -> set:
    """All real field names on a DocType, plus Frappe's standard default
    fields, cached briefly. Used anywhere the code previously called
    meta.has_field(x) just to check presence."""
    cache_key = f"erpnext_copilot_fields:{doctype}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return set(cached)

    meta = frappe.get_meta(doctype)
    fieldnames = {f.fieldname for f in meta.fields} | _DEFAULT_DOCFIELDS
    frappe.cache().set_value(cache_key, list(fieldnames), expires_in_sec=_META_CACHE_TTL)
    return fieldnames


def _has_field_cached(doctype: str, fieldname: str) -> bool:
    return fieldname in _cached_doctype_fieldnames(doctype)


def _cached_list_view_fieldnames(doctype: str) -> list:
    """Fieldnames flagged in_list_view, cached briefly — used to build a
    default column set when the agent doesn't specify which fields it wants."""
    cache_key = f"erpnext_copilot_list_fields:{doctype}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return cached

    meta = frappe.get_meta(doctype)
    fieldnames = [f.fieldname for f in meta.fields if f.in_list_view]
    frappe.cache().set_value(cache_key, fieldnames, expires_in_sec=_META_CACHE_TTL)
    return fieldnames


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_invoices(days: int = 20, customer: Optional[str] = None, only_unpaid: bool = False):
    """List Sales Invoices from the last N days, respecting the calling
    user's own ERPNext permissions."""

    from datetime import date, timedelta

    filters = {"posting_date": [">=", (date.today() - timedelta(days=days)).isoformat()]}
    if customer:
        filters["customer"] = customer
    if only_unpaid:
        filters["outstanding_amount"] = [">", 0]

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "customer", "posting_date", "grand_total", "outstanding_amount", "status"],
        order_by="posting_date desc",
    )
    # Gemini's SDK needs JSON-serializable results — date objects aren't.
    for inv in invoices:
        if inv.get("posting_date"):
            inv["posting_date"] = str(inv["posting_date"])
    return invoices


@frappe.whitelist()
def aggregate_documents(doctype: str, group_by: str, days: Optional[int] = None, date_field: str = "creation"):
    """Count (and sum, if a total/amount field exists) any DocType's
    records, grouped by any field. Generic so it works for
    'Sales Invoice grouped by customer', 'Purchase Order grouped by
    supplier', 'Support Ticket grouped by status', etc.

    doctype and group_by are validated against Frappe's real metadata
    before being used in a raw SQL query, since Frappe does not
    parameterize identifiers (table/column names) the way it does values.
    """
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    fieldnames = _cached_doctype_fieldnames(doctype)
    if group_by not in fieldnames:
        return {"error": f"'{group_by}' is not a valid field on {doctype}."}
    if days and date_field not in fieldnames:
        return {"error": f"'{date_field}' is not a valid field on {doctype}."}

    conditions = []
    values = []

    if days:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conditions.append(f"`{date_field}` >= %s")
        values.append(cutoff)

    if "docstatus" in fieldnames:
        conditions.append("docstatus != 2")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_field = None
    for candidate in ("grand_total", "total", "amount"):
        if candidate in fieldnames:
            total_field = candidate
            break

    if total_field:
        query = f"""
            SELECT `{group_by}` as group_value, COUNT(*) as count, SUM(`{total_field}`) as total
            FROM `tab{doctype}`
            {where_clause}
            GROUP BY `{group_by}`
            ORDER BY count DESC
        """
    else:
        query = f"""
            SELECT `{group_by}` as group_value, COUNT(*) as count
            FROM `tab{doctype}`
            {where_clause}
            GROUP BY `{group_by}`
            ORDER BY count DESC
        """

    results = frappe.db.sql(query, tuple(values), as_dict=True)
    for r in results:
        if "total" in r and r["total"] is not None:
            r["total"] = float(r["total"])
    return results


@frappe.whitelist()
def get_doctype_fields(doctype: str):
    """Return the field schema (fieldname, label, fieldtype, required) for
    a DocType — lets the agent check what already exists instead of
    guessing, e.g. before proposing a custom field or querying data."""
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    meta = frappe.get_meta(doctype)
    return [
        {
            "fieldname": f.fieldname,
            "label": f.label,
            "fieldtype": f.fieldtype,
            "required": bool(f.reqd),
        }
        for f in meta.fields
    ]


@frappe.whitelist()
def read_uploaded_file(file_name: str, preview_rows: int = 10):
    """Preview a CSV or Excel file already uploaded to ERPNext as a File
    record. Returns column names, row count, and the first N rows. Does
    NOT import anything — this is preview-only.
    """
    import pandas as pd
    import json

    file_doc = None
    if frappe.db.exists("File", file_name):
        file_doc = frappe.get_doc("File", file_name)
    else:
        matches = frappe.get_all("File", filters={"file_url": file_name}, limit=1)
        if matches:
            file_doc = frappe.get_doc("File", matches[0].name)

    if not file_doc:
        return {"error": f"No uploaded file found matching '{file_name}'."}

    path = file_doc.get_full_path()
    try:
        if path.lower().endswith(".csv"):
            df = pd.read_csv(path)
        elif path.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(path)
        else:
            return {"error": f"Unsupported file type: {path}"}
    except Exception as e:
        return {"error": f"Could not parse file: {str(e)}"}

    preview = json.loads(df.head(preview_rows).to_json(orient="records", date_format="iso"))

    return {
        "file_name": file_doc.file_name,
        "columns": list(df.columns),
        "row_count": len(df),
        "preview": preview,
    }


@frappe.whitelist()
def clean_uploaded_file(file_name: str, target_doctype: str, cleaning_instructions: Optional[str] = None):
    """Read an uploaded CSV/Excel file, inspect target DocType field schema,
    and use Gemini to intelligently clean, parse messy dates (e.g. '17th March' -> '2025-03-17'),
    trim whitespace, resolve fuzzy names, and map fields.
    """
    import pandas as pd
    from google import genai
    from google.genai import types

    file_doc = None
    if frappe.db.exists("File", file_name):
        file_doc = frappe.get_doc("File", file_name)
    else:
        matches = frappe.get_all("File", filters={"file_url": file_name}, limit=1)
        if matches:
            file_doc = frappe.get_doc("File", matches[0].name)

    if not file_doc:
        return {"error": f"No uploaded file found matching '{file_name}'."}

    if not frappe.db.exists("DocType", target_doctype):
        return {"error": f"Target DocType '{target_doctype}' does not exist."}

    meta = frappe.get_meta(target_doctype)
    target_fields = [
        {"fieldname": f.fieldname, "label": f.label, "fieldtype": f.fieldtype, "reqd": f.reqd}
        for f in meta.fields
        if not f.read_only
    ]

    path = file_doc.get_full_path()
    try:
        if path.lower().endswith(".csv"):
            df = pd.read_csv(path)
        elif path.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(path)
        else:
            return {"error": f"Unsupported file type: {path}"}
    except Exception as e:
        return {"error": f"Could not parse file: {str(e)}"}

    raw_rows = json.loads(df.fillna("").head(50).to_json(orient="records", date_format="iso"))

    gemini_api_key = frappe.conf.get("gemini_api_key")
    if not gemini_api_key:
        return {"error": "Gemini API key not configured on site."}

    client = genai.Client(api_key=gemini_api_key)

    prompt = f"""You are an expert ERPNext data cleaning AI assistant.
Transform and clean the following raw file data to strictly match the target DocType '{target_doctype}' schema.

Target DocType Fields:
{json.dumps(target_fields, indent=2)}

User Cleaning Instructions:
{cleaning_instructions or "Standardize dates to YYYY-MM-DD (e.g. '17th March' -> '2025-03-17'), clean text formatting/whitespace, fix messy names, handle missing values."}

Raw Input Data (first {len(raw_rows)} rows):
{json.dumps(raw_rows, indent=2)}

Output strictly valid JSON with no markdown formatting or text wrappers, matching this exact structure:
{{
  "cleaned_records": [
    {{ "fieldname1": "cleaned_value1", "fieldname2": "cleaned_value2" }}
  ],
  "cleaning_summary": ["List of specific transformations performed, e.g. converted '17th March' to '2025-03-17'"]
}}
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    try:
        cleaned_result = json.loads(response.text)
        return {
            "success": True,
            "target_doctype": target_doctype,
            "total_raw_rows": len(df),
            "cleaned_records": cleaned_result.get("cleaned_records", []),
            "summary": cleaned_result.get("cleaning_summary", []),
        }
    except Exception as e:
        return {"error": f"Failed to parse cleaned result from LLM: {str(e)}", "raw_response": response.text}


@frappe.whitelist()
def import_data_to_doctype(target_doctype: str, records: list):
    """Import a list of cleaned records into an ERPNext DocType.
    Creates documents using frappe.get_doc and commits to database.
    Runs with the calling user's real permissions (no ignore_permissions),
    so rows the user isn't allowed to create come back as per-row errors
    rather than silently succeeding.
    """
    if not frappe.db.exists("DocType", target_doctype):
        return {"error": f"Target DocType '{target_doctype}' does not exist."}

    if isinstance(records, str):
        records = json.loads(records)

    created = []
    errors = []

    for idx, row in enumerate(records):
        try:
            doc_dict = {"doctype": target_doctype}
            doc_dict.update(row)
            doc = frappe.get_doc(doc_dict)
            doc.insert()
            created.append(doc.name)
        except frappe.PermissionError:
            errors.append(f"Row {idx + 1}: permission denied - you don't have create access to {target_doctype}.")
        except Exception as e:
            errors.append(f"Row {idx + 1}: {str(e)}")

    frappe.db.commit()

    # success only reflects what actually happened — at least one row
    # created and no total wipeout — so the agent can't report a fully
    # failed import as a success.
    return {
        "success": len(created) > 0,
        "target_doctype": target_doctype,
        "created_count": len(created),
        "created_names": created,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Writes — every function below should be gated by a confirmation step in
# gemini_agent.py's WRITE_TOOLS set before being executed.
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_doctype(doctype_name: str, fields: list, module: str = "Custom"):
    """Create a brand-new custom DocType. Each item in `fields` must be a
    dict like {"fieldname": "vehicle", "label": "Vehicle", "fieldtype": "Data",
    "reqd": 0}. fieldtype is required for every field — do not guess it;
    ask the user if it wasn't specified.
    """
    if frappe.db.exists("DocType", doctype_name):
        return {"error": f"DocType '{doctype_name}' already exists."}

    bad_fields = [f for f in fields if f.get("fieldtype") not in VALID_FIELDTYPES]
    if bad_fields:
        return {
            "error": "Invalid or missing fieldtype on some fields.",
            "problem_fields": bad_fields,
            "valid_fieldtypes": sorted(VALID_FIELDTYPES),
        }

    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": doctype_name,
        "module": module,
        "custom": 1,
        "autoname": "hash",
        "fields": fields,
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}
        ],
    })
    try:
        doc.insert()
    except frappe.PermissionError:
        return {"error": "You don't have permission to create a new DocType. This usually requires the System Manager role."}
    return {"success": True, "doctype": doctype_name, "fields_created": [f["fieldname"] for f in fields]}


@frappe.whitelist()
def list_dashboards():
    """List all available Dashboards in ERPNext (e.g. Selling, Buying, Accounts)."""
    dashboards = frappe.get_all("Dashboard", fields=["name", "dashboard_name", "module"])
    return json.loads(json.dumps(dashboards, default=str))


@frappe.whitelist()
def add_chart_to_dashboard(chart_name: str, dashboard_name: str):
    """Add an existing Dashboard Chart to a Dashboard by adding a link row in
    the Dashboard's `charts` child table.

    Both mutations go through frappe.get_doc(...).save()/.insert() rather
    than frappe.db.set_value(), because db.set_value() is a raw DB write
    that skips has_permission() checks entirely — using it here would let
    a user with no write access to Dashboard Chart / Dashboard silently
    modify them, the same class of bug this file's other writes were
    already fixed for.
    """
    real_chart = chart_name
    if not frappe.db.exists("Dashboard Chart", real_chart):
        charts = frappe.get_all("Dashboard Chart", filters={"chart_name": chart_name}, limit=1)
        if charts:
            real_chart = charts[0].name
        else:
            return {"error": f"Dashboard Chart '{chart_name}' does not exist in Frappe."}

    real_dashboard = dashboard_name
    if not frappe.db.exists("Dashboard", real_dashboard):
        dashboards = frappe.get_all("Dashboard", filters={"dashboard_name": dashboard_name}, limit=1)
        if dashboards:
            real_dashboard = dashboards[0].name
        else:
            return {"error": f"Dashboard '{dashboard_name}' does not exist in Frappe."}

    # NOTE: earlier versions of this function flipped is_standard=1 on the
    # chart here (first via an unchecked frappe.db.set_value(), later via a
    # permission-checked .save()). Both were wrong: is_standard tells Frappe
    # the chart is fixture code backed by a module on disk, and a chart
    # created dynamically through frappe.get_doc().insert() has no such
    # module — flipping the flag causes "Module None not found" the next
    # time the chart is resolved/rendered. Non-standard charts attach to a
    # Dashboard and render fine via the Dashboard Chart Link below, so this
    # step is simply not needed.
    dashboard = frappe.get_doc("Dashboard", real_dashboard)

    # Check if already added
    for link in dashboard.charts:
        if link.chart == real_chart:
            return {
                "success": True,
                "message": f"Chart '{real_chart}' is already attached to Dashboard '{real_dashboard}'.",
                "dashboard": real_dashboard,
                "chart_name": real_chart,
            }

    chart_link = frappe.get_doc({
        "doctype": "Dashboard Chart Link",
        "parent": real_dashboard,
        "parenttype": "Dashboard",
        "parentfield": "charts",
        "chart": real_chart,
        "idx": len(dashboard.charts) + 1,
    })
    try:
        chart_link.insert()
    except frappe.PermissionError:
        return {"error": "You don't have permission to modify this Dashboard."}
    frappe.db.commit()

    return {
        "success": True,
        "message": f"Chart '{real_chart}' successfully added to Dashboard '{real_dashboard}'.",
        "dashboard": real_dashboard,
        "chart_name": real_chart,
    }


@frappe.whitelist()
def create_dashboard_chart(
    chart_name: str,
    document_type: str,
    group_by_based_on: str,
    chart_type: str = "Bar",
    dashboard_name: Optional[str] = None,
):
    """Create a native ERPNext Dashboard Chart for any DocType, grouped by
    any field. Viewable in ERPNext's own Dashboard/Workspace UI after
    creation — not just returned as raw data. Option to link to a Dashboard.
    """
    if not frappe.db.exists("DocType", document_type):
        return {"error": f"'{document_type}' is not a valid DocType."}
    if not _has_field_cached(document_type, group_by_based_on):
        return {"error": f"'{group_by_based_on}' is not a valid field on {document_type}."}
    if frappe.db.exists("Dashboard Chart", chart_name):
        return {"error": f"A Dashboard Chart named '{chart_name}' already exists."}

    doc = frappe.get_doc({
        "doctype": "Dashboard Chart",
        "chart_name": chart_name,
        "chart_type": "Group By",
        "document_type": document_type,
        "group_by_type": "Count",
        "group_by_based_on": group_by_based_on,
        "type": chart_type,  # Bar, Line, Pie, Percentage, Donut
        "timespan": "Last Year",
        "time_interval": "Monthly",
        "filters_json": "{}",   # required by ERPNext even when there are no filters
        "is_public": 1,
    })
    try:
        doc.insert()
        frappe.db.commit()
    except frappe.PermissionError:
        return {"error": "You don't have permission to create Dashboard Charts."}

    added_to_dashboard_msg = ""
    if dashboard_name:
        add_res = add_chart_to_dashboard(chart_name=doc.name, dashboard_name=dashboard_name)
        if "error" in add_res:
            added_to_dashboard_msg = f" (Failed to add to dashboard '{dashboard_name}': {add_res['error']})"
        else:
            added_to_dashboard_msg = f" and added to Dashboard '{dashboard_name}'."

    return {
        "success": True,
        "chart_name": doc.name,
        "message": f"Chart '{chart_name}' created{added_to_dashboard_msg}. View it under Dashboard > Charts, or in the Dashboard UI.",
    }

@frappe.whitelist()
def search_documents(doctype: str, filters: Optional[dict] = None, fields: Optional[list] = None, limit: int = 20):
    """Search a DocType with structured filters — the generic version of
    something like 'list invoices for customer X'. filters uses Frappe's
    filter dict syntax, e.g. {"customer": "Acme", "outstanding_amount": [">", 0]}.
    """
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    if fields:
        bad_fields = [f for f in fields if f != "name" and not _has_field_cached(doctype, f)]
        if bad_fields:
            return {"error": f"Invalid fields for {doctype}: {bad_fields}"}
    else:
        fields = ["name"] + _cached_list_view_fieldnames(doctype)[:6]

    if filters:
        for key in filters:
            if not _has_field_cached(doctype, key) and key != "name":
                return {"error": f"'{key}' is not a valid field on {doctype}."}

    results = frappe.get_all(doctype, filters=filters or {}, fields=fields, limit=limit)
    return json.loads(json.dumps(results, default=str))


@frappe.whitelist()
def search_doctype(doctype: str, query: str, limit: int = 20):
    """Text search within a single DocType — searches its title/name field
    for a loose match, e.g. find customers by partial name."""
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    meta = frappe.get_meta(doctype)
    search_field = meta.title_field or "name"

    results = frappe.get_all(
        doctype,
        filters={search_field: ["like", f"%{query}%"]},
        fields=["name", search_field] if search_field != "name" else ["name"],
        limit=limit,
    )
    return json.loads(json.dumps(results, default=str))


@frappe.whitelist()
def fetch(doctype: str, name: str):
    """Get a single document by its ID/name."""
    if not frappe.db.exists(doctype, name):
        return {"error": f"No {doctype} found with name '{name}'."}
    doc = frappe.get_doc(doctype, name)
    return json.loads(doc.as_json())


@frappe.whitelist()
def search(query: str, limit: int = 20):
    """Global search across all DocTypes using Frappe's built-in search,
    for when the user doesn't know which DocType to look in."""
    from frappe.utils.global_search import search as global_search
    results = global_search(text=query, start=0, limit=limit)
    return json.loads(json.dumps(results, default=str))


# ---------------------------------------------------------------------------
# Ask AI — natural language to report/dashboard (like lumen_reports.ai.ask_ai)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def ask_ai(prompt: str, history: list | None = None, existing_titles: list | None = None, answered: bool = False, analyze: bool = True):
    """
    Build a report/dashboard from natural language.
    
    Args:
        prompt: User's natural language request
        history: Conversation history for context
        existing_titles: Titles of widgets already on the board (to avoid duplicates)
        answered: If True, this is a follow-up answer to a clarification question
        analyze: Whether to include AI analysis of the results
    
    Returns:
        Dict with widgets, title, analysis, suggestions, questions, and optionally clarify
    """
    from google import genai
    from google.genai import types
    import json
    
    gemini_api_key = frappe.conf.get("gemini_api_key")
    if not gemini_api_key:
        frappe.throw(_("Add a Gemini API key in AI settings first"), title=_("No API key"))
    
    client = genai.Client(api_key=gemini_api_key)
    
    # Build context about available data
    data_context = _get_data_context()
    
    system_instruction = f"""You are an expert ERPNext report builder. Convert the user's natural language request into a structured report specification.

Available data in this ERPNext site:
{data_context}

Widgets already on the board (avoid duplicates):
{json.dumps(existing_titles or [], ensure_ascii=False)}

Output ONLY valid JSON matching this exact structure:
{{
  "title": "Report title",
  "widgets": [
    {{
      "widget": {{
        "title": "Widget title",
        "widget_type": "Number Card|Bar Chart|Line Chart|Pie Chart|Table|Donut Chart|Area Chart",
        "query": {{...}},
        "style": {{}}
      }},
      "empty": false
    }}
  ],
  "analysis": {{"headline": "...", "findings": [...], "watch": [...]}},
  "suggestions": ["Follow-up idea 1", "Follow-up idea 2"],
  "questions": ["Clarifying question 1", "Clarifying question 2"]
}}

Query definition shape:
{{
  "doctype": "Sales Invoice",
  "aggregate": {{"function": "sum", "field": "grand_total"}},
  "group_by": {{"field": "posting_date", "time_grain": "month"}},
  "filters": [["status", "=", "Paid"]],
  "sort": {{"field": "grand_total", "order": "desc"}},
  "limit": 100
}}

Rules:
- Use only real DocTypes and field names from the data context
- For "number" results: omit group_by, use aggregate
- For "series" (charts): include group_by with time_grain for trends, or without for categories
- For "table": omit aggregate, include fields list
- Valid aggregate functions: count, sum, avg, min, max
- Valid chart types: Number Card, Bar Chart, Line Chart, Pie Chart, Donut Chart, Area Chart, Table
- If the request is ambiguous, return {{"clarify": "question", "options": ["opt1", "opt2"]}} instead
- Maximum 8 widgets per response
- Set "empty": true if a widget would have no data (the UI will show a badge)"""

    # Build conversation for the model
    contents = []
    if history:
        for h in history[-10:]:  # Last 10 turns
            role = "user" if h.get("role") == "user" else "model"
            text = h.get("text", "")
            if text:
                contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=contents,
            config=config,
        )
        
        result = json.loads(response.text)
        
        # If clarification needed, return early
        if result.get("clarify"):
            return {
                "clarify": result["clarify"],
                "options": result.get("options", [])
            }
        
        # Execute queries for each widget
        executed_widgets = []
        for entry in result.get("widgets", [])[:8]:
            widget_spec = entry.get("widget", {})
            query = widget_spec.get("query", {})
            
            if not query.get("doctype"):
                continue
            
            try:
                query_result = execute_query(query)
                entry["result"] = query_result
                entry["empty"] = _is_empty_result(query_result)
            except Exception as e:
                frappe.clear_last_message()
                entry["result"] = {"error": str(e)}
                entry["empty"] = True
            
            executed_widgets.append(entry)
        
        # Generate analysis if requested and we have data
        analysis = None
        if analyze and executed_widgets:
            analysis = _analyze_results(prompt, executed_widgets, data_context)
        
        return {
            "title": result.get("title", "Untitled Report"),
            "widgets": executed_widgets,
            "analysis": analysis,
            "suggestions": result.get("suggestions", [])[:3],
            "questions": result.get("questions", [])[:3],
        }
        
    except Exception as e:
        frappe.clear_last_message()
        frappe.throw(_("Failed to generate report: {0}").format(str(e)))


def _get_data_context(doctypes: list | None = None) -> str:
    """Get a summary of available DocTypes and their key fields for the AI."""
    if doctypes is None:
        # Get commonly used ERPNext DocTypes
        doctypes = [
            "Sales Invoice", "Sales Order", "Purchase Invoice", "Purchase Order",
            "Customer", "Supplier", "Item", "Payment Entry", "Journal Entry",
            "Stock Entry", "Delivery Note", "Quotation", "Lead", "Opportunity"
        ]
    
    context_parts = []
    for dt in doctypes:
        if not frappe.db.exists("DocType", dt):
            continue
        try:
            meta = frappe.get_meta(dt)
            key_fields = []
            for f in meta.fields:
                if f.fieldtype in ("Link", "Currency", "Float", "Int", "Date", "Datetime", "Select") and f.in_list_view:
                    key_fields.append(f"{f.fieldname} ({f.fieldtype})")
                if len(key_fields) >= 10:
                    break
            if key_fields:
                context_parts.append(f"{dt}: {', '.join(key_fields)}")
        except Exception:
            pass
    
    return "\n".join(context_parts) if context_parts else "No standard DocTypes found"


def _is_empty_result(result: dict) -> bool:
    """Check if a query result is empty."""
    if not result or result.get("error"):
        return True
    rt = result.get("result_type")
    if rt == "number":
        return result.get("value", 0) == 0
    elif rt == "series":
        return not result.get("labels") or all(v == 0 for v in result.get("values", []))
    elif rt == "rows":
        return not result.get("rows")
    return True


def _analyze_results(question: str, executed_widgets: list, data_context: str) -> dict:
    """Use AI to analyze the query results and produce an executive summary."""
    from google import genai
    from google.genai import types
    import json
    
    gemini_api_key = frappe.conf.get("gemini_api_key")
    if not gemini_api_key:
        return None
    
    client = genai.Client(api_key=gemini_api_key)
    
    # Prepare summary of results
    results_summary = []
    for entry in executed_widgets[:12]:  # Max 12 widgets for analysis
        widget = entry.get("widget", {})
        result = entry.get("result", {})
        if result.get("error"):
            continue
        results_summary.append({
            "title": widget.get("title"),
            "type": widget.get("widget_type"),
            "result_type": result.get("result_type"),
            "data": result
        })
    
    if not results_summary:
        return None
    
    prompt = f"""Analyze these ERPNext report results and write an executive summary.

Original question: {question}

Results:
{json.dumps(results_summary, indent=2, default=str)}

Output ONLY valid JSON:
{{
  "headline": "One-sentence summary of what the data shows",
  "findings": ["Specific insight 1 with numbers", "Specific insight 2 with numbers", ...],
  "watch": ["Potential concern or trend to monitor", ...]
}}

Rules:
- 3-5 findings, each with concrete numbers from the data
- 1-3 watch items (optional)
- No vague statements — every finding must reference actual data
- Keep it concise, executive-ready
- Use Western digits (0-9) even for Arabic"""

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return json.loads(response.text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# AI Settings Management
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_ai_status():
    """Get current AI configuration status."""
    personal_key = frappe.conf.get("gemini_api_key")
    site_key = frappe.db.get_single_value("System Settings", "gemini_api_key") if frappe.db.has_table("System Settings") else None
    
    return {
        "configured": bool(personal_key or site_key),
        "personal_configured": bool(personal_key),
        "site_configured": bool(site_key),
        "source": "user" if personal_key else ("site" if site_key else None),
        "model": frappe.conf.get("gemini_model", "gemini-3.6-flash"),
        "is_admin": "System Manager" in frappe.get_roles(),
    }


@frappe.whitelist()
def save_ai_settings(api_key: str | None = None, model: str = "gemini-3.6-flash"):
    """Save personal AI settings to site_config.json."""
    if api_key:
        frappe.conf.gemini_api_key = api_key
    frappe.conf.gemini_model = model
    frappe.conf.write()
    return get_ai_status()


@frappe.whitelist()
def clear_ai_key():
    """Clear personal AI key."""
    if "gemini_api_key" in frappe.conf:
        del frappe.conf["gemini_api_key"]
    frappe.conf.write()
    return get_ai_status()


@frappe.whitelist()
def list_models():
    """List available Gemini models."""
    # In practice, this could call the Gemini API to list models
    # For now, return known models
    return [
        "gemini-3.6-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]


# ---------------------------------------------------------------------------
# Dashboard/Chart Save/Load (for Ask AI page)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def save_ai_result(widgets: list, title: str | None = None, slug: str | None = None):
    """Save Ask AI result as a Dashboard or add to existing Dashboard."""
    if slug:
        # Append to existing dashboard
        dashboard = frappe.get_doc("Dashboard", slug)
        for w in widgets:
            # Create chart for each widget
            chart_name = f"{title or 'AI Chart'} - {w['widget']['title']}"[:140]
            chart = frappe.get_doc({
                "doctype": "Dashboard Chart",
                "chart_name": chart_name,
                "chart_type": "Group By",
                "document_type": w["widget"]["query"].get("doctype"),
                "group_by_type": "Count" if not w["widget"]["query"].get("aggregate") else w["widget"]["query"]["aggregate"]["function"].title(),
                "group_by_based_on": w["widget"]["query"].get("group_by", {}).get("field") or "name",
                "type": _map_widget_type_to_chart(w["widget"]["widget_type"]),
                "timespan": "Last Year",
                "time_interval": "Monthly",
                "filters_json": json.dumps(w["widget"]["query"].get("filters", [])),
                "is_public": 1,
            })
            chart.insert()
            frappe.get_doc({
                "doctype": "Dashboard Chart Link",
                "parent": dashboard.name,
                "parenttype": "Dashboard",
                "parentfield": "charts",
                "chart": chart.name,
                "idx": len(dashboard.charts) + 1,
            }).insert()
        frappe.db.commit()
        return {"slug": dashboard.route_slug or dashboard.name}
    else:
        # Create new dashboard
        dashboard = frappe.get_doc({
            "doctype": "Dashboard",
            "dashboard_name": title or "AI Generated Dashboard",
            "module": "Custom",
            "is_standard": 0,
            "is_public": 1,
        })
        dashboard.insert()
        
        for i, w in enumerate(widgets):
            chart_name = f"{title or 'AI Chart'} - {w['widget']['title']}"[:140]
            chart = frappe.get_doc({
                "doctype": "Dashboard Chart",
                "chart_name": chart_name,
                "chart_type": "Group By",
                "document_type": w["widget"]["query"].get("doctype"),
                "group_by_type": "Count" if not w["widget"]["query"].get("aggregate") else w["widget"]["query"]["aggregate"]["function"].title(),
                "group_by_based_on": w["widget"]["query"].get("group_by", {}).get("field") or "name",
                "type": _map_widget_type_to_chart(w["widget"]["widget_type"]),
                "timespan": "Last Year",
                "time_interval": "Monthly",
                "filters_json": json.dumps(w["widget"]["query"].get("filters", [])),
                "is_public": 1,
            })
            chart.insert()
            frappe.get_doc({
                "doctype": "Dashboard Chart Link",
                "parent": dashboard.name,
                "parenttype": "Dashboard",
                "parentfield": "charts",
                "chart": chart.name,
                "idx": i + 1,
            }).insert()
        
        frappe.db.commit()
        return {"slug": dashboard.route_slug or dashboard.name}


def _map_widget_type_to_chart(widget_type: str) -> str:
    mapping = {
        "Number Card": "Percentage",
        "Bar Chart": "Bar",
        "Line Chart": "Line",
        "Pie Chart": "Pie",
        "Donut Chart": "Donut",
        "Area Chart": "Line",
        "Table": "Bar",
    }
    return mapping.get(widget_type, "Bar")


@frappe.whitelist()
def get_dashboards():
    """List all dashboards for the 'Add to existing' dropdown."""
    dashboards = frappe.get_all("Dashboard", 
        filters={"is_standard": 0},  # Custom dashboards only
        fields=["name", "dashboard_name", "route_slug", "module"],
        order_by="modified desc"
    )
    return {"dashboards": dashboards}


# ---------------------------------------------------------------------------
# Save as Report (Report Builder) — traditional ERPNext Report
# ---------------------------------------------------------------------------

@frappe.whitelist()
def save_ai_result_as_report(widgets: list, title: str, report_name: str | None = None, module: str = "Custom"):
    """
    Save Ask AI result as a traditional ERPNext Report (Report Builder).
    Creates a Query Report with the query definition stored in the report.
    """
    if not report_name:
        # Generate a valid report name from title
        report_name = re.sub(r'[^a-zA-Z0-9_]', '_', title)[:100]
    
    if frappe.db.exists("Report", report_name):
        return {"error": f"Report '{report_name}' already exists."}
    
    # Build the query definition from widgets
    # For a Report, we use the first widget's query as the base
    if not widgets:
        return {"error": "No widgets to save as report"}
    
    first_widget = widgets[0].get("widget", {})
    query = first_widget.get("query", {})
    
    if not query.get("doctype"):
        return {"error": "Widget must have a doctype to create a report"}
    
    # Build columns from query
    columns = _build_report_columns(query)
    filters = _build_report_filters(query)
    
    # Create the Report
    report = frappe.get_doc({
        "doctype": "Report",
        "report_name": report_name,
        "module": module,
        "ref_doctype": query["doctype"],
        "report_type": "Query Report",
        "is_standard": "No",
        "roles": [{"role": "System Manager"}],
        "json": json.dumps({
            "query": query,
            "columns": columns,
            "filters": filters,
        }),
    })
    
    try:
        report.insert()
        frappe.db.commit()
    except frappe.PermissionError:
        return {"error": "You don't have permission to create a Report. Requires System Manager role."}
    except Exception as e:
        return {"error": f"Failed to create report: {str(e)}"}
    
    return {
        "success": True,
        "report_name": report_name,
        "message": f"Report '{report_name}' created. Open it from Report Builder or run via /app/query-report/{report_name}"
    }


def _build_report_columns(query: dict) -> list:
    """Build Report column definitions from query."""
    columns = []
    
    # Add group_by field if present
    group_by = query.get("group_by")
    if group_by:
        columns.append({
            "fieldname": group_by["field"],
            "label": group_by["field"].replace("_", " ").title(),
            "fieldtype": "Data",
            "width": 200
        })
    
    # Add aggregate field
    aggregate = query.get("aggregate")
    if aggregate:
        fieldtype = "Currency" if "total" in aggregate.get("field", "").lower() or "amount" in aggregate.get("field", "").lower() else "Float"
        if aggregate["function"] == "count":
            fieldtype = "Int"
        columns.append({
            "fieldname": f"{aggregate['function']}_{aggregate.get('field', 'count')}",
            "label": f"{aggregate['function'].title()} of {aggregate.get('field', '').replace('_', ' ').title()}",
            "fieldtype": fieldtype,
            "width": 150
        })
    
    # Add fields for table queries
    fields = query.get("fields") or []
    for f in fields:
        if f not in [group_by.get("field") if group_by else None]:
            columns.append({
                "fieldname": f,
                "label": f.replace("_", " ").title(),
                "fieldtype": "Data",
                "width": 150
            })
    
    return columns


def _build_report_filters(query: dict) -> list:
    """Build Report filter definitions from query filters."""
    filters = []
    for f in query.get("filters", []):
        if isinstance(f, (list, tuple)) and len(f) == 3:
            fieldname, operator, value = f
            filters.append({
                "fieldname": fieldname,
                "label": fieldname.replace("_", " ").title(),
                "fieldtype": "Data",
                "default": value,
                "operator": operator
            })
    return filters


import re