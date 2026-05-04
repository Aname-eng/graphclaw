
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, Dict

import requests

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text


def _check_comfyui_available() -> bool:
    """Check if local ComfyUI is available."""
    try:
        response = requests.get("http://127.0.0.1:8188/system_stats", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def _generate_with_comfyui(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    model: str = "stabilityai/stable-diffusion-2-1",
    on_log: Optional[Callable[[str], None]] = None,
    task_id: str = "",
) -> Path:
    """Generate image with local ComfyUI."""
    if on_log:
        on_log("[%s] Using local ComfyUI" % task_id)
    
    comfyui_prompt = {
        "3": {
            "inputs": {
                "seed": 0,
                "steps": 20,
                "cfg": 8,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            },
            "class_type": "KSampler"
        },
        "4": {
            "inputs": {
                "ckpt_name": "v1-5-pruned-emaonly.ckpt"
            },
            "class_type": "CheckpointLoaderSimple"
        },
        "5": {
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            },
            "class_type": "EmptyLatentImage"
        },
        "6": {
            "inputs": {
                "text": prompt,
                "clip": ["4", 1]
            },
            "class_type": "CLIPTextEncode"
        },
        "7": {
            "inputs": {
                "text": negative_prompt or "blurry, bad quality, distorted",
                "clip": ["4", 1]
            },
            "class_type": "CLIPTextEncode"
        },
        "8": {
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            },
            "class_type": "VAEDecode"
        },
        "9": {
            "inputs": {
                "filename_prefix": "ComfyUI",
                "images": ["8", 0]
            },
            "class_type": "SaveImage"
        }
    }
    
    queue_response = requests.post("http://127.0.0.1:8188/prompt", json={"prompt": comfyui_prompt})
    queue_response.raise_for_status()
    prompt_id = queue_response.json()["prompt_id"]
    
    if on_log:
        on_log("[%s] ComfyUI task submitted: %s" % (task_id, prompt_id))
    
    history = {}
    while True:
        history_response = requests.get("http://127.0.0.1:8188/history/%s" % prompt_id)
        history_response.raise_for_status()
        history = history_response.json()
        if prompt_id in history:
            break
        time.sleep(1)
    
    image_info = history[prompt_id]["outputs"]["9"]["images"][0]
    image_url = "http://127.0.0.1:8188/view?filename=%s&subfolder=%s&type=%s" % (
        image_info["filename"], image_info["subfolder"], image_info["type"],
    )
    
    image_response = requests.get(image_url)
    image_response.raise_for_status()
    
    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_file.write(image_response.content)
    temp_file.close()
    
    return Path(temp_file.name)


def _generate_with_huggingface(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    model: str = "stabilityai/stable-diffusion-2-1",
    api_key: Optional[str] = None,
    on_log: Optional[Callable[[str], None]] = None,
    task_id: str = "",
) -> Path:
    """Generate image with Hugging Face Inference API."""
    if on_log:
        on_log("[%s] Using Hugging Face API" % task_id)
    
    hf_token = api_key or os.getenv("HF_TOKEN")
    if not hf_token:
        raise ValueError("HF_TOKEN environment variable not set and no api_key provided")
    
    api_url = "https://api-inference.huggingface.co/models/%s" % model
    headers = {"Authorization": "Bearer %s" % hf_token}
    
    payload = {
        "inputs": prompt,
        "parameters": {
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
        },
    }
    
    response = requests.post(api_url, headers=headers, json=payload)
    response.raise_for_status()
    
    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_file.write(response.content)
    temp_file.close()
    
    return Path(temp_file.name)


def generate_image(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    model: str = "stabilityai/stable-diffusion-2-1",
    api_key: Optional[str] = None,
    task_id: str = "",
    on_log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Generate an image from a text prompt.
    
    Tries local ComfyUI first, falls back to Hugging Face if ComfyUI not available.
    """
    try:
        if on_log:
            on_log("[%s] Starting image generation: %s" % (task_id, redact_sensitive_text(prompt[:50])))
        
        image_path: Optional[Path] = None
        
        if _check_comfyui_available():
            try:
                image_path = _generate_with_comfyui(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    model=model,
                    on_log=on_log,
                    task_id=task_id,
                )
            except Exception as e:
                if on_log:
                    on_log("[%s] ComfyUI failed: %s, trying Hugging Face" % (task_id, str(e)))
        
        if image_path is None:
            image_path = _generate_with_huggingface(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                model=model,
                api_key=api_key,
                on_log=on_log,
                task_id=task_id,
            )
        
        if on_log:
            on_log("[%s] Image generated: %s" % (task_id, image_path))
        
        return tool_result(
            data={
                "image_path": str(image_path),
                "prompt": redact_sensitive_text(prompt),
                "width": width,
                "height": height,
            }
        )
    except Exception as e:
        return tool_error(
            code="IMAGE_GEN_ERROR",
            message="Failed to generate image: %s" % str(e),
        )


def register_all() -> None:
    registry.register(
        name="generate_image",
        handler=generate_image,
        description="Generate an image from a text prompt (ComfyUI or Hugging Face)",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the image"},
                "negative_prompt": {"type": "string", "description": "Negative prompt (what to avoid)", "default": ""},
                "width": {"type": "integer", "description": "Image width in pixels", "default": 512},
                "height": {"type": "integer", "description": "Image height in pixels", "default": 512},
                "model": {"type": "string", "description": "Model name", "default": "stabilityai/stable-diffusion-2-1"},
                "api_key": {"type": "string", "description": "Hugging Face API key (optional, uses HF_TOKEN env var)"},
                "task_id": {"type": "string", "description": "Optional task ID for logging", "default": ""},
            },
            "required": ["prompt"],
        },
        toolset="image",
        emoji="🎨",
    )


register_all()
