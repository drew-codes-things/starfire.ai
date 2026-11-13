'use strict';

const TEXT_EXTENSIONS = /\.(txt|md|markdown|csv|tsv|json|ya?ml|toml|ini|cfg|conf|log|xml|html?|css|js|mjs|ts|tsx|jsx|py|rb|go|rs|java|kt|c|h|cpp|hpp|cs|php|sh|bash|zsh|sql|lua|swift|r|tex)$/i;

let attachments = [];
let attachmentCounter = 0;
let mentionModel = null;

function renderAttachments() {
  const box = $('attachChips');
  box.innerHTML = '';
  if (mentionModel) {
    const chip = document.createElement('span');
    chip.className = 'chip mention';
    chip.innerHTML = `<span class="chip-label">@${escapeHtml(mentionModel.id)}</span><button type="button" aria-label="Remove model mention">×</button>`;
    chip.querySelector('button').onclick = () => { mentionModel = null; renderAttachments(); };
    box.appendChild(chip);
  }
  for (const a of attachments) {
    const chip = document.createElement('span');
    chip.className = 'chip' + (a.status === 'loading' ? ' pending' : '') + (a.status === 'error' ? ' error' : '');
    const note = a.status === 'loading' ? ' (reading…)' : a.note ? ` (${a.note})` : '';
    chip.title = a.status === 'error' ? a.note : a.name;
    chip.innerHTML = `<span class="chip-label">${escapeHtml(a.name + note)}</span><button type="button" aria-label="Remove ${escapeHtml(a.name)}">×</button>`;
    chip.querySelector('button').onclick = () => {
      attachments = attachments.filter(x => x !== a);
      renderAttachments();
    };
    box.appendChild(chip);
  }
}

function limitText(text) {
  if (text.length <= MAX_ATTACHMENT_CHARS) return { text, truncated: false };
  return { text: text.slice(0, MAX_ATTACHMENT_CHARS), truncated: true };
}

const MEDIA_TYPE = /^(image|audio|video)\//;

async function readAttachment(file) {
  if (MEDIA_TYPE.test(file.type)) {
    const form = new FormData();
    form.append('file', file);
    const media = await apiJson('/api/attachments', { method: 'POST', body: form });
    return { media: { id: media.id, kind: media.kind, name: media.name }, text: '', note: media.kind };
  }
  if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
    const form = new FormData();
    form.append('file', file);
    const doc = await apiJson('/api/documents', { method: 'POST', body: form });
    const { text, truncated } = await apiJson(`/api/documents/${doc.id}/text`);
    return { text, note: truncated ? 'added to knowledge, truncated' : 'added to knowledge' };
  }
  const raw = await file.text();
  if (!(file.type.startsWith('text/') || TEXT_EXTENSIONS.test(file.name)) && raw.includes('\u0000')) {
    throw new Error('binary file - only text and PDF files can be attached');
  }
  const { text, truncated } = limitText(raw);
  return { text, note: truncated ? 'truncated' : '' };
}

async function addAttachmentFiles(files) {
  for (const file of files) {
    const entry = { id: ++attachmentCounter, name: file.name, status: 'loading', text: '', note: '' };
    attachments.push(entry);
    renderAttachments();
    try {
      Object.assign(entry, await readAttachment(file), { status: 'ready' });
    } catch (e) {
      Object.assign(entry, { status: 'error', note: e.message });
    }
    renderAttachments();
  }
}

async function attachKnowledge(doc) {
  const entry = { id: ++attachmentCounter, name: '#' + doc.filename, status: 'loading', text: '', note: '' };
  attachments.push(entry);
  renderAttachments();
  try {
    const { text, truncated } = await apiJson(`/api/documents/${doc.id}/text`);
    Object.assign(entry, { text, note: truncated ? 'truncated' : '', status: 'ready' });
  } catch (e) {
    Object.assign(entry, { status: 'error', note: e.message });
  }
  renderAttachments();
}

$('attachFileInput').addEventListener('change', e => {
  addAttachmentFiles([...e.target.files]);
  e.target.value = '';
});

const chatArea = document.querySelector('.chat-area');
chatArea.addEventListener('dragover', e => {
  if ([...e.dataTransfer.types].includes('Files')) e.preventDefault();
});
chatArea.addEventListener('drop', e => {
  if (!e.dataTransfer.files.length) return;
  e.preventDefault();
  addAttachmentFiles([...e.dataTransfer.files]);
});
els.input.addEventListener('paste', e => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  e.preventDefault();
  addAttachmentFiles(files);
});

function takeComposerExtras(text) {
  const ready = attachments.filter(a => a.status === 'ready');
  const overrideModel = mentionModel;
  attachments = attachments.filter(a => a.status === 'loading');
  mentionModel = null;
  renderAttachments();
  if (!ready.length) return { overrideModel, extra: {} };
  const textFiles = ready.filter(a => !a.media);
  const media = ready.filter(a => a.media).map(a => a.media);
  const extra = { files: ready.filter(a => !a.media || a.media.kind !== 'image').map(a => a.name) };
  if (textFiles.length) {
    const blocks = textFiles.map(a => `<file name="${a.name.replace(/"/g, "'")}">\n${a.text}\n</file>`).join('\n\n');
    extra.content = `${text}\n\nAttached files:\n\n${blocks}`;
  }
  if (media.length) extra.attachments = media;
  return { overrideModel, extra };
}
