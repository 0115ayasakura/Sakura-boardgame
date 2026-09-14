"""ComfyUI 出图客户端：提交 txt2img 任务、等待完成、取回 PNG。

用法（torch 装好后）：
    python comfy_client.py --prompt "..." --out out.png [--seed 123] [--size 512]
需要 ComfyUI 已在 127.0.0.1:8188 运行。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HOST = "127.0.0.1:8188"


def _post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"http://{HOST}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get(path: str) -> bytes:
    with urllib.request.urlopen(f"http://{HOST}{path}", timeout=60) as resp:
        return resp.read()


def build_workflow(prompt: str, negative: str, seed: int, size: int,
                   steps: int, cfg: float, ckpt: str, sampler: str, batch: int) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": size, "height": size, "batch_size": batch}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler,
                         "scheduler": "karras", "denoise": 1.0,
                         "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "poi", "images": ["6", 0]}},
    }


def run(prompt: str, negative: str, seed: int, size: int, steps: int, cfg: float,
        ckpt: str, sampler: str, batch: int, out_paths: list[str], timeout: float = 900.0) -> list[str]:
    client_id = uuid.uuid4().hex
    wf = build_workflow(prompt, negative, seed, size, steps, cfg, ckpt, sampler, batch)
    result = _post("/prompt", {"prompt": wf, "client_id": client_id})
    prompt_id = result["prompt_id"]
    print("prompt_id:", prompt_id, flush=True)

    started = time.time()
    images: list[dict] = []
    while time.time() - started < timeout:
        time.sleep(2)
        try:
            history = json.loads(_get(f"/history/{prompt_id}"))
        except urllib.error.HTTPError:
            continue
        entry = history.get(prompt_id)
        if not entry:
            continue
        outputs = entry.get("outputs") or {}
        for node in outputs.values():
            images.extend(node.get("images") or [])
        if images or entry.get("status", {}).get("completed"):
            break
    if not images:
        raise RuntimeError("未取到输出图片（超时或失败）")

    saved = []
    for index, img in enumerate(images):
        query = urllib.parse.urlencode({
            "filename": img["filename"], "subfolder": img.get("subfolder", ""),
            "type": img.get("type", "output")})
        data = _get(f"/view?{query}")
        target = out_paths[index] if index < len(out_paths) else f"out_{index}.png"
        with open(target, "wb") as handle:
            handle.write(data)
        saved.append(target)
        print("saved:", target, len(data) // 1024, "KB", flush=True)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--negative", default="")
    parser.add_argument("--out", required=True, help="输出文件，多个用逗号分隔")
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--cfg", type=float, default=7.0)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--ckpt", default="Counterfeit-V3.0_fp16.safetensors")
    parser.add_argument("--sampler", default="dpmpp_2m")
    args = parser.parse_args()
    outs = [p.strip() for p in args.out.split(",")]
    run(args.prompt, args.negative, args.seed, args.size, args.steps, args.cfg,
        args.ckpt, args.sampler, args.batch, outs)


if __name__ == "__main__":
    main()
