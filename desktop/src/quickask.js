const MAX_QUESTION_CHARS = 4000;
const DELIVERY_ATTEMPTS = 20;
const DELIVERY_INTERVAL_MS = 400;

function cleanQuestion(value) {
  if (typeof value !== 'string') return '';
  return value.trim().slice(0, MAX_QUESTION_CHARS).trim();
}

function composerScript(question) {
  return `(() => {
  const input = document.getElementById('msgInput');
  const send = document.getElementById('sendBtn');
  const newChat = document.getElementById('clearBtn');
  if (!input || !send || input.disabled) return 'not-ready';
  if (newChat) newChat.click();
  input.value = ${JSON.stringify(question)};
  input.dispatchEvent(new Event('input', { bubbles: true }));
  if (send.disabled) return 'not-ready';
  send.click();
  return 'sent';
})()`;
}

async function deliverQuestion({ question, run, wait, attempts = DELIVERY_ATTEMPTS, intervalMs = DELIVERY_INTERVAL_MS }) {
  const script = composerScript(question);
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let outcome = 'not-ready';
    try {
      outcome = await run(script);
    } catch (error) {
      outcome = 'not-ready';
    }
    if (outcome === 'sent') return 'sent';
    await wait(intervalMs);
  }
  return 'not-ready';
}

module.exports = { composerScript, cleanQuestion, deliverQuestion, MAX_QUESTION_CHARS };
