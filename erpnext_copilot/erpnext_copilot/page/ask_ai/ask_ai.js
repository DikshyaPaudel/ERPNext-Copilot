// Ask AI Page - Natural Language to Report/Dashboard
// Uses legacy frappe.pages API (like copilot_chat)

frappe.pages['ask_ai'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Ask AI'),
		single_column: true,
	});

	// One-time CSS
	if (!$('#ask-ai-style').length) {
		$('head').append(`
			<style id="ask-ai-style">
				.ask-ai-container { max-width: 1200px; margin: 0 auto; padding: 24px; }
				.eyebrow-line { font-size: 12.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--blue, #3b82f6); display: inline-flex; align-items: center; gap: 9px; }
				.eyebrow-line .bar { width: 20px; height: 2px; background: var(--blue, #3b82f6); display: inline-block; }
				.ask-ai-title { font-size: 30px; font-weight: 800; letter-spacing: -0.03em; margin-top: 8px; color: var(--text-color); margin-bottom: 0; }
				.ask-ai-subtitle { color: var(--text-muted); margin-top: 4px; font-size: 14.5px; line-height: 1.5; }
				.ask-ai-panel { background: var(--panel-bg, #ffffff); border: 1px solid var(--border-color, #e5e7eb); border-radius: 12px; padding: 18px; }
				.key-eyebrow { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted); font-weight: 500; }
				.guide-summary { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--blue, #3b82f6); cursor: pointer; }
				.guide { font-size: 13px; color: var(--text-muted); margin: 8px 0 0; padding-inline-start: 18px; line-height: 1.9; list-style: decimal; }
				.kpi-icon { display: inline-flex; align-items: center; justify-content: center; }
				.icon-wrapper { display: inline-flex; align-items: center; justify-content: center; }
				.icon-wrapper.blue { background: color-mix(in srgb, var(--blue, #3b82f6) 12%, var(--panel-bg)); color: var(--blue, #3b82f6); }
				.indicator { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
				.indicator.green { background: var(--success, #10b981); }
				.badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
				.badge .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
				.badge.amber { background: color-mix(in srgb, var(--warning, #f59e0b) 12%, var(--panel-bg)); color: var(--warning, #f59e0b); border: 1px solid color-mix(in srgb, var(--warning, #f59e0b) 30%, var(--border-color)); }
				.badge.green { background: color-mix(in srgb, var(--success, #10b981) 12%, var(--panel-bg)); color: var(--success, #10b981); border: 1px solid color-mix(in srgb, var(--success, #10b981) 30%, var(--border-color)); }
				.badge.violet { background: color-mix(in srgb, var(--violet, #8b5cf6) 12%, var(--panel-bg)); color: var(--violet, #8b5cf6); border: 1px solid color-mix(in srgb, var(--violet, #8b5cf6) 30%, var(--border-color)); }
				.badge.hidden { display: none; }
				.form-group { display: flex; flex-direction: column; gap: 4px; }
				.form-group label { font-size: 12px; font-weight: 600; color: var(--text-color); }
				.form-control { height: 36px; padding: 0 12px; border-radius: 10px; border: 1px solid var(--border-color, #e5e7eb); background: var(--panel-bg, #ffffff); color: var(--text-color); font-family: inherit; font-size: 13.5px; outline: none; width: 100%; box-sizing: border-box; }
				.form-control:focus { border-color: var(--blue, #3b82f6); box-shadow: 0 0 0 3px color-mix(in srgb, var(--blue, #3b82f6) 14%, transparent); }
				.ask-input { font-size: 15px; flex: 1; min-width: 0; }
				.skeleton { background: linear-gradient(90deg, var(--border-color) 25%, var(--panel-bg) 50%, var(--border-color) 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; border-radius: 4px; }
				@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
				.error-panel { border-color: color-mix(in srgb, var(--danger, #ef4444) 40%, var(--border-color)); background: color-mix(in srgb, var(--danger, #ef4444) 5%, var(--panel-bg)); }
				.chip-label { font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); font-weight: 500; }
				.ai-grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; }
				.ai-card { position: relative; display: flex; flex-direction: column; }
				.ai-card > .panel { flex: 1; min-height: 0; }
				.ai-kpi { grid-column: span 3; min-height: 128px; align-self: start; }
				.ai-chart { grid-column: span 6; height: 366px; }
				.ai-table { grid-column: span 12; min-height: 300px; max-height: 430px; }
				.ai-card.ai-off > .panel { opacity: 0.45; filter: grayscale(0.4); }
				.ai-toggle { position: absolute; top: -8px; inset-inline-start: -8px; z-index: 5; width: 24px; height: 24px; border-radius: 50%; border: 1px solid var(--border-color); background: var(--panel-bg); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
				.ai-toggle.on { background: var(--blue, #3b82f6); border-color: var(--blue, #3b82f6); color: #fff; }
				.ai-empty { position: absolute; top: -9px; inset-inline-end: 10px; z-index: 5; }
				.analysis-panel { padding: 15px 18px; margin-bottom: 14px; border-inline-start: 3px solid var(--blue, #3b82f6); }
				.analysis-eyebrow { font-size: 9.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 5px; }
				.analysis-head { font-size: 14.5px; font-weight: 800; letter-spacing: -0.01em; color: var(--text-color); margin-bottom: 8px; }
				.analysis-list { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: var(--text-muted); padding-inline-start: 18px; list-style: disc; }
				.analysis-list li::marker { color: var(--blue, #3b82f6); }
				.analysis-watch { margin-top: 7px; }
				.analysis-watch li::marker { color: var(--warning, #f59e0b); }
				.analysis-watch li { color: var(--text-muted); }
				.example-chip { border-color: var(--border-color); background: var(--panel-bg); color: var(--text-muted); }
				.example-chip:hover { border-color: var(--blue, #3b82f6); color: var(--blue, #3b82f6); }
				.panel-header { display: flex; align-items: center; justify-content: space-between; padding: 13px 18px; border-bottom: 1px solid var(--border-color); }
				.panel-h { padding: 13px 18px; }
				.panel-h .t { font-weight: 600; color: var(--text-color); }
				.kpi-panel { height: 100%; display: flex; flex-direction: column; justify-content: center; padding: 16px; }
				.kpi-value { font-size: 32px; font-weight: 800; line-height: 1.1; color: var(--text-color); }
				.kpi-label { font-size: 12px; color: var(--text-muted); margin-top: 4px; font-weight: 500; }
				.chart-container { height: 100%; min-height: 250px; }
				.table-container { height: 100%; overflow: auto; }
				.table-container table { width: 100%; border-collapse: collapse; font-size: 12px; }
				.table-container th, .table-container td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border-color); }
				.table-container th { font-weight: 600; color: var(--text-color); background: var(--panel-bg); position: sticky; top: 0; }
				.table-container tr:hover td { background: color-mix(in srgb, var(--blue, #3b82f6) 4%, var(--panel-bg)); }
				.widget-variants { position: absolute; bottom: 8px; inset-inline-end: 8px; z-index: 10; display: flex; gap: 4px; opacity: 0; transition: opacity 0.2s; }
				.ai-card:hover .widget-variants { opacity: 1; }
				.variant-btn { width: 28px; height: 28px; border-radius: 7px; border: 1px solid var(--border-color); background: var(--panel-bg); color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
				.variant-btn:hover { color: var(--text-color); border-color: var(--blue, #3b82f6); }
				.variant-btn.active { background: var(--blue, #3b82f6); border-color: var(--blue, #3b82f6); color: #fff; }
				.form-control-mini { height: 32px; padding: 0 10px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--panel-bg); color: var(--text-color); font-family: inherit; font-size: 12.5px; font-weight: 600; outline: none; }
				details summary::-webkit-details-marker { color: var(--blue, #3b82f6); }
				@media (max-width: 900px) { .ai-kpi { grid-column: span 6; } .ai-chart, .ai-table { grid-column: span 12; } }
				@media (max-width: 600px) { .ai-kpi { grid-column: span 12; } .ask-ai-container { padding: 16px; } }
			</style>
		`);
	}

	// Render main HTML
	const html = `
	<div class="ask-ai-container" id="ask-ai-root">
		<div class="ask-ai-header">
			<div class="eyebrow-line">
				<span class="bar"></span>
				${__('Ask AI')}
			</div>
			<div class="flex flex-wrap items-end justify-between gap-3">
				<h1 class="ask-ai-title">${__('Ask your data anything')}</h1>
				<button class="btn btn-secondary btn-sm" id="settings-toggle">
					<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" /></svg>
					${__('AI Settings')}
				</button>
			</div>
			<p class="ask-ai-subtitle">${__("Describe the report you want. It is built using only data you are permitted to see. Keep it as a one-off answer or pin it to a dashboard.")}</p>
			<div id="ai-status-badge" class="hidden">
				<span class="indicator green"></span>
				<span id="ai-status-text"></span>
			</div>
		</div>

		<div id="settings-panel" class="panel ask-ai-panel hidden"></div>

		<div id="ask-box" class="panel ask-ai-panel hidden">
			<div class="flex items-center gap-2">
				<input type="text" id="question-input" class="form-control ask-input" placeholder="${__('e.g. revenue by brand this year, or the top 10 unpaid invoices')}" />
				<button class="btn btn-primary" id="ask-btn">${__('Ask')}</button>
			</div>
		</div>

		<div id="examples-row" class="hidden mt-3 flex flex-wrap gap-2"></div>

		<div id="loading-state" class="panel ask-ai-panel hidden mt-3">
			<div class="flex items-center gap-3">
				<div class="skeleton" style="width: 34px; height: 34px; border-radius: 10px"></div>
				<div style="flex: 1">
					<div class="skeleton" style="height: 14px; width: 40%"></div>
					<div class="skeleton mt-2" style="height: 11px; width: 65%"></div>
				</div>
			</div>
			<div class="skeleton mt-4" style="height: 220px"></div>
			<p class="key-eyebrow mt-3">${__('Picking the data, building the report, checking it against your permissions')}</p>
		</div>

		<div id="clarify-state" class="panel ask-ai-panel hidden mt-3"></div>
		<div id="error-state" class="panel ask-ai-panel hidden mt-3 error-panel"></div>
		<div id="result-state" class="hidden"></div>
	</div>
	`;

	page.main.html(html);

	// Initialize the AskAIPage class
	window.askAIPage = new AskAIPage();
	window.askAIPage.init();
};

// AskAIPage class (same logic as before, but binds to rendered DOM)
class AskAIPage {
	constructor() {
		this.state = null;
		this.question = '';
		this.asking = false;
		this.answer = null;
		this.askError = null;
		this.clarify = null;
		this.clarifyReply = '';
		this.history = [];
		this.followUpText = '';
		this.followingUp = false;
		this.followUpError = '';
		this.dashboards = [];
		this.targetDashboard = '';
		this.newDashTitle = '';
		this.pinning = '';
		this.pinnedSlug = '';
		this.pinnedReport = '';
		this.pinError = '';
		this.KPI_TINTS = ['blue', 'green', 'amber', 'violet'];
		this.showSettings = false;
		this.modelsLoaded = false;
		this.modelOptions = ['gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-1.5-flash', 'gemini-1.5-pro'];
	}

	async init() {
		const urlParams = new URLSearchParams(window.location.search);
		const seeded = (urlParams.get('q') || '').trim();

		await this.loadAIStatus();
		await this.loadDashboards();
		this.bindEvents();
		this.renderSettingsPanel();
		this.renderExamples();

		if (seeded) {
			this.question = seeded;
			document.getElementById('question-input').value = seeded;
			if (this.state?.configured) {
				this.ask();
			}
		}
	}

	// ===== Settings Panel HTML =====
	renderSettingsPanel() {
		const panel = document.getElementById('settings-panel');
		panel.innerHTML = `
			<div class="flex items-start gap-3">
				<div class="kpi-icon blue" style="width: 38px; height: 38px; border-radius: 11px; display: flex; align-items: center; justify-content: center; flex: none">
					<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78Zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4" /></svg>
				</div>
				<div style="flex: 1; min-width: 0">
					<div id="settings-title" style="font-weight: 700; font-size: 15px">${this.state?.configured ? __('AI settings') : __('Connect a Gemini API key')}</div>
					<p style="font-size: 13px; color: var(--text-muted); margin-top: 2px">${__('The AI runs on a Google Gemini key, either your personal one (billed to your Google account, the free tier is plenty) or a site-wide key shared by everyone. Keys are stored encrypted and never leave the server. A personal key always takes priority.')}</p>

					<div class="key-section">
						<div class="key-eyebrow">${__('Your personal key')} <span id="personal-key-badge" class="badge ${this.state?.personal_configured ? '' : 'hidden'}"><span class="dot"></span>${__('set')}</span></div>
						<div class="flex flex-wrap items-end gap-2 mt-2">
							<div class="form-group" style="flex: 1; min-width: 220px"><label>${__('Gemini API key')}</label><input type="password" id="personal-key-input" class="form-control" placeholder="${this.state?.personal_configured ? __('Saved. Paste a new key to replace it') : 'AIza...'}" autocomplete="off" /></div>
							<div class="form-group" style="width: 200px"><label>${__('Model')}</label><select id="personal-model-select" class="form-control"></select></div>
							<button class="btn btn-primary" id="save-personal-key" disabled>${__('Save')}</button>
							<button class="btn btn-danger ${this.state?.personal_configured ? '' : 'hidden'}" id="remove-personal-key">${__('Remove')}</button>
						</div>
						<p id="site-key-note" class="${!this.state?.personal_configured && this.state?.site_configured ? '' : 'hidden'}" style="font-size: 12.5px; color: var(--text-muted); margin-top: 6px">${__('You are covered by the site-wide key. Add a personal key only if you want your own quota.')}</p>
					</div>

					<div id="site-key-section" class="${this.state?.is_admin ? '' : 'hidden'}">
						<div class="key-eyebrow" style="border-top: 1px solid var(--border-color); padding-top: 14px; margin-top: 4px">${__('Site-wide key, shared by all users')} <span id="site-key-badge" class="badge ${this.state?.site_configured ? '' : 'hidden'}"><span class="dot"></span>${__('set')}</span></div>
						<div class="flex flex-wrap items-end gap-2 mt-2">
							<div class="form-group" style="flex: 1; min-width: 220px"><label>${__('Site Gemini API key')}</label><input type="password" id="site-key-input" class="form-control" placeholder="${this.state?.site_configured ? __('Saved. Paste a new key to replace it') : 'AIza...'}" autocomplete="off" /></div>
							<div class="form-group" style="width: 200px"><label>${__('Model')}</label><select id="site-model-select" class="form-control"></select></div>
							<button class="btn btn-primary" id="save-site-key" disabled>${__('Save')}</button>
							<button class="btn btn-danger ${this.state?.site_configured ? '' : 'hidden'}" id="remove-site-key">${__('Remove')}</button>
						</div>
					</div>

					<details class="mt-3" open>
						<summary class="key-eyebrow guide-summary">${__('How to get a free Gemini API key')}</summary>
						<ol class="guide">
							<li>${__('Go to aistudio.google.com/apikey and sign in with any Google account.')}</li>
							<li>${__('Click Create API key (pick "Create in new project" if asked).')}</li>
							<li>${__('Copy the key that starts with AIza and paste it above.')}</li>
							<li>${__('The free tier includes generous daily use of Gemini Flash, with no credit card needed.')}</li>
						</ol>
					</details>
				</div>
			</div>
		`;
		this.bindSettingsEvents();
		this.populateModelSelects();
	}

	renderExamples() {
		const container = document.getElementById('examples-row');
		const examples = ['Revenue by brand', 'Monthly sales this year', 'Top customers by revenue', 'Unpaid invoices'];
		container.innerHTML = examples.map(q => `<button class="btn btn-secondary btn-sm example-chip" data-q="${q}">${__(q)}</button>`).join('');
	}

	async loadAIStatus() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.get_ai_status');
			this.state = response.message;
			this.applyStatus(this.state);
		} catch (e) {
			this.state = { configured: true };
			this.applyStatus(this.state);
		}
	}

	applyStatus(status) {
		this.state = status;
		const configured = status.configured;

		document.getElementById('settings-panel').classList.toggle('hidden', configured && !this.showSettings);
		document.getElementById('ask-box').classList.toggle('hidden', !configured);
		document.getElementById('examples-row').classList.toggle('hidden', !configured || this.answer || this.asking);

		const badge = document.getElementById('ai-status-badge');
		const badgeText = document.getElementById('ai-status-text');
		if (configured) {
			badge.classList.remove('hidden');
			badgeText.textContent = `${status.source === 'user' ? __('Using your personal key') : __('Using the site-wide shared key')} · ${status.model}`;
		} else {
			badge.classList.add('hidden');
		}

		this.renderSettingsPanel();
		this.renderExamples();
		this.loadModels();
	}

	async loadModels() {
		if (this.modelsLoaded) return;
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.list_models');
			if (response.message?.length) {
				this.modelOptions = response.message;
				this.populateModelSelects();
			}
		} catch (e) { /* keep defaults */ }
		this.modelsLoaded = true;
	}

	populateModelSelects() {
		const personalSelect = document.getElementById('personal-model-select');
		const siteSelect = document.getElementById('site-model-select');
		if (!personalSelect || !siteSelect) return;

		const currentPersonal = personalSelect.value || 'gemini-3.6-flash';
		const currentSite = siteSelect.value || 'gemini-3.6-flash';

		[personalSelect, siteSelect].forEach(select => {
			select.innerHTML = '';
			this.modelOptions.forEach(m => {
				const opt = document.createElement('option');
				opt.value = m;
				opt.textContent = m;
				select.appendChild(opt);
			});
			if (currentPersonal && !this.modelOptions.includes(currentPersonal)) {
				const opt = document.createElement('option');
				opt.value = currentPersonal;
				opt.textContent = currentPersonal + ' (deprecated)';
				personalSelect.appendChild(opt);
			}
			if (currentSite && !this.modelOptions.includes(currentSite)) {
				const opt = document.createElement('option');
				opt.value = currentSite;
				opt.textContent = currentSite + ' (deprecated)';
				siteSelect.appendChild(opt);
			}
		});

		personalSelect.value = currentPersonal;
		siteSelect.value = currentSite;
	}

	async loadDashboards() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.get_dashboards');
			this.dashboards = response.message?.dashboards || [];
			this.populateDashboardSelect();
		} catch (e) { console.error('Failed to load dashboards', e); }
	}

	populateDashboardSelect() {
		const select = document.getElementById('target-dashboard');
		if (!select) return;
		const current = select.value;
		select.innerHTML = '<option value="" disabled>' + __("Add all to an existing one…") + '</option>';
		this.dashboards.forEach(d => {
			const opt = document.createElement('option');
			opt.value = d.route_slug || d.name;
			opt.textContent = d.dashboard_name || d.title || d.name;
			select.appendChild(opt);
		});
		if (current) select.value = current;
	}

	bindEvents() {
		document.getElementById('question-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.ask(); });
		document.getElementById('ask-btn').addEventListener('click', () => this.ask());
		document.getElementById('followup-btn').addEventListener('click', () => this.followUp());
		document.getElementById('followup-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.followUp(); });
		document.getElementById('clarify-submit').addEventListener('click', () => this.answerClarify());
		document.getElementById('clarify-reply').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.answerClarify(); });
		document.getElementById('save-new-btn').addEventListener('click', () => this.saveAsNew());
		document.getElementById('save-report-btn').addEventListener('click', () => this.saveAsReport());
		document.getElementById('append-btn').addEventListener('click', () => this.appendToExisting());
		document.getElementById('reset-btn').addEventListener('click', () => this.reset());
		document.getElementById('new-dash-title').addEventListener('input', () => this.updateSaveButtons());
		document.getElementById('target-dashboard').addEventListener('change', () => this.updateSaveButtons());
		document.getElementById('settings-toggle').addEventListener('click', () => this.toggleSettings());
	}

	bindSettingsEvents() {
		const personalKeyInput = document.getElementById('personal-key-input');
		const siteKeyInput = document.getElementById('site-key-input');
		if (personalKeyInput) personalKeyInput.addEventListener('input', () => this.updateSaveKeyButtons());
		if (siteKeyInput) siteKeyInput.addEventListener('input', () => this.updateSaveSiteKeyButtons());

		const savePersonalKeyBtn = document.getElementById('save-personal-key');
		const removePersonalKeyBtn = document.getElementById('remove-personal-key');
		const saveSiteKeyBtn = document.getElementById('save-site-key');
		const removeSiteKeyBtn = document.getElementById('remove-site-key');
		if (savePersonalKeyBtn) savePersonalKeyBtn.addEventListener('click', () => this.savePersonalKey());
		if (removePersonalKeyBtn) removePersonalKeyBtn.addEventListener('click', () => this.removePersonalKey());
		if (saveSiteKeyBtn) saveSiteKeyBtn.addEventListener('click', () => this.saveSiteKey());
		if (removeSiteKeyBtn) removeSiteKeyBtn.addEventListener('click', () => this.removeSiteKey());

		document.querySelectorAll('.example-chip').forEach(btn => {
			btn.addEventListener('click', () => {
				this.question = btn.dataset.q;
				const input = document.getElementById('question-input');
				if (input) input.value = this.question;
				this.ask();
			});
		});
	}

	toggleSettings() {
		this.showSettings = !this.showSettings;
		document.getElementById('settings-panel').classList.toggle('hidden', !this.showSettings);
		if (this.showSettings) this.loadModels();
	}

	updateSaveKeyButtons() {
		const hasKey = document.getElementById('personal-key-input').value.trim() || this.state?.personal_configured;
		document.getElementById('save-personal-key').disabled = !hasKey;
		document.getElementById('remove-personal-key').classList.toggle('hidden', !this.state?.personal_configured);
	}

	updateSaveSiteKeyButtons() {
		const hasKey = document.getElementById('site-key-input').value.trim() || this.state?.site_configured;
		document.getElementById('save-site-key').disabled = !hasKey;
		document.getElementById('remove-site-key').classList.toggle('hidden', !this.state?.site_configured);
	}

	async savePersonalKey() {
		const key = document.getElementById('personal-key-input').value.trim();
		const model = document.getElementById('personal-model-select').value;
		const btn = document.getElementById('save-personal-key');
		btn.disabled = true;
		btn.textContent = __('Saving…');
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.save_ai_settings', { api_key: key || null, model });
			this.applyStatus(response.message);
			document.getElementById('personal-key-input').value = '';
			this.showSettings = false;
			document.getElementById('settings-panel').classList.add('hidden');
		} catch (e) { frappe.msgprint({title: __('Error'), message: e.message, indicator: 'red'}); }
		finally { btn.disabled = false; btn.textContent = __('Save'); }
	}

	async removePersonalKey() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.clear_ai_key');
			this.applyStatus(response.message);
		} catch (e) { frappe.msgprint({title: __('Error'), message: e.message, indicator: 'red'}); }
	}

	async saveSiteKey() {
		const key = document.getElementById('site-key-input').value.trim();
		const model = document.getElementById('site-model-select').value;
		const btn = document.getElementById('save-site-key');
		btn.disabled = true;
		btn.textContent = __('Saving…');
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.save_site_ai_settings', { api_key: key || null, model });
			this.applyStatus(response.message);
			document.getElementById('site-key-input').value = '';
			this.showSettings = false;
			document.getElementById('settings-panel').classList.add('hidden');
		} catch (e) { frappe.msgprint({title: __('Error'), message: e.message, indicator: 'red'}); }
		finally { btn.disabled = false; btn.textContent = __('Save'); }
	}

	async removeSiteKey() {
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.clear_site_ai_key');
			this.applyStatus(response.message);
		} catch (e) { frappe.msgprint({title: __('Error'), message: e.message, indicator: 'red'}); }
	}

	async ask(promptText = null, isAnswer = false) {
		const text = (typeof promptText === 'string' ? promptText : this.question).trim();
		if (!text || this.asking) return;

		this.asking = true;
		this.answer = null;
		this.askError = null;
		this.clarify = null;
		this.clarifyReply = '';
		this.pinnedSlug = '';
		this.pinnedReport = '';
		this.pinError = '';
		this.updateUI();

		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.ask_ai', { prompt: text, history: this.history, answered: isAnswer });
			this.history.push({ role: 'user', text });

			if (response.message?.clarify) {
				this.clarify = { question: response.message.clarify, options: response.message.options || [] };
				this.history.push({ role: 'assistant', text: response.message.clarify });
			} else {
				const widgets = (response.message?.widgets || []).map(w => ({ ...w, included: !w.empty }));
				this.answer = { ...response.message, widgets };
				this.newDashTitle = response.message.title || '';
				this.history.push({ role: 'assistant', text: `Built: ${response.message.title}` });
			}
		} catch (e) { this.askError = e.message || String(e); }
		finally { this.asking = false; this.updateUI(); }
	}

	answerClarify() {
		const text = (this.clarifyReply || '').trim();
		if (!text) return;
		this.ask(text, true);
	}

	async followUp(text = null) {
		const prompt = (typeof text === 'string' ? text : this.followUpText).trim();
		if (!prompt || this.followingUp) return;

		this.followingUp = true;
		this.followUpError = '';
		this.updateUI();

		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.ask_ai', { prompt, history: this.history, existing_titles: this.answer?.widgets?.map(w => w.widget?.title) || [] });
			this.history.push({ role: 'user', text: prompt });

			if (response.message?.clarify) {
				this.followUpError = response.message.clarify;
				this.history.push({ role: 'assistant', text: response.message.clarify });
				return;
			}

			this.history.push({ role: 'assistant', text: `Added: ${response.message.title}` });

			const seen = new Set(this.answer.widgets.map(w => w.widget.title + JSON.stringify(w.widget.query)));
			for (const entry of response.message.widgets || []) {
				const key = entry.widget.title + JSON.stringify(entry.widget.query);
				if (!seen.has(key)) { this.answer.widgets.push({ ...entry, included: !entry.empty }); seen.add(key); }
			}

			this.answer.suggestions = response.message.suggestions || [];
			this.answer.questions = response.message.questions || [];
			if (response.message.analysis) this.answer.analysis = response.message.analysis;
			this.followUpText = '';
			this.pinnedSlug = '';
		} catch (e) { this.followUpError = e.message || String(e); }
		finally { this.followingUp = false; this.updateUI(); }
	}

	async saveResult(payload, mode) {
		this.pinning = mode;
		this.pinError = '';
		this.updateUI();
		try {
			const response = await frappe.call('erpnext_copilot.custom_code.api.save_ai_result', payload);
			this.pinnedSlug = response.message?.slug;
		} catch (e) { this.pinError = e.message || String(e); }
		finally { this.pinning = ''; this.updateUI(); }
	}

	async saveAsReport() {
		this.pinning = 'report';
		this.pinError = '';
		this.updateUI();
		try {
			const reportName = 'rpt_' + frappe.utils.generate_hash(8);
			const response = await frappe.call('erpnext_copilot.custom_code.api.save_ai_result_as_report', { widgets: this.getIncludedWidgets(), title: this.newDashTitle, report_name: reportName });
			if (response.message?.success) { this.pinnedReport = response.message.report_name; this.pinnedSlug = null; }
			else { this.pinError = response.message?.error || 'Failed to create report'; }
		} catch (e) { this.pinError = e.message || String(e); }
		finally { this.pinning = ''; this.updateUI(); }
	}

	getIncludedWidgets() { return this.answer?.widgets?.filter(w => w.included)?.map(w => w.widget) || []; }

	saveAsNew() { this.saveResult({ widgets: this.getIncludedWidgets(), title: this.newDashTitle }, 'new'); }
	appendToExisting() { this.saveResult({ widgets: this.getIncludedWidgets(), slug: this.targetDashboard }, 'append'); }

	reset() {
		this.answer = null; this.askError = null; this.clarify = null; this.clarifyReply = '';
		this.question = ''; this.pinnedSlug = ''; this.pinnedReport = ''; this.targetDashboard = '';
		this.newDashTitle = ''; this.history = []; this.followUpText = ''; this.followUpError = '';
		['question-input', 'followup-input', 'clarify-reply', 'new-dash-title', 'target-dashboard'].forEach(id => {
			const el = document.getElementById(id); if (el) el.value = '';
		});
		this.updateUI();
	}

	updateSaveButtons() {
		const includedCount = this.getIncludedWidgets().length;
		const hasTitle = this.newDashTitle.trim();
		const hasTarget = this.targetDashboard;
		const busy = this.pinning;

		document.getElementById('save-new-btn').disabled = !hasTitle || busy || includedCount === 0;
		document.getElementById('save-new-btn').textContent = busy === 'new' ? __('Creating…') : __('Save as new dashboard ({0})', [includedCount]);

		document.getElementById('save-report-btn').disabled = !hasTitle || busy || includedCount === 0;
		document.getElementById('save-report-btn').textContent = busy === 'report' ? __('Creating…') : __('Save as Report ({0})', [includedCount]);

		document.getElementById('append-btn').disabled = !hasTarget || busy || includedCount === 0;
		document.getElementById('append-btn').textContent = busy === 'append' ? __('Adding…') : __('Add');

		document.getElementById('open-dashboard-link').classList.toggle('hidden', !this.pinnedSlug);
		if (this.pinnedSlug) document.getElementById('open-dashboard-link').href = `/app/dashboard-view/${this.pinnedSlug}`;

		document.getElementById('open-report-link').classList.toggle('hidden', !this.pinnedReport);
		if (this.pinnedReport) document.getElementById('open-report-link').href = `/app/query-report/${this.pinnedReport}`;
	}

	updateUI() {
		document.getElementById('loading-state').classList.toggle('hidden', !this.asking);
		const askBtn = document.getElementById('ask-btn');
		if (askBtn) { askBtn.disabled = this.asking || !this.question.trim(); askBtn.textContent = this.asking ? __('Thinking…') : __('Ask'); }

		document.getElementById('error-state').classList.toggle('hidden', !this.askError);
		document.getElementById('error-message').textContent = this.askError || '';

		const clarifyEl = document.getElementById('clarify-state');
		clarifyEl.classList.toggle('hidden', !this.clarify);
		if (this.clarify) {
			clarifyEl.innerHTML = `
				<div class="flex items-start gap-3">
					<div class="icon-wrapper blue" style="width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex: none">
						<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01" /><circle cx="12" cy="12" r="9.2" /></svg>
					</div>
					<div style="flex: 1; min-width: 0">
						<div id="clarify-question" style="font-weight: 700">${this.clarify.question}</div>
						<div id="clarify-options" class="mt-2 flex flex-wrap gap-2">${(this.clarify.options||[]).map(opt => `<button class="btn btn-secondary btn-sm" style="dir: auto" onclick="window.askAIPage.clarifyReply='${opt}'; window.askAIPage.answerClarify()">${opt}</button>`).join('')}</div>
						<div class="mt-3 flex items-center gap-2">
							<input type="text" id="clarify-reply" class="form-control" placeholder="${__('Type your answer…')}" style="flex: 1; height: 36px;" />
							<button class="btn btn-primary btn-sm" id="clarify-submit">${__('Answer')}</button>
						</div>
					</div>
				</div>
			`;
			document.getElementById('clarify-submit').addEventListener('click', () => this.answerClarify());
			document.getElementById('clarify-reply').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.answerClarify(); });
		}

		const hasResult = this.answer && !this.asking && !this.askError && !this.clarify;
		document.getElementById('result-state').classList.toggle('hidden', !hasResult);
		document.getElementById('examples-row').classList.toggle('hidden', hasResult || this.asking);

		if (hasResult) this.renderResult();

		const followupBtn = document.getElementById('followup-btn');
		if (followupBtn) { followupBtn.disabled = this.followingUp || !this.followUpText.trim(); followupBtn.textContent = this.followingUp ? __('Thinking…') : __('Add'); }
		document.getElementById('followup-error').classList.toggle('hidden', !this.followUpError);
		document.getElementById('followup-error').textContent = this.followUpError || '';

		const refineContainer = document.getElementById('refine-chips');
		refineContainer.innerHTML = '<span class="chip-label">' + __("Refine") + '</span>';
		if (this.answer?.questions?.length) {
			refineContainer.classList.remove('hidden');
			this.answer.questions.forEach(q => {
				const btn = document.createElement('button');
				btn.className = 'btn btn-secondary btn-sm chip-q';
				btn.textContent = q;
				btn.style.dir = 'auto';
				btn.disabled = this.followingUp;
				btn.onclick = () => this.followUp(q);
				refineContainer.appendChild(btn);
			});
		} else { refineContainer.classList.add('hidden'); }

		const suggContainer = document.getElementById('suggestion-chips');
		suggContainer.innerHTML = '<span class="chip-label">' + __("Ideas") + '</span>';
		if (this.answer?.suggestions?.length) {
			suggContainer.classList.remove('hidden');
			this.answer.suggestions.forEach(s => {
				const btn = document.createElement('button');
				btn.className = 'btn btn-secondary btn-sm';
				btn.textContent = '+ ' + s;
				btn.style.dir = 'auto';
				btn.disabled = this.followingUp;
				btn.onclick = () => this.followUp(s);
				suggContainer.appendChild(btn);
			});
		} else { suggContainer.classList.add('hidden'); }

		document.getElementById('pin-error').classList.toggle('hidden', !this.pinError);
		document.getElementById('pin-error').textContent = this.pinError || '';

		this.updateSaveButtons();
	}

	renderResult() {
		if (!this.answer) return;
		document.getElementById('result-title').textContent = this.answer.title || '';
		const explEl = document.getElementById('result-explanation');
		if (this.answer.explanation) { explEl.textContent = this.answer.explanation; explEl.classList.remove('hidden'); } else { explEl.classList.add('hidden'); }
		const hasEmpty = this.answer.widgets?.some(w => w.empty);
		document.getElementById('empty-widgets-note').classList.toggle('hidden', !hasEmpty);

		const analysisEl = document.getElementById('analysis-panel');
		if (this.answer.analysis) {
			analysisEl.classList.remove('hidden');
			analysisEl.innerHTML = `
				<div class="analysis-eyebrow">${__('Analysis')}</div>
				<div class="analysis-head">${this.answer.analysis.headline || ''}</div>
				<ul class="analysis-list">${(this.answer.analysis.findings||[]).map(f => `<li>${f}</li>`).join('')}</ul>
				${this.answer.analysis.watch?.length ? `<ul class="analysis-list analysis-watch">${this.answer.analysis.watch.map(w => `<li>${w}</li>`).join('')}</ul>` : ''}
			`;
		} else { analysisEl.classList.add('hidden'); }

		this.renderWidgets();
	}

	renderWidgets() {
		const grid = document.getElementById('widgets-grid');
		if (!grid || !this.answer?.widgets?.length) return;
		grid.innerHTML = '';

		this.answer.widgets.forEach((entry, i) => {
			const widget = entry.widget;
			const result = entry.result;
			const empty = entry.empty;
			const included = entry.included;

			const card = document.createElement('div');
			let widgetClass = 'ai-chart';
			if (widget.widget_type === 'Number Card') widgetClass = 'ai-kpi';
			else if (widget.widget_type === 'Table') widgetClass = 'ai-table';
			card.className = `ai-card ${included ? '' : 'ai-off'} ${widgetClass}`;

			const toggle = document.createElement('button');
			toggle.className = `ai-toggle ${included ? 'on' : ''}`;
			toggle.title = included ? __('Included. Click to leave out') : __('Left out. Click to include it');
			toggle.innerHTML = included ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="m4.5 12.5 5 5 10-11" /></svg>' : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>';
			toggle.onclick = () => { entry.included = !entry.included; this.updateUI(); };
			card.appendChild(toggle);

			if (empty) {
				const badge = document.createElement('span');
				badge.className = 'badge amber ai-empty';
				badge.innerHTML = '<span class="dot"></span>' + __('No data yet');
				card.appendChild(badge);
			}

			if (widget.widget_type === 'Number Card') {
				card.innerHTML += this.renderNumberCard(widget, result, i);
			} else {
				card.innerHTML += this.renderChartOrTable(widget, result, i);
			}

			if (widget.widget_type !== 'Table') {
				card.innerHTML += this.renderChartVariants(widget, result, i);
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
				html += `<td>${frappe.utils.escape_html(String(val))}</td>`;
			});
			html += '</tr>';
		});
		html += '</tbody></table>';
		return html;
	}

	renderChartVariants(widget, result, index) {
		const variants = this.getChartVariants(widget.widget_type);
		if (!variants.length) return '';
		const currentType = widget.widget_type;
		let html = '<div class="widget-variants">';
		variants.forEach(v => {
			const isActive = v === currentType;
			const icon = this.getChartIcon(v);
			html += `<button class="variant-btn ${isActive ? 'active' : ''}" data-type="${v}" title="${v}" onclick="window.askAIPage?.changeChartType('${widget.widget_id}', '${v}')">${icon}</button>`;
		});
		html += '</div>';
		return html;
	}

	getChartVariants(widgetType) {
		const families = { 'Number Card': ['Number Card', 'Gauge'], 'Bar Chart': ['Bar Chart', 'Horizontal Bar', 'Pie Chart', 'Donut Chart'], 'Line Chart': ['Line Chart', 'Area Chart', 'Bar Chart', 'Sparkline'], 'Pie Chart': ['Pie Chart', 'Donut Chart', 'Bar Chart'], 'Donut Chart': ['Donut Chart', 'Pie Chart', 'Bar Chart'], 'Area Chart': ['Area Chart', 'Line Chart', 'Bar Chart'], 'Table': ['Table'] };
		return families[widgetType] || [];
	}

	getChartIcon(type) {
		const icons = { 'Number Card': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 17V9M15 17V13"/></svg>', 'Bar Chart': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 16v-6M12 16v-10M17 16v-3"/></svg>', 'Line Chart': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 16l5-6 4 3 5-7"/></svg>', 'Pie Chart': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3v9h9"/></svg>', 'Donut Chart': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/></svg>', 'Area Chart': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 16l5-6 4 3 5-7" fill="none" stroke-opacity="0.3"/></svg>', 'Table': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18"/></svg>', 'Gauge': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 12l3-5"/></svg>', 'Sparkline': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 12l3-3 3 3 4-4"/></svg>' };
		return icons[type] || icons['Bar Chart'];
	}

	getKPIIndex(i) { let n = 0; for (let j = 0; j < i; j++) if (this.answer.widgets[j].widget.widget_type === 'Number Card') n++; return n; }
	formatNumber(num) { if (num === null || num === undefined) return '—'; if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B'; if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M'; if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K'; return Number(num).toLocaleString(); }
	formatCurrency(num) { return frappe.format(num, { fieldtype: 'Currency' }); }
	changeChartType(widgetId, newType) { const entry = this.answer.widgets.find(w => w.widget.widget_id === widgetId); if (entry) { entry.widget.widget_type = newType; this.updateUI(); } }
}