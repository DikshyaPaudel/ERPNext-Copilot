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
"""

import frappe
from google import genai
from google.genai import types

from erpnext_copilot.custom_code.gemini_agent import (
    TOOLS, TOOL_DISPATCH, WRITE_TOOLS, SYSTEM_INSTRUCTION,
    _sanitize_tool_result, _extract_text,
)

MAX_TOOL_STEPS = 5


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
        response = client.models.generate_content(model="gemini-3.6-flash", contents=history, config=config_with_tools)
        candidate_content = response.candidates[0].content
        history.append(candidate_content)
        history_raw.append(_content_to_raw(candidate_content))

        function_call = next((p.function_call for p in candidate_content.parts if p.function_call), None)

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
        response = client.models.generate_content(model="gemini-3.6-flash", contents=history, config=config_with_tools)
        candidate_content = response.candidates[0].content
        history.append(candidate_content)
        history_raw.append(_content_to_raw(candidate_content))

        function_call = next((p.function_call for p in candidate_content.parts if p.function_call), None)

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