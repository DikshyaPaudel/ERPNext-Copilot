frappe.pages['copilot_chat'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'ERPNext Copilot',
		single_column: true,
	});

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

	function handleResponse(res) {
		const data = res.message;
		if (data.type === 'reply') {
			addMessage(data.text, 'agent');
		} else if (data.type === 'pending_action') {
			const argsText = JSON.stringify(data.args, null, 2);
			frappe.confirm(
				`The assistant wants to run <b>${data.tool}</b> with:<pre>${frappe.utils.escape_html(argsText)}</pre>Proceed?`,
				() => confirmAction(true),
				() => confirmAction(false)
			);
		}
	}

	function confirmAction(approved) {
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

		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.ask_agent',
			args: { message: text },
			callback: (res) => {
				handleResponse(res);
				$send.prop('disabled', false);
			},
			error: () => {
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