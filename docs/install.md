# Install

## Requirements

- Python 3.12+
- [Ollama](https://ollama.com/download), installed and running, for local models (optional
  if you only plan to use hosted providers)
- `prlimit` (util-linux, installed on most Linux systems) for code checks: every checker runs
  under it, and without it every code check is reported as skipped
- [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`, optional): C and C++ are only
  checked inside it, and run_shell runs in it when it is installed and can start (without it, or
  where it cannot start, run_shell runs unsandboxed, logs that and says so on its approval card; a
  failed start is tried again after five minutes)

## Run it from source

```bash
cd starfire.ai
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python server.py
```

Then open [http://localhost:8080](http://localhost:8080).

It listens on `127.0.0.1` only by default.

## Docker

The easiest way to run it in Docker is via the included compose file:

```bash
cp .env.example .env
docker compose up -d --build
```

A host-run Ollama is reachable from inside the container via `host.docker.internal`.
Data persists in `./data` on the host (mounted as a volume), so it survives container
rebuilds and restarts.

### Building the image manually

If you don't want compose, build and run the image directly:

```bash
docker build -t starfire.ai .
docker run -d -p 127.0.0.1:8080:8080 -v $(pwd)/data:/app/data --add-host host.docker.internal:host-gateway starfire.ai
```

To push the built image to a registry instead of just running it locally:

```bash
docker tag starfire.ai ghcr.io/<your-username>/starfire.ai:latest
docker push ghcr.io/<your-username>/starfire.ai:latest
```

(swap `ghcr.io/<your-username>` for Docker Hub's `<your-username>/starfire.ai` if you'd
rather use that instead)
