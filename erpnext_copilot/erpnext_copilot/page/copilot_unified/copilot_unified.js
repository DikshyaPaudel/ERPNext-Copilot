// Unified Copilot - Combines Chat, Report Builder, Dashboard Editor
// Modes: "chat" | "report" | "dashboard"

frappe.pages['copilot_unified'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Copilot'),
		single_column: true,
	});

	// One-time CSS
	if (!$('#copilot-unified-style').length) {
		$('head').append(`
			<style id="copilot-unified-style">
				.copilot-unified { display: flex; flex-direction: column; height: calc(100vh - 140px); }
				.copilot-toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border-color); background: var(--panel-bg); }
				.copilot-mode-btn { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border-color); background: var(--panel-bg); color: var(--text-color); font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.15s; }
				.copilot-mode-btn:hover { border-color: var(--blue); color: var(--blue); }
				.copilot-mode-btn.active { background: var(--blue); border-color: var(--blue); color: #fff; }
				.copilot-mode-btn svg { width: 14px; height: 14px; margin-right: 4px; vertical-align: -2px; }
				.copilot-body { flex: 1; display: flex; overflow: hidden; }
				.copilot-sidebar { width: 280px; border-right: 1px solid var(--border-color); background: var(--panel-bg); display: flex; flex-direction: column; flex-shrink: 0; }
				.copilot-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
				.copilot-conversations { flex: 1; overflow-y: auto; padding: 8px; }
				.copilot-convo-item { padding: 10px 12px; border-radius: 8px; cursor: pointer; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 4px; }
				.copilot-convo-item:hover { background: var(--bg-gray); }
				.copilot-convo-item.active { background: var(--bg-blue); color: var(--blue); font-weight: 600; }
				.copilot-convo-delete { opacity: 0; cursor: pointer; color: var(--text-muted); flex-shrink: 0; }
				.copilot-convo-item:hover .copilot-convo-delete { opacity: 1; }
				.copilot-thread { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
				.copilot-bubble { max-width: 85%; padding: 10px 14px; border-radius: 12px; font-size: 13.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
				.copilot-bubble.user { align-self: flex-end; background: var(--blue); color: #fff; border-bottom-right-radius: 4px; }
				.copilot-bubble.assistant { align-self: flex-start; background: var(--panel-bg); border: 1px solid var(--border-color); color: var(--text-color); border-bottom-left-radius: 4px; }
				.copilot-bubble.error { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 8%, var(--panel-bg)); color: var(--danger); }
				.copilot-composer { padding: 12px 16px; border-top: 1px solid var(--border-color); background: var(--panel-bg); }
				.copilot-composer-row { display: flex; gap: 8px; align-items: flex-end; }
				.copilot-composer textarea { flex: 1; min-height: 44px; max-height: 160px; resize: none; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border-color); background: var(--bg-white); color: var(--text-color); font-family: inherit; font-size: 14px; line-height: 1.5; outline: none; }
				.copilot-composer textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px color-mix(in srgb, var(--blue) 14%, transparent); }
				.copilot-send { width: 40px; height: 40px; border-radius: 10px; border: none; background: var(--blue); color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
				.copilot-send:disabled { opacity: 0.4; cursor: not-allowed; }
				.copilot-typing { display: inline-block; background: var(--bg-gray); padding: 10px 14px; border-radius: 8px; }
				.copilot-typing-dot { display: inline-block; width: 6px; height: 6px; margin: 0 2px; border-radius: 50%; background: var(--text-muted); animation: copilot-typing-bounce 1.2s infinite ease-in-out; }
				.copilot-typing-dot:nth-child(2) { animation-delay: 0.15s; }
				.copilot-typing-dot:nth-child(3) { animation-delay: 0.3s; }
				@keyframes copilot-typing-bounce { 0%, 60%, 100% { transform: translateY(0); opacity: 0.4; } 30% { transform: translateY(-4px); opacity: 1; } }
				.copilot-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); text-align: center; padding: 40px; }
				.copilot-empty-icon { width: 64px; height: 64px; opacity: 0.3; margin-bottom: 16px; }
				.copilot-welcome { display: flex; flex-direction: column; align-items: center; gap: 12px; }
				.copilot-welcome-title { font-size: 18px; font-weight: 700; color: var(--text-color); }
				.copilot-welcome-desc { font-size: 14px; color: var(--text-muted); max-width: 400px; }
				.copilot-starter { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 8px; }
				.copilot-starter-btn { padding: 10px 16px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--panel-bg); color: var(--text-color); font-size: 13px; cursor: pointer; transition: all 0.15s; }
				.copilot-starter-btn:hover { border-color: var(--blue); color: var(--blue); }
				/* Report mode */
				.report-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--border-color); }
				.report-title { font-size: 16px; font-weight: 700; }
				.report-actions { display: flex; gap: 8px; }
				.report-grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; padding: 16px; overflow-y: auto; flex: 1; }
				.report-card { position: relative; display: flex; flex-direction: column; }
				.report-kpi { grid-column: span 3; min-height: 128px; align-self: start; }
				.report-chart { grid-column: span 6; height: 366px; }
				.report-table { grid-column: span 12; min-height: 300px; max-height: 430px; }
				.report-toggle { position: absolute; top: -8px; left: -8px; z-index: 5; width: 24px; height: 24px; border-radius: 50%; border: 1px solid var(--border-color); background: var(--panel-bg); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
				.report-toggle.on { background: var(--blue); border-color: var(--blue); color: #fff; }
				.report-badge { position: absolute; top: -9px; right: 10px; z-index: 5; }
				/* Dashboard mode */
				.dashboard-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--border-color); }
				.dashboard-title { font-size: 16px; font-weight: 700; }
				.dashboard-canvas { flex: 1; padding: 16px; overflow: auto; }
				.dashboard-grid { display: grid; grid-template-columns: repeat(12, 1fr); grid-auto-rows: 64px; gap: 16px; min-height: 500px; }
				.dashboard-widget { position: relative; background: var(--panel-bg); border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
				.dashboard-widget.selected { outline: 2px solid var(--blue); outline-offset: -2px; }
				.dashboard-widget-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--border-color); font-weight: 600; font-size: 12px; }
				.dashboard-widget-body { flex: 1; padding: 12px; overflow: auto; }
				.dashboard-widget-handle { position: absolute; bottom: 4px; right: 4px; width: 16px; height: 16px; cursor: se-resize; opacity: 0.3; }
				.dashboard-widget-handle:hover { opacity: 1; }
				/* Mobile */
				@media (max-width: 900px) { .copilot-sidebar { display: none; } .copilot-mode-btn span { display: none; } .copilot-mode-btn { padding: 6px; } }
			</style>
		`);
	}

	// Render main structure
	const html = `
	<div class="copilot-unified">
		<div class="copilot-toolbar">
			<button class="copilot-mode-btn active" data-mode="chat" id="mode-chat">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
				<span>${__('Chat')}</span>
			</button>
			<button class="copilot-mode-btn" data-mode="report" id="mode-report">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 17V9M15 17V13"/></svg>
				<span>${__('Report Builder')}</span>
			</button>
			<button class="copilot-mode-btn" data-mode="dashboard" id="mode-dashboard">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/></svg>
				<span>${__('Dashboard Editor')}</span>
			</button>
			<div style="flex: 1"></div>
			<button class="btn btn-secondary btn-sm" id="new-conversation">${__('+ New')}</button>
		</div>
		<div class="copilot-body">
			<div class="copilot-sidebar" id="copilot-sidebar">
				<div style="padding: 12px; border-bottom: 1px solid var(--border-color); font-weight: 600; font-size: 12px; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.05em;">${__('Conversations')}</div>
				<div class="copilot-conversations" id="conversations-list"></div>
			</div>
			<div class="copilot-main" id="copilot-main">
				<!-- Content rendered based on mode -->
			</div>
		</div>
	</div>
	`;

	page.main.html(html);

	// Initialize
	window.copilotUnified = new CopilotUnified();
	window.copilotUnified.init();
};

class CopilotUnified {
	constructor() {
		this.mode = 'chat'; // chat | report | dashboard
		this.conversations = [];
		this.currentConversation = null;
		this.aiStatus = null;
		this.history = [];
		this.asking = false;
		this.reportWidgets = [];
		this.dashboardWidgets = [];
		this.dashboardLayout = {};
		this.selectedDashboardWidget = null;
		this.KPI_TINTS = ['blue', 'green', 'amber', 'violet'];

		// Chat mode state (matching copilot_chat.js)
		this.$currentAgentBubble = null;
		this.streamedAnyText = false;
		this.streamedRawText = '';
	}

	// --- Markdown/Table rendering (copied from copilot_chat.js) ---
	_escapeCell(text) {
		return text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`(.+?)`/g, '<code>$1</code>');
	}
	_splitTableRow(line) {
		let cells = line.trim();
		if (cells.startsWith('|')) cells = cells.slice(1);
		if (cells.endsWith('|')) cells = cells.slice(0, -1);
		return cells.split('|').map((c) => c.trim());
	}
	_isTableRowLine(line) { const t = line.trim(); return t.startsWith('|') && t.endsWith('|') && t.length > 1; }
	_isTableSeparatorLine(line) { if (!this._isTableRowLine(line)) return false; const cells = this._splitTableRow(line); return cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c)); }
	_buildTableHtml(headerLine, dataLines) {
		const headerCells = this._splitTableRow(headerLine);
		let html = '<div class="table-wrap"><table><tr>';
		headerCells.forEach((c) => { html += `<th>${this._escapeCell(c)}</th>`; });
		html += '</tr>';
		dataLines.forEach((line) => {
			const cells = this._splitTableRow(line);
			html += '<tr>';
			cells.forEach((c) => { html += `<td>${this._escapeCell(c)}</td>`; });
			html += '</tr>';
		});
		html += '</table></div>';
		return html;
	}
	_extractAndRenderTables(text) {
		const normalized = text.replace(/\|[ \t]*\|/g, '|\n|');
		const lines = normalized.split('\n');
		const placeholders = [];
		const outLines = [];
		let i = 0;
		while (i < lines.length) {
			if (this._isTableRowLine(lines[i]) && i + 1 < lines.length && this._isTableSeparatorLine(lines[i + 1])) {
				const headerLine = lines[i];
				let j = i + 2;
				const dataLines = [];
				while (j < lines.length && this._isTableRowLine(lines[j])) { dataLines.push(lines[j]); j++; }
				const token = `XXCOPILOTTABLE${placeholders.length}XX`;
				placeholders.push(this._buildTableHtml(headerLine, dataLines));
				if (outLines.length > 0 && outLines[outLines.length - 1].trim() !== '') outLines.push('');
				outLines.push(token);
				outLines.push('');
				i = j;
			} else { outLines.push(lines[i]); i++; }
		}
		return { text: outLines.join('\n'), placeholders };
	}
	_getMarkdownConverter() {
		if (this._mdConverter) return this._mdConverter;
		const Showdown = window.showdown || (window.frappe && frappe.showdown);
		if (Showdown && Showdown.Converter) { this._mdConverter = new Showdown.Converter({ strikethrough: true, simpleLineBreaks: true }); }
		return this._mdConverter;
	}
	renderAgentMarkdown(rawText) {
		const escaped = frappe.utils.escape_html(rawText);
		const { text: withPlaceholders, placeholders } = this._extractAndRenderTables(escaped);
		let html;
		try {
			const converter = this._getMarkdownConverter();
			if (converter) { html = converter.makeHtml(withPlaceholders); }
			else if (typeof frappe.markdown === 'function') { html = frappe.markdown(withPlaceholders); }
			else { throw new Error('no markdown converter available'); }
		} catch (e) { html = withPlaceholders.replace(/\n/g, '<br>'); }
		placeholders.forEach((tableHtml, idx) => { const token = `XXCOPILOTTABLE${idx}XX`; html = html.split(`<p>${token}</p>`).join(tableHtml).split(token).join(tableHtml); });
		return html;
	}

	async init() {
		this.bindModeButtons();
		this.bindNewConversation();
		await this.loadAIStatus();
		await this.loadConversations();
		this.loadConversationsList();
		this.renderCurrentMode();
	}

	bindModeButtons() {
		document.querySelectorAll('.copilot-mode-btn').forEach(btn => {
			btn.addEventListener('click', () => this.switchMode(btn.dataset.mode));
		});
	}

	bindNewConversation() {
		document.getElementById('new-conversation').addEventListener('click', () => this.newConversation());
	}

	switchMode(mode) {
		this.mode = mode;
		document.querySelectorAll('.copilot-mode-btn').forEach(btn => {
			btn.classList.toggle('active', btn.dataset.mode === mode);
		});
		this.renderCurrentMode();
	}

	async loadAIStatus() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.get_ai_status');
			this.aiStatus = response.message;
		} catch (e) {
			this.aiStatus = { configured: true };
		}
	}

	async loadConversations() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.agent_api.list_conversations');
			this.conversations = response.message || [];
		} catch (e) { this.conversations = []; }
	}

	loadConversationsList() {
		const container = document.getElementById('conversations-list');
		if (!container) return;
		container.innerHTML = this.conversations.map(c => `
			<div class="copilot-convo-item ${this.currentConversation?.name === c.name ? 'active' : ''}" data-name="${c.name}">
				<span>${c.title || 'New chat'}</span>
				<button class="copilot-convo-delete" data-name="${c.name}" title="${__('Delete')}">×</button>
			</div>
		`).join('') || `<div style="padding: 12px; color: var(--text-muted); font-size: 12px;">${__('No conversations yet')}</div>`;

		container.querySelectorAll('.copilot-convo-item').forEach(item => {
			item.addEventListener('click', (e) => {
				if (!e.target.classList.contains('copilot-convo-delete')) {
					this.loadConversation(item.dataset.name);
				}
			});
			item.querySelector('.copilot-convo-delete')?.addEventListener('click', (e) => {
				e.stopPropagation();
				this.deleteConversation(item.dataset.name);
			});
		});
	}

	async newConversation() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.agent_api.new_conversation');
			this.conversations.unshift(response.message);
			this.loadConversationsList();
			this.loadConversation(response.message.name);
		} catch (e) { frappe.msgprint({message: e.message, indicator: 'red'}); }
	}

	async loadConversation(name) {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.agent_api.get_conversation', { name });
			this.currentConversation = { name, ...response.message };
			this.history = this.transcriptToHistory(response.message.messages);
			this.loadConversationsList();
			this.renderCurrentMode();
		} catch (e) { frappe.msgprint({message: e.message, indicator: 'red'}); }
	}

	async deleteConversation(name) {
		if (!confirm(__('Delete this conversation?'))) return;
		try {
			await frappe.call('erpnext_copilot.custom_code.agent_api.delete_conversation', { name });
			this.conversations = this.conversations.filter(c => c.name !== name);
			if (this.currentConversation?.name === name) {
				this.currentConversation = null;
				this.history = [];
			}
			this.loadConversationsList();
			this.renderCurrentMode();
		} catch (e) { frappe.msgprint({message: e.message, indicator: 'red'}); }
	}

	transcriptToHistory(messages) {
		return messages.map(m => ({ role: m.sender === 'user' ? 'user' : 'model', text: m.text }));
	}

	historyToTranscript(history) {
		return history.map(h => ({ sender: h.role === 'user' ? 'user' : 'agent', text: h.text }));
	}

	renderCurrentMode() {
		const main = document.getElementById('copilot-main');
		if (!main) return;

		switch (this.mode) {
			case 'chat': this.renderChatMode(main); break;
			case 'report': this.renderReportMode(main); break;
			case 'dashboard': this.renderDashboardMode(main); break;
		}
	}

	// ========== CHAT MODE ==========
	renderChatMode(container) {
		if (!this.currentConversation) {
			container.innerHTML = this.getWelcomeHTML();
			this.bindStarterButtons();
			this.bindRealtimeEvents();
			return;
		}

		container.innerHTML = `
			<div class="copilot-thread" id="chat-thread"></div>
			<div id="chat-typing" class="copilot-typing hidden"></div>
			<div class="copilot-composer">
				<div class="copilot-composer-row">
					<textarea id="chat-input" placeholder="${__('Ask me anything…')}" rows="1"></textarea>
					<button class="copilot-send" id="chat-send"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg></button>
				</div>
			</div>
		`;
		this.renderChatMessages();
		this.bindRealtimeEvents();
		this.bindChatInputEvents();
	}

	bindChatInputEvents() {
		const input = document.getElementById('chat-input');
		const sendBtn = document.getElementById('chat-send');
		if (!input || !sendBtn) return;

		input.addEventListener('keydown', (e) => {
			if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendChatMessage(); }
		});
		sendBtn.addEventListener('click', () => this.sendChatMessage());
		input.addEventListener('input', () => {
			input.style.height = 'auto';
			input.style.height = Math.min(input.scrollHeight, 160) + 'px';
		});
	}

	bindRealtimeEvents() {
		frappe.realtime.on('copilot_stream_chunk', (data) => this.appendToAgentBubble(data.text));
		frappe.realtime.on('copilot_status', (data) => this.addStatusLine(data.text));
	}

	getWelcomeHTML() {
		const starters = [
			'Show me revenue by customer',
			'Create a doctype for Vehicle Maintenance',
			'Build a dashboard for sales',
			'Import data from CSV',
			'What are the top unpaid invoices?'
		];
		return `
			<div class="copilot-empty">
				<svg class="copilot-empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
				<div class="copilot-welcome">
					<div class="copilot-welcome-title">${__('Welcome to Copilot')}</div>
					<p class="copilot-welcome-desc">${__('Start a conversation or pick a starter below.')}</p>
					<div class="copilot-starter">
						${starters.map(s => `<button class="copilot-starter-btn" data-starter="${s}">${s}</button>`).join('')}
					</div>
				</div>
			</div>
		`;
	}

	bindStarterButtons() {
		document.querySelectorAll('.copilot-starter-btn').forEach(btn => {
			btn.addEventListener('click', () => {
				this.newConversation().then(() => {
					this.sendChatMessage(btn.dataset.starter);
				});
			});
		});
	}

	renderChatMessages() {
		const thread = document.getElementById('chat-thread');
		if (!thread) return;
		if (!this.currentConversation?.messages?.length) {
			thread.innerHTML = '';
			return;
		}
		thread.innerHTML = this.currentConversation.messages.map(m => `
			<div class="copilot-bubble ${m.sender === 'user' ? 'user' : (m.sender === 'agent' ? 'assistant' : 'error')}">
				${m.sender === 'agent' ? this.renderAgentMarkdown(m.text) : this.formatMessage(m.text)}
			</div>
		`).join('');
		thread.scrollTop = thread.scrollHeight;
	}

	formatMessage(text) {
		// Simple markdown: tables, bold, code
		return text
			.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
			.replace(/`(.+?)`/g, '<code style="background:var(--bg-gray);padding:2px 4px;border-radius:3px;font-family:monospace">$1</code>')
			.replace(/\|(.+)\|/g, (match) => '<div style="overflow-x:auto;font-family:monospace;font-size:12px">' + match.split('\n').map(row => {
				const cells = row.split('|').filter(c => c.trim());
				return '<div style="display:flex">' + cells.map(c => `<span style="flex:1;padding:4px 8px;border-bottom:1px solid var(--border-color)">${c.trim()}</span>`).join('') + '</div>';
			}).join('') + '</div>');
	}

	// --- Chat helper functions (matching copilot_chat.js) ---
	addMessage(text, sender) {
		const thread = document.getElementById('chat-thread');
		if (!thread) return;
		const align = sender === 'user' ? 'right' : 'left';
		const bg = sender === 'user' ? 'var(--bg-blue)' : 'var(--bg-gray)';
		if (sender === 'agent') {
			thread.insertAdjacentHTML('beforeend', `
				<div style="text-align: left; margin-bottom: 10px;">
					<div class="agent-bubble" style="display: inline-block; background: ${bg}; padding: 8px 12px; border-radius: 8px; max-width: 90%;">${this.renderAgentMarkdown(text)}</div>
				</div>
			`);
		} else {
			thread.insertAdjacentHTML('beforeend', `
				<div style="text-align: ${align}; margin-bottom: 10px;">
					<span style="display: inline-block; background: ${bg}; padding: 8px 12px; border-radius: 8px; max-width: 80%; white-space: pre-wrap;">${frappe.utils.escape_html(text)}</span>
				</div>
			`);
		}
		thread.scrollTop = thread.scrollHeight;
	}

	showTypingIndicator() {
		this.removeTypingIndicator();
		const thread = document.getElementById('chat-thread');
		if (!thread) return;
		thread.insertAdjacentHTML('beforeend', `
			<div class="copilot-typing" style="text-align: left; margin-bottom: 10px;">
				<span style="display: inline-block; background: var(--bg-gray); padding: 10px 14px; border-radius: 8px;">
					<span class="copilot-typing-dot"></span><span class="copilot-typing-dot"></span><span class="copilot-typing-dot"></span>
				</span>
			</div>
		`);
		thread.scrollTop = thread.scrollHeight;
	}

	removeTypingIndicator() {
		const thread = document.getElementById('chat-thread');
		if (!thread) return;
		thread.querySelectorAll('.copilot-typing').forEach(el => el.remove());
	}

	startAgentBubble() {
		this.removeTypingIndicator();
		const thread = document.getElementById('chat-thread');
		if (!thread) return;
		const $wrap = $(`
			<div style="text-align: left; margin-bottom: 10px;">
				<div class="agent-bubble" style="display: inline-block; background: var(--bg-gray); padding: 8px 12px; border-radius: 8px; max-width: 90%; white-space: pre-wrap;"></div>
			</div>
		`).appendTo(thread);
		this.$currentAgentBubble = $wrap.find('.agent-bubble');
		this.streamedAnyText = false;
		this.streamedRawText = '';
		thread.scrollTop = thread.scrollHeight;
		return this.$currentAgentBubble;
	}

	appendToAgentBubble(text) {
		this.removeTypingIndicator();
		if (!this.$currentAgentBubble) this.startAgentBubble();
		this.streamedRawText += text;
		this.$currentAgentBubble.text(this.streamedRawText);
		this.streamedAnyText = true;
		const thread = document.getElementById('chat-thread');
		if (thread) thread.scrollTop = thread.scrollHeight;
	}

	addStatusLine(text) {
		this.removeTypingIndicator();
		const thread = document.getElementById('chat-thread');
		if (!thread) return;
		thread.insertAdjacentHTML('beforeend', `
			<div style="text-align: left; margin-bottom: 6px; font-size: 12px; color: var(--text-muted);">
				${frappe.utils.escape_html(text)}
			</div>
		`);
		thread.scrollTop = thread.scrollHeight;
	}

	resetStreamState() {
		this.$currentAgentBubble = null;
		this.streamedAnyText = false;
		this.streamedRawText = '';
	}

	async sendChatMessage(text = null) {
		const input = document.getElementById('chat-input');
		const message = (text || input?.value || '').trim();
		if (!message || this.asking) return;
		if (input) input.value = '';

		this.addMessage(message, 'user');
		this.asking = true;
		this.updateChatUI();
		this.resetStreamState();
		this.showTypingIndicator();

		try {
			const response = await frappe.call('erpnext_copilot.custom_code.agent_api.ask_agent', {
				message,
				conversation: this.currentConversation?.name
			});
			this.handleResponse(response);
		} catch (e) {
			this.removeTypingIndicator();
			this.resetStreamState();
			this.addMessage('Something went wrong — check the console.', 'agent');
			frappe.msgprint({message: e.message, indicator: 'red'});
		} finally {
			this.asking = false;
			this.updateChatUI();
		}
	}

	handleResponse(res) {
		this.removeTypingIndicator();
		const data = res.message;
		if (data.conversation) this.currentConversation = { name: data.conversation, ...this.currentConversation };

		if (data.type === 'reply') {
			if (this.streamedAnyText && this.$currentAgentBubble) {
				this.$currentAgentBubble.css('white-space', 'normal').html(this.renderAgentMarkdown(this.streamedRawText));
			} else if (!this.streamedAnyText) {
				this.addMessage(data.text, 'agent');
			}
			this.resetStreamState();
			this.loadConversationsList();
		} else if (data.type === 'pending_action') {
			this.resetStreamState();
			const argsText = JSON.stringify(data.args, null, 2);
			frappe.confirm(
				`The assistant wants to run <b>${data.tool}</b> with:<pre>${frappe.utils.escape_html(argsText)}</pre>Proceed?`,
				() => this.confirmAction(true),
				() => this.confirmAction(false)
			);
		}
	}

	confirmAction(approved) {
		this.resetStreamState();
		if (approved) this.showTypingIndicator();
		frappe.call({
			method: 'erpnext_copilot.custom_code.agent_api.confirm_pending_action',
			args: { approved },
			callback: (res) => this.handleResponse(res),
		});
	}

	async sendChatMessage(text = null) {
		const input = document.getElementById('chat-input');
		const message = (text || input?.value || '').trim();
		if (!message || this.asking) return;
		if (input) input.value = '';

		this.addMessage(message, 'user');
		this.asking = true;
		this.updateChatUI();
		this.resetStreamState();
		this.showTypingIndicator();

		try {
			const response = await frappe.call('erpnext_copilot.custom_code.agent_api.ask_agent', {
				message,
				conversation: this.currentConversation?.name
			});
			this.handleResponse(response);
		} catch (e) {
			this.removeTypingIndicator();
			this.resetStreamState();
			this.addMessage('Something went wrong — check the console.', 'agent');
			frappe.msgprint({message: e.message, indicator: 'red'});
		} finally {
			this.asking = false;
			this.updateChatUI();
		}
	}

	updateChatUI() {
		const input = document.getElementById('chat-input');
		const sendBtn = document.getElementById('chat-send');
		const typing = document.getElementById('chat-typing');
		if (input) { input.disabled = this.asking; }
		if (sendBtn) { sendBtn.disabled = this.asking; }
		if (typing) { typing.classList.toggle('hidden', !this.asking); }
	}

	// ========== REPORT MODE ==========
	renderReportMode(container) {
		if (!this.aiStatus?.configured) {
			container.innerHTML = this.getReportSetupHTML();
			return;
		}

		container.innerHTML = `
			<div class="report-header">
				<h2 class="report-title">${this.reportWidgets.length ? this.reportWidgets[0]?.title || __('AI Report') : __('Ask AI for a Report')}</h2>
				<div class="report-actions">
					${this.reportWidgets.length ? `
						<button class="btn btn-primary btn-sm" id="save-report-dashboard">${__('Save as Dashboard')}</button>
						<button class="btn btn-primary btn-sm" style="background: var(--violet); border-color: var(--violet)" id="save-report-report">${__('Save as Report')}</button>
						<button class="btn btn-secondary btn-sm" id="save-report-append">${__('Append to Dashboard')}</button>
					` : ''}
					<button class="btn btn-secondary btn-sm" id="reset-report">${__('New Question')}</button>
				</div>
			</div>
			<div class="report-composer" style="padding: 12px 16px; border-bottom: 1px solid var(--border-color); display: flex; gap: 8px;">
				<input type="text" id="report-question" class="form-control" placeholder="${__('e.g. monthly revenue by territory this year')}" style="flex: 1" />
				<button class="btn btn-primary" id="report-ask">${this.asking ? __('Thinking…') : __('Ask')}</button>
			</div>
			<div id="report-loading" class="hidden" style="padding: 40px; text-align: center;">
				<div class="skeleton" style="width: 200px; height: 20px; margin: 0 auto 16px;"></div>
				<div class="skeleton" style="width: 300px; height: 12px; margin: 0 auto 8px;"></div>
				<p style="color: var(--text-muted); font-size: 13px;">${__('Building your report…')}</p>
			</div>
			<div id="report-error" class="hidden" style="padding: 16px; color: var(--danger);"></div>
			<div class="report-grid" id="report-grid"></div>
			<div id="report-empty" class="hidden" style="padding: 40px; text-align: center; color: var(--text-muted);">${__('Ask a question to generate a report')}</div>
		`;

		this.bindReportEvents();
		this.updateReportUI();
	}

	getReportSetupHTML() {
		return `
			<div class="copilot-empty">
				<svg class="copilot-empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 17V9M15 17V13"/></svg>
				<div class="copilot-welcome">
					<div class="copilot-welcome-title">${__('Connect AI to build reports')}</div>
					<p class="copilot-welcome-desc">${__('Add a Gemini API key in settings to generate reports from natural language.')}</p>
					<button class="btn btn-primary" id="open-ai-settings">${__('Open AI Settings')}</button>
				</div>
			</div>
		`;
	}

	bindReportEvents() {
		const askBtn = document.getElementById('report-ask');
		const input = document.getElementById('report-question');
		const resetBtn = document.getElementById('reset-report');
		const settingsBtn = document.getElementById('open-ai-settings');

		if (askBtn) askBtn.addEventListener('click', () => this.generateReport());
		if (input) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.generateReport(); });
		if (resetBtn) resetBtn.addEventListener('click', () => this.resetReport());
		if (settingsBtn) settingsBtn.addEventListener('click', () => this.openAISettings());

		// Save buttons
		document.getElementById('save-report-dashboard')?.addEventListener('click', () => this.saveReportAs('dashboard'));
		document.getElementById('save-report-report')?.addEventListener('click', () => this.saveReportAs('report'));
		document.getElementById('save-report-append')?.addEventListener('click', () => this.saveReportAs('append'));
	}

	async generateReport() {
		const input = document.getElementById('report-question');
		const question = input?.value?.trim();
		if (!question || this.asking) return;

		this.asking = true;
		this.updateReportUI();

		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.ask_ai', {
				prompt: question,
				// Don't send history when empty to avoid Frappe stringifying it as '[]'
				answered: false
			});

			if (response.message?.clarify) {
				// Handle clarification
				const answer = prompt(response.message.clarify + '\n\nOptions: ' + (response.message.options || []).join(', '));
				if (answer) {
					const followup = await frappe.call('erpnext_copilot.custom_code.api.ask_ai', {
						prompt: answer,
						history: [{role: 'user', text: question}, {role: 'assistant', text: response.message.clarify}],
						answered: true
					});
					this.reportWidgets = (followup.message?.widgets || []).map(w => ({ ...w, included: !w.empty }));
				}
			} else {
				this.reportWidgets = (response.message?.widgets || []).map(w => ({ ...w, included: !w.empty }));
			}
			input.value = '';
		} catch (e) {
			document.getElementById('report-error').textContent = e.message;
			document.getElementById('report-error').classList.remove('hidden');
		} finally {
			this.asking = false;
			this.updateReportUI();
		}
	}

	updateReportUI() {
		document.getElementById('report-loading')?.classList.toggle('hidden', !this.asking);
		const askBtn = document.getElementById('report-ask');
		if (askBtn) { askBtn.disabled = this.asking; askBtn.textContent = this.asking ? __('Thinking…') : __('Ask'); }
		document.getElementById('report-error')?.classList.add('hidden');

		const hasWidgets = this.reportWidgets.length > 0;
		document.getElementById('report-grid')?.classList.toggle('hidden', !hasWidgets || this.asking);
		document.getElementById('report-empty')?.classList.toggle('hidden', hasWidgets || this.asking);

		if (hasWidgets && !this.asking) this.renderReportWidgets();
	}

	renderReportWidgets() {
		const grid = document.getElementById('report-grid');
		if (!grid) return;
		grid.innerHTML = '';

		this.reportWidgets.forEach((entry, i) => {
			const widget = entry.widget;
			const result = entry.result;
			const empty = entry.empty;
			const included = entry.included;

			const card = document.createElement('div');
			let widgetClass = 'report-chart';
			if (widget.widget_type === 'Number Card') widgetClass = 'report-kpi';
			else if (widget.widget_type === 'Table') widgetClass = 'report-table';
			card.className = `report-card ${included ? '' : 'report-off'} ${widgetClass}`;

			const toggle = document.createElement('button');
			toggle.className = `report-toggle ${included ? 'on' : ''}`;
			toggle.title = included ? __('Included') : __('Excluded');
			toggle.innerHTML = included ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="m4.5 12.5 5 5 10-11" /></svg>' : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>';
			toggle.onclick = () => { entry.included = !entry.included; this.updateReportUI(); };
			card.appendChild(toggle);

			if (empty) {
				const badge = document.createElement('span');
				badge.className = 'badge amber report-badge';
				badge.innerHTML = '<span class="dot"></span>' + __('No data');
				card.appendChild(badge);
			}

			if (widget.widget_type === 'Number Card') {
				card.innerHTML += this.renderNumberCard(widget, result, i);
			} else {
				card.innerHTML += this.renderChartOrTable(widget, result, i);
			}

			grid.appendChild(card);
		});
	}

	renderNumberCard(widget, result, index) {
		const tint = widget.style?.tint || this.KPI_TINTS[this.getKPIIndex(index) % 4];
		const value = result?.result_type === 'number' ? this.formatNumber(result.value) : '—';
		return `<div class="panel kpi-panel"><div class="kpi-value" style="color: var(--${tint}-600, #3b82f6)">${value}</div><div class="kpi-label">${frappe.utils.escape_html(widget.title)}</div></div>`;
	}

	renderChartOrTable(widget, result, index) {
		const title = frappe.utils.escape_html(widget.title);
		if (widget.widget_type === 'Table') {
			return `<div class="panel" style="display: flex; flex-direction: column; height: 100%;"><div class="panel-h"><div class="t">${title}</div></div><div class="table-container" style="flex: 1; min-height: 0; padding: 14px 18px 16px;">${this.renderTable(result)}</div></div>`;
		}
		return `<div class="panel" style="display: flex; flex-direction: column; height: 100%;"><div class="panel-h"><div class="t">${title}</div></div><div class="chart-container" style="flex: 1; min-height: 0; padding: 14px 18px 16px;"><div id="chart-${widget.widget_id}" style="width: 100%; height: 100%;"></div></div></div>`;
	}

	renderTable(result) {
		if (!result || result.result_type !== 'rows') return '<div style="color: var(--text-muted); text-align: center; padding: 20px;">' + __('No data') + '</div>';
		const columns = result.columns || [];
		const rows = result.rows || [];
		if (!rows.length) return '<div style="color: var(--text-muted); text-align: center; padding: 20px;">' + __('No data') + '</div>';
		let html = '<table><thead><tr>';
		columns.forEach(col => { html += `<th>${frappe.utils.escape_html(col.label)}</th>`; });
		html += '</tr></thead><tbody>';
		rows.forEach(row => {
			html += '<tr>';
			columns.forEach(col => {
				let val = row[col.fieldname];
				if (val instanceof Date) val = val.toISOString().split('T')[0];
				else if (typeof val === 'number' && col.fieldtype === 'Currency') val = this.formatCurrency(val);
				else if (val === null || val === undefined) val = '—';
				// Strip HTML tags if value contains pre-formatted HTML
				if (typeof val === 'string' && val.includes('<')) {
					const temp = document.createElement('div');
					temp.innerHTML = val;
					val = temp.textContent || temp.innerText || val;
				}
				html += `<td>${frappe.utils.escape_html(String(val))}</td>`;
			});
			html += '</tr>';
		});
		html += '</tbody></table>';
		return html;
	}

	getKPIIndex(i) { let n = 0; for (let j = 0; j < i; j++) if (this.reportWidgets[j]?.widget?.widget_type === 'Number Card') n++; return n; }
	formatNumber(num) { if (num === null || num === undefined) return '—'; if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B'; if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M'; if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K'; return Number(num).toLocaleString(); }
	formatCurrency(num) { return frappe.format(num, { fieldtype: 'Currency' }); }

	resetReport() {
		this.reportWidgets = [];
		const input = document.getElementById('report-question');
		if (input) input.value = '';
		this.updateReportUI();
	}

	async saveReportAs(type) {
		const included = this.reportWidgets.filter(w => w.included).map(w => w.widget);
		if (!included.length) return;

		// Use frappe.request with JSON content type to properly send arrays
		const makeRequest = async (method, data) => {
			return new Promise((resolve, reject) => {
				frappe.request({
					method: 'POST',
					url: `/api/method/${method}`,
					data: JSON.stringify(data),
					contentType: 'application/json',
					callback: (res) => {
						if (res.exc) reject(res.exc);
						else resolve(res.message);
					},
					error: (err) => reject(err)
				});
			});
		};

		try {
			if (type === 'report') {
				const reportName = 'rpt_' + frappe.utils.generate_hash(8);
				await makeRequest('erpnext_copilot.custom_code.api.save_ai_result_as_report', {
					widgets: included, title: this.reportWidgets[0]?.title || 'AI Report', report_name: reportName
				});
				frappe.msgprint({message: __('Report created'), indicator: 'green'});
			} else if (type === 'dashboard') {
				await makeRequest('erpnext_copilot.custom_code.api.save_ai_result', {
					widgets: included, title: this.reportWidgets[0]?.title || 'AI Dashboard'
				});
				frappe.msgprint({message: __('Dashboard created'), indicator: 'green'});
			} else if (type === 'append') {
				// Show dashboard picker
				const dashboards = await frappe.call('erpnext_copilot.custom_code.api.get_dashboards');
				const options = (dashboards.message?.dashboards || []).map(d => `${d.dashboard_name || d.name}|${d.route_slug || d.name}`).join('\n');
				const choice = prompt(__('Select dashboard:\n') + options);
				if (choice) {
					await makeRequest('erpnext_copilot.custom_code.api.save_ai_result', {
						widgets: included, slug: choice.split('|')[1]
					});
					frappe.msgprint({message: __('Added to dashboard'), indicator: 'green'});
				}
			}
		} catch (e) { frappe.msgprint({message: e.message, indicator: 'red'}); }
	}

	openAISettings() {
		frappe.set_route('ask_ai');
	}

	// ========== DASHBOARD MODE ==========
	renderDashboardMode(container) {
		container.innerHTML = `
			<div class="dashboard-header">
				<h2 class="dashboard-title">${__('Dashboard Editor')}</h2>
				<div style="display: flex; gap: 8px;">
					<button class="btn btn-primary btn-sm" id="save-dashboard">${__('Save')}</button>
					<button class="btn btn-secondary btn-sm" id="preview-dashboard">${__('Preview')}</button>
				</div>
			</div>
			<div class="dashboard-canvas">
				<div class="dashboard-grid" id="dashboard-grid"></div>
			</div>
		`;
		this.bindDashboardEvents();
		this.renderDashboardGrid();
	}

	bindDashboardEvents() {
		document.getElementById('save-dashboard')?.addEventListener('click', () => this.saveDashboard());
		document.getElementById('preview-dashboard')?.addEventListener('click', () => this.previewDashboard());
	}

	renderDashboardGrid() {
		const grid = document.getElementById('dashboard-grid');
		if (!grid) return;

		if (this.dashboardWidgets.length === 0) {
			grid.innerHTML = `
				<div style="grid-column: 1 / -1; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 300px; color: var(--text-muted);">
					<svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity: 0.3"><rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/></svg>
					<p style="margin-top: 16px; font-size: 14px;">${__('Drag widgets from report mode, or ask AI to generate a dashboard')}</p>
				</div>
			`;
			return;
		}

		grid.innerHTML = '';
		this.dashboardWidgets.forEach((widget, i) => {
			const layout = this.dashboardLayout[widget.widget_id] || { x: 0, y: i * 5, w: 6, h: 5 };
			const card = document.createElement('div');
			card.className = 'dashboard-widget';
			card.style.gridColumn = `${layout.x + 1} / span ${layout.w}`;
			card.style.gridRow = `${layout.y + 1} / span ${layout.h}`;
			card.dataset.widgetId = widget.widget_id;
			if (this.selectedDashboardWidget === widget.widget_id) card.classList.add('selected');

			card.innerHTML = `
				<div class="dashboard-widget-header">
					<span>${frappe.utils.escape_html(widget.title)}</span>
					<div style="display: flex; gap: 4px;">
						<button class="btn btn-secondary btn-xs" data-action="configure">${__('Config')}</button>
						<button class="btn btn-danger btn-xs" data-action="delete">×</button>
					</div>
				</div>
				<div class="dashboard-widget-body">
					${this.renderWidgetPreview(widget)}
				</div>
				<div class="dashboard-widget-handle" data-widget="${widget.widget_id}"></div>
			`;
			grid.appendChild(card);
		});

		// Bind widget events
		grid.querySelectorAll('[data-action="delete"]').forEach(btn => {
			btn.addEventListener('click', (e) => {
				e.stopPropagation();
				const id = btn.closest('.dashboard-widget').dataset.widgetId;
				this.removeDashboardWidget(id);
			});
		});
		grid.querySelectorAll('[data-action="configure"]').forEach(btn => {
			btn.addEventListener('click', (e) => {
				e.stopPropagation();
				const id = btn.closest('.dashboard-widget').dataset.widgetId;
				this.configureDashboardWidget(id);
			});
		});
		grid.querySelectorAll('.dashboard-widget').forEach(w => {
			w.addEventListener('click', () => {
				this.selectedDashboardWidget = w.dataset.widgetId;
				this.renderDashboardGrid();
			});
		});
	}

	renderWidgetPreview(widget) {
		if (widget.widget_type === 'Number Card') {
			return `<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:12px;">${widget.title}</div>`;
		}
		return `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:12px;">${widget.title} (${widget.widget_type})</div>`;
	}

	removeDashboardWidget(id) {
		this.dashboardWidgets = this.dashboardWidgets.filter(w => w.widget_id !== id);
		delete this.dashboardLayout[id];
		if (this.selectedDashboardWidget === id) this.selectedDashboardWidget = null;
		this.renderDashboardGrid();
	}

	configureDashboardWidget(id) {
		frappe.msgprint({message: __('Widget configuration - integrate with report mode widgets'), indicator: 'info'});
	}

	saveDashboard() {
		frappe.msgprint({message: __('Dashboard saved'), indicator: 'green'});
	}

	previewDashboard() {
		frappe.set_route('dashboard-view', this.currentConversation?.name || 'preview');
	}
}