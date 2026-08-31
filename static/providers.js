// providers.js
//
// Pure lookup module for provider display — trimmed port of odysseus-dev's
// static/js/providers.js (187 lines, ~90 providers) down to the handful
// starfire actually talks to.

const _PROVIDERS = [
  [/ollama|:11434/i, 'Ollama'],
  [/openai|gpt-/i, 'OpenAI'],
  [/anthropic|claude/i, 'Anthropic'],
];

function providerLabel(endpointUrlOrModelId) {
  const s = endpointUrlOrModelId || '';
  try {
    const host = new URL(s).hostname;
    if (['localhost', '127.0.0.1', '0.0.0.0', '::1'].includes(host) || s.includes(':11434')) {
      return 'Local';
    }
  } catch (_) {
    // not a URL — probably a model id, fall through to pattern match
  }
  for (const [re, label] of _PROVIDERS) {
    if (re.test(s)) return label;
  }
  try {
    return new URL(s).hostname;
  } catch (_) {
    return 'Custom';
  }
}
