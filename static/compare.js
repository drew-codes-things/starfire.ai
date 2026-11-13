'use strict';

const compareEls = {
  modal: $('compareModal'), closeBtn: $('compareCloseBtn'), form: $('compareForm'),
  input: $('comparePromptInput'), columns: $('compareColumns'), addColumnBtn: $('compareAddColumnBtn'),
};

function compareModelOptionsHtml() {
  return models.map(m =>
    `<option value="${escapeHtml(m.id)}" data-endpoint-id="${escapeHtml(m.endpoint_id)}">${escapeHtml(m.id)} (${escapeHtml(window.providerLabel ? window.providerLabel(m.provider) : m.provider)})</option>`
  ).join('');
}

const COMPARE_EMPTY_TEXT = 'The reply shows here';
const COMPARE_WAITING_TEXT = 'Waiting for the reply…';

function comparedModelKeys() {
  return [...compareEls.columns.querySelectorAll('.compare-model-select')]
    .map(select => select.selectedOptions[0])
    .filter(Boolean)
    .map(option => modelKey({ endpoint_id: option.dataset.endpointId, id: option.value }));
}

function addCompareColumn() {
  const unusedModel = firstUnusedModel(models, comparedModelKeys(), modelKey);
  const col = document.createElement('div');
  col.className = 'compare-column';
  col.innerHTML =
    `<div class="compare-column-header">` +
      `<select class="compare-model-select">${compareModelOptionsHtml()}</select>` +
      `<button class="btn ghost compare-remove" type="button" title="Remove column" aria-label="Remove column">×</button>` +
    `</div>` +
    `<div class="compare-column-output line ai" data-empty="${COMPARE_EMPTY_TEXT}"></div>`;
  if (unusedModel) col.querySelector('.compare-model-select').selectedIndex = models.indexOf(unusedModel);
  col.querySelector('.compare-remove').onclick = () => {
    if (compareEls.columns.children.length > 1) col.remove();
  };
  compareEls.columns.appendChild(col);
}

function openCompare() {
  compareEls.modal.hidden = false;
  if (!compareEls.columns.children.length) {
    addCompareColumn();
    addCompareColumn();
  }
}
function closeCompare() { compareEls.modal.hidden = true; }

els.compareBtn = $('compareBtn');
els.compareBtn.onclick = openCompare;
compareEls.closeBtn.onclick = closeCompare;
compareEls.addColumnBtn.onclick = addCompareColumn;
compareEls.modal.addEventListener('click', e => { if (e.target === compareEls.modal) closeCompare(); });

async function runCompareColumn(col, prompt) {
  const select = col.querySelector('.compare-model-select');
  const output = col.querySelector('.compare-column-output');
  const opt = select.selectedOptions[0];
  if (!opt) { output.textContent = 'no model selected'; return; }
  output.textContent = '';
  output.dataset.empty = COMPARE_WAITING_TEXT;
  let full = '';
  try {
    const r = await fetch('/api/chat_stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        endpoint_id: opt.dataset.endpointId, model: opt.value,
        messages: [{ role: 'user', content: prompt }],
      }),
    });
    if (!r.ok) { output.textContent = 'error: server ' + r.status; return; }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const line = frame.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const obj = JSON.parse(line.slice(5).trim());
          if (obj.delta) { full += obj.delta; output.textContent = full; }
          else if (obj.error) { full += '\n[error: ' + obj.error + ']'; output.textContent = full; }
        } catch (_) {}
      }
    }
    renderAiContent(output, full);
  } catch (e) {
    output.textContent = 'error: ' + e.message;
  }
}

compareEls.form.addEventListener('submit', e => {
  e.preventDefault();
  const prompt = compareEls.input.value.trim();
  if (!prompt) return;
  for (const col of compareEls.columns.children) runCompareColumn(col, prompt);
});
