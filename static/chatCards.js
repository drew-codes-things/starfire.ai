'use strict';

function diffToHtml(diffText) {
  const escape = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  return diffText.split('\n').map(line => {
    const esc = escape(line);
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) return `<span class="diff-line-meta">${esc}</span>`;
    if (line.startsWith('+')) return `<span class="diff-line-add">${esc}</span>`;
    if (line.startsWith('-')) return `<span class="diff-line-remove">${esc}</span>`;
    return `<span class="diff-line-context">${esc}</span>`;
  }).join('');
}

const openApprovals = new Map();

function trackApproval(pendingId, card) {
  card.dataset.pendingId = pendingId;
  openApprovals.set(pendingId, card);
}

function showOpenApprovals() {
  if (!openApprovals.size) return;
  const w = $('welcome');
  if (w) w.remove();
  openApprovals.forEach(card => els.output.appendChild(card));
  els.output.scrollTop = els.output.scrollHeight;
}

async function restorePendingApprovals() {
  try {
    const [shell, edits] = await Promise.all(['/api/pending-shell', '/api/pending-edits'].map(async url => {
      const r = await fetch(url);
      return r.ok ? r.json() : {};
    }));
    for (const card of pendingApprovalCards(shell.commands, edits.edits)) {
      if (openApprovals.has(card.id)) continue;
      if (card.kind === 'shell') renderShellApprovalCard(card.data);
      else renderDiffCard(JSON.stringify(card.data));
    }
  } catch (e) {
    console.warn('starfire.ai: could not load pending approvals', e);
  }
}

function renderDiffCard(resultJson) {
  let data;
  try { data = JSON.parse(resultJson); } catch (_) { printLine('» edit_file done', 'info'); return; }
  const w = $('welcome');
  if (w) w.remove();

  const card = document.createElement('div');
  card.className = 'diff-card';
  const status = data.staged ? 'proposed edit - awaiting approval' : data.applied ? 'applied edit' : 'edit failed';
  card.innerHTML =
    `<div class="diff-card-header">${status} - ${escapeHtml(data.path || '')}${data.error ? ' - ' + escapeHtml(data.error) : ''}</div>` +
    `<pre>${diffToHtml(data.diff || '')}</pre>`;

  if (data.staged && data.pending_id) {
    trackApproval(data.pending_id, card);
    const guard = answerGuard();
    const actionsRow = document.createElement('div');
    actionsRow.className = 'diff-card-actions';
    const approveBtn = document.createElement('button');
    approveBtn.type = 'button'; approveBtn.className = 'btn send'; approveBtn.textContent = 'approve';
    const rejectBtn = document.createElement('button');
    rejectBtn.type = 'button'; rejectBtn.className = 'btn ghost'; rejectBtn.textContent = 'reject';
    approveBtn.onclick = async () => {
      if (!guard.isReady()) return;
      approveBtn.disabled = true; rejectBtn.disabled = true;
      openApprovals.delete(data.pending_id);
      try {
        const r = await fetch(`/api/pending-edits/${data.pending_id}/approve`, { method: 'POST' });
        actionsRow.textContent = r.ok ? 'approved - written to disk' : 'failed to approve';
      } catch (_) { actionsRow.textContent = 'failed to approve'; }
    };
    rejectBtn.onclick = async () => {
      if (!guard.isReady()) return;
      approveBtn.disabled = true; rejectBtn.disabled = true;
      openApprovals.delete(data.pending_id);
      try {
        await fetch(`/api/pending-edits/${data.pending_id}/reject`, { method: 'POST' });
        actionsRow.textContent = 'rejected - no changes written';
      } catch (_) { actionsRow.textContent = 'failed to reject'; }
    };
    actionsRow.appendChild(approveBtn);
    actionsRow.appendChild(rejectBtn);
    card.appendChild(actionsRow);
    flashNewQuestion(card, [approveBtn, rejectBtn], guard);
  }

  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderShellApprovalCard(data) {
  const w = $('welcome');
  if (w) w.remove();
  const card = document.createElement('div');
  card.className = 'diff-card shell-card';
  const where = data.cwd ? ` in ${escapeHtml(data.cwd)}` : '';
  card.innerHTML =
    `<div class="diff-card-header">shell command - waiting for your approval${where}</div>` +
    `<pre>${escapeHtml(data.command || '')}</pre>`;
  const actionsRow = document.createElement('div');
  actionsRow.className = 'diff-card-actions';
  const approveBtn = document.createElement('button');
  approveBtn.type = 'button'; approveBtn.className = 'btn send'; approveBtn.textContent = 'run it';
  const rejectBtn = document.createElement('button');
  rejectBtn.type = 'button'; rejectBtn.className = 'btn ghost'; rejectBtn.textContent = 'reject';
  trackApproval(data.pending_id, card);
  const guard = answerGuard();
  const decide = async (approved) => {
    if (!guard.isReady()) return;
    approveBtn.disabled = true; rejectBtn.disabled = true;
    openApprovals.delete(data.pending_id);
    try {
      const r = await fetch(`/api/pending-shell/${data.pending_id}/${approved ? 'approve' : 'reject'}`, { method: 'POST' });
      actionsRow.textContent = r.ok ? (approved ? 'approved - running…' : 'rejected - not run') : 'that command is no longer waiting';
    } catch (_) {
      actionsRow.textContent = 'failed to send your answer';
    }
  };
  approveBtn.onclick = () => decide(true);
  rejectBtn.onclick = () => decide(false);
  actionsRow.append(approveBtn, rejectBtn);
  card.appendChild(actionsRow);
  flashNewQuestion(card, [approveBtn, rejectBtn], guard);
  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderShellResultCard(result) {
  const w = $('welcome');
  if (w) w.remove();
  const text = result || '';
  const failed = text.startsWith('error:');
  const card = document.createElement('div');
  card.className = 'diff-card shell-card';
  card.innerHTML =
    `<div class="diff-card-header">${failed ? 'shell command not run' : 'shell output'}</div>` +
    `<pre>${escapeHtml(text)}</pre>`;
  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderGeneratedFileCard(toolName, resultJson) {
  let data;
  try { data = JSON.parse(resultJson); }
  catch (_) { printLine('» ' + toolName + ': ' + resultJson, resultJson.startsWith('error') ? 'err' : 'info'); return; }
  if (!data.url) { printLine('» ' + toolName + ': ' + resultJson, 'err'); return; }

  const w = $('welcome');
  if (w) w.remove();

  const card = document.createElement('div');
  card.className = 'diff-card';

  if (data.kind === 'image') {
    card.innerHTML = `<img src="${data.url}" alt="${escapeHtml(data.prompt || 'generated image')}" style="max-width:100%;display:block;" />`;
    if (data.source || data.image_url) {
      const from = document.createElement('div');
      from.className = 'diff-card-source';
      const link = document.createElement('a');
      link.href = data.source || data.image_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = data.source ? new URL(data.source, location.href).hostname : 'image source';
      from.append(escapeHtml(data.prompt || '') ? data.prompt + ' · from ' : 'from ', link);
      card.appendChild(from);
    }
  } else if (data.kind === 'audio') {
    card.innerHTML = `<div class="diff-card-header">generated audio</div><audio controls src="${data.url}" style="width:100%"></audio>`;
  } else if (data.kind === 'document') {
    card.innerHTML =
      `<div class="diff-card-header">generated document</div>` +
      `<div class="diff-card-actions"><a class="btn send" href="${data.url}" download="${escapeHtml(data.filename || 'document')}">download ${escapeHtml(data.filename || '')}</a></div>`;
  }
  els.output.appendChild(card);
  els.output.scrollTop = els.output.scrollHeight;
}

function renderGeneratedVideoCard(resultJson) {
  let data;
  try { data = JSON.parse(resultJson); }
  catch (_) { printLine('» generate_video: ' + resultJson, resultJson.startsWith('error') ? 'err' : 'info'); return; }
  const files = data.files || [];
  if (!files.length) { printLine('» generate_video: no output files', 'err'); return; }

  const w = $('welcome');
  if (w) w.remove();

  for (const file of files) {
    const card = document.createElement('div');
    card.className = 'diff-card';
    if (file.kind === 'video') {
      card.innerHTML = `<video controls src="${file.url}" style="width:100%;display:block;"></video>`;
    } else {
      card.innerHTML = `<img src="${file.url}" alt="${escapeHtml(file.filename || 'generated frame')}" style="max-width:100%;display:block;" />`;
    }
    els.output.appendChild(card);
  }
  els.output.scrollTop = els.output.scrollHeight;
}
