'use strict';

const $   = id => document.getElementById(id);
const MAX_HISTORY = 20;

const els = {
  output:     $('output'),
  form:       $('form'),
  input:      $('msgInput'),
  sendBtn:    $('sendBtn'),
  modelList:  $('modelList'),
  ctxSize:    $('ctxSize'),
  exportBtn:  $('exportBtn'),
  clearBtn:   $('clearBtn'),
  dot:        $('dot'),
  statusText: $('statusText'),
  sysPrompt:  $('sysPrompt'),
};

let messages = [];
let busy     = false;

function setStatus(state, text) {
  els.dot.className          = 'dot ' + state;
  els.statusText.textContent = text;
}

function printLine(text, cls) {
  const w = $('welcome');
  if (w) w.remove();

  const div       = document.createElement('div');
  div.className   = cls ? 'line ' + cls : 'line';
  div.textContent = text;
  els.output.appendChild(div);
  els.output.scrollTop = els.output.scrollHeight;
  return div;
}

function printSep() {
  const div       = document.createElement('div');
  div.className   = 'line sep';
  div.textContent = '-'.repeat(72);
  els.output.appendChild(div);
  els.output.scrollTop = els.output.scrollHeight;
}

function warmModel(name) {
  fetch('/api/warm', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ model: name }),
  }).catch(() => {});
}

async function loadModels() {
  setStatus('loading', 'connecting...');
  try {
    const r                    = await fetch('/api/models');
    const { models = [], error } = await r.json();
    if (error) throw new Error(error);
    if (!models.length) throw new Error('no models found - run: ollama pull <model>');

    els.modelList.innerHTML = '';
    for (const m of models) {
      const opt       = document.createElement('option');
      opt.value       = m.name;
      opt.textContent = m.name + (m.size ? ' (' + (m.size / 1073741824).toFixed(1) + ' GB)' : '');
      els.modelList.appendChild(opt);
    }

    setStatus('ready', els.modelList.value);
    warmModel(els.modelList.value);
    els.input.disabled   = false;
    els.sendBtn.disabled = false;
    els.input.focus();
  } catch (e) {
    setStatus('error', 'ollama unreachable');
    printLine('error: ' + e.message, 'err');
  }
}

async function send(text) {
  if (busy) return;
  busy = true;
  els.sendBtn.disabled = true;
  els.input.disabled   = true;

  const userIdx = messages.length;
  messages.push({ role: 'user', content: text });
  printLine(text, 'user');

  const aiLine  = printLine('', 'ai streaming');
  const model   = els.modelList.value;
  let fullText  = '';
  let gotToken  = false;

  setStatus('loading', 'waiting...');

  const history = messages.length > MAX_HISTORY
    ? messages.slice(messages.length - MAX_HISTORY)
    : messages.slice();

  try {
    const r = await fetch('/api/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        model,
        messages: history,
        system:   els.sysPrompt.value.trim() || undefined,
        options:  { num_ctx: parseInt(els.ctxSize?.value || '2048', 10), num_gpu: 99 },
      }),
    });

    if (!r.ok) {
      const { error } = await r.json().catch(() => ({}));
      throw new Error(error || 'server error ' + r.status);
    }

    const reader = r.body.getReader();
    const dec    = new TextDecoder();
    let   buf    = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.message?.content) {
            if (!gotToken) { gotToken = true; setStatus('ready', model); }
            fullText            += obj.message.content;
            aiLine.textContent   = fullText;
            els.output.scrollTop = els.output.scrollHeight;
          }
        } catch (_) {}
      }
    }

    aiLine.classList.remove('streaming');
    setStatus('ready', model);
    if (fullText) messages.push({ role: 'assistant', content: fullText });
    printSep();
  } catch (e) {
    if (gotToken) {
      if (fullText) messages.push({ role: 'assistant', content: fullText });
    } else {
      messages.splice(userIdx, 1);
    }
    aiLine.classList.remove('streaming');
    setStatus('error', 'error');
    printLine('error: ' + e.message, 'err');
  } finally {
    busy                   = false;
    els.input.disabled     = false;
    els.sendBtn.disabled   = false;
    els.input.value        = '';
    els.input.style.height = 'auto';
    els.input.focus();
  }
}

function exportChat() {
  if (!messages.length) return;
  const text = messages.map(m => '### ' + m.role + '\n\n' + m.content).join('\n\n---\n\n');
  const url  = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const a    = Object.assign(document.createElement('a'), { href: url, download: 'starfire-' + Date.now() + '.md' });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 100);
}

function clearChat() {
  messages             = [];
  els.output.innerHTML =
    '<div class="welcome" id="welcome">' +
    '<div class="welcome-brand">starfire<span class="dim">.ai</span></div>' +
    '<p class="info-line">// chat cleared</p>' +
    '</div>';
}

els.modelList.addEventListener('change', () => {
  messages = [];
  warmModel(els.modelList.value);
  setStatus('ready', els.modelList.value);
});

els.exportBtn.onclick = exportChat;
els.clearBtn.onclick  = clearChat;

els.form.addEventListener('submit', e => {
  e.preventDefault();
  const t = els.input.value.trim();
  if (t) send(t);
});

els.input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, 140) + 'px';
});

try { els.sysPrompt.value = localStorage.getItem('sf_sys') || ''; } catch (_) {}
els.sysPrompt.addEventListener('input', () => {
  try { localStorage.setItem('sf_sys', els.sysPrompt.value); } catch (_) {}
});

loadModels();
