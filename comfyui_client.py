from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx

PROBE_TIMEOUT = 5.0
GENERATE_TIMEOUT = 300.0
POLL_INTERVAL = 1.0
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=None)

QUALITY_PRESETS = {
    "low": {"steps": 10, "width": 512, "height": 512},
    "medium": {"steps": 20, "width": 768, "height": 768},
    "high": {"steps": 35, "width": 1024, "height": 1024},
}

async def pull_checkpoint(url: str, checkpoints_dir: str, filename: str):
    if not os.path.isdir(checkpoints_dir):
        raise RuntimeError(f"checkpoints directory does not exist: {checkpoints_dir}")
    dest_path = os.path.join(checkpoints_dir, filename)
    tmp_path = dest_path + ".part"

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            total = int(r.headers["content-length"]) if "content-length" in r.headers else None
            downloaded = 0
            try:
                with open(tmp_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        yield downloaded, total
            except BaseException:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
    os.replace(tmp_path, dest_path)

async def detect(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            r = await client.get(base_url.rstrip("/") + "/system_stats")
        return r.status_code == 200
    except httpx.HTTPError:
        return False

async def list_checkpoints(base_url: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            r = await client.get(base_url.rstrip("/") + "/object_info/CheckpointLoaderSimple")
        r.raise_for_status()
        data = r.json()
        return data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return []

def _build_workflow(prompt: str, checkpoint: str, negative_prompt: str,
                     width: int, height: int, seed: int, steps: int) -> dict:
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal",
            "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0],
        }},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "starfire", "images": ["8", 0]}},
    }

async def _queue_and_wait(base_url: str, workflow: dict) -> dict:
    base_url = base_url.rstrip("/")
    client_id = uuid.uuid4().hex

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            r = await client.post(base_url + "/prompt", json={"prompt": workflow, "client_id": client_id})
            r.raise_for_status()
            prompt_id = r.json()["prompt_id"]
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"ComfyUI rejected the workflow ({e.response.status_code}): {e.response.text[:400]}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"could not reach ComfyUI: {e}") from e
    except KeyError as e:
        raise RuntimeError(f"unexpected response queuing the workflow: missing {e}") from e

    deadline = time.monotonic() + GENERATE_TIMEOUT
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{base_url}/history/{prompt_id}")
                r.raise_for_status()
                history = r.json()
            except httpx.HTTPError:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            if prompt_id in history:
                return history[prompt_id].get("outputs", {})
            await asyncio.sleep(POLL_INTERVAL)

    raise RuntimeError(f"ComfyUI generation timed out after {GENERATE_TIMEOUT:.0f}s")

def _extract_file_refs(outputs: dict) -> list[dict]:
    refs = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for value in node_output.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, dict) and "filename" in item:
                    refs.append(item)
    return refs

async def _fetch_view(client: httpx.AsyncClient, base_url: str, ref: dict) -> bytes:
    r = await client.get(f"{base_url.rstrip('/')}/view", params={
        "filename": ref["filename"], "subfolder": ref.get("subfolder", ""), "type": ref.get("type", "output"),
    })
    r.raise_for_status()
    return r.content

async def generate(prompt: str, base_url: str, checkpoint: str, negative_prompt: str = "",
                    width: int = 512, height: int = 512, steps: int = 20) -> bytes:
    seed = uuid.uuid4().int & 0xFFFFFFFF
    workflow = _build_workflow(prompt, checkpoint, negative_prompt, width, height, seed, steps)
    outputs = await _queue_and_wait(base_url, workflow)
    refs = _extract_file_refs(outputs)
    if not refs:
        raise RuntimeError("ComfyUI finished the workflow but produced no image output")
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        return await _fetch_view(client, base_url, refs[0])

async def run_custom_workflow(base_url: str, workflow: dict, prompt_node_id: str,
                                prompt_input_key: str, prompt: str) -> list[tuple[str, bytes]]:
    workflow = json.loads(json.dumps(workflow))
    try:
        workflow[prompt_node_id]["inputs"][prompt_input_key] = prompt
    except KeyError as e:
        raise RuntimeError(f"workflow has no node '{prompt_node_id}' with input '{prompt_input_key}': {e}") from e

    outputs = await _queue_and_wait(base_url, workflow)
    refs = _extract_file_refs(outputs)
    if not refs:
        raise RuntimeError("ComfyUI finished the workflow but produced no file output")

    results = []
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for ref in refs:
            data = await _fetch_view(client, base_url, ref)
            results.append((ref["filename"], data))
    return results
