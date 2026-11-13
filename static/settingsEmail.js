'use strict';

let currentEmailAccountId = null;

async function refreshEmailAccountList() {
  try {
    const r = await fetch('/api/email/accounts');
    const { accounts = [] } = await r.json();
    settingsEls.emailAccountList.innerHTML = '';
    if (!accounts.length) {
      settingsEls.emailAccountList.innerHTML = '<li class="settings-hint">none added yet</li>';
      settingsEls.emailInboxCard.hidden = true;
      return;
    }
    for (const a of accounts) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(a.label)}</span>` +
        `<span class="endpoint-meta">${escapeHtml(a.email_address)}</span>` +
        `<button class="btn ghost" data-act="open">open</button>` +
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('[data-act="open"]').onclick = () => openEmailInbox(a.id);
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this email account')) return;
        await fetch('/api/email/accounts/' + a.id, { method: 'DELETE' });
        if (currentEmailAccountId === a.id) { currentEmailAccountId = null; settingsEls.emailInboxCard.hidden = true; }
        await refreshEmailAccountList();
        await refreshEmailRules();
      };
      settingsEls.emailAccountList.appendChild(li);
    }
    if (!currentEmailAccountId) openEmailInbox(accounts[0].id);
  } catch (_) {
    settingsEls.emailAccountList.innerHTML = '<li class="settings-hint error">failed to load accounts</li>';
  }
}

settingsEls.addEmailAccountBtn.onclick = async () => {
  const email_address = settingsEls.emailAddress.value.trim();
  const password = settingsEls.emailPassword.value;
  const imap_host = settingsEls.emailImapHost.value.trim();
  if (!email_address || !password || !imap_host) {
    settingsEls.emailAddResult.textContent = 'email, password, and IMAP host are required';
    settingsEls.emailAddResult.className = 'settings-hint error';
    return;
  }
  settingsEls.emailAddResult.textContent = 'connecting…';
  settingsEls.emailAddResult.className = 'settings-hint';
  try {
    const r = await fetch('/api/email/accounts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        label: settingsEls.emailLabel.value.trim(), email_address, password, imap_host,
        imap_port: parseInt(settingsEls.emailImapPort.value || '993', 10),
        smtp_host: settingsEls.emailSmtpHost.value.trim(),
        smtp_port: parseInt(settingsEls.emailSmtpPort.value || '587', 10),
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'add failed');
    }
    settingsEls.emailAddResult.textContent = 'connected';
    settingsEls.emailAddResult.className = 'settings-hint ok';
    settingsEls.emailPassword.value = '';
    await refreshEmailAccountList();
    await refreshEmailRules();
  } catch (e) {
    settingsEls.emailAddResult.textContent = e.message;
    settingsEls.emailAddResult.className = 'settings-hint error';
  }
};

async function openEmailInbox(accountId) {
  currentEmailAccountId = accountId;
  settingsEls.emailInboxCard.hidden = false;
  settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">select a message</p>';
  try {
    const r = await fetch(`/api/email/${accountId}/folders`);
    const { folders = ['INBOX'] } = await r.json();
    settingsEls.emailFolderSelect.innerHTML = '';
    for (const f of folders) {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f;
      settingsEls.emailFolderSelect.appendChild(opt);
    }
    if (folders.includes('INBOX')) settingsEls.emailFolderSelect.value = 'INBOX';
  } catch (_) {
    settingsEls.emailFolderSelect.innerHTML = '<option value="INBOX">INBOX</option>';
  }
  await loadEmailMessages();
}

settingsEls.emailRefreshBtn.onclick = loadEmailMessages;
settingsEls.emailFolderSelect.onchange = loadEmailMessages;

async function loadEmailMessages() {
  if (!currentEmailAccountId) return;
  const folder = settingsEls.emailFolderSelect.value || 'INBOX';
  settingsEls.emailMessageList.innerHTML = '<li class="settings-hint">loading…</li>';
  try {
    const r = await fetch(`/api/email/${currentEmailAccountId}/messages?folder=${encodeURIComponent(folder)}`);
    if (!r.ok) throw new Error('failed to load messages');
    const { messages = [] } = await r.json();
    settingsEls.emailMessageList.innerHTML = '';
    if (!messages.length) {
      settingsEls.emailMessageList.innerHTML = '<li class="settings-hint">empty</li>';
      return;
    }
    for (const m of messages) {
      const li = document.createElement('li');
      li.className = 'endpoint-row' + (m.unread ? ' unread' : '');
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(m.subject || '(no subject)')}</span>` +
        `<span class="endpoint-meta">${escapeHtml(m.from || '')}</span>`;
      li.onclick = () => openEmailMessage(folder, m.uid);
      settingsEls.emailMessageList.appendChild(li);
    }
  } catch (e) {
    settingsEls.emailMessageList.innerHTML = `<li class="settings-hint error">${escapeHtml(e.message)}</li>`;
  }
}

async function openEmailMessage(folder, uid) {
  settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">loading…</p>';
  try {
    const r = await fetch(`/api/email/${currentEmailAccountId}/message/${uid}?folder=${encodeURIComponent(folder)}`);
    if (!r.ok) throw new Error('failed to load message');
    const msg = await r.json();
    settingsEls.emailReadingPane.innerHTML =
      `<div class="email-subject">${escapeHtml(msg.subject || '(no subject)')}</div>` +
      `<div class="email-meta">from ${escapeHtml(msg.from || '')} · ${escapeHtml(msg.date || '')}</div>` +
      `<div class="email-actions">` +
        `<button class="btn ghost" data-act="summarize">summarize</button>` +
        `<button class="btn ghost" data-act="urgency">check urgency</button>` +
        `<button class="btn ghost" data-act="archive">archive</button>` +
        `<button class="btn ghost" data-act="delete">delete</button>` +
      `</div>` +
      `<div class="email-body">${escapeHtml(msg.body)}</div>` +
      `<textarea id="emailReplyBox" placeholder="write a reply…"></textarea>` +
      `<div class="email-actions">` +
        `<button class="btn ghost" data-act="draft">AI draft reply</button>` +
        `<button class="btn send" data-act="reply">send reply</button>` +
      `</div>` +
      `<div class="email-ai-result" id="emailAiResult" hidden></div>`;

    const aiResult = document.getElementById('emailAiResult');
    const showAi = text => { aiResult.hidden = false; aiResult.textContent = text; };

    settingsEls.emailReadingPane.querySelector('[data-act="summarize"]').onclick = () => runEmailAi('summarize', folder, uid, showAi);
    settingsEls.emailReadingPane.querySelector('[data-act="urgency"]').onclick = () => runEmailAi('check_urgency', folder, uid, showAi);
    settingsEls.emailReadingPane.querySelector('[data-act="draft"]').onclick = async () => {
      await runEmailAi('draft_reply', folder, uid, text => { document.getElementById('emailReplyBox').value = text; });
    };
    settingsEls.emailReadingPane.querySelector('[data-act="archive"]').onclick = async () => {
      await fetch(`/api/email/${currentEmailAccountId}/message/${uid}/archive?folder=${encodeURIComponent(folder)}`, { method: 'POST' });
      await loadEmailMessages();
      settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">select a message</p>';
    };
    settingsEls.emailReadingPane.querySelector('[data-act="delete"]').onclick = async () => {
      await fetch(`/api/email/${currentEmailAccountId}/message/${uid}?folder=${encodeURIComponent(folder)}`, { method: 'DELETE' });
      await loadEmailMessages();
      settingsEls.emailReadingPane.innerHTML = '<p class="settings-hint">select a message</p>';
    };
    settingsEls.emailReadingPane.querySelector('[data-act="reply"]').onclick = async () => {
      const body = document.getElementById('emailReplyBox').value.trim();
      if (!body) return;
      await fetch(`/api/email/${currentEmailAccountId}/message/${uid}/reply?folder=${encodeURIComponent(folder)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body }),
      });
      showAi('reply sent');
    };
  } catch (e) {
    settingsEls.emailReadingPane.innerHTML = `<p class="settings-hint error">${escapeHtml(e.message)}</p>`;
  }
}

async function runEmailAi(action, folder, uid, onResult) {
  const model = currentModel();
  if (!model) { onResult('select a model in the header first'); return; }
  onResult('thinking…');
  try {
    const r = await fetch(`/api/email/${currentEmailAccountId}/message/${uid}/ai?action=${action}&folder=${encodeURIComponent(folder)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'AI action failed');
    }
    const { result } = await r.json();
    onResult(result);
  } catch (e) {
    onResult('error: ' + e.message);
  }
}

async function refreshEmailRules() {
  try {
    const accountsRes = await fetch('/api/email/accounts');
    const { accounts = [] } = await accountsRes.json();
    const choices = ruleAccountChoices(accounts);
    settingsEls.ruleAccountSelect.innerHTML = '';
    for (const choice of choices.options) {
      const opt = document.createElement('option');
      opt.value = choice.value;
      opt.textContent = choice.label;
      settingsEls.ruleAccountSelect.appendChild(opt);
    }
    settingsEls.ruleAccountSelect.disabled = !choices.usable;
    settingsEls.createRuleBtn.disabled = !choices.usable;

    settingsEls.ruleModelSelect.innerHTML = '';
    for (const m of models) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.dataset.endpointId = m.endpoint_id;
      opt.textContent = m.id;
      settingsEls.ruleModelSelect.appendChild(opt);
    }

    const rulesRes = await fetch('/api/email/rules');
    const { rules = [] } = await rulesRes.json();
    settingsEls.ruleList.innerHTML = '';
    if (!rules.length) {
      settingsEls.ruleList.innerHTML = '<li class="settings-hint">no rules yet</li>';
      return;
    }
    const accountById = Object.fromEntries(accounts.map(a => [a.id, a]));
    for (const rule of rules) {
      const li = document.createElement('li');
      li.className = 'endpoint-row';
      const accountLabel = accountById[rule.account_id]?.label || '(deleted account)';
      li.innerHTML =
        `<span class="endpoint-label">${escapeHtml(rule.match_field)} contains "${escapeHtml(rule.match_value)}"</span>` +
        `<span class="endpoint-meta">${escapeHtml(accountLabel)}/${escapeHtml(rule.folder)} &rarr; ${escapeHtml(rule.action)}</span>` +
        `<label class="theme-toggle"><input type="checkbox" ${rule.enabled ? 'checked' : ''} /><span>on</span></label>` +
        `<button class="endpoint-remove" type="button" title="Remove" aria-label="Remove">×</button>`;
      li.querySelector('input[type="checkbox"]').onchange = async e => {
        await fetch(`/api/email/rules/${rule.id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: e.target.checked }),
        });
      };
      li.querySelector('.endpoint-remove').onclick = async () => {
        if (!await confirmRemove('this rule')) return;
        await fetch('/api/email/rules/' + rule.id, { method: 'DELETE' });
        await refreshEmailRules();
      };
      settingsEls.ruleList.appendChild(li);
    }
  } catch (_) {
    settingsEls.ruleList.innerHTML = '<li class="settings-hint error">failed to load rules</li>';
  }
}

settingsEls.ruleAction.addEventListener('change', () => {
  settingsEls.ruleModelRow.hidden = settingsEls.ruleAction.value !== 'ai_summarize_note';
});

settingsEls.createRuleBtn.onclick = async () => {
  const matchValue = settingsEls.ruleMatchValue.value.trim();
  if (!matchValue) return;
  const accountId = settingsEls.ruleAccountSelect.value;
  if (!accountId) {
    settingsEls.ruleCreateResult.textContent = 'add an email account first';
    settingsEls.ruleCreateResult.className = 'settings-hint error';
    return;
  }
  const modelOpt = settingsEls.ruleModelSelect.selectedOptions[0];
  try {
    const r = await fetch('/api/email/rules', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_id: accountId, folder: settingsEls.ruleFolder.value || 'INBOX',
        match_field: settingsEls.ruleMatchField.value, match_value: matchValue,
        action: settingsEls.ruleAction.value,
        endpoint_id: modelOpt ? modelOpt.dataset.endpointId : '',
        model: modelOpt ? modelOpt.value : '',
      }),
    });
    if (!r.ok) {
      const { detail } = await r.json().catch(() => ({}));
      throw new Error(detail || 'create failed');
    }
    settingsEls.ruleCreateResult.textContent = 'added';
    settingsEls.ruleCreateResult.className = 'settings-hint ok';
    settingsEls.ruleMatchValue.value = '';
    await refreshEmailRules();
  } catch (e) {
    settingsEls.ruleCreateResult.textContent = e.message;
    settingsEls.ruleCreateResult.className = 'settings-hint error';
  }
};
