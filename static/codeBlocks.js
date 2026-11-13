'use strict';

function extractCodeBlocks(text) {
  const blocks = [];
  const lines = text.split('\n');
  let pos = 0;
  let open = null;
  for (const line of lines) {
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence && !open) {
      open = { marker: fence[1], lang: fence[2].trim().split(/\s+/)[0].toLowerCase(), start: pos, fenceLine: line, codeLines: [] };
    } else if (fence && open && fence[1][0] === open.marker[0] && fence[1].length >= open.marker.length && !fence[2].trim()) {
      blocks.push({ lang: open.lang, code: open.codeLines.join('\n'), start: open.start, end: pos + line.length, fenceLine: open.fenceLine, closeLine: line });
      open = null;
    } else if (open) {
      open.codeLines.push(line);
    }
    pos += line.length + 1;
  }
  return blocks;
}

function replaceCodeBlocks(text, replacements) {
  let out = text;
  for (const { block, code } of [...replacements].sort((a, b) => b.block.start - a.block.start)) {
    out = out.slice(0, block.start) + block.fenceLine + '\n' + code.replace(/\n+$/, '') + '\n' + block.closeLine + out.slice(block.end);
  }
  return out;
}

const CODE_STATUS_LABELS = { checking: '… checking', fixing: '… fixing', ok: '✓ syntax ok', error: '✗ syntax error' };

function setCodeStatus(box, check) {
  const status = box.querySelector('.code-status');
  const btn = box.querySelector('.code-copy-btn');
  if (!status) return;
  const state = check ? check.status : '';
  let label = CODE_STATUS_LABELS[state] || '';
  if (state === 'ok' && check.fixed) label = '✓ fixed · syntax ok';
  if (state === 'error' && check.line) label += ' on line ' + check.line;
  status.textContent = label;
  status.hidden = !label;
  status.className = 'code-status ' + state;
  status.title = check && check.message ? check.message : '';
  box.dataset.check = state;
  if (btn) btn.disabled = state === 'checking' || state === 'fixing';
}

function codeBoxForBlock(container, blocks, index) {
  const pres = [...container.querySelectorAll('pre')];
  const pre = pres.length === blocks.length
    ? pres[index]
    : pres.find(p => (p.textContent || '').trim() === blocks[index].code.trim());
  return pre && pre.parentElement && pre.parentElement.classList.contains('code-box') ? pre.parentElement : null;
}

function decorateCodeBlocks(container, text, checks) {
  const blocks = text ? extractCodeBlocks(text) : [];
  const pres = [...container.querySelectorAll('pre')];
  const aligned = pres.length === blocks.length;
  pres.forEach((pre, i) => {
    const code = pre.querySelector('code');
    let box = pre.parentElement && pre.parentElement.classList.contains('code-box') ? pre.parentElement : null;
    if (!box) {
      const langClass = code ? [...code.classList].find(c => c.startsWith('language-') && c !== 'language-undefined') : '';
      const lang = langClass ? langClass.slice('language-'.length) : '';
      box = document.createElement('div');
      box.className = 'code-box';
      const head = document.createElement('div');
      head.className = 'code-head';
      head.innerHTML = `<span class="code-lang">${escapeHtml(lang || 'code')}</span><span class="code-status" hidden></span>`;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'code-copy-btn';
      btn.innerHTML = icon('copy') + '<span>Copy</span>';
      btn.title = 'Copy this code';
      btn.onclick = () => copyToClipboard(btn, code ? code.textContent : pre.textContent);
      head.appendChild(btn);
      pre.replaceWith(box);
      box.append(head, pre);
    }
    let check = null;
    if (checks && checks.length) {
      const blockIndex = aligned ? i : blocks.findIndex(b => b.code.trim() === (pre.textContent || '').trim());
      check = checks.find(c => c.block === blockIndex) || null;
    }
    setCodeStatus(box, check);
  });
}

async function checkCodeBlock(lang, code) {
  const r = await fetch('/api/code-check', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ language: lang, code }),
  });
  if (!r.ok) throw new Error('code check failed (' + r.status + ')');
  return r.json();
}

async function requestCodeFix(model, block, check) {
  const prompt =
    `This ${block.lang} code block from your last reply fails a ${check.checker || 'syntax'} check:\n\n` +
    `${check.message}\n\n${block.fenceLine}\n${block.code}\n${block.closeLine}\n\n` +
    `Reply with only the corrected ${block.lang} code in one fenced code block and nothing else.`;
  const r = await fetch('/api/quick-complete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint_id: model.endpoint_id, model: model.id, prompt }),
  });
  if (!r.ok) return null;
  const { text } = await r.json();
  const blocks = extractCodeBlocks(text || '');
  return blocks.length ? blocks[0].code : null;
}

const CODE_FIX_ATTEMPTS = 2;

async function verifyCodeBlocks(container, aiIdx, model) {
  const msg = messages[aiIdx];
  if (!msg || !prefs.checkCode) return;
  const text = msg.content;
  const blocks = extractCodeBlocks(text);
  const todo = blocks.map((block, index) => ({ block, index })).filter(x => x.block.lang);
  if (!todo.length) return;
  for (const { index } of todo) {
    const box = codeBoxForBlock(container, blocks, index);
    if (box) setCodeStatus(box, { status: 'checking' });
  }
  const checks = [];
  const replacements = [];
  for (const { block, index } of todo) {
    const box = codeBoxForBlock(container, blocks, index);
    let current = block;
    let check;
    try {
      check = await checkCodeBlock(block.lang, block.code);
      const firstError = check;
      for (let attempt = 1; check.status === 'error' && attempt <= CODE_FIX_ATTEMPTS && model; attempt++) {
        if (box) setCodeStatus(box, { status: 'fixing', message: `attempt ${attempt} of ${CODE_FIX_ATTEMPTS}: ${check.message}` });
        const fixed = await requestCodeFix(model, current, check);
        if (!fixed || fixed.trim() === current.code.trim()) { check = firstError; break; }
        const again = await checkCodeBlock(block.lang, fixed);
        if (again.status === 'ok') {
          replacements.push({ block, code: fixed });
          check = { ...again, fixed: attempt, message: 'was: ' + firstError.message };
          break;
        }
        current = { ...current, code: fixed };
        check = attempt === CODE_FIX_ATTEMPTS ? firstError : again;
      }
    } catch (e) {
      check = { status: 'skipped', message: e.message };
    }
    checks.push({ block: index, ...check });
    if (box) setCodeStatus(box, check);
  }
  if (messages[aiIdx] !== msg) return;
  msg.code_checks = checks;
  if (replacements.length) {
    msg.content = replaceCodeBlocks(text, replacements);
    if (container.isConnected) renderAiContent(container, msg.content, checks);
  }
  persistSession();
}
