# Audio and images (local generation)

Settings → Audio & Images configures fully local image, audio, and video generation - no API key,
no hosted service, no content moderation layer other than whatever model you load:

- **Image** - via [ComfyUI](https://github.com/comfyanonymous/ComfyUI), a plain local server
  (same trust model as Ollama). Point starfire at your ComfyUI base URL and checkpoints
  directory; a generic URL-based downloader can pull any checkpoint file you give it a link to
  - starfire doesn't curate or recommend which ones. Low/medium/high quality presets trade off
  steps and resolution.
- **Audio** - via [Piper](https://github.com/rhasspy/piper), a one-shot local CLI (not a
  server) with low/medium/high quality voice variants you download per-voice.
- **Video** - no fixed built-in workflow (the ecosystem is too fragmented for one); export a
  working ComfyUI workflow graph as JSON, tell starfire which node/input holds the prompt text,
  and save it under a name. The `generate_video` tool then queues it by name.

Enable the corresponding tool (Generate image / Generate audio / Generate document) in Settings
→ Tools. If local generation isn't configured, image/audio generation fall back to OpenAI's API
(DALL-E/TTS) when a hosted OpenAI endpoint is configured - otherwise they're unavailable.
