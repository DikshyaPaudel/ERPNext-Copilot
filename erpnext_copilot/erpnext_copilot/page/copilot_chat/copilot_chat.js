frappe.pages['copilot_chat'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'ERPNext Copilot',
		single_column: true,
	});

	// One-time CSS for the typing indicator (WhatsApp-style bouncing dots)
	if (!$('#copilot-typing-style').length) {
		$('head').append(`
			<style id="copilot-typing-style">
				.copilot-typing-dot {
					display: inline-block;
					width: 6px;
					height: 6px;
					margin: 0 2px;
					border-radius: 50%;
					background: var(--text-muted);
					animation: copilot-typing-bounce 1.2s infinite ease-in-out;
				}
				.copilot-typing-dot:nth-child(2) { animation-delay: 0.15s; }
				.copilot-typing-dot:nth-child(3) { animation-delay: 0.3s; }
				@keyframes copilot-typing-bounce {
					0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
					30% { transform: translateY(-4px); opacity: 1; }
				}
			</style>
		`);
	}

	const $chat = $(`
		<div style="max-width: 700px; margin: 0 auto;">
			<div class="copilot-messages" style="height: 60vh; overflow-y: auto; border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; margin-bottom: 12px;"></div>
			<div class="d-flex" style="gap: 8px;">
				<input type="text" class="form-control copilot-input" placeholder="Ask about invoices, create a doctype, build a chart...">
				<button class="btn btn-primary copilot-send">Send</button>
			</div>
		</div>
	`).appendTo(page.body);

	const $messages = $chat.find('.copilot-messages');
	const $input = $chat.find('.copilot-input');
	const $send = $chat.find('.copilot-send');

	// --- streaming state for the turn currently in flight -------------
	// (#2) $currentAgentBubble accumulates streamed text chunks live.
	// (#3) status lines get their own small entries above the bubble.
	let $currentAgentBubble = null;
	let streamedAnyText = false;

	function addMessage(text, sender) {
		const align = sender === 'user' ? 'right' : 'left';
		const bg = sender === 'user' ? 'var(--bg-blue)' : 'var(--bg-gray)';
		$messages.append(`
			<div style="text-align: ${align}; margin-bottom: 10px;">
				<span style="display: inline-block; background: ${bg}; padding: 8px 12px; border-radius: 8px; max-width: 80%; white-space: pre-wrap;">${frappe.utils.escape_html(text)}</span>
			</div>
		`);
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function showTypingIndicator() {
		removeTypingIndicator(); // never stack more than one
		$(`
			<div class="copilot-typing" style="text-align: left; margin-bottom: 10px;">
				<span style="display: inline-block; background: var(--bg-gray); padding: 10px 14px; border-radius: 8px;">
					<span class="copilot-typing-dot"></span><span class="copilot-typing-dot"></span><span class="copilot-typing-dot"></span>
				</span>
			</div>
		`).appendTo($messages);
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function removeTypingIndicator() {
		$messages.find('.copilot-typing').remove();
	}

	function startAgentBubble() {
		removeTypingIndicator();
		const $wrap = $(`
			<div style="text-align: left; margin-bottom: 10px;">
				<span class="agent-bubble" style="display: inline-block; background: var(--bg-gray); padding: 8px 12px; border-radius: 8px; max-width: 80%; white-space: pre-wrap;"></span>
			</div>
		`).appendTo($messages);
		$currentAgentBubble = $wrap.find('.agent-bubble');
		streamedAnyText = false;
		$messages.scrollTop($messages[0].scrollHeight);
		return $currentAgentBubble;
	}

	function appendToAgentBubble(text) {
		removeTypingIndicator();
		if (!$currentAgentBubble) startAgentBubble();
		$currentAgentBubble.text($currentAgentBubble.text() + text);
		streamedAnyText = true;
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function addStatusLine(text) {
		removeTypingIndicator();
		$(`
			<div style="text-align: left; margin-bottom: 6px; font-size: 12px; color: var(--text-muted);">
				${frappe.utils.escape_html(text)}
			</div>
		`).appendTo($messages);
		$messages.scrollTop($messages[0].scrollHeight);
	}

	// (#2)/(#3) live updates pushed from agent_api.py via frappe.publish_realtime
	frappe.realtime.on('copilot_stream_chunk', (data) => appendToAgentBubble(data.text));
	frappe.realtime.on('copilot_status', (data) => addStatusLine(data.text));

	function resetStreamState() {
		$currentAgentBubble = null;
		streamedAnyText = false;
	}

	function handleResponse(res) {
		removeTypingIndicator(); // safety net if no realtime event ever arrived
		const data = res.message;
		if (data.type === 'reply') {
			if (!streamedAnyText) {
				// Streaming chunks never arrived (e.g. realtime hiccup, or
				// this was a tool-result summary with no chunks) — fall
				// back to showing the full text at once, same as before.
				addMessage(data.text, 'agent');
			}
			resetStreamState();
		} else if (data.type === 'pending_action') {
			resetStreamState();
			const argsText = JSON.stringify(data.args, null, 2);
			frappe.confirm(
				`The assistant wants to run <b>${data.tool}</b> with:<pre>${frappe.utils.escape_html(argsText)}</pre>Proceed?`,
				() => confirmAction(true),
				() => confirmAction(false)
			);
		}
	}

	function confirmAction(approved) {
		resetStreamState();
		if (approved) showTypingIndicator(); // a cancel resolves instantly, no need to show it there
		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.confirm_pending_action',
			args: { approved },
			callback: handleResponse,
		});
	}

	function sendMessage() {
		const text = $input.val().trim();
		if (!text) return;
		addMessage(text, 'user');
		$input.val('');
		$send.prop('disabled', true);
		resetStreamState();
		showTypingIndicator();

		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.ask_agent',
			args: { message: text },
			callback: (res) => {
				handleResponse(res);
				$send.prop('disabled', false);
			},

			error: () => {
				removeTypingIndicator();
				resetStreamState();
				addMessage('Something went wrong — check the console.', 'agent');
				$send.prop('disabled', false);
			},
		});
	}

	$send.on('click', sendMessage);
	$input.on('keydown', (e) => {
		if (e.key === 'Enter') sendMessage();
	});
};