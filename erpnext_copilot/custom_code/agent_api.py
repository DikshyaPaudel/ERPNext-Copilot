"""
agent_api.py — whitelisted HTTP-callable endpoints for the erpnext_copilot
web chat page. Adapts the console REPL logic in gemini_agent.py into a
request/response shape: one call in, one reply out, with conversation
history and any pending write-confirmation kept server-side in cache
(keyed per user) since HTTP has no persistent process the way a console
session does.

History is stored as the full serialized Content/Part objects (not a
hand-picked subset of fields) because Gemini 3.x attaches a
thought_signature to function-call parts, and requires that signature to
still be present when that turn is sent back in a later request. Dropping
unrecognized fields when flattening to a simplified dict caused a
400 INVALID_ARGUMENT error the first time a multi-turn tool-confirmation
flow was tested through the web interface (never surfaced in bench console
testing, since that flow kept the in-memory history list directly rather
than round-tripping through cache).

--------------------------------------------------------------------------
Perf notes (see project retro on response latency):

#1 History trimming — the FULL history is still what's cached (needed for
   the thought_signature reason above), but only the last HISTORY_TURNS
   genuine user turns (and everything chained after each of them —
   tool calls, tool responses) are sent to the model on each call. See
   _trimmed() for why this has to cut at turn boundaries, not a fixed
   Content-object count.

#2/#3 Streaming + status — client.models.generate_content_stream() is used
   instead of a single blocking call, so plain-text replies are pushed to
   the browser token-chunk by token-chunk over Frappe's realtime (socketio)
   channel as they arrive, and a short status line ("Calling X...") is
   pushed the moment a tool is about to be dispatched. This doesn't reduce
   total model latency, but removes the "nothing is happening" dead air
   that's most of what reads as slow, especially across a multi-tool chain.

   Function-call parts are not token-streamed by Gemini — they arrive
   whole in a single chunk — so they're appended to the reconstructed
   Content verbatim, untouched, specifically so their thought_signature
   survives exactly as it did in the non-streaming code path. Only text
   parts are coalesced from multiple chunks. (Streaming chunk granularity
   can vary by SDK version — if you're on a different google-genai
   version than this was written against, sanity-check that function-call
   parts still arrive as a single complete part before trusting this in
   production.)

#6 Timeout — every model call is wrapped in a hard wall-clock timeout so a
   hung/slow Gemini API call can't leave the user staring at a disabled
   Send button indefinitely. Times out gracefully with a clear message
   instead of an unbounded wait.
--------------------------------------------------------------------------
"""

import concurrent.futures
import json
import math
import re
from typing import Optional

import frappe
from frappe import _
from google import genai
from google.genai import types

from erpnext_copilot.custom_code.gemini_agent import (
    TOOLS, TOOL_DISPATCH, WRITE_TOOLS, SYSTEM_INSTRUCTION,
    _sanitize_tool_result, _extract_text,
)

MAX_TOOL_STEPS = 5
HISTORY_TURNS = 8             # (#1) genuine user turns kept per model call — see _trimmed()
MODEL_TIMEOUT_SECONDS = 20    # (#6)
STREAM_EVENT = "copilot_stream_chunk"
STATUS_EVENT = "copilot_status"
TITLE_MAX_CHARS = 40


class ModelTimeoutError(Exception):
    pass


def _pending_key():
    return f"erpnext_copilot_pending:{frappe.session.user}"


def _get_client():
    gemini_api_key = frappe.conf.get("gemini_api_key")
    if not gemini_api_key:
        frappe.throw("No Gemini API key configured. Run: bench set-config gemini_api_key '<key>'")
    return genai.Client(api_key=gemini_api_key)


def _content_to_raw(content):
    """Store the full Content as a JSON-safe dict, preserving everything —
    including thought_signature on function-call parts, which Gemini 3.x
    requires to be present on any function-call turn sent back to it."""
    return {"role": content.role, "parts": [p.model_dump(exclude_none=True, mode="json") for p in content.parts]}


def _raw_to_history(raw):
    history = []
    for item in raw:
        parts = [types.Part.model_validate(p) for p in item["parts"]]
        history.append(types.Content(role=item["role"], parts=parts))
    return history


# ---------------------------------------------------------------------------
# Conversation persistence — one Copilot Conversation doc per thread, scoped
# to the owning user by the doctype's own if_owner permission. Replaces the
# single per-user Redis blob the earlier version kept (that only ever held
# ONE conversation and expired after an hour — no way to have more than one
# thread, or come back to an older one, which is what a sidebar needs).
# ---------------------------------------------------------------------------

def _new_conversation_doc():
    doc = frappe.get_doc({
        "doctype": "Copilot Conversation",
        "title": "New chat",
        "history_json": "[]",
    })
    doc.insert()
    return doc


def _load_conversation(name):
    """Frappe's if_owner permission enforces that this raises
    frappe.PermissionError for a conversation the caller doesn't own —
    no manual ownership check needed here."""
    return frappe.get_doc("Copilot Conversation", name)


def _save_conversation(doc, history_raw, set_title_from=None):
    doc.history_json = json.dumps(history_raw)
    doc.last_message_at = frappe.utils.now_datetime()
    if set_title_from and doc.title in (None, "", "New chat"):
        stripped = set_title_from.strip()
        doc.title = (stripped[:TITLE_MAX_CHARS] + "…") if len(stripped) > TITLE_MAX_CHARS else stripped
    doc.save()


def _transcript_from_raw(history_raw):
    """Reduce the raw Gemini Content/Part history — which also contains
    internal function_call / function_response turns — down to just what
    the user actually saw: their own messages and the agent's text
    replies. Used to redraw the chat when the sidebar switches threads."""
    transcript = []
    for item in history_raw:
        role = item.get("role")
        parts = item.get("parts", [])
        text = "".join(p.get("text", "") for p in parts if p.get("text"))
        if not text:
            continue  # function_call / function_response turns carry no plain text
        if role == "user":
            transcript.append({"sender": "user", "text": text})
        elif role == "model":
            transcript.append({"sender": "agent", "text": text})
    return transcript


@frappe.whitelist()
def list_conversations():
    """Sidebar list — current user's own conversations, most recent first."""
    return frappe.get_all(
        "Copilot Conversation",
        filters={"owner": frappe.session.user},
        fields=["name", "title", "last_message_at"],
        order_by="last_message_at desc",
        limit=100,
    )


@frappe.whitelist()
def new_conversation():
    doc = _new_conversation_doc()
    return {"name": doc.name, "title": doc.title}


@frappe.whitelist()
def get_conversation(name: str):
    """Loads one thread for the sidebar to redraw in the main panel."""
    doc = _load_conversation(name)
    history_raw = json.loads(doc.history_json or "[]")
    return {"name": doc.name, "title": doc.title, "messages": _transcript_from_raw(history_raw)}


@frappe.whitelist()
def delete_conversation(name: str):
    frappe.delete_doc("Copilot Conversation", name)
    return {"success": True}


# ---------------------------------------------------------------------------
# Tool-call audit log — one Copilot Tool Call record per WRITE_TOOLS
# invocation. Logged as "Awaiting Confirmation" the moment the model
# proposes it, then resolved to Success/Error/Cancelled once the user
# decides and the tool actually runs. This exists independently of
# conversation history (which a user can delete) so there's a permanent
# record of who told the agent to change data, with what arguments, and
# what actually happened — the first thing anyone auditing an ERP system
# will ask for.
# ---------------------------------------------------------------------------

def _log_tool_call_pending(conversation_name, fn_name, fn_args):
    log = frappe.get_doc({
        "doctype": "Copilot Tool Call",
        "conversation": conversation_name,
        "tool_name": fn_name,
        "arguments": json.dumps(fn_args, default=str),
        "status": "Awaiting Confirmation",
        "requires_confirmation": 1,
        "started_at": frappe.utils.now_datetime(),
    })
    log.insert()
    return log.name


def _log_tool_call_resolved(log_name, approved, result):
    if not log_name:
        return  # best-effort — a missing log entry shouldn't break the actual tool call
    try:
        log = frappe.get_doc("Copilot Tool Call", log_name)
    except frappe.DoesNotExistError:
        return

    if not approved:
        log.status = "Cancelled"
    elif isinstance(result, dict) and result.get("error"):
        log.status = "Error"
    else:
        log.status = "Success"

    log.result = json.dumps(result, default=str)
    log.completed_at = frappe.utils.now_datetime()
    log.save()


def _trimmed(history):
    """(#1) Trim to the last HISTORY_TURNS genuine user turns and
    everything after them.

    NOT a fixed Content-object-count slice — that was the original
    version of this function, and it caused a real bug: Gemini requires
    a model turn containing a function_call to be immediately preceded,
    in whatever's actually sent, by a genuine user turn or a
    function-response turn. A tool-calling chain (user -> model calls
    tool A -> function response -> model calls tool B -> ...) has to
    stay intact; slicing by raw count can land the cut in the middle of
    that chain and strip off the turn Gemini expects to see right before
    a function_call, which is exactly what produced the
    400 INVALID_ARGUMENT "function call turn comes immediately after a
    user turn or after a function response turn" error.

    A function-response Content also has role == "user" (see
    types.Part.from_function_response usage below) but isn't a genuine
    turn boundary — only a Content whose parts contain neither a
    function_call nor a function_response is. Those are the only safe
    places to cut.
    """
    boundaries = [
        i for i, c in enumerate(history)
        if c.role == "user" and not any(
            getattr(p, "function_call", None) or getattr(p, "function_response", None)
            for p in c.parts
        )
    ]
    if len(boundaries) <= HISTORY_TURNS:
        return history
    start = boundaries[-HISTORY_TURNS]
    return history[start:]


def _publish_status(text):
    """(#3) Push a lightweight status line to just this user's browser
    session so a multi-step tool chain doesn't look frozen. Best-effort —
    a pub/sub hiccup should never break the actual agent turn."""
    try:
        frappe.publish_realtime(STATUS_EVENT, {"text": text}, user=frappe.session.user)
    except Exception:
        pass


def _publish_chunk(text):
    """(#2) Push one piece of streamed reply text to the browser."""
    try:
        frappe.publish_realtime(STREAM_EVENT, {"text": text}, user=frappe.session.user)
    except Exception:
        pass


def _generate_and_stream(client, history, config):
    """Runs one streamed model call. Text parts are coalesced and pushed
    to the browser as they arrive; function-call parts are collected
    verbatim (untouched) so their thought_signature is preserved exactly
    as the non-streaming path would have kept it.

    Returns (candidate_content, function_call_or_None) — candidate_content
    is a reconstructed types.Content matching what response.candidates[0]
    .content would have been from a non-streaming call, so every other
    piece of this file's history handling is unaffected by the switch.
    """
    collected_parts = []
    text_so_far = ""

    for chunk in client.models.generate_content_stream(model="gemini-3.6-flash", contents=history, config=config):
        candidate = chunk.candidates[0] if chunk.candidates else None
        if not candidate or not candidate.content or not candidate.content.parts:
            continue
        for part in candidate.content.parts:
            if getattr(part, "function_call", None):
                collected_parts.append(part)
            elif getattr(part, "text", None):
                text_so_far += part.text
                _publish_chunk(part.text)
            else:
                collected_parts.append(part)

    if text_so_far:
        collected_parts.append(types.Part(text=text_so_far))

    candidate_content = types.Content(role="model", parts=collected_parts)
    function_call = next((p.function_call for p in collected_parts if getattr(p, "function_call", None)), None)
    return candidate_content, function_call


def _call_model(client, history, config):
    """(#6) Wraps the streamed call with a hard timeout so a hung/slow
    API call can't leave the request open indefinitely."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_generate_and_stream, client, _trimmed(history), config)
        try:
            return future.result(timeout=MODEL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            raise ModelTimeoutError(f"Gemini didn't respond within {MODEL_TIMEOUT_SECONDS}s.")


@frappe.whitelist()
def ask_agent(message: str, conversation: Optional[str] = None):
    """One turn of the conversation. Returns either a final reply, or a
    pending_action the frontend must confirm before it's executed. If no
    conversation id is given (or it's a brand-new thread), a new
    Copilot Conversation doc is created and its name is returned so the
    frontend can pass it back on the next turn."""
    client = _get_client()
    config_with_tools = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, tools=[TOOLS])

    doc = _load_conversation(conversation) if conversation else _new_conversation_doc()
    history_raw = json.loads(doc.history_json or "[]")
    history = _raw_to_history(history_raw)

    user_content = types.Content(role="user", parts=[types.Part(text=message)])
    history.append(user_content)
    history_raw.append(_content_to_raw(user_content))

    for _ in range(MAX_TOOL_STEPS):
        try:
            candidate_content, function_call = _call_model(client, history, config_with_tools)
        except ModelTimeoutError as e:
            _save_conversation(doc, history_raw, set_title_from=message)
            return {"type": "reply", "text": f"Still working on that — {e} Please try again in a moment.", "conversation": doc.name}

        history.append(candidate_content)
        history_raw.append(_content_to_raw(candidate_content))

        if not function_call:
            _save_conversation(doc, history_raw, set_title_from=message)
            return {"type": "reply", "text": _extract_text(candidate_content), "conversation": doc.name}

        fn_name = function_call.name
        fn_args = dict(function_call.args)

        if fn_name in WRITE_TOOLS:
            # Stop here — don't execute yet. Store enough state to resume
            # after the frontend gets user confirmation.
            log_name = _log_tool_call_pending(doc.name, fn_name, fn_args)
            frappe.cache().set_value(_pending_key(), {
                "tool": fn_name, "args": fn_args, "history_raw": history_raw, "conversation": doc.name,
                "tool_call_log": log_name,
            }, expires_in_sec=600)
            _save_conversation(doc, history_raw, set_title_from=message)
            return {"type": "pending_action", "tool": fn_name, "args": fn_args, "conversation": doc.name}

        _publish_status(f"Calling {fn_name}...")

        fn = TOOL_DISPATCH.get(fn_name)
        result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
        clean_result = _sanitize_tool_result(result)

        fn_response_content = types.Content(role="user", parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})])
        history.append(fn_response_content)
        history_raw.append(_content_to_raw(fn_response_content))

    _save_conversation(doc, history_raw, set_title_from=message)
    return {"type": "reply", "text": "(stopped after reaching the tool-call safety limit)", "conversation": doc.name}


@frappe.whitelist()
def confirm_pending_action(approved: bool):
    """Executes (or cancels) the write action left pending by ask_agent,
    then continues the conversation so the model can report the outcome."""
    pending = frappe.cache().get_value(_pending_key())
    if not pending:
        return {"type": "reply", "text": "No pending action to confirm."}

    frappe.cache().delete_value(_pending_key())

    fn_name = pending["tool"]
    fn_args = pending["args"]
    history_raw = pending["history_raw"]
    doc = _load_conversation(pending["conversation"])

    if approved:
        _publish_status(f"Calling {fn_name}...")
        fn = TOOL_DISPATCH.get(fn_name)
        result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
    else:
        result = {"cancelled": True, "message": "Action cancelled by user."}

    _log_tool_call_resolved(pending.get("tool_call_log"), approved, result)

    clean_result = _sanitize_tool_result(result)
    fn_response_content = types.Content(role="user", parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})])
    history_raw.append(_content_to_raw(fn_response_content))
    _save_conversation(doc, history_raw)  # save first, so this result is in place before we continue the conversation

    return _continue_after_tool_result(doc, history_raw)


def _continue_after_tool_result(doc, history_raw):
    """Lets the model produce a natural-language summary of a tool result
    (or chain into another tool call, e.g. list_dashboards right after creating a chart)."""
    client = _get_client()
    config_with_tools = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, tools=[TOOLS])
    history = _raw_to_history(history_raw)

    for _ in range(MAX_TOOL_STEPS):
        try:
            candidate_content, function_call = _call_model(client, history, config_with_tools)
        except ModelTimeoutError as e:
            _save_conversation(doc, history_raw)
            return {"type": "reply", "text": f"Still working on that — {e} Please try again in a moment.", "conversation": doc.name}

        history.append(candidate_content)
        history_raw.append(_content_to_raw(candidate_content))

        if not function_call:
            _save_conversation(doc, history_raw)
            return {"type": "reply", "text": _extract_text(candidate_content), "conversation": doc.name}

        fn_name = function_call.name
        fn_args = dict(function_call.args)

        if fn_name in WRITE_TOOLS:
            log_name = _log_tool_call_pending(doc.name, fn_name, fn_args)
            frappe.cache().set_value(_pending_key(), {
                "tool": fn_name, "args": fn_args, "history_raw": history_raw, "conversation": doc.name,
                "tool_call_log": log_name,
            }, expires_in_sec=600)
            _save_conversation(doc, history_raw)
            return {"type": "pending_action", "tool": fn_name, "args": fn_args, "conversation": doc.name}

        _publish_status(f"Calling {fn_name}...")

        fn = TOOL_DISPATCH.get(fn_name)
        result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
        clean_result = _sanitize_tool_result(result)

        fn_response_content = types.Content(
            role="user",
            parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})],
        )
        history.append(fn_response_content)
        history_raw.append(_content_to_raw(fn_response_content))

    _save_conversation(doc, history_raw)
    return {"type": "reply", "text": "(stopped after reaching the tool-call safety limit)", "conversation": doc.name}


@frappe.whitelist()
def get_tool_calls(conversation: str):
    """Audit trail for one conversation — every write tool the agent
    proposed, whether it was confirmed or cancelled, and what happened.
    if_owner permission on Copilot Tool Call means this naturally only
    returns the calling user's own records."""
    return frappe.get_all(
        "Copilot Tool Call",
        filters={"conversation": conversation},
        fields=["name", "tool_name", "status", "arguments", "result", "started_at", "completed_at"],
        order_by="creation asc",
    )


@frappe.whitelist()
def reset_conversation():
    """Kept for backward compatibility — clears any pending write
    confirmation for the current user. Starting a fresh thread is now
    done via new_conversation(), which the sidebar's '+ New chat' calls."""
    frappe.cache().delete_value(_pending_key())
    return {"success": True}


# ---------------------------------------------------------------------------
# Copilot for Dashboard/Report Editing (like lumen_reports.copilot.copilot)
# ---------------------------------------------------------------------------
# This endpoint works with a "board" state (widgets + layout) and returns
# a proposal with changes that the user can apply/discard. One Undo takes
# the whole turn back.

import copy
import math
import re

COLS = 12
MAX_OPS = 20
MAX_ROW = 300
HISTORY_TURNS = 10
MAX_SUMMARY_WIDGETS = 12

STATIC_TYPES = ("Heading", "Text", "Divider", "Image")
AI_ELEMENTS = ("Heading", "Text", "Divider")

THEME_PRESETS = {
    "light": "Clean light look with blue accent",
    "dark": "Near-black navy with bright blue accent",
    "emerald": "Dark green with teal and lime accents",
    "midnight": "Deep indigo dark with periwinkle accent",
    "sand": "Warm beige light with ochre and olive",
    "paper": "Editorial white with black ink, one red accent",
}
CARD_STYLES = ("flat", "outlined", "elevated", "glass")
DENSITIES = ("compact", "comfort")
FONTS = ("", "system", "serif", "mono")
SURFACES = ("solid", "gradient", "tint")
TINTS = ("blue", "green", "amber", "violet")

TYPE_FAMILIES = {
    "number": {"Number Card", "Gauge"},
    "category": {
        "Bar Chart", "Horizontal Bar", "Pie Chart", "Donut Chart",
        "Funnel", "Rings", "Radar", "Progress Bars",
    },
    "time": {"Line Chart", "Area Chart", "Bar Chart", "Sparkline", "Waterfall"},
    "matrix": {"Bar Chart", "Stacked Bar", "Line Chart", "Area Chart", "Heatmap", "Radar"},
    "points": {"Scatter"},
    "tree": {"Tree Report"},
    "rows": {"Table"},
}

SIZES = {
    "Number Card": (3, 2),
    "Gauge": (3, 3),
    "Sparkline": (3, 3),
    "Heading": (12, 1),
    "Divider": (12, 1),
    "Text": (12, 2),
    "Table": (12, 5),
    "Heatmap": (12, 5),
    "Tree Report": (12, 7),
}


_DIACRITICS = re.compile("[ً-ْٰ]")
_ARABIC = re.compile("[؀-ۿ]")


def _plain(text, limit=400) -> str:
    """Copy a person reads. No Arabic diacritics and no dashes as punctuation."""
    text = _DIACRITICS.sub("", str(text or "")).strip()
    comma = "، " if _ARABIC.search(text) else ", "
    text = re.sub(r"\s*[—–]\s*", comma, text)
    text = re.sub(r"\s{2,}", " ", text)
    return text[:limit]


def _shape(widget) -> str:
    """The kind of result a widget's query produces."""
    t = widget.get("widget_type")
    if t in STATIC_TYPES:
        return "element"
    q = widget.get("query") or {}
    if q.get("shape") == "tree":
        return "tree"
    if q.get("aggregate_y"):
        return "points"
    if q.get("group_by2"):
        return "matrix"
    if q.get("group_by"):
        return "time" if (q.get("group_by") or {}).get("time_grain") else "category"
    if q.get("fields") and not q.get("aggregate"):
        return "rows"
    return "number"


def _read_board(board) -> dict:
    """Normalise the studio's board into the state the simulator works on."""
    board = frappe.parse_json(board) if isinstance(board, str) else (board or {})
    widgets = []
    for w in board.get("widgets") or []:
        if not isinstance(w, dict) or not w.get("widget_id"):
            continue
        widgets.append(
            {
                "widget_id": str(w["widget_id"]),
                "title": w.get("title") or "",
                "widget_type": w.get("widget_type") or "Bar Chart",
                "query": w.get("query") or {},
                "style": w.get("style") or {},
            }
        )
    types = {w["widget_id"]: w["widget_type"] for w in widgets}
    layout = {}
    for item in board.get("layout") or []:
        wid = str((item or {}).get("widget_id") or "")
        if wid in types:
            layout[wid] = _clamp(item, element=types[wid] in STATIC_TYPES)
    for w in widgets:
        if w["widget_id"] not in layout:
            width, height = SIZES.get(w["widget_type"], (6, 5))
            layout[w["widget_id"]] = {"x": 0, "y": _bottom(layout), "w": width, "h": height}
    return {
        "title": str(board.get("title") or ""),
        "theme": board.get("theme") if isinstance(board.get("theme"), dict) else {},
        "widgets": widgets,
        "layout": layout,
    }


def _clamp(item, element=False) -> dict:
    def num(key, default):
        try:
            return int(round(float(item.get(key, default))))
        except (TypeError, ValueError):
            return default

    w = max(2, min(COLS, num("w", 6)))
    h = max(1 if element else 2, min(24, num("h", 5)))
    x = max(0, min(COLS - w, num("x", 0)))
    y = max(0, min(MAX_ROW, num("y", 0)))
    return {"x": x, "y": y, "w": w, "h": h}


def _bottom(layout) -> int:
    return max((p["y"] + p["h"] for p in layout.values()), default=0)


def _collides(a, b) -> bool:
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def _compact(layout: dict) -> dict:
    """Resolve overlaps, then float everything up to close the gaps."""
    order = sorted(layout.items(), key=lambda kv: (kv[1]["y"], kv[1]["x"]))
    placed = []
    for wid, item in order:
        item = dict(item)
        while any(_collides(item, other) for _, other in placed):
            item["y"] += 1
        placed.append((wid, item))
    for i, (_wid, item) in enumerate(placed):
        while item["y"] > 0:
            trial = dict(item, y=item["y"] - 1)
            if any(_collides(trial, other) for j, (_, other) in enumerate(placed) if j != i):
                break
            item["y"] -= 1
    return {wid: item for wid, item in placed}


def _shift_down(layout: dict, rows: int):
    for item in layout.values():
        item["y"] += rows


def _new_id(prefix="cp") -> str:
    return prefix + frappe.generate_hash(length=6)


def _describe(widget, pos) -> dict:
    """What the model is told about one widget."""
    d = {
        "id": widget["widget_id"],
        "title": widget.get("title"),
        "type": widget.get("widget_type"),
        "x": pos["x"],
        "y": pos["y"],
        "w": pos["w"],
        "h": pos["h"],
    }
    if widget["widget_type"] in STATIC_TYPES:
        d["text"] = str((widget.get("style") or {}).get("text") or "")[:80]
        return d
    q = widget.get("query") or {}
    d["source"] = (
        f"{q.get('doctype')} (lines of {q['parent_doctype']})" if q.get("parent_doctype") else q.get("doctype")
    )
    agg = q.get("aggregate") or {}
    if agg:
        d["measure"] = " ".join(p for p in (agg.get("function"), agg.get("field")) if p)
    group = q.get("group_by") or {}
    if group:
        d["by"] = group.get("field") + (f" per {group['time_grain']}" if group.get("time_grain") else "")
    if q.get("group_by2"):
        d["split_by"] = (q.get("group_by2") or {}).get("field")
    d["shape"] = _shape(widget)
    return d


def _guide() -> str:
    presets = "\n".join(f"    {k}: {v}" for k, v in THEME_PRESETS.items())
    families = "\n".join(f"    {k}: {', '.join(sorted(v))}" for k, v in TYPE_FAMILIES.items())
    return (
        "OPERATIONS (use only these, each is a JSON object with an \"op\" and a \"label\"):\n"
        '- {"op": "set_layout", "items": [{"id", "x", "y", "w", "h"}]}\n'
        "    Move and resize. List every widget whose position changes. x + w must not exceed 12.\n"
        "    Sizes that read well: number card 3x2 (four in a row), gauge 3x3, chart 6x5 (two in a row)\n"
        "    or 8x5 beside a 4x5 companion, wide chart or table 12x5, heading or divider 12x1.\n"
        '- {"op": "set_theme", "theme": {...}}  Only the keys you change:\n'
        "    preset, one of:\n" + presets + "\n"
        '    brand: a hex color such as "#2bc79b". card: flat, outlined, elevated or glass.\n'
        "    radius: 0 to 28. density: compact or comfort. font: \"\" (default), system, serif or mono.\n"
        "    surface: solid, gradient or tint.\n"
        '- {"op": "retitle", "id", "title"}\n'
        '- {"op": "set_type", "id", "widget_type"}  Only within the widget\'s shape family:\n'
        + families
        + "\n"
        '- {"op": "set_style", "id", "accent": 0-7}  chart color slot. Number cards take "tint":\n'
        "    blue, green, amber or violet instead.\n"
        '- {"op": "remove", "id"}\n'
        '- {"op": "add_element", "element": "Heading" | "Text" | "Divider", "text", "subtext" (Heading),\n'
        '    "level": 1 | 2 | 3 (Heading), "position": "top" | "bottom"}\n'
        '- {"op": "add_widgets", "request", "position": "top" | "bottom"}  New KPIs, charts or tables\n'
        "    built from data. \"request\" is a precise English description naming the measures,\n"
        "    dimensions and period. One operation may ask for several widgets.\n"
        '- {"op": "change_data", "id", "instruction"}  Change what one widget measures, filters or\n'
        "    groups by. \"instruction\" in English.\n"
        '- {"op": "add_summary", "position": "top" | "bottom"}  An executive summary written from the\n'
        "    board's real numbers.\n"
        '- {"op": "explain"}  They asked what the numbers show, not for a change.\n'
        '- {"op": "rename_dashboard", "title"}\n\n'
        "RULES\n"
        "- Every operation has a \"label\": one short line telling the person what it does, in THEIR\n"
        "  language, naming widgets by title and never by id.\n"
        "- \"reply\" is one or two short sentences in THEIR language.\n"
        "- If they wrote Arabic in any dialect, write formal Modern Standard Arabic with Western digits\n"
        "  (0-9) and no diacritics. In any language, never use the em dash.\n"
        "- Use only ids from the widget list. Never invent one.\n"
        "- Arranging a board (tidy, organize, arrange): number cards across the first row,\n"
        "  the main trend chart wide under them with a companion beside it, then the remaining charts\n"
        "  in pairs, tables and wide charts last, and any heading directly above what it introduces.\n"
        "- Dark mode means a dark preset (dark, midnight or emerald). Match color words to the\n"
        "  closest preset, and add a brand color only when they name a specific color.\n"
        "- When the message is ambiguous in a way that changes the result, return instead\n"
        '  {"clarify": "<one short question in their language>", "options": ["<2 to 4 short answers>"]}.\n'
        "- A greeting or thanks needs no operations: return an empty list with a reply.\n"
        "- Add 2 or 3 \"suggestions\": short next steps, in their language, that would genuinely\n"
        "  improve THIS board.\n\n"
        'Respond with ONLY JSON: {"reply": "...", "operations": [...], "suggestions": [...]}'
    )


def _history_block(history) -> str:
    if not history:
        return ""
    lines = "\n".join(f"{h.get('role', 'user')}: {str(h.get('text', ''))[:300]}" for h in history)
    return "Conversation so far:\n" + lines + "\n\n"


class _Turn:
    """Everything one turn needs while it simulates operations."""

    def __init__(self, state, prompt, key, model):
        self.state = state
        self.prompt = prompt
        self.key = key
        self.model = model
        self.changes = []
        self.notes = []
        self.analysis = None
        self.layout_touched = False

    def widget(self, wid):
        return next((w for w in self.state["widgets"] if w["widget_id"] == str(wid or "")), None)

    def done(self, op, fallback):
        self.changes.append(_plain(op.get("label") or fallback, 160))

    def refuse(self, text):
        self.notes.append(_plain(text, 200))

    def base_doctype(self):
        counts = {}
        for w in self.state["widgets"]:
            q = w.get("query") or {}
            dt = q.get("parent_doctype") or q.get("doctype")
            if dt:
                counts[dt] = counts.get(dt, 0) + 1
        return max(counts, key=counts.get) if counts else None

    def place(self, new_widgets, position):
        """Put freshly built widgets on the grid, as a block at the top or the bottom."""
        layout = self.state["layout"]
        packed = _auto_layout(new_widgets, start_y=0)
        block = max((p["y"] + p["h"] for p in packed), default=0)
        if position == "top":
            _shift_down(layout, block)
            start = 0
        else:
            start = _bottom(layout)
        for p in packed:
            layout[p["widget_id"]] = {"x": p["x"], "y": p["y"] + start, "w": p["w"], "h": p["h"]}
        if position == "top":
            self.state["widgets"][0:0] = new_widgets
        else:
            self.state["widgets"].extend(new_widgets)


def _auto_layout(widgets, start_y=0):
    """Simple auto-layout: pack widgets in rows."""
    layout = {}
    y = start_y
    x = 0
    row_height = 0
    for w in widgets:
        width, height = SIZES.get(w["widget_type"], (6, 5))
        if x + width > COLS:
            x = 0
            y += row_height
            row_height = 0
        layout[w["widget_id"]] = {"x": x, "y": y, "w": width, "h": height}
        x += width
        row_height = max(row_height, height)
    return [{"widget_id": wid, **pos} for wid, pos in layout.items()]


# Import AI functions
from erpnext_copilot.custom_code.api import ask_ai as ai_ask_ai
from erpnext_copilot.erpnext_copilot.query_engine import execute as query_execute


def _op_set_layout(turn, op):
    moved = 0
    for item in op.get("items") or []:
        if not isinstance(item, dict):
            continue
        w = turn.widget(item.get("id"))
        if not w:
            continue
        turn.state["layout"][w["widget_id"]] = _clamp(item, element=w["widget_type"] in STATIC_TYPES)
        moved += 1
    if moved:
        turn.layout_touched = True
        turn.done(op, _("Rearranged {0} widgets").format(moved))


def _op_set_theme(turn, op):
    raw = op.get("theme") if isinstance(op.get("theme"), dict) else {}
    clean = {}
    if raw.get("preset") in THEME_PRESETS:
        clean["preset"] = raw["preset"]
    brand = str(raw.get("brand") or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", brand):
        clean["brand"] = brand.lower()
    if raw.get("card") in CARD_STYLES:
        clean["card"] = raw["card"]
    if raw.get("density") in DENSITIES:
        clean["density"] = raw["density"]
    if raw.get("font") in FONTS:
        clean["font"] = raw["font"]
    if raw.get("surface") in SURFACES:
        clean["surface"] = raw["surface"]
    try:
        if raw.get("radius") not in (None, ""):
            clean["radius"] = max(0, min(28, int(raw["radius"])))
    except (TypeError, ValueError):
        pass
    if not clean:
        turn.refuse(_("The theme change did not name anything the theme panel offers."))
        return
    turn.state["theme"] = {**turn.state["theme"], **clean}
    turn.done(op, _("Changed the theme"))


def _op_retitle(turn, op):
    w = turn.widget(op.get("id"))
    title = _plain(op.get("title"), 120)
    if not w or not title:
        return
    w["title"] = title
    if w["widget_type"] == "Heading":
        w["style"] = {**w["style"], "text": title}
    turn.done(op, _("Renamed a widget to {0}").format(title))


def _op_set_type(turn, op):
    w = turn.widget(op.get("id"))
    target = op.get("widget_type")
    if not w or not target or w["widget_type"] == target:
        return
    family = TYPE_FAMILIES.get(_shape(w), set())
    if target not in family:
        turn.refuse(
            _("{0} cannot be shown as {1}: its data does not fit that chart.").format(w["title"], target)
        )
        return
    w["widget_type"] = target
    turn.done(op, _("Changed {0} to {1}").format(w["title"], target))


def _op_set_style(turn, op):
    w = turn.widget(op.get("id"))
    if not w or w["widget_type"] in STATIC_TYPES:
        return
    style = dict(w["style"])
    if w["widget_type"] == "Number Card":
        if op.get("tint") not in TINTS:
            return
        style["tint"] = op["tint"]
    else:
        try:
            style["accent"] = max(0, min(7, int(op.get("accent"))))
        except (TypeError, ValueError):
            return
    w["style"] = style
    turn.done(op, _("Recolored {0}").format(w["title"]))


def _op_remove(turn, op):
    w = turn.widget(op.get("id"))
    if not w:
        return
    turn.state["widgets"] = [x for x in turn.state["widgets"] if x["widget_id"] != w["widget_id"]]
    turn.state["layout"].pop(w["widget_id"], None)
    turn.layout_touched = True
    turn.done(op, _("Removed {0}").format(w["title"]))


def _op_add_element(turn, op):
    kind = op.get("element")
    if kind not in AI_ELEMENTS:
        return
    text = _plain(op.get("text"), 600)
    if kind != "Divider" and not text:
        return
    style = {"text": text}
    if kind == "Heading":
        style["align"] = "left"
        try:
            style["level"] = max(1, min(3, int(op.get("level") or 1)))
        except (TypeError, ValueError):
            style["level"] = 1
        if op.get("subtext"):
            style["subtext"] = _plain(op["subtext"], 160)
    elif kind == "Text":
        style.update({"align": "left", "size": "md", "framed": True})
    width, height = SIZES[kind]
    if kind == "Text":
        height = max(2, min(5, 1 + math.ceil(len(text) / 140)))
    element = {
        "widget_id": _new_id(),
        "title": text[:60] or kind,
        "widget_type": kind,
        "query": {},
        "style": style,
    }
    layout = turn.state["layout"]
    if op.get("position") == "top":
        _shift_down(layout, height)
        layout[element["widget_id"]] = {"x": 0, "y": 0, "w": width, "h": height}
        turn.state["widgets"].insert(0, element)
    else:
        layout[element["widget_id"]] = {"x": 0, "y": _bottom(layout), "w": width, "h": height}
        turn.state["widgets"].append(element)
    turn.done(op, _("Added a {0}").format(kind.lower()))


def _op_add_widgets(turn, op):
    request = str(op.get("request") or "").strip()
    if not request:
        return
    base = turn.base_doctype()
    if base:
        request = f"This dashboard is built on {base}. {request}"
    try:
        built = ai_ask_ai(
            request,
            existing_titles=[w["title"] for w in turn.state["widgets"] if w.get("title")],
            answered=True,
            analyze=False,
        )
    except Exception as e:
        frappe.clear_last_message()
        turn.refuse(_("Could not build the new widgets: {0}").format(_plain(str(e), 160)))
        return
    fresh = []
    for entry in built.get("widgets") or []:
        spec = entry.get("widget") or {}
        fresh.append(
            {
                "widget_id": _new_id(),
                "title": spec.get("title") or "New widget",
                "widget_type": spec.get("widget_type"),
                "query": spec.get("query") or {},
                "style": spec.get("style") or {},
            }
        )
        if entry.get("empty"):
            turn.refuse(_("{0} has no data yet. It was added anyway so it fills in when data arrives.").format(
                spec.get("title") or "A widget"
            ))
    if not fresh:
        turn.refuse(_("No widget could be built for that request."))
        return
    turn.place(fresh, "top" if op.get("position") == "top" else "bottom")
    turn.done(op, _("Added {0} widgets").format(len(fresh)))


def _op_change_data(turn, op):
    w = turn.widget(op.get("id"))
    instruction = str(op.get("instruction") or "").strip()
    if not w or not instruction:
        return
    if w["widget_type"] in STATIC_TYPES:
        turn.refuse(_("{0} carries no data to change.").format(w["title"] or w["widget_type"]))
        return
    try:
        # For now, we'll rebuild the widget using ask_ai with the instruction
        # In a full implementation, this would call modify_ai_widget
        built = ai_ask_ai(
            f"Modify this widget: {instruction}. Current widget: {w['title']} ({w['widget_type']})",
            existing_titles=[],
            answered=True,
            analyze=False,
        )
    except Exception as e:
        frappe.clear_last_message()
        turn.refuse(_("Could not change {0}: {1}").format(w["title"], _plain(str(e), 160)))
        return
    for entry in built.get("widgets") or []:
        spec = entry.get("widget") or {}
        w["title"] = spec.get("title") or w["title"]
        w["widget_type"] = spec.get("widget_type") or w["widget_type"]
        w["query"] = spec.get("query") or w["query"]
    turn.done(op, _("Changed the data behind {0}").format(w["title"]))


def _board_results(turn):
    """Run the board's data widgets once, as this person, for a narrative."""
    out = []
    for w in turn.state["widgets"]:
        if w["widget_type"] in STATIC_TYPES or not (w.get("query") or {}).get("doctype"):
            continue
        try:
            out.append({"widget": w, "result": query_execute(w["query"])})
        except Exception:
            frappe.clear_last_message()
        if len(out) >= MAX_SUMMARY_WIDGETS:
            break
    return out


def _read_numbers(turn, question):
    executed = _board_results(turn)
    if not executed:
        return None, []
    doctypes = []
    for item in executed:
        q = item["widget"]["query"]
        dt = q.get("parent_doctype") or q.get("doctype")
        if dt and dt not in doctypes:
            doctypes.append(dt)
    context = _ai_data_context(doctypes[:3])
    return _ai_analyze(question, executed, context, turn.key, turn.model), executed


def _ai_data_context(doctypes):
    """Get data context for AI analysis."""
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
    return "\n".join(context_parts) if context_parts else ""


def _ai_analyze(question, executed, context, key, model):
    """Use AI to analyze the query results."""
    from google import genai
    from google.genai import types
    import json
    
    client = genai.Client(api_key=key)
    
    results_summary = []
    for item in executed[:12]:
        widget = item["widget"]
        result = item["result"]
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

Question: {question}

Available data context:
{context}

Results:
{json.dumps(results_summary, indent=2, default=str)}

Output ONLY valid JSON:
{{
  "headline": "One-sentence summary of what the data shows",
  "findings": ["Specific insight 1 with numbers", "Specific insight 2 with numbers"],
  "watch": ["Potential concern or trend to monitor"]
}}"""

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return json.loads(response.text)
    except Exception:
        return None


def _op_add_summary(turn, op):
    analysis, _executed = _read_numbers(turn, turn.prompt or "Summarize this dashboard for an executive")
    if not analysis:
        turn.refuse(_("There was not enough data on the board to write a summary from."))
        return
    lines = [_plain(analysis["headline"], 220), ""]
    lines += ["• " + _plain(f, 220) for f in analysis["findings"]]
    text = "\n".join(lines)
    height = max(3, min(7, 2 + math.ceil(len(analysis["findings"]) * 0.8)))
    element = {
        "widget_id": _new_id(),
        "title": "Executive summary",
        "widget_type": "Text",
        "query": {},
        "style": {"text": text, "align": "left", "size": "md", "framed": True},
    }
    layout = turn.state["layout"]
    if op.get("position") == "bottom":
        layout[element["widget_id"]] = {"x": 0, "y": _bottom(layout), "w": 12, "h": height}
        turn.state["widgets"].append(element)
    else:
        _shift_down(layout, height)
        layout[element["widget_id"]] = {"x": 0, "y": 0, "w": 12, "h": height}
        turn.state["widgets"].insert(0, element)
    turn.done(op, _("Added an executive summary"))


def _op_explain(turn, op):
    analysis, _executed = _read_numbers(turn, turn.prompt)
    if not analysis:
        turn.refuse(_("There is no data on this board to read yet."))
        return
    turn.analysis = {
        "headline": _plain(analysis["headline"], 220),
        "findings": [_plain(f, 220) for f in analysis["findings"]],
        "watch": [_plain(x, 220) for x in analysis.get("watch") or []],
    }


def _op_rename_dashboard(turn, op):
    title = _plain(op.get("title"), 120)
    if not title:
        return
    turn.state["title"] = title
    turn.done(op, _("Renamed the dashboard to {0}").format(title))


OPERATIONS = {
    "set_layout": _op_set_layout,
    "set_theme": _op_set_theme,
    "retitle": _op_retitle,
    "set_type": _op_set_type,
    "set_style": _op_set_style,
    "remove": _op_remove,
    "add_element": _op_add_element,
    "add_widgets": _op_add_widgets,
    "change_data": _op_change_data,
    "add_summary": _op_add_summary,
    "explain": _op_explain,
    "rename_dashboard": _op_rename_dashboard,
}


@frappe.whitelist()
def copilot(
    prompt: str,
    board: str | dict,
    history: str | list | None = None,
    selected: str | None = None,
    answered: bool | str = False,
):
    """
    One copilot turn for dashboard/report editing.
    
    Returns the reply, list of changes, and the proposed board state.
    The frontend applies the proposal; nothing is saved here.
    
    Args:
        prompt: User's message
        board: Current board state (widgets + layout + theme)
        history: Conversation history (last 10 turns)
        selected: Widget ID currently selected (for "this widget" context)
        answered: Whether this is an answer to a clarification question
    
    Returns:
        {
            "reply": "Short reply",
            "changes": ["List of changes made"],
            "notes": ["Warnings/notes"],
            "analysis": {"headline": "...", "findings": [...], "watch": [...]},
            "suggestions": ["Next step 1", "Next step 2"],
            "proposal": {new board state} or null if no changes
        }
    """
    from erpnext_copilot.custom_code.api import get_ai_status
    
    # Check AI configuration
    ai_status = get_ai_status()
    if not ai_status.get("configured"):
        frappe.throw(_("Add a Gemini API key in AI settings first"), title=_("No API key"))
    
    # Check builder permission (user must have permission to create dashboards/charts)
    if not frappe.has_permission("Dashboard Chart", "create"):
        frappe.throw(_("You need permission to create Dashboard Charts to use the copilot"))
    
    prompt = (prompt or "").strip()
    if not prompt:
        frappe.throw(_("Type a request first"))
    
    key = frappe.conf.get("gemini_api_key")
    model = frappe.conf.get("gemini_model", "gemini-3.6-flash")
    if not key:
        frappe.throw(_("Add a Gemini API key in AI settings first"), title=_("No API key"))

    state = _read_board(board)
    before = copy.deepcopy(state)
    history = [h for h in (frappe.parse_json(history or "[]") or []) if isinstance(h, dict)][-HISTORY_TURNS:]
    answered = frappe.parse_json(answered) if isinstance(answered, str) else bool(answered)

    digest = [_describe(w, state["layout"][w["widget_id"]]) for w in state["widgets"]]
    focus = ""
    chosen = next((w for w in state["widgets"] if w["widget_id"] == str(selected or "")), None)
    if chosen:
        focus = (
            f'\nThe person has selected the widget "{chosen["title"]}" (id {chosen["widget_id"]}). '
            "Words like this, it, هذا, هذي and هذه refer to it. Change only it unless they clearly mean "
            "the whole board.\n"
        )
    answered_block = ""
    if answered:
        answered_block = (
            "\nThe person has just answered your question. Act on it now and do not ask again.\n"
        )

    plan_prompt = (
        "You are the copilot inside ERPNext Copilot, a dashboard/report editor for ERPNext. "
        "The person is editing the board below. Turn their message into a short reply and a "
        "list of operations that the studio will apply once they confirm.\n"
        f"Today's date is {frappe.utils.nowdate()}.\n\n"
        f'Board "{state["title"] or "Untitled"}". Theme: {json.dumps(state["theme"])}\n'
        "Grid: 12 columns. x is the column (0-11), y the row, w the width in columns, h the height "
        "in rows of 64px.\n"
        "Widgets:\n" + json.dumps(digest, ensure_ascii=False, default=str) + "\n" + focus + answered_block + "\n"
        + _history_block(history)
        + f"Message: {prompt}\n\n"
        + _guide()
    )
    
    from google import genai
    client = genai.Client(api_key=key)
    
    plan = client.models.generate_content(
        model=model,
        contents=plan_prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    
    try:
        plan = json.loads(plan.text)
    except Exception:
        frappe.throw(_("The AI returned an unreadable response. Try rephrasing."))

    if plan.get("clarify") and not answered:
        return {
            "clarify": _plain(plan["clarify"], 240),
            "options": [_plain(o, 80) for o in (plan.get("options") or []) if isinstance(o, str)][:4],
        }

    turn = _Turn(state, prompt, key, model)
    for op in (plan.get("operations") or [])[:MAX_OPS]:
        if not isinstance(op, dict):
            continue
        handler = OPERATIONS.get(op.get("op"))
        if handler:
            handler(turn, op)

    if turn.layout_touched:
        state["layout"] = _compact(state["layout"])

    changed = json.dumps(before, sort_keys=True, default=str) != json.dumps(state, sort_keys=True, default=str)
    return {
        "reply": _plain(plan.get("reply") or "", 400),
        "changes": turn.changes,
        "notes": turn.notes,
        "analysis": turn.analysis,
        "suggestions": [_plain(s, 90) for s in (plan.get("suggestions") or []) if isinstance(s, str)][:3],
        "proposal": (
            {
                "title": state["title"],
                "theme": state["theme"],
                "widgets": state["widgets"],
                "layout": [{"widget_id": wid, **pos} for wid, pos in state["layout"].items()],
            }
            if changed
            else None
        ),
    }