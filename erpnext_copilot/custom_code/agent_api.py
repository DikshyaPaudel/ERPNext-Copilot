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


class ModelTimeoutError(Exception):
    pass


def _history_key():
    return f"erpnext_copilot_history:{frappe.session.user}"


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


def _save_history(history_raw):
    frappe.cache().set_value(_history_key(), history_raw, expires_in_sec=3600)


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
def ask_agent(message: str):
    """One turn of the conversation. Returns either a final reply, or a
    pending_action the frontend must confirm before it's executed."""
    client = _get_client()
    config_with_tools = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, tools=[TOOLS])

    history_raw = frappe.cache().get_value(_history_key()) or []
    history = _raw_to_history(history_raw)

    user_content = types.Content(role="user", parts=[types.Part(text=message)])
    history.append(user_content)
    history_raw.append(_content_to_raw(user_content))

    for _ in range(MAX_TOOL_STEPS):
        try:
            candidate_content, function_call = _call_model(client, history, config_with_tools)
        except ModelTimeoutError as e:
            _save_history(history_raw)
            return {"type": "reply", "text": f"Still working on that — {e} Please try again in a moment."}

        history.append(candidate_content)
        history_raw.append(_content_to_raw(candidate_content))

        if not function_call:
            _save_history(history_raw)
            return {"type": "reply", "text": _extract_text(candidate_content)}

        fn_name = function_call.name
        fn_args = dict(function_call.args)

        if fn_name in WRITE_TOOLS:
            # Stop here — don't execute yet. Store enough state to resume
            # after the frontend gets user confirmation.
            frappe.cache().set_value(_pending_key(), {
                "tool": fn_name, "args": fn_args, "history_raw": history_raw,
            }, expires_in_sec=600)
            return {"type": "pending_action", "tool": fn_name, "args": fn_args}

        _publish_status(f"Calling {fn_name}...")

        fn = TOOL_DISPATCH.get(fn_name)
        result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
        clean_result = _sanitize_tool_result(result)

        fn_response_content = types.Content(role="user", parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})])
        history.append(fn_response_content)
        history_raw.append(_content_to_raw(fn_response_content))

    _save_history(history_raw)
    return {"type": "reply", "text": "(stopped after reaching the tool-call safety limit)"}


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

    if approved:
        _publish_status(f"Calling {fn_name}...")
        fn = TOOL_DISPATCH.get(fn_name)
        result = fn(**fn_args) if fn else {"error": f"Unknown tool {fn_name}"}
    else:
        result = {"cancelled": True, "message": "Action cancelled by user."}

    clean_result = _sanitize_tool_result(result)
    fn_response_content = types.Content(role="user", parts=[types.Part.from_function_response(name=fn_name, response={"result": clean_result})])
    history_raw.append(_content_to_raw(fn_response_content))
    _save_history(history_raw)  # save first, so this result is in place before we continue the conversation

    return _continue_after_tool_result(history_raw)


def _continue_after_tool_result(history_raw):
    """Lets the model produce a natural-language summary of a tool result
    (or chain into another tool call, e.g. list_dashboards right after creating a chart)."""
    client = _get_client()
    config_with_tools = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, tools=[TOOLS])
    history = _raw_to_history(history_raw)

    for _ in range(MAX_TOOL_STEPS):
        try:
            candidate_content, function_call = _call_model(client, history, config_with_tools)
        except ModelTimeoutError as e:
            _save_history(history_raw)
            return {"type": "reply", "text": f"Still working on that — {e} Please try again in a moment."}

        history.append(candidate_content)
        history_raw.append(_content_to_raw(candidate_content))

        if not function_call:
            _save_history(history_raw)
            return {"type": "reply", "text": _extract_text(candidate_content)}

        fn_name = function_call.name
        fn_args = dict(function_call.args)

        if fn_name in WRITE_TOOLS:
            frappe.cache().set_value(_pending_key(), {
                "tool": fn_name, "args": fn_args, "history_raw": history_raw,
            }, expires_in_sec=600)
            _save_history(history_raw)
            return {"type": "pending_action", "tool": fn_name, "args": fn_args}

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

    _save_history(history_raw)
    return {"type": "reply", "text": "(stopped after reaching the tool-call safety limit)"}


@frappe.whitelist()
def reset_conversation():
    frappe.cache().delete_value(_history_key())
    frappe.cache().delete_value(_pending_key())
    return {"success": True}