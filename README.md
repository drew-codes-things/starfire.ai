# starfire.ai

A terminal-themed local AI chat interface powered by [Ollama](https://ollama.com). Runs entirely on your machine - nothing is sent to any server.

## Requirements

- [Node.js](https://nodejs.org) 18+
- [Ollama](https://ollama.com/download) installed and running

## Setup

```bash
cd starfire.ai
npm install --no-bin-links
cp .env.example .env
npm start
```

Then open [http://localhost:8080](http://localhost:8080).

## Getting a model

```bash
ollama pull huihui_ai/qwen3.5-abliterated:2b
```

Any model listed by `ollama list` will appear in the dropdown automatically.

## Configuration

Copy `.env.example` to `.env` and edit as needed.

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | Port the server listens on |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_API_KEY` | _(empty)_ | Only needed for hosted Ollama endpoints |

## Features

- Streams responses token by token
- Context window selector (512 - 8192 tokens)
- Optional system prompt saved in browser
- Model stays loaded in memory between messages
- Export chat as markdown
- Runs 100% locally - no telemetry, no accounts

## License

MIT
