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
from typing import Optional

import frappe
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
    return frappe.get_list(
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
                if getattr(part, "thought", False):
                    # This is an internal reasoning/thinking chunk.
                    # Collect it into history (Gemini requires thought
                    # parts to be present in subsequent turns on some
                    # model versions — same reason we kept
                    # thought_signature on function-call parts) but never
                    # stream it to the user or include it in the final
                    # displayed text.
                    collected_parts.append(part)
                else:
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
    API call can't leave the request open indefinitely.

    The executor is deliberately NOT used as a `with` block: its __exit__
    calls shutdown(wait=True), which blocks until the worker thread
    finishes — so a timed-out call would still hold the request open for
    as long as the slow API call took, making the timeout a no-op. Here
    shutdown(wait=False) lets the request return right at the deadline;
    the abandoned worker simply finishes in the background (a thread
    blocked on a network call can't be killed, only walked away from).
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_generate_and_stream, client, _trimmed(history), config)
    try:
        return future.result(timeout=MODEL_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        raise ModelTimeoutError(f"Gemini didn't respond within {MODEL_TIMEOUT_SECONDS}s.")
    finally:
        executor.shutdown(wait=False)


EMPTY_RESPONSE_MESSAGE = (
    "I ran into a hiccup generating a response (the model returned nothing). "
    "Your data wasn't changed — please send that again."
)


def _has_content(candidate_content, function_call):
    """A model turn is usable only if it either calls a tool or carries
    some non-empty non-thought text. A turn with only thought parts or
    only whitespace is treated as empty and will be retried."""
    if function_call:
        return True
    return any(
        (getattr(p, "text", None) or "").strip()
        for p in candidate_content.parts
        if not getattr(p, "thought", False)
    )


def _call_model_reliably(client, history, config_with_tools):
    """Calls the model, retrying when it returns an EMPTY turn (no text
    and no function call).

    Gemini occasionally does this right after a function response — the
    tool ran fine, but the follow-up call that should summarize the result
    comes back blank. Treating that as a final answer is what produced
    "(no text response)" followed by a normal answer on the user's next
    message. Retrying is cheap and usually succeeds.

    Attempt plan:
      1. normal call (tools enabled)
      2. same again (empty responses are usually transient)
      3. tools DISABLED — if the model keeps stalling (e.g. wanting to
         call a tool but emitting nothing), removing tools forces it to
         answer in plain text. The original console version of this
         project used a tools-free config for exactly this "turn a tool
         result into prose" step, so this combination is known to work on
         this SDK version.

    Returns (candidate_content, function_call), or (None, None) if every
    attempt came back empty. The empty turns are never returned, so
    callers never append them to history or persist them.
    """
    config_no_tools = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION)
    for config in (config_with_tools, config_with_tools, config_no_tools):
        candidate_content, function_call = _call_model(client, history, config)
        if _has_content(candidate_content, function_call):
            return candidate_content, function_call
    return None, None


def _report_failure(stage, exc):
    """Log the FULL traceback to Frappe's Error Log (/app/error-log) and
    return a short message for the chat that names the real exception.

    Without this, any unexpected exception (a DB error, a Gemini client
    error, a Redis hiccup) escaped as a bare HTTP 500: the chat showed
    nothing useful and the cause had to be dug out of terminal output.
    Showing the exception type + message (truncated) in the chat, with the
    traceback one click away in Error Log, makes the next failure
    diagnosable immediately instead of a guessing game.
    """
    try:
        frappe.log_error(title=f"Copilot: failure while {stage}", message=frappe.get_traceback())
    except Exception:
        pass  # never let the error reporter itself mask the original error
    return (
        f"Something went wrong while {stage}: {type(exc).__name__}: {str(exc)[:300]}. "
        "The full traceback is in Error Log (/app/error-log)."
    )


def _run_tool(fn_name, fn_args):
    """Run one tool, converting an unexpected exception into an
    {"error": ...} result instead of letting it escape as a raw 500.
    The model then sees the failure as a normal tool result and can
    explain it, and the traceback is logged for debugging."""
    fn = TOOL_DISPATCH.get(fn_name)
    if not fn:
        return {"error": f"Unknown tool {fn_name}"}
    try:
        return fn(**fn_args)
    except Exception as e:
        _report_failure(f"running tool {fn_name}", e)
        return {"error": f"{fn_name} failed: {type(e).__name__}: {str(e)[:300]}"}


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
            candidate_content, function_call = _call_model_reliably(client, history, config_with_tools)
        except ModelTimeoutError as e:
            _save_conversation(doc, history_raw, set_title_from=message)
            return {"type": "reply", "text": f"Still working on that — {e} Please try again in a moment.", "conversation": doc.name}
        except Exception as e:
            _save_conversation(doc, history_raw, set_title_from=message)
            return {"type": "reply", "text": _report_failure("asking the model", e), "conversation": doc.name}

        if candidate_content is None:
            # Every attempt came back empty. Save what happened so far
            # (the user's message and any tool results) but NOT an empty
            # model turn — persisting one would leave a blank turn in the
            # transcript for every later call to trip over.
            _save_conversation(doc, history_raw, set_title_from=message)
            return {"type": "reply", "text": EMPTY_RESPONSE_MESSAGE, "conversation": doc.name}

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

        result = _run_tool(fn_name, fn_args)
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
        result = _run_tool(fn_name, fn_args)
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
            candidate_content, function_call = _call_model_reliably(client, history, config_with_tools)
        except ModelTimeoutError as e:
            _save_conversation(doc, history_raw)
            return {"type": "reply", "text": f"Still working on that — {e} Please try again in a moment.", "conversation": doc.name}
        except Exception as e:
            _save_conversation(doc, history_raw)
            return {"type": "reply", "text": _report_failure("asking the model", e), "conversation": doc.name}

        if candidate_content is None:
            _save_conversation(doc, history_raw)
            return {"type": "reply", "text": EMPTY_RESPONSE_MESSAGE, "conversation": doc.name}

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

        result = _run_tool(fn_name, fn_args)
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

    MUST use frappe.get_list(), not get_all(). get_all() skips Frappe's
    permission/match-condition filtering entirely, which means the
    if_owner restriction on Copilot Tool Call would NEVER actually have
    applied here — any authenticated user could have passed any other
    user's conversation name and read their full write-action audit log,
    arguments included. get_list() is what actually enforces if_owner.
    """
    return frappe.get_list(
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