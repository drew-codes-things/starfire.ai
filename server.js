import 'dotenv/config';
import express from 'express';
import path    from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT   = Number(process.env.PORT) || 8080;
const OLLAMA = (process.env.OLLAMA_BASE_URL || 'http://localhost:11434').replace(/\/$/, '');
const KEY    = process.env.OLLAMA_API_KEY || '';

const headers = () => {
  const h = { 'Content-Type': 'application/json' };
  if (KEY) h.Authorization = `Bearer ${KEY}`;
  return h;
};

const app = express();
app.use(express.json());
app.use(express.static(__dirname));

app.post('/api/warm', async (req, res) => {
  const { model } = req.body;
  if (!model) return res.status(400).json({ error: 'model required' });
  try {
    const wr = await fetch(`${OLLAMA}/api/generate`, {
      method:  'POST',
      headers: headers(),
      body:    JSON.stringify({ model, keep_alive: -1 }),
    });
    await wr.body?.cancel();
    res.json({ ok: true });
  } catch (e) {
    res.status(502).json({ error: e.message });
  }
});

app.get('/api/models', async (req, res) => {
  try {
    const r = await fetch(`${OLLAMA}/api/tags`, { headers: headers() });
    if (!r.ok) throw new Error(`status ${r.status}`);
    const { models = [] } = await r.json();
    res.json({ models: models.map(m => ({ name: m.name, size: m.size })) });
  } catch (e) {
    res.status(502).json({ error: `Cannot reach Ollama at ${OLLAMA}: ${e.message}` });
  }
});

app.post('/api/chat', async (req, res) => {
  const { model, messages, system, options } = req.body;
  if (!model || !Array.isArray(messages))
    return res.status(400).json({ error: 'model and messages required' });

  const payload = {
    model,
    stream:     true,
    keep_alive: -1,
    messages:   system ? [{ role: 'system', content: system }, ...messages] : messages,
    ...(options && { options }),
  };

  try {
    const r = await fetch(`${OLLAMA}/api/chat`, {
      method:  'POST',
      headers: headers(),
      body:    JSON.stringify(payload),
    });
    if (!r.ok) {
      const t = await r.text();
      return res.status(502).json({ error: `Ollama ${r.status}: ${t}` });
    }
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    r.body.pipeTo(new WritableStream({
      write(chunk) { res.write(chunk); },
      close()      { res.end(); },
      abort(e)     { res.destroy(e instanceof Error ? e : new Error(String(e || 'stream aborted'))); },
    })).catch(() => {});
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => {
  console.log(`\n  starfire.ai  ->  http://localhost:${PORT}`);
  console.log(`  ollama       ->  ${OLLAMA}\n`);
});
