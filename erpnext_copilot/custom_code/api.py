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

VALID_FIELDTYPES = {
    "Data", "Int", "Float", "Currency", "Date", "Datetime", "Select",
    "Link", "Check", "Text", "Small Text", "Long Text", "Attach",
}


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

    meta = frappe.get_meta(doctype)
    if not meta.has_field(group_by):
        return {"error": f"'{group_by}' is not a valid field on {doctype}."}
    if days and not meta.has_field(date_field):
        return {"error": f"'{date_field}' is not a valid field on {doctype}."}

    conditions = []
    values = []

    if days:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conditions.append(f"`{date_field}` >= %s")
        values.append(cutoff)

    if meta.has_field("docstatus"):
        conditions.append("docstatus != 2")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_field = None
    for candidate in ("grand_total", "total", "amount"):
        if meta.has_field(candidate):
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
            doc.insert(ignore_permissions=True)
            created.append(doc.name)
        except Exception as e:
            errors.append(f"Row {idx + 1}: {str(e)}")

    frappe.db.commit()

    return {
        "success": True,
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
    doc.insert()
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

    dashboard.append("charts", {"chart": real_chart})
    dashboard.save(ignore_permissions=True)
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
    if not frappe.get_meta(document_type).has_field(group_by_based_on):
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
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

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

    meta = frappe.get_meta(doctype)

    if fields:
        bad_fields = [f for f in fields if f != "name" and not meta.has_field(f)]
        if bad_fields:
            return {"error": f"Invalid fields for {doctype}: {bad_fields}"}
    else:
        fields = ["name"] + [f.fieldname for f in meta.fields if f.in_list_view][:6]

    if filters:
        for key in filters:
            if not meta.has_field(key) and key != "name":
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