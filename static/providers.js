

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
