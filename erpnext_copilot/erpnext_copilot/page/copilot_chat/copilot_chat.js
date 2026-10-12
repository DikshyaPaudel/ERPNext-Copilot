frappe.pages['copilot_chat'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'ERP AI Assistant',
		single_column: true,
	});

	// One-time CSS. Built on Frappe's own theme variables (--bg-gray,
	// --border-color, --text-muted, etc.) so it never clashes with the
	// desk's light/dark theme — the one thing NOT inherited is a single
	// deliberate accent color (a muted teal, distinct from Frappe's
	// default blue-wash selected state), used sparingly: the active
	// conversation marker, the send button, and the streaming indicator.
	// Everywhere else stays neutral on purpose.
	if (!$('#copilot-style').length) {
		$('head').append(`
			<style id="copilot-style">
				.copilot-root {
					--copilot-accent: #2F6F5E;
					--copilot-accent-hover: #3B8A73;
					--copilot-accent-soft: rgba(47, 111, 94, 0.12);
					--copilot-accent-ink: #fff;
				}
				[data-theme="dark"] .copilot-root {
					--copilot-accent: #5AC4A8;
					--copilot-accent-hover: #74D4BB;
					--copilot-accent-soft: rgba(90, 196, 168, 0.18);
					/* dark mode's accent is a LIGHT mint, not a saturated
					   color — white text on it would be low-contrast, so
					   the "ink" (text/icon-on-accent) color flips to dark
					   here instead of staying white like the light-mode one */
					--copilot-accent-ink: #0B1F1A;
				}

				/* ---- typing indicator ---- */
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

				/* ---- sidebar ---- */
				.copilot-new-chat {
					width: 100%;
					display: flex;
					align-items: center;
					justify-content: center;
					gap: 6px;
					padding: 7px 10px;
					border-radius: 7px;
					border: 1px solid var(--border-color);
					background: transparent;
					font-size: 13px;
					font-weight: 500;
					cursor: pointer;
					transition: border-color 120ms ease, color 120ms ease;
				}
				.copilot-new-chat:hover {
					border-color: var(--copilot-accent);
					color: var(--copilot-accent);
				}
				.copilot-convo-item {
					position: relative;
					padding: 8px 10px 8px 12px;
					margin-bottom: 2px;
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
					border-left: 2px solid transparent;
					transition: background 120ms ease, border-color 120ms ease;
				}
				.copilot-convo-item:hover { background: var(--bg-light-gray, var(--bg-gray)); }
				.copilot-convo-item.active {
					border-left-color: var(--copilot-accent);
					background: var(--copilot-accent-soft);
					font-weight: 600;
				}
				/* The title must truncate itself, not just its parent row.
				   Flex items default to min-width:auto (won't shrink below
				   their text's full width), so without min-width:0 a long
				   title never ellipsizes — it just pushes the delete button
				   past the row's clipped edge, which is exactly the "can't
				   delete chats with long titles" bug. */
				.copilot-convo-title {
					flex: 1 1 auto;
					min-width: 0;
					overflow: hidden;
					text-overflow: ellipsis;
					white-space: nowrap;
				}
				.copilot-convo-delete {
					opacity: 0;
					cursor: pointer;
					color: var(--text-muted);
					flex-shrink: 0;
					width: 18px;
					height: 18px;
					display: flex;
					align-items: center;
					justify-content: center;
					border-radius: 4px;
					transition: opacity 120ms ease, background 120ms ease;
				}
				.copilot-convo-item:hover .copilot-convo-delete { opacity: 1; }
				.copilot-convo-delete:hover { background: var(--bg-gray); color: var(--text-color); }
				.copilot-empty-convos {
					font-size: 12.5px;
					color: var(--text-muted);
					padding: 10px 8px;
					line-height: 1.5;
				}
				.copilot-convo-group-label {
					font-size: 11px;
					text-transform: uppercase;
					letter-spacing: 0.03em;
					color: var(--text-muted);
					padding: 10px 10px 4px;
				}
				.copilot-convo-group-label:first-child { padding-top: 2px; }

				/* ---- status chip (e.g. "Calling aggregate_documents...") ---- */
				.copilot-status-row { margin: -4px 0 8px 30px; }
				.copilot-status-chip {
					display: inline-flex;
					align-items: center;
					gap: 5px;
					background: var(--bg-gray);
					color: var(--text-muted);
					font-size: 11.5px;
					padding: 3px 9px;
					border-radius: 20px;
				}
				.copilot-status-chip svg { color: var(--copilot-accent); flex-shrink: 0; }

				/* ---- chat header ---- */
				.copilot-chat-header {
					font-size: 13px;
					font-weight: 600;
					color: var(--text-color);
					padding: 2px 2px 10px;
					margin-bottom: 8px;
					border-bottom: 1px solid var(--border-color);
				}

				/* ---- messages ---- */
				.copilot-messages { scrollbar-width: thin; }
				.copilot-messages::-webkit-scrollbar { width: 8px; }
				.copilot-messages::-webkit-scrollbar-thumb {
					background: var(--border-color);
					border-radius: 8px;
				}
				.copilot-msg-row {
					display: flex;
					align-items: flex-start;
					gap: 8px;
					margin-bottom: 12px;
					animation: copilot-msg-in 160ms ease-out;
				}
				.copilot-msg-row.user { flex-direction: row-reverse; }
				@keyframes copilot-msg-in {
					from { opacity: 0; transform: translateY(4px); }
					to { opacity: 1; transform: translateY(0); }
				}
				.copilot-avatar {
					flex-shrink: 0;
					width: 22px;
					height: 22px;
					border-radius: 50%;
					margin-top: 2px;
					background: var(--copilot-accent-soft);
					display: flex;
					align-items: center;
					justify-content: center;
				}
				.copilot-avatar::after {
					content: '';
					width: 6px;
					height: 6px;
					border-radius: 50%;
					background: var(--copilot-accent);
				}
				.copilot-bubble-user {
					display: inline-block;
					background: var(--bg-blue);
					padding: 8px 12px;
					border-radius: 10px 10px 2px 10px;
					max-width: 80%;
					white-space: pre-wrap;
				}
				.agent-bubble {
					display: inline-block;
					background: var(--bg-gray);
					padding: 8px 12px;
					border-radius: 10px 10px 10px 2px;
					max-width: 90%;
				}

				/* ---- empty state ---- */
				.copilot-empty-state {
					height: 100%;
					display: flex;
					align-items: center;
					justify-content: center;
					text-align: center;
					color: var(--text-muted);
					font-size: 13.5px;
					padding: 24px;
					line-height: 1.6;
				}

				/* ---- composer ---- */
				.copilot-composer {
					display: flex;
					align-items: center;
					gap: 8px;
					border: 1px solid var(--border-color);
					border-radius: 24px;
					padding: 4px 4px 4px 16px;
					transition: border-color 120ms ease;
				}
				.copilot-composer:focus-within { border-color: var(--copilot-accent); }
				.copilot-input {
					border: none !important;
					box-shadow: none !important;
					background: transparent !important;
					padding-left: 0 !important;
				}
				.copilot-send {
					flex-shrink: 0;
					width: 34px;
					height: 34px;
					border-radius: 50%;
					border: none;
					background: var(--copilot-accent);
					color: var(--copilot-accent-ink);
					display: flex;
					align-items: center;
					justify-content: center;
					cursor: pointer;
					transition: background 120ms ease, opacity 120ms ease;
				}
				.copilot-send:hover { background: var(--copilot-accent-hover); }
				.copilot-send:disabled { opacity: 0.5; cursor: default; }

				/* ---- markdown rendered inside agent replies ---- */
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

				@media (max-width: 720px) {
					.copilot-layout { flex-direction: column; }
					.copilot-sidebar {
						width: 100% !important;
						border-right: none !important;
						border-bottom: 1px solid var(--border-color);
						padding-right: 0 !important;
						padding-bottom: 10px;
						margin-bottom: 10px;
					}
				}
			</style>
		`);
	}

	const $chat = $(`
		<div class="copilot-root" style="max-width: 960px; margin: 0 auto;">
			<div class="copilot-layout" style="display: flex; gap: 20px;">
				<div class="copilot-sidebar" style="width: 220px; flex-shrink: 0; border-right: 1px solid var(--border-color); padding-right: 14px;">
					<button class="copilot-new-chat">
						<span>+</span><span>New chat</span>
					</button>
					<div class="copilot-convo-list" style="max-height: 65vh; overflow-y: auto; margin-top: 10px;"></div>
				</div>
				<div style="flex: 1; min-width: 0;">
					<div class="copilot-chat-header">New chat</div>
					<div class="copilot-messages" style="height: 56vh; overflow-y: auto; padding: 4px 2px; margin-bottom: 12px;"></div>
					<div class="copilot-composer">
						<input type="text" class="form-control copilot-input" placeholder="Ask about invoices, create a doctype, build a chart...">
						<button class="copilot-send" title="Send">
							<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>
						</button>
					</div>
				</div>
			</div>
		</div>
	`).appendTo(page.body);

	const $messages = $chat.find('.copilot-messages');
	const $input = $chat.find('.copilot-input');
	const $send = $chat.find('.copilot-send');
	const $convoList = $chat.find('.copilot-convo-list');
	const $newChatBtn = $chat.find('.copilot-new-chat');
	const $chatHeader = $chat.find('.copilot-chat-header');

	// --- state ----------------------------------------------------------
	let currentConversation = null;   // name of the Copilot Conversation doc, or null until the first message
	let $currentAgentBubble = null;   // (#2) accumulates streamed text chunks live
	let streamedAnyText = false;
	let streamedRawText = '';         // raw markdown accumulated during streaming, re-rendered once the reply completes

	/**
	 * Converts the agent's raw reply text into HTML.
	 *
	 * Tables are parsed and built into real <table> HTML by hand, in
	 * extractAndRenderTables() below — NOT delegated to Showdown or
	 * frappe.markdown(). Two attempts at getting Frappe's bundled
	 * markdown converter to render GFM tables (enabling {tables: true}
	 * explicitly, then normalizing missing newlines between rows) both
	 * failed to actually fix it in practice, which means either
	 * window.showdown isn't exposed globally on this Frappe version (so
	 * the code was silently falling back to frappe.markdown(), which
	 * apparently doesn't have tables enabled either), or there's some
	 * other host-specific config issue that isn't practical to keep
	 * guessing at remotely. Building the table HTML ourselves removes
	 * that dependency entirely — it only needs String.split, no library.
	 *
	 * Everything that ISN'T a table (bold text, headings, horizontal
	 * rules) already rendered correctly through frappe.markdown() per
	 * earlier screenshots, so that part is left as-is — tables are
	 * extracted out, replaced with placeholder tokens, the rest of the
	 * text still goes through the normal converter, then the rendered
	 * table HTML is substituted back in.
	 */

	function _escapeCell(text) {
		// cell text has already been through frappe.utils.escape_html at
		// the top level, so this only handles the light inline markdown
		// (bold, code) that commonly shows up inside table cells.
		return text
			.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
			.replace(/`(.+?)`/g, '<code>$1</code>');
	}

	function _splitTableRow(line) {
		let cells = line.trim();
		if (cells.startsWith('|')) cells = cells.slice(1);
		if (cells.endsWith('|')) cells = cells.slice(0, -1);
		return cells.split('|').map((c) => c.trim());
	}

	function _isTableRowLine(line) {
		const t = line.trim();
		return t.startsWith('|') && t.endsWith('|') && t.length > 1;
	}

	function _isTableSeparatorLine(line) {
		if (!_isTableRowLine(line)) return false;
		const cells = _splitTableRow(line);
		return cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c));
	}

	function _buildTableHtml(headerLine, dataLines) {
		const headerCells = _splitTableRow(headerLine);
		let html = '<div class="table-wrap"><table><tr>';
		headerCells.forEach((c) => { html += `<th>${_escapeCell(c)}</th>`; });
		html += '</tr>';
		dataLines.forEach((line) => {
			const cells = _splitTableRow(line);
			html += '<tr>';
			cells.forEach((c) => { html += `<td>${_escapeCell(c)}</td>`; });
			html += '</tr>';
		});
		html += '</table></div>';
		return html;
	}

	/**
	 * Scans line by line for "row line, then separator line, then one or
	 * more row lines" and swaps each such block for a placeholder token,
	 * so the surrounding text can still go through normal markdown
	 * conversion without the converter trying (and failing) to touch the
	 * table syntax itself.
	 *
	 * Also tolerates the collapsed-row-boundary case seen in practice
	 * (rows run together on one line, "| a | b | | c | d |" with no \n
	 * between them) by first splitting on any "| |" (closing pipe,
	 * whitespace only, opening pipe) — that pattern is never a real
	 * populated cell, only a lost row boundary or, rarely, a genuinely
	 * empty cell, which splitting still leaves as valid (if slightly
	 * different) table structure.
	 */
	function _extractAndRenderTables(text) {
		const normalized = text.replace(/\|[ \t]*\|/g, '|\n|');
		const lines = normalized.split('\n');
		const placeholders = [];
		const outLines = [];
		let i = 0;
		while (i < lines.length) {
			if (_isTableRowLine(lines[i]) && i + 1 < lines.length && _isTableSeparatorLine(lines[i + 1])) {
				const headerLine = lines[i];
				let j = i + 2;
				const dataLines = [];
				while (j < lines.length && _isTableRowLine(lines[j])) {
					dataLines.push(lines[j]);
					j++;
				}
				const token = `XXCOPILOTTABLE${placeholders.length}XX`;
				placeholders.push(_buildTableHtml(headerLine, dataLines));
				if (outLines.length > 0 && outLines[outLines.length - 1].trim() !== '') {
					outLines.push(''); // isolate the placeholder so nothing above it (e.g. a heading) merges with it
				}
				outLines.push(token);
				outLines.push(''); // and nothing below it (e.g. a "---" divider) reinterprets it as a Setext heading underline
				i = j;
			} else {
				outLines.push(lines[i]);
				i++;
			}
		}
		return { text: outLines.join('\n'), placeholders };
	}

	let _mdConverter = null;
	function _getMarkdownConverter() {
		if (_mdConverter) return _mdConverter;
		const Showdown = window.showdown || (window.frappe && frappe.showdown);
		if (Showdown && Showdown.Converter) {
			_mdConverter = new Showdown.Converter({ strikethrough: true, simpleLineBreaks: true });
		}
		return _mdConverter;
	}

	function renderAgentMarkdown(rawText) {
		const escaped = frappe.utils.escape_html(rawText);
		const { text: withPlaceholders, placeholders } = _extractAndRenderTables(escaped);

		let html;
		try {
			const converter = _getMarkdownConverter();
			if (converter) {
				html = converter.makeHtml(withPlaceholders);
			} else if (typeof frappe.markdown === 'function') {
				html = frappe.markdown(withPlaceholders);
			} else {
				throw new Error('no markdown converter available');
			}
		} catch (e) {
			html = withPlaceholders.replace(/\n/g, '<br>');
		}

		// the converter may have wrapped a lone placeholder token in <p>...</p>
		placeholders.forEach((tableHtml, idx) => {
			const token = `XXCOPILOTTABLE${idx}XX`;
			html = html.split(`<p>${token}</p>`).join(tableHtml).split(token).join(tableHtml);
		});

		return html;
	}

	// --- empty state ------------------------------------------------------

	function showEmptyStateIfNeeded() {
		if ($messages.children().length === 0) {
			$messages.html(`
				<div class="copilot-empty-state">
					Ask about invoices, customers, or any ERPNext data —<br>
					or ask me to build a doctype, chart, or report.
				</div>
			`);
		}
	}

	function clearEmptyState() {
		$messages.find('.copilot-empty-state').remove();
	}

	// --- message rendering --------------------------------------------------

	function addMessage(text, sender) {
		clearEmptyState();
		if (sender === 'agent') {
			$messages.append(`
				<div class="copilot-msg-row agent">
					<div class="copilot-avatar"></div>
					<div class="agent-bubble">${renderAgentMarkdown(text)}</div>
				</div>
			`);
		} else {
			$messages.append(`
				<div class="copilot-msg-row user">
					<div class="copilot-bubble-user">${frappe.utils.escape_html(text)}</div>
				</div>
			`);
		}
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function showTypingIndicator() {
		removeTypingIndicator();
		clearEmptyState();
		$(`
			<div class="copilot-msg-row agent copilot-typing">
				<div class="copilot-avatar"></div>
				<div class="agent-bubble" style="padding: 10px 14px;">
					<span class="copilot-typing-dot"></span><span class="copilot-typing-dot"></span><span class="copilot-typing-dot"></span>
				</div>
			</div>
		`).appendTo($messages);
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function removeTypingIndicator() {
		$messages.find('.copilot-typing').remove();
	}

	function startAgentBubble() {
		removeTypingIndicator();
		clearEmptyState();
		const $wrap = $(`
			<div class="copilot-msg-row agent">
				<div class="copilot-avatar"></div>
				<div class="agent-bubble" style="white-space: pre-wrap;"></div>
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
		clearEmptyState();
		$(`
			<div class="copilot-status-row">
				<span class="copilot-status-chip">
					<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
					${frappe.utils.escape_html(text)}
				</span>
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

	/**
	 * Today / Yesterday / Older, comparing local calendar dates (not a
	 * rolling 24h window — 11pm yesterday and 1am today are different
	 * "days" even though they're 2 hours apart). Frappe datetimes come
	 * back as "YYYY-MM-DD HH:MM:SS"; the space-to-T swap makes that a
	 * format Date() parses reliably cross-browser.
	 */
	function _dateGroupLabel(datetimeStr) {
		if (!datetimeStr) return 'Older';
		const d = new Date(String(datetimeStr).replace(' ', 'T'));
		if (isNaN(d)) return 'Older';
		const startOfDay = (dt) => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
		const diffDays = Math.round((startOfDay(new Date()) - startOfDay(d)) / 86400000);
		if (diffDays <= 0) return 'Today';
		if (diffDays === 1) return 'Yesterday';
		return 'Older';
	}

	function renderConvoList(rows) {
		$convoList.empty();
		if (!rows.length) {
			$convoList.append(`<div class="copilot-empty-convos">No conversations yet — start one below.</div>`);
		} else {
			let lastGroup = null;
			rows.forEach((row) => {
				// rows already arrive sorted most-recent-first from the
				// backend, so a simple "did the group change" check is
				// enough — no need to re-sort or bucket client-side.
				const group = _dateGroupLabel(row.last_message_at);
				if (group !== lastGroup) {
					$convoList.append(`<div class="copilot-convo-group-label">${group}</div>`);
					lastGroup = group;
				}
				const $item = $(`
					<div class="copilot-convo-item ${row.name === currentConversation ? 'active' : ''}" data-name="${row.name}" title="${frappe.utils.escape_html(row.title || 'New chat')}">
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

		// keep the header title in sync with whatever the list now says
		// about the active conversation (its title may have just changed)
		if (currentConversation) {
			const active = rows.find((r) => r.name === currentConversation);
			if (active) $chatHeader.text(active.title || 'New chat');
		}
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
				$chatHeader.text(data.title || 'New chat');
				$messages.empty();
				(data.messages || []).forEach((m) => addMessage(m.text, m.sender));
				showEmptyStateIfNeeded();
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
				$chatHeader.text('New chat');
				$messages.empty();
				showEmptyStateIfNeeded();
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
					$chatHeader.text('New chat');
					$messages.empty();
					showEmptyStateIfNeeded();
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
			frappe.confirm(
				_describeToolCall(data.tool, data.args),
				() => confirmAction(true),
				() => confirmAction(false)
			);
		}
	}

	/**
	 * Turns a proposed write-tool call into a readable action card instead
	 * of a raw JSON dump, so the confirm dialog reads like "here's what's
	 * about to happen" rather than "here's an API payload, good luck."
	 *
	 * Deliberately conservative: every known WRITE_TOOLS entry gets an
	 * explicit, hand-written description below. Anything NOT explicitly
	 * covered falls through to the old raw-args display — a tool added
	 * later without a matching case here should never silently show
	 * nothing useful, it should look like what it used to look like, not
	 * disappear.
	 */
	function _toolCardShell(icon, title, summaryHtml, detailsHtml) {
		return `
			<div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
				<span style="font-size: 20px;">${icon}</span>
				<span style="font-weight: 600; font-size: 15px;">${title}</span>
			</div>
			<div style="margin-bottom: ${detailsHtml ? '10px' : '4px'};">${summaryHtml}</div>
			${detailsHtml ? `<div style="background: var(--bg-gray); border-radius: 6px; padding: 8px 12px; font-size: 13px;">${detailsHtml}</div>` : ''}
		`;
	}

	function _detailRow(label, value) {
		return `<div style="display: flex; justify-content: space-between; gap: 12px; padding: 2px 0;">
			<span style="color: var(--text-muted);">${frappe.utils.escape_html(label)}</span>
			<span style="font-weight: 500; text-align: right;">${frappe.utils.escape_html(String(value))}</span>
		</div>`;
	}

	function _rawArgsFallback(tool, args) {
		const argsText = JSON.stringify(args, null, 2);
		return _toolCardShell(
			'⚙️', `Run ${frappe.utils.escape_html(tool)}`,
			'The assistant wants to run this action with:',
			`<pre style="margin: 0; white-space: pre-wrap;">${frappe.utils.escape_html(argsText)}</pre>`
		);
	}

	function _describeToolCall(tool, args) {
		args = args || {};
		try {
			if (tool === 'create_doctype') {
				const fields = args.fields || [];
				const rows = fields.map((f) => _detailRow(f.label || f.fieldname, f.fieldtype + (f.reqd ? ' · required' : ''))).join('');
				return _toolCardShell(
					'🛠️', 'Create DocType',
					`Create a new DocType called <b>${frappe.utils.escape_html(args.doctype_name)}</b> with ${fields.length} field${fields.length === 1 ? '' : 's'}:`,
					rows
				);
			}
			if (tool === 'create_dashboard_chart') {
				let summary = `Create a <b>${frappe.utils.escape_html(args.chart_type || 'Bar')}</b> chart named <b>${frappe.utils.escape_html(args.chart_name)}</b>, grouping <b>${frappe.utils.escape_html(args.document_type)}</b> by <b>${frappe.utils.escape_html(args.group_by_based_on)}</b>.`;
				if (args.dashboard_name) summary += ` It will be added to the <b>${frappe.utils.escape_html(args.dashboard_name)}</b> dashboard.`;
				return _toolCardShell('📊', 'Create Dashboard Chart', summary, null);
			}
			if (tool === 'add_chart_to_dashboard') {
				return _toolCardShell(
					'📌', 'Add Chart to Dashboard',
					`Add the chart <b>${frappe.utils.escape_html(args.chart_name)}</b> to the <b>${frappe.utils.escape_html(args.dashboard_name)}</b> dashboard.`,
					null
				);
			}
			if (tool === 'import_data_to_doctype') {
				const records = args.records || [];
				return _toolCardShell(
					'📥', 'Import Data',
					`Import <b>${records.length}</b> record${records.length === 1 ? '' : 's'} into <b>${frappe.utils.escape_html(args.target_doctype)}</b>.`,
					null
				);
			}
			if (tool === 'save_report') {
				const columns = args.columns || [];
				const filters = args.filters || {};
				const filterRows = Object.keys(filters).length
					? Object.entries(filters).map(([k, v]) => _detailRow(k, v)).join('')
					: '';
				return _toolCardShell(
					'💾', 'Save Report',
					`Save a report named <b>${frappe.utils.escape_html(args.report_name)}</b> on <b>${frappe.utils.escape_html(args.doctype)}</b> with columns: ${frappe.utils.escape_html(columns.join(', '))}.`,
					filterRows
				);
			}
		} catch (e) {
			// malformed/unexpected args shape for an otherwise-known tool —
			// fall through to the raw display rather than showing a broken card
		}
		return _rawArgsFallback(tool, args);
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
	showEmptyStateIfNeeded();
	refreshConvoList();
};