"""
api.py — whitelisted methods exposed to the erpnext_copilot Gemini agent.

Every function here is decorated with @frappe.whitelist(), meaning it runs
as whichever Frappe user is currently authenticated (e.g. the bench console
session user) and is subject to that user's real ERPNext permissions via
frappe.get_list() and explicit doc.check_permission() calls. (NOT
frappe.get_all() or a bare frappe.get_doc() — both skip Frappe's
permission/row-level restriction checks by design; every read in this
file was audited and fixed to use the permission-respecting equivalent.)
No separate API key or manual permission logic is used — this is the main
advantage of running inside the Frappe app rather than as an external
script.
"""
import json
from typing import Optional
import frappe

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


def _resolve_file_doc(file_name: str):
    """Look up a File record by name or file_url, with an explicit read
    permission check. frappe.get_doc() alone does not check permissions —
    without this, any authenticated user could preview/read the content
    of ANY uploaded file on the site by name or URL, regardless of
    whether they have access to whatever it's attached to. Raises
    frappe.PermissionError (caller should catch it) if the file exists
    but isn't readable by the current user; returns None if no match.
    """
    file_doc = None
    if frappe.db.exists("File", file_name):
        file_doc = frappe.get_doc("File", file_name)
    else:
        matches = frappe.get_list("File", filters={"file_url": file_name}, limit=1)
        if matches:
            file_doc = frappe.get_doc("File", matches[0].name)
    if file_doc:
        file_doc.check_permission("read")
    return file_doc


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

    # frappe.get_list (not get_all) — get_all explicitly skips Frappe's
    # permission/match-condition filtering by design; get_list applies it.
    invoices = frappe.get_list(
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
    before being used to build a query, since Frappe does not parameterize
    identifiers (table/column names) the way it does values.

    Built on frappe.get_list() with aggregate expressions in `fields`,
    NOT frappe.db.sql(). The identifier-safety story was already correct
    (validated field names before interpolation) — what raw db.sql()
    never did, and get_list() does automatically, is apply Frappe's
    row-level permission/match-condition filtering for the calling user.
    A user restricted to certain records (by User Permission, e.g. scoped
    to one Company or Territory) would previously have seen every record
    regardless of that restriction; get_list() closes that.
    """
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    fieldnames = _cached_doctype_fieldnames(doctype)
    if group_by not in fieldnames:
        return {"error": f"'{group_by}' is not a valid field on {doctype}."}
    if days and date_field not in fieldnames:
        return {"error": f"'{date_field}' is not a valid field on {doctype}."}

    filters = {}
    if days:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        filters[date_field] = [">=", cutoff]
    if "docstatus" in fieldnames:
        filters["docstatus"] = ["!=", 2]

    total_field = None
    for candidate in ("grand_total", "total", "amount"):
        if candidate in fieldnames:
            total_field = candidate
            break

    fields = [f"`{group_by}` as group_value", "count(name) as count"]
    if total_field:
        fields.append(f"sum(`{total_field}`) as total")

    try:
        results = frappe.get_list(
            doctype,
            filters=filters,
            fields=fields,
            group_by=group_by,
            order_by="count desc",
        )
    except frappe.PermissionError:
        return {"error": f"You don't have permission to read {doctype}."}

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

    try:
        file_doc = _resolve_file_doc(file_name)
    except frappe.PermissionError:
        return {"error": "You don't have permission to read this file."}

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

    try:
        file_doc = _resolve_file_doc(file_name)
    except frappe.PermissionError:
        return {"error": "You don't have permission to read this file."}

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
    return {
        "success": True,
        "doctype": doctype_name,
        "fields_created": [f["fieldname"] for f in fields],
        "url": _desk_list_url(doctype_name),
    }


@frappe.whitelist()
def list_dashboards():
    """List all available Dashboards in ERPNext (e.g. Selling, Buying, Accounts)."""
    dashboards = frappe.get_list("Dashboard", fields=["name", "dashboard_name", "module"])
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
        charts = frappe.get_list("Dashboard Chart", filters={"chart_name": chart_name}, limit=1)
        if charts:
            real_chart = charts[0].name
        else:
            return {"error": f"Dashboard Chart '{chart_name}' does not exist in Frappe."}

    real_dashboard = dashboard_name
    if not frappe.db.exists("Dashboard", real_dashboard):
        dashboards = frappe.get_list("Dashboard", filters={"dashboard_name": dashboard_name}, limit=1)
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
                "url": _dashboard_view_url(real_dashboard),
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
        "url": _dashboard_view_url(real_dashboard),
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
    dashboard_url = None
    if dashboard_name:
        add_res = add_chart_to_dashboard(chart_name=doc.name, dashboard_name=dashboard_name)
        if "error" in add_res:
            added_to_dashboard_msg = f" (Failed to add to dashboard '{dashboard_name}': {add_res['error']})"
        else:
            added_to_dashboard_msg = f" and added to Dashboard '{dashboard_name}'."
            dashboard_url = add_res.get("url")

    result = {
        "success": True,
        "chart_name": doc.name,
        "url": _dashboard_chart_url(doc.name),
        "message": f"Chart '{chart_name}' created{added_to_dashboard_msg}. View it under Dashboard > Charts, or in the Dashboard UI.",
    }
    if dashboard_url:
        result["dashboard_url"] = dashboard_url
    return result

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

    try:
        results = frappe.get_list(doctype, filters=filters or {}, fields=fields, limit=limit)
    except frappe.PermissionError:
        return {"error": f"You don't have permission to read {doctype}."}
    return json.loads(json.dumps(results, default=str))


@frappe.whitelist()
def search_doctype(doctype: str, query: str, limit: int = 20):
    """Text search within a single DocType — searches its title/name field
    for a loose match, e.g. find customers by partial name."""
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    meta = frappe.get_meta(doctype)
    search_field = meta.title_field or "name"

    try:
        results = frappe.get_list(
            doctype,
            filters={search_field: ["like", f"%{query}%"]},
            fields=["name", search_field] if search_field != "name" else ["name"],
            limit=limit,
        )
    except frappe.PermissionError:
        return {"error": f"You don't have permission to read {doctype}."}
    return json.loads(json.dumps(results, default=str))


@frappe.whitelist()
def fetch(doctype: str, name: str):
    """Get a single document by its ID/name."""
    if not frappe.db.exists(doctype, name):
        return {"error": f"No {doctype} found with name '{name}'."}
    doc = frappe.get_doc(doctype, name)
    # frappe.get_doc() alone does not check permission — without this, the
    # agent could fetch ANY document by name regardless of whether the
    # calling user could actually open it in the UI.
    try:
        doc.check_permission("read")
    except frappe.PermissionError:
        return {"error": f"You don't have permission to read this {doctype}."}
    return json.loads(doc.as_json())


@frappe.whitelist()
def search(query: str, limit: int = 20):
    """Global search across all DocTypes using Frappe's built-in search,
    for when the user doesn't know which DocType to look in."""
    from frappe.utils.global_search import search as global_search
    results = global_search(text=query, start=0, limit=limit)
    return json.loads(json.dumps(results, default=str))


_VALID_AGGREGATIONS = {"sum", "count", "avg"}


@frappe.whitelist()
def cross_doctype_summary(rows: list):
    """Compute a small summary where EACH ROW aggregates a different
    DocType — e.g. total outstanding Sales Invoice amount on one row,
    total Delivery Note grand_total on another. This is exactly the case
    a single Report Builder report can't express (it's scoped to one
    doctype); this always answers live in chat instead, and can never be
    persisted as a saved Report — there's no `report_type` that covers a
    genuine cross-doctype join without either raw SQL (which bypasses
    Frappe's permission model) or a stored Script Report (arbitrary code
    execution), both of which this app deliberately avoids.

    Each row's field/filter names are checked against real metadata before
    use, and the aggregation itself runs through frappe.get_list() — not
    frappe.db.count()/frappe.db.get_value(), which operate below Frappe's
    permission layer and don't apply row-level restrictions (User
    Permissions, match_conditions) the way get_list() does.
    """
    if isinstance(rows, str):
        rows = json.loads(rows)

    results = []
    for row in rows:
        doctype = row.get("doctype")
        field = row.get("field")
        aggregation = (row.get("aggregation") or "sum").lower()
        filters = row.get("filters") or {}
        label = row.get("label") or f"{aggregation}({field or 'name'}) on {doctype}"

        if not frappe.db.exists("DocType", doctype):
            results.append({"label": label, "error": f"'{doctype}' is not a valid DocType."})
            continue
        if aggregation not in _VALID_AGGREGATIONS:
            results.append({"label": label, "error": f"'{aggregation}' must be one of {sorted(_VALID_AGGREGATIONS)}."})
            continue

        fieldnames = _cached_doctype_fieldnames(doctype)
        if aggregation != "count":
            if not field:
                results.append({"label": label, "error": "'field' is required unless aggregation is 'count'."})
                continue
            if field not in fieldnames:
                results.append({"label": label, "error": f"'{field}' is not a valid field on {doctype}."})
                continue

        bad_filters = [k for k in filters if k not in fieldnames]
        if bad_filters:
            results.append({"label": label, "error": f"Invalid filter fields for {doctype}: {bad_filters}"})
            continue

        try:
            # field name is validated against real metadata above, so it's
            # safe to interpolate into the aggregate expression; filters
            # are passed as values, not string-built, same as elsewhere.
            agg_field = "count(name) as total" if aggregation == "count" else f"{aggregation}(`{field}`) as total"
            rows_result = frappe.get_list(doctype, filters=filters, fields=[agg_field])
            value = (rows_result[0].total if rows_result else 0) or 0
        except Exception as e:
            results.append({"label": label, "error": str(e)})
            continue

        results.append({"label": label, "doctype": doctype, "aggregation": aggregation, "value": value})

    return {"rows": results}


# ---------------------------------------------------------------------------
# Reports — the agent never depends on one of these existing to answer a
# question: search_documents/aggregate_documents already handle any
# doctype/filter combination inline in chat, and that's the universal
# fallback regardless of what's below. find_reports is purely additive
# ("there's also a saved report for this, here's a link"); save_report is
# only ever called when the user explicitly asks to save the current view
# as a reusable report, never automatically.
# ---------------------------------------------------------------------------

def _desk_list_url(doctype: str) -> str:
    """Standard Frappe desk route to a DocType's list view
    (/app/<slug>) — this specific pattern is stable and well-documented
    across modern Frappe versions, more so than the chart/dashboard
    routes below."""
    slug = frappe.scrub(doctype).replace("_", "-")
    return f"/app/{slug}"


def _dashboard_chart_url(chart_name: str) -> str:
    """Route to a Dashboard Chart's own form view. Less certain than
    _desk_list_url — chart-specific desk routes have had more version
    variance. Verify this resolves on your instance."""
    from urllib.parse import quote
    return f"/app/dashboard-chart/{quote(chart_name)}"


def _dashboard_view_url(dashboard_name: str) -> str:
    """Route to a Dashboard's own view page. Same caveat as
    _dashboard_chart_url — verify against your instance before relying
    on it in a demo."""
    from urllib.parse import quote
    return f"/app/dashboard-view/{quote(dashboard_name)}"


def _report_url(doctype: str, report_name: Optional[str] = None) -> str:
    """Best-effort desk route to a doctype's Report view, or a specific
    saved report. Frappe's exact URL scheme for report routing has shifted
    across versions — verify this resolves correctly on your instance
    before relying on it; the doctype-route slug (spaces/underscores to
    hyphens, lowercased) is the part most likely to need adjusting for an
    unusual doctype name.
    """
    slug = frappe.scrub(doctype).replace("_", "-")
    if report_name:
        from urllib.parse import quote
        return f"/app/{slug}/view/report?report_name={quote(report_name)}"
    return f"/app/{slug}/view/report"


@frappe.whitelist()
def find_reports(doctype: str):
    """List existing saved Reports already available for a DocType, so the
    agent can offer one instead of assuming nothing fits. Read-only —
    never creates anything."""
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}

    reports = frappe.get_list(
        "Report",
        filters={"ref_doctype": doctype, "disabled": 0},
        fields=["name", "report_type"],
        limit=20,
    )
    for r in reports:
        r["url"] = _report_url(doctype, r["name"])
    return {
        "doctype": doctype,
        "reports": reports,
        "generic_report_view_url": _report_url(doctype),
    }


@frappe.whitelist()
def save_report(report_name: str, doctype: str, columns: list, filters: Optional[dict] = None):
    """Save the current filtered view as a reusable Report Builder report —
    only called when the user explicitly asks to save something, never
    automatically after just answering a question. columns and any
    filtered field are validated against real metadata first, same
    discipline as aggregate_documents/search_documents.

    NOTE: the exact JSON schema Frappe's Report Builder expects in the
    `json` field (the shape of `filters` and `columns` below) has varied
    across Frappe versions in ways I could not verify without a live
    instance — treat this as a solid starting point to test against your
    actual site, not a guaranteed-correct implementation.
    """
    if not frappe.db.exists("DocType", doctype):
        return {"error": f"'{doctype}' is not a valid DocType."}
    if frappe.db.exists("Report", report_name):
        return {"error": f"A report named '{report_name}' already exists."}

    fieldnames = _cached_doctype_fieldnames(doctype)
    bad_columns = [c for c in columns if c not in fieldnames]
    if bad_columns:
        return {"error": f"Invalid columns for {doctype}: {bad_columns}", "valid_fields": sorted(fieldnames)}

    if filters:
        bad_filters = [k for k in filters if k not in fieldnames]
        if bad_filters:
            return {"error": f"Invalid filter fields for {doctype}: {bad_filters}"}

    report_json = json.dumps({
        "filters": [[doctype, k, "=", v] for k, v in (filters or {}).items()],
        "columns": [{"fieldname": c, "fieldtype": "Data", "label": c.replace("_", " ").title()} for c in columns],
    })

    doc = frappe.get_doc({
        "doctype": "Report",
        "report_name": report_name,
        "ref_doctype": doctype,
        "report_type": "Report Builder",
        "is_standard": "No",
        "json": report_json,
    })
    try:
        doc.insert()
    except frappe.PermissionError:
        return {"error": "You don't have permission to create Reports."}

    return {"success": True, "report_name": doc.name, "url": _report_url(doctype, doc.name)}