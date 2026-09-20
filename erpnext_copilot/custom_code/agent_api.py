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
   the thought_signature reason above), but only the last HISTORY_WINDOW
   Content objects are sent to the model on each call. A long-running
   session was resending its entire transcript on every single turn.

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
from typing import Optional

import frappe
from google import genai
from google.genai import types

from erpnext_copilot.custom_code.gemini_agent import (
    TOOLS, TOOL_DISPATCH, WRITE_TOOLS, SYSTEM_INSTRUCTION,
    _sanitize_tool_result, _extract_text,
)

MAX_TOOL_STEPS = 5
HISTORY_WINDOW = 8            # (#1) Content objects sent to the model per call
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
    """(#1) Only the most recent turns go to the model. Doesn't touch what
    gets cached/returned — just what's sent on THIS call."""
    return history[-HISTORY_WINDOW:] if len(history) > HISTORY_WINDOW else history


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