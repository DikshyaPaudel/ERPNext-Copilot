frappe.pages['copilot_chat'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'ERPNext Copilot',
		single_column: true,
	});

	// One-time CSS: typing indicator + sidebar list styling
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
				.copilot-convo-item {
					padding: 8px 10px;
					border-radius: 6px;
					cursor: pointer;
					font-size: 13px;
					white-space: nowrap;
					overflow: hidden;
					text-overflow: ellipsis;
					display: flex;
					justify-content: space-between;
					align-items: center;
					gap: 6px;
				}
				.copilot-convo-item:hover { background: var(--bg-light-gray, var(--bg-gray)); }
				.copilot-convo-item.active { background: var(--bg-blue); font-weight: 600; }
				.copilot-convo-delete {
					opacity: 0;
					cursor: pointer;
					color: var(--text-muted);
					flex-shrink: 0;
				}
				.copilot-convo-item:hover .copilot-convo-delete { opacity: 1; }

				/* Markdown rendered inside agent replies — tables especially,
				   since the model frequently returns them for list-style data. */
				.agent-bubble table {
					border-collapse: collapse;
					margin: 6px 0;
					font-size: 13px;
				}
				.agent-bubble th, .agent-bubble td {
					border: 1px solid var(--border-color);
					padding: 4px 10px;
					text-align: left;
				}
				.agent-bubble th { background: var(--bg-light-gray, var(--bg-gray)); }
				.agent-bubble .table-wrap { overflow-x: auto; }
				.agent-bubble p { margin: 4px 0; }
				.agent-bubble ul, .agent-bubble ol { margin: 4px 0; padding-left: 20px; }
				.agent-bubble code {
					background: var(--bg-light-gray, var(--bg-gray));
					padding: 1px 4px;
					border-radius: 3px;
					font-size: 90%;
				}
				.agent-bubble pre {
					background: var(--bg-light-gray, var(--bg-gray));
					padding: 8px;
					border-radius: 6px;
					overflow-x: auto;
				}
			</style>
		`);
	}

	const $chat = $(`
		<div style="display: flex; gap: 16px; max-width: 960px; margin: 0 auto;">
			<div class="copilot-sidebar" style="width: 220px; flex-shrink: 0; border-right: 1px solid var(--border-color); padding-right: 12px;">
				<button class="btn btn-default btn-sm copilot-new-chat" style="width: 100%; margin-bottom: 10px;">+ New chat</button>
				<div class="copilot-convo-list" style="max-height: 65vh; overflow-y: auto;"></div>
			</div>
			<div style="flex: 1; min-width: 0;">
				<div class="copilot-messages" style="height: 60vh; overflow-y: auto; border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; margin-bottom: 12px;"></div>
				<div class="d-flex" style="gap: 8px;">
					<input type="text" class="form-control copilot-input" placeholder="Ask about invoices, create a doctype, build a chart...">
					<button class="btn btn-primary copilot-send">Send</button>
				</div>
			</div>
		</div>
	`).appendTo(page.body);

	const $messages = $chat.find('.copilot-messages');
	const $input = $chat.find('.copilot-input');
	const $send = $chat.find('.copilot-send');
	const $convoList = $chat.find('.copilot-convo-list');
	const $newChatBtn = $chat.find('.copilot-new-chat');

	// --- state ----------------------------------------------------------
	let currentConversation = null;   // name of the Copilot Conversation doc, or null until the first message
	let $currentAgentBubble = null;   // (#2) accumulates streamed text chunks live
	let streamedAnyText = false;
	let streamedRawText = '';         // raw markdown accumulated during streaming, re-rendered once the reply completes

	/**
	 * Converts the agent's raw reply text (which often contains markdown —
	 * tables for list-style data especially) into HTML.
	 *
	 * The text is HTML-escaped BEFORE being handed to the markdown
	 * converter. This isn't redundant with the converter's own output: it
	 * neutralizes any literal "<", ">" or "&" the model's text might
	 * contain (accidental or adversarial — the model's output is not
	 * fully trusted input) so it can never be interpreted as a real tag,
	 * while leaving markdown syntax characters (*, |, #, -) untouched, so
	 * frappe.markdown still recognizes them and builds real <table>,
	 * <strong>, <li> etc. elements from them.
	 *
	 * Uses frappe.markdown() (Showdown-based, already bundled with the
	 * framework) rather than pulling in a separate markdown library —
	 * worth confirming it's present on whatever Frappe version you're
	 * running before relying on it in a live demo; the fallback below
	 * degrades to plain text with line breaks if it isn't.
	 */
	function renderAgentMarkdown(rawText) {
		const escaped = frappe.utils.escape_html(rawText);
		try {
			if (frappe && typeof frappe.markdown === 'function') {
				const html = frappe.markdown(escaped);
				// wrap tables so wide ones scroll horizontally instead of
				// blowing out the bubble/page width
				return html.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>');
			}
		} catch (e) {
			// fall through to plain-text fallback below
		}
		return escaped.replace(/\n/g, '<br>');
	}

	function addMessage(text, sender) {
		const align = sender === 'user' ? 'right' : 'left';
		const bg = sender === 'user' ? 'var(--bg-blue)' : 'var(--bg-gray)';
		if (sender === 'agent') {
			$messages.append(`
				<div style="text-align: left; margin-bottom: 10px;">
					<div class="agent-bubble" style="display: inline-block; background: ${bg}; padding: 8px 12px; border-radius: 8px; max-width: 90%;">${renderAgentMarkdown(text)}</div>
				</div>
			`);
		} else {
			$messages.append(`
				<div style="text-align: ${align}; margin-bottom: 10px;">
					<span style="display: inline-block; background: ${bg}; padding: 8px 12px; border-radius: 8px; max-width: 80%; white-space: pre-wrap;">${frappe.utils.escape_html(text)}</span>
				</div>
			`);
		}
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function showTypingIndicator() {
		removeTypingIndicator();
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
				<div class="agent-bubble" style="display: inline-block; background: var(--bg-gray); padding: 8px 12px; border-radius: 8px; max-width: 90%; white-space: pre-wrap;"></div>
			</div>
		`).appendTo($messages);
		$currentAgentBubble = $wrap.find('.agent-bubble');
		streamedAnyText = false;
		streamedRawText = '';
		$messages.scrollTop($messages[0].scrollHeight);
		return $currentAgentBubble;
	}

	function appendToAgentBubble(text) {
		removeTypingIndicator();
		if (!$currentAgentBubble) startAgentBubble();
		// Shown as growing plain text WHILE streaming (re-parsing partial
		// markdown — e.g. an unclosed table row — on every chunk would
		// flicker/break mid-stream). Once the reply is complete,
		// handleResponse() swaps this same bubble's content for the fully
		// rendered markdown version.
		streamedRawText += text;
		$currentAgentBubble.text(streamedRawText);
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

	frappe.realtime.on('copilot_stream_chunk', (data) => appendToAgentBubble(data.text));
	frappe.realtime.on('copilot_status', (data) => addStatusLine(data.text));

	function resetStreamState() {
		$currentAgentBubble = null;
		streamedAnyText = false;
		streamedRawText = '';
	}

	// --- sidebar ----------------------------------------------------------

	function renderConvoList(rows) {
		$convoList.empty();
		if (!rows.length) {
			$convoList.append(`<div style="font-size: 12px; color: var(--text-muted); padding: 8px;">No conversations yet</div>`);
			return;
		}
		rows.forEach((row) => {
			const $item = $(`
				<div class="copilot-convo-item ${row.name === currentConversation ? 'active' : ''}" data-name="${row.name}">
					<span class="copilot-convo-title">${frappe.utils.escape_html(row.title || 'New chat')}</span>
					<span class="copilot-convo-delete" title="Delete">&times;</span>
				</div>
			`).appendTo($convoList);

			$item.on('click', (e) => {
				if ($(e.target).hasClass('copilot-convo-delete')) return;
				selectConversation(row.name);
			});
			$item.find('.copilot-convo-delete').on('click', (e) => {
				e.stopPropagation();
				frappe.confirm(
					`Delete conversation "${frappe.utils.escape_html(row.title || 'New chat')}"? This can't be undone.`,
					() => deleteConversation(row.name)
				);
			});
		});
	}

	function refreshConvoList() {
		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.list_conversations',
			callback: (res) => renderConvoList(res.message || []),
		});
	}

	function selectConversation(name) {
		removeTypingIndicator();
		resetStreamState();
		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.get_conversation',
			args: { name },
			callback: (res) => {
				const data = res.message;
				currentConversation = data.name;
				$messages.empty();
				(data.messages || []).forEach((m) => addMessage(m.text, m.sender));
				$convoList.find('.copilot-convo-item').removeClass('active');
				$convoList.find(`.copilot-convo-item[data-name="${data.name}"]`).addClass('active');
			},
		});
	}

	function startNewChat() {
		removeTypingIndicator();
		resetStreamState();
		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.new_conversation',
			callback: (res) => {
				currentConversation = res.message.name;
				$messages.empty();
				refreshConvoList();
				$input.trigger('focus');
			},
		});
	}

	function deleteConversation(name) {
		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.delete_conversation',
			args: { name },
			callback: () => {
				if (name === currentConversation) {
					currentConversation = null;
					$messages.empty();
				}
				refreshConvoList();
			},
		});
	}

	$newChatBtn.on('click', startNewChat);

	// --- main send/response flow ------------------------------------------

	function handleResponse(res) {
		removeTypingIndicator(); // safety net if no realtime event ever arrived
		const data = res.message;
		if (data.conversation) currentConversation = data.conversation;

		if (data.type === 'reply') {
			if (streamedAnyText && $currentAgentBubble) {
				// Streaming showed growing plain text live — now that the
				// full reply is in, replace it with the properly rendered
				// markdown (tables, bold, lists) in one swap.
				$currentAgentBubble.css('white-space', 'normal').html(renderAgentMarkdown(streamedRawText));
			} else if (!streamedAnyText) {
				addMessage(data.text, 'agent');
			}
			resetStreamState();
			refreshConvoList(); // title/order may have just changed
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
		if (approved) showTypingIndicator();
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
			args: { message: text, conversation: currentConversation },
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

	// initial load
	refreshConvoList();
};