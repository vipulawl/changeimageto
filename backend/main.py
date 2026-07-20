from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageEnhance, ImageOps, ImageFilter
import io
import logging
import asyncio
import json
import os
from datetime import datetime
from typing import Optional, List
import numpy as np
import re
import hashlib
import requests
import zipfile
import tempfile
import base64
import cv2
from skimage import measure, filters, exposure
from skimage.metrics import structural_similarity as ssim
import sqlite3
from enum import Enum
import subprocess
import shutil

try:
    from google.cloud import storage
except Exception:
    storage = None  # optional in local dev

try:
    from pytrends.request import TrendReq
except Exception:
    TrendReq = None

try:
    import pytesseract
except Exception:
    pytesseract = None

# vtracer - we'll use the binary via subprocess
VTracer_AVAILABLE = True  # We'll check for binary availability at runtime

from rembg import remove, new_session
try:
    import onnxruntime as ort
except Exception:
    ort = None

# IndexNow integration
try:
    from indexnow import submit_blog_post
    INDEXNOW_AVAILABLE = True
except Exception:
    INDEXNOW_AVAILABLE = False
    submit_blog_post = None

# LaMa PyTorch imports (lama-cleaner)
try:
    from lama_cleaner.model_manager import ModelManager
    from lama_cleaner.schema import HDStrategy
except Exception:
    ModelManager = None
    HDStrategy = None

_LAMA_MANAGER = None

# Track used payment sessions to prevent reuse
_USED_PAYMENT_SESSIONS = set()

# Upscayl/Real-ESRGAN NCNN-Vulkan binary path (for local testing)
# Upscayl uses Real-ESRGAN NCNN under the hood
UPSCAYL_NCNN_PATH = os.getenv("UPSCAYL_NCNN_PATH", "./realesrgan-ncnn-vulkan")
UPSCAYL_MODELS_PATH = os.getenv("UPSCAYL_MODELS_PATH", "./realesrgan-models")

def _check_upscayl_ncnn_available():
    """Check if Upscayl NCNN binary is available (same as Real-ESRGAN NCNN)"""
    # Check multiple possible locations
    possible_paths = [
        UPSCAYL_NCNN_PATH,
        "./realesrgan-ncnn-vulkan",
        os.path.join(os.getcwd(), "realesrgan-ncnn-vulkan"),
        os.path.join(os.path.dirname(__file__), "..", "realesrgan-ncnn-vulkan"),
        "/usr/local/bin/realesrgan-ncnn-vulkan",
    ]
    
    for path in possible_paths:
        abs_path = os.path.abspath(path) if not os.path.isabs(path) else path
        if os.path.exists(abs_path) and os.access(abs_path, os.X_OK):
            logger.info(f"Found Upscayl NCNN binary at: {abs_path}")
            return abs_path
    
    logger.warning(f"Upscayl NCNN binary not found. Checked: {possible_paths}")
    return None

app = FastAPI(title="BG Remover", description="Simple background removal API", version="1.0.0")

@app.get("/")
async def root():
    """Root endpoint with links to main features"""
    return {
        "message": "Image Processing API",
        "version": "1.0.0",
        "endpoints": {
            "blog_cms": "/blog-admin",
            "blog_index": "/blog", 
            "image_tools": "/api/",
            "docs": "/docs"
        }
    }

# Setup logging
# Determine log file path based on environment
log_file_path = os.getenv('LOG_FILE', 'app.log')
# If running in Cloud Run or production, use absolute path in /tmp or current directory
if os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production":
    # In Cloud Run, logs go to stdout/stderr (Cloud Logging), but also log to file if needed
    # Use /tmp for Cloud Run (ephemeral storage) or current directory
    log_file_path = os.path.join(os.getcwd(), 'app.log')
    # Ensure directory exists
    os.makedirs(os.path.dirname(log_file_path) if os.path.dirname(log_file_path) else '.', exist_ok=True)

handlers = [logging.StreamHandler()]  # Always log to stdout/stderr (Cloud Logging in production)
# Add file handler for local development or if explicitly requested
# In Cloud Run, we still log to file if LOG_TO_FILE env var is set (for debugging)
if not os.getenv("K_SERVICE") or os.getenv("LOG_TO_FILE") == "true":
    try:
        handlers.append(logging.FileHandler(log_file_path))
    except Exception as e:
        print(f"Warning: Could not create file handler for {log_file_path}: {e}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)
logger.info(f"Logging initialized. Log file: {log_file_path}, K_SERVICE: {os.getenv('K_SERVICE')}, ENVIRONMENT: {os.getenv('ENVIRONMENT')}")

# Limit heavy CPU tasks to protect memory/CPU. Override via env MAX_CONCURRENCY
PROCESS_SEM = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENCY", "2")))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_NAME_FOR_CATEGORY = {
    "product": os.getenv("DEFAULT_MODEL", "u2netp"),
    "portrait": os.getenv("DEFAULT_MODEL", "u2net_human_seg"),
}

BLOG_BUCKET = os.getenv("BLOG_BUCKET", "")
CRON_TOKEN = os.getenv("CRON_TOKEN", "")
IS_PRODUCTION = os.getenv("K_SERVICE") is not None or os.getenv("ENVIRONMENT") == "production"

# IndexNow configuration
INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "")
INDEXNOW_SITE_DOMAIN = os.getenv("INDEXNOW_SITE_DOMAIN", "")

# Pixabay API configuration
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_API_URL = "https://pixabay.com/api/"

# Resolve path to static frontend assets so blog pages can use site-wide styles in local/dev
FRONTEND_DIR = os.getenv(
    "FRONTEND_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
)

# Minimal static file endpoints used by blog HTML and local tests
@app.get("/styles.css")
async def serve_styles():
    path = os.path.join(FRONTEND_DIR, "styles.css")
    return FileResponse(path, media_type="text/css")


@app.get("/script.js")
async def serve_script():
    path = os.path.join(FRONTEND_DIR, "script.js")
    return FileResponse(path, media_type="application/javascript")


@app.get("/logo.png")
async def serve_logo():
    path = os.path.join(FRONTEND_DIR, "logo.png")
    return FileResponse(path, media_type="image/png")


@app.get("/favicon.ico")
async def serve_favicon():
    # Reuse logo for now if favicon not present
    path = os.path.join(FRONTEND_DIR, "logo.png")
    return FileResponse(path, media_type="image/png")


@app.get("/test-unscribe.html")
async def serve_test_unscribe():
    """Serve local test page for unscribe text removal."""
    path = os.path.join(FRONTEND_DIR, "test-unscribe.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="test-unscribe.html not found")
    return FileResponse(path, media_type="text/html")

_sessions_cache = {}
def downscale_image_if_needed(image: Image.Image, max_side: int = int(os.getenv("MAX_IMAGE_SIDE", "1600"))) -> Image.Image:
    """Downscale very large images to reduce memory/CPU load while preserving aspect ratio."""
    try:
        w, h = image.width, image.height
        longest = max(w, h)
        if longest <= max_side:
            return image
        scale = max_side / float(longest)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        return image.resize((new_w, new_h), Image.LANCZOS)
    except Exception:
        # In case of any issues, return original
        return image

def log_user_action(action: str, details: dict):
    """Log user actions with timestamp and details"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "details": details
    }
    logger.info(f"USER_ACTION: {json.dumps(log_entry)}")

# Background warmup for LaMa to reduce first-hit latency on Remove People
# 
# IMPROVEMENTS MADE TO PERSON REMOVAL QUALITY:
# 1. Upgraded LaMa model to 'big-lama' for better quality
# 2. Added mask preprocessing with Gaussian blur and morphological operations
# 3. Implemented post-processing for better edge blending and color matching
# 4. Enhanced OpenCV fallback with dual algorithm approach (Telea + Navier-Stokes)
# 5. Reduced LaMa threshold to use AI for smaller areas (better quality)
# 6. Added smart blending that adapts based on inpainting method used
@app.on_event("startup")
async def _warmup_lama_background():
    try:
        if os.getenv("MODEL_WARMUP", "true").lower() not in ("1","true","yes"):
            return
        # Warm in a thread to avoid blocking startup
        loop = asyncio.get_event_loop()
        def init_models():
            try:
                if os.getenv("USE_LAMA", "true").lower() in ("1","true","yes"):
                    _ = _get_lama_session()
            except Exception as e:
                logger.warning(f"Warmup failed: {e}")
        loop.run_in_executor(None, init_models)
    except Exception as e:
        logger.warning(f"Warmup scheduling failed: {e}")


# -----------------------------
# Inpainting helpers (exemplar + blending)
# -----------------------------
def _mask_center(binary_mask: np.ndarray) -> tuple:
    try:
        m = cv2.moments(binary_mask, binaryImage=True)
        if m["m00"] > 0:
            cx = int(m["m10"] / m["m00"])
            cy = int(m["m01"] / m["m00"]) 
            return (cx, cy)
    except Exception:
        pass
    h, w = binary_mask.shape[:2]
    return (w // 2, h // 2)

def exemplar_inpaint_patchmatch(bgr_image: np.ndarray, binary_mask: np.ndarray) -> np.ndarray:
    """Try exemplar-based inpainting using OpenCV contrib xphoto Shift-Map.
    If unavailable, returns original image unchanged.
    """
    try:
        if hasattr(cv2, 'xphoto') and hasattr(cv2.xphoto, 'inpaint'):
            dst = bgr_image.copy()
            cv2.xphoto.inpaint(bgr_image, binary_mask, dst, cv2.xphoto.INPAINT_SHIFTMAP)
            return dst
    except Exception as e:
        logger.warning(f"PatchMatch inpaint failed: {e}")
    return bgr_image

# -----------------------------
# Enhanced mask preprocessing
# -----------------------------
def improve_mask_quality(binary_mask: np.ndarray) -> np.ndarray:
    """Improve mask quality for better inpainting results."""
    try:
        # Convert to uint8 if needed
        if binary_mask.dtype != np.uint8:
            binary_mask = (binary_mask > 0).astype(np.uint8) * 255
        
        # Smooth the mask edges with Gaussian blur
        kernel_size = 5
        smoothed_mask = cv2.GaussianBlur(binary_mask, (kernel_size, kernel_size), 0)
        
        # Use morphological operations to clean up the mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        
        # Close small gaps
        smoothed_mask = cv2.morphologyEx(smoothed_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        
        # Dilate slightly to ensure complete coverage
        smoothed_mask = cv2.dilate(smoothed_mask, kernel, iterations=1)
        
        # Final smoothing pass
        smoothed_mask = cv2.GaussianBlur(smoothed_mask, (3, 3), 0)
        
        return smoothed_mask
    except Exception as e:
        logger.warning(f"Mask improvement failed: {e}")
        return binary_mask

def post_process_inpainted_result(original: np.ndarray, inpainted: np.ndarray, mask: np.ndarray, blur_kernel_size: int = 15) -> np.ndarray:
    """Post-process inpainting result for better blending."""
    try:
        # Create a soft mask for blending - use smaller kernel for less aggressive blending
        soft_mask = cv2.GaussianBlur(mask, (blur_kernel_size, blur_kernel_size), 0) / 255.0
        
        # Blend the inpainted result with original at edges
        result = inpainted.copy()
        
        # For edge pixels, blend with original to reduce artifacts
        edge_mask = cv2.Canny(mask, 50, 150)
        edge_dilated = cv2.dilate(edge_mask, np.ones((3, 3), np.uint8), iterations=1)
        
        # Smooth blending at edges - only within mask region
        # Create a strict mask to ensure we only blend where mask exists
        strict_mask = (mask > 0).astype(np.uint8)
        edge_dilated = cv2.bitwise_and(edge_dilated, strict_mask)
        
        # Smooth blending at edges
        for c in range(3):  # For each color channel
            result[:, :, c] = np.where(
                edge_dilated > 0,
                original[:, :, c] * 0.3 + inpainted[:, :, c] * 0.7,
                inpainted[:, :, c]
            )
        
        # Final color matching with surrounding areas
        # Get border pixels from original image
        border_pixels = []
        h, w = mask.shape
        for i in range(h):
            for j in range(w):
                if mask[i, j] > 0 and (
                    i == 0 or i == h-1 or j == 0 or j == w-1 or
                    mask[i-1, j] == 0 or mask[i+1, j] == 0 or
                    mask[i, j-1] == 0 or mask[i, j+1] == 0
                ):
                    border_pixels.append((i, j))
        
        if border_pixels:
            # Calculate average color of border area in original
            border_colors = [original[y, x] for y, x in border_pixels]
            avg_border_color = np.mean(border_colors, axis=0)
            
            # Adjust inpainted area colors to match
            for y, x in border_pixels:
                result[y, x] = 0.8 * result[y, x] + 0.2 * avg_border_color
        
        return result.astype(np.uint8)
    except Exception as e:
        logger.warning(f"Post-processing failed: {e}")
        return inpainted

def post_process_inpainted_result_no_blur(original: np.ndarray, inpainted: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Post-process inpainting result for better blending WITHOUT Gaussian blur on mask."""
    try:
        # Blend the inpainted result with original at edges - NO Gaussian blur on mask
        result = inpainted.copy()
        
        # For edge pixels, blend with original to reduce artifacts
        edge_mask = cv2.Canny(mask, 50, 150)
        edge_dilated = cv2.dilate(edge_mask, np.ones((3, 3), np.uint8), iterations=1)
        
        # Smooth blending at edges - only within mask region
        # Create a strict mask to ensure we only blend where mask exists
        strict_mask = (mask > 0).astype(np.uint8)
        edge_dilated = cv2.bitwise_and(edge_dilated, strict_mask)
        
        # Smooth blending at edges
        for c in range(3):  # For each color channel
            result[:, :, c] = np.where(
                edge_dilated > 0,
                original[:, :, c] * 0.3 + inpainted[:, :, c] * 0.7,
                inpainted[:, :, c]
            )
        
        # Final color matching with surrounding areas
        # Get border pixels from original image
        border_pixels = []
        h, w = mask.shape
        for i in range(h):
            for j in range(w):
                if mask[i, j] > 0 and (
                    i == 0 or i == h-1 or j == 0 or j == w-1 or
                    mask[i-1, j] == 0 or mask[i+1, j] == 0 or
                    mask[i, j-1] == 0 or mask[i, j+1] == 0
                ):
                    border_pixels.append((i, j))
        
        if border_pixels:
            # Calculate average color of border area in original
            border_colors = [original[y, x] for y, x in border_pixels]
            avg_border_color = np.mean(border_colors, axis=0)
            
            # Adjust inpainted area colors to match
            for y, x in border_pixels:
                result[y, x] = 0.8 * result[y, x] + 0.2 * avg_border_color
        
        return result.astype(np.uint8)
    except Exception as e:
        logger.warning(f"Post-processing failed: {e}")
        return inpainted

# -----------------------------
# LaMa ONNX integration (optional)
# -----------------------------
_LAMA_SESSION = None
_LAMA_PROVIDERS = None

def _maybe_download_lama(model_path: str) -> None:
    try:
        url = os.getenv("LAMA_ONNX_URL", "").strip()
        if not url or os.path.exists(model_path):
            return
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        import requests
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        with open(model_path, "wb") as f:
            f.write(r.content)
    except Exception as e:
        logger.warning(f"LaMa ONNX download skipped/failed: {e}")

def _get_lama_session():
    global _LAMA_SESSION, _LAMA_PROVIDERS
    if _LAMA_SESSION is not None:
        return _LAMA_SESSION
    model_path = os.getenv("LAMA_ONNX_PATH", "").strip()
    if not model_path or ort is None:
        return None
    if not os.path.exists(model_path):
        _maybe_download_lama(model_path)
    if not os.path.exists(model_path):
        return None
    try:
        providers = ["CPUExecutionProvider"]
        _LAMA_PROVIDERS = providers
        _LAMA_SESSION = ort.InferenceSession(model_path, providers=providers)
        logger.info(f"LaMa ONNX loaded with providers: {_LAMA_SESSION.get_providers()}")
        return _LAMA_SESSION
    except Exception as e:
        logger.warning(f"Failed to load LaMa ONNX: {e}")
        return None

def lama_inpaint_onnx(bgr_image: np.ndarray, binary_mask: np.ndarray) -> np.ndarray:
    """Attempt LaMa ONNX inference. Returns original on failure."""
    sess = _get_lama_session()
    if sess is None:
        return bgr_image
    
    try:
        # Store original size
        orig_h, orig_w = bgr_image.shape[:2]
        
        # Resize to 512x512 as required by the model
        img_resized = cv2.resize(bgr_image, (512, 512))
        mask_resized = cv2.resize(binary_mask, (512, 512))
        
        # Convert to RGB and normalize to [0,1]
        img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mask_norm = mask_resized.astype(np.float32) / 255.0
        
        # Prepare inputs - the Hugging Face model expects specific input names
        img_input = np.expand_dims(img_rgb.transpose(2, 0, 1), axis=0)  # (1, 3, 512, 512)
        mask_input = np.expand_dims(mask_norm, axis=(0, 1))  # (1, 1, 512, 512)
        
        # Run inference
        logger.info(f"LaMa input shapes: image={img_input.shape}, mask={mask_input.shape}")
        outputs = sess.run(None, {
            "image": img_input,
            "mask": mask_input
        })
        logger.info(f"LaMa output shape: {outputs[0].shape}")
        
        # Convert output back to image - model already outputs [0,255] range
        result = outputs[0][0].transpose(1, 2, 0)  # (512, 512, 3)
        result = np.clip(result, 0, 255).astype(np.uint8)
        result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
        
        # Resize back to original size
        result_final = cv2.resize(result_bgr, (orig_w, orig_h))
        
        return result_final
        
    except Exception as e:
        logger.warning(f"LaMa ONNX inference failed: {e}")
        return bgr_image

# -----------------------------
# LaMa PyTorch (lama-cleaner) integration (lazy)
# -----------------------------
def _get_lama_manager():
    global _LAMA_MANAGER
    if _LAMA_MANAGER is not None:
        return _LAMA_MANAGER
    if ModelManager is None:
        return None
    try:
        # Improved config for better quality
        device = "cpu"
        sd = os.getenv("LAMA_SD", "big-lama")  # Use big-lama for better quality
        # Lazy download on first init handled by ModelManager
        _LAMA_MANAGER = ModelManager(name=sd, device=device)
        logger.info("LaMa PyTorch (lama-cleaner) initialized")
        return _LAMA_MANAGER
    except Exception as e:
        logger.warning(f"Failed to init LaMa (lama-cleaner): {e}")
        return None

def lama_inpaint_torch(bgr_image: np.ndarray, binary_mask: np.ndarray, conservative_blend: bool = False, blur_kernel_size: int = None) -> np.ndarray:
    mgr = _get_lama_manager()
    if mgr is None:
        return bgr_image
    try:
        # Improve mask quality before processing
        improved_mask = improve_mask_quality(binary_mask)
        
        img = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        mask = (improved_mask > 0).astype(np.uint8) * 255
        
        # Use CROP strategy for better quality on large images
        # This crops to the mask area and then resizes back
        hd_strategy = HDStrategy.CROP if improved_mask.shape[0] * improved_mask.shape[1] > 1024*1024 else HDStrategy.ORIGINAL
        
        # Perform inpainting with improved parameters
        res = mgr.inpaint(img, mask, hd_strategy=hd_strategy)
        
        # Post-process for better blending - use custom blur kernel if provided, otherwise use default based on conservative_blend
        if blur_kernel_size is not None:
            blur_kernel = blur_kernel_size
        else:
            blur_kernel = 5 if conservative_blend else 15
        
        # For conservative mode (watermark removal), skip Gaussian blur on mask to prevent foreground blur
        # Use post-processing without the blur step
        if conservative_blend:
            # Skip the Gaussian blur step - use strict mask boundaries only
            result = post_process_inpainted_result_no_blur(bgr_image, cv2.cvtColor(res, cv2.COLOR_RGB2BGR), improved_mask)
        else:
            result = post_process_inpainted_result(bgr_image, cv2.cvtColor(res, cv2.COLOR_RGB2BGR), improved_mask, blur_kernel_size=blur_kernel)
        
        # Apply light sharpening to restore detail if in conservative mode or if custom blur kernel is small
        should_sharpen = conservative_blend or (blur_kernel_size is not None and blur_kernel_size <= 7)
        if should_sharpen:
            # Create a proper sharpening kernel that preserves brightness (sums to 1.0)
            sharpen_kernel = np.array([[0, -0.1, 0],
                                      [-0.1, 1.4, -0.1],
                                      [0, -0.1, 0]])  # Sums to 1.0, preserves brightness
            # Only sharpen the masked area
            mask_3d = (improved_mask > 0).astype(np.float32)[:, :, np.newaxis]
            sharpened = cv2.filter2D(result, -1, sharpen_kernel)
            # Blend: sharpened in mask area, original elsewhere
            result = (mask_3d * sharpened + (1.0 - mask_3d) * result).astype(np.uint8)
        
        return result
    except Exception as e:
        logger.warning(f"LaMa torch inpaint failed, fallback: {e}")
        return bgr_image
    try:
        img = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        mask = (binary_mask > 0).astype(np.uint8) * 255
        h, w = img.shape[:2]
        # LaMa prefers multiples of 8
        pad_h = (8 - h % 8) % 8
        pad_w = (8 - w % 8) % 8
        if pad_h or pad_w:
            img = cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT)
            mask = cv2.copyMakeBorder(mask, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT, value=0)
        inp = img.astype(np.float32) / 255.0
        m = (mask.astype(np.float32) / 255.0)
        # BCHW
        inp = np.transpose(inp, (2, 0, 1))[None, ...]
        m = m[None, None, ...]
        # Find input names heuristically
        inputs = {sess.get_inputs()[0].name: inp, sess.get_inputs()[1].name: m}
        out = sess.run(None, inputs)[0]
        # CHW -> HWC
        out = np.squeeze(out, axis=0)
        out = np.transpose(out, (1, 2, 0))
        out = np.clip(out * 255.0, 0, 255).astype(np.uint8)
        if pad_h or pad_w:
            out = out[:h, :w]
        return cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.warning(f"LaMa ONNX inference failed, falling back: {e}")
        return bgr_image

# -----------------
# Blog helper utils
# -----------------
def get_storage_client():
    if not BLOG_BUCKET:
        raise RuntimeError("BLOG_BUCKET not configured")
    if storage is None:
        raise RuntimeError("google-cloud-storage not installed")
    return storage.Client()

def get_or_create_bucket(client):
    """Return the GCS bucket for BLOG_BUCKET, creating it if it doesn't exist.

    This avoids 404 errors on first run when the bucket hasn't been created yet.
    Location defaults to US; can be overridden via env GCS_BUCKET_LOCATION.
    """
    try:
        bucket = client.lookup_bucket(BLOG_BUCKET)
        if bucket is None:
            location = os.getenv("GCS_BUCKET_LOCATION", "US")
            bucket = storage.Bucket(client, BLOG_BUCKET)
            bucket.location = location
            bucket = client.create_bucket(bucket)
        return bucket
    except Exception as e:
        raise RuntimeError(f"Failed to access or create BLOG_BUCKET '{BLOG_BUCKET}': {str(e)}")

def normalize_slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:120].strip("-") or hashlib.md5(text.encode()).hexdigest()[:12]

def fetch_google_autocomplete(seed: str) -> list:
    try:
        url = "https://suggestqueries.google.com/complete/search"
        resp = requests.get(url, params={"client": "firefox", "q": seed}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [s for s in data[1] if isinstance(s, str)]
    except Exception:
        return []

def fetch_trends_related(seed: str) -> list:
    if TrendReq is None:
        return []
    try:
        pt = TrendReq(hl='en-US', tz=0)
        pt.build_payload([seed], timeframe='today 12-m')
        rel = pt.related_queries()
        out = []
        for v in rel.values():
            for kind in ("top", "rising"):
                df = v.get(kind)
                if df is not None:
                    out.extend(df['query'].tolist()[:10])
        return out
    except Exception:
        return []

def pick_keywords(seeds: list, existing_slugs: set) -> list:
    ideas = []
    for seed in seeds:
        ideas.extend(fetch_google_autocomplete(seed))
        ideas.extend(fetch_trends_related(seed))
    # de-dup and prefer long-tail 3+ words
    uniq = []
    seen = set()
    for k in ideas:
        kk = k.strip().lower()
        if kk and kk not in seen and len(kk.split()) >= 3:
            slug = normalize_slug(kk)
            if slug not in existing_slugs:
                uniq.append((kk, slug))
                seen.add(kk)
    return uniq[:5]

def render_article_html(title: str, slug: str, body_sections: list) -> str:
    # minimal, reuse global CSS and script
    now_iso = datetime.utcnow().isoformat()
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "datePublished": now_iso,
        "author": {"@type": "Organization", "name": "ChangeImageTo.com Team"},
    }
    sections_html = "\n".join(body_sections)
    comprehensive_footer = """<footer class="comprehensive-footer">
      <div class="container">
        <div class="footer-grid">
          <!-- Brand Section -->
          <div class="footer-brand">
            <div class="footer-logo">
              <img src="/logo.png?v=20250921-1" alt="ChangeImageTo" style="height: 32px; margin-bottom: 16px;">
            </div>
            <p class="footer-description">
              The leading platform for free online image editing. Remove backgrounds, change colors, resize images, and enhance photos instantly with AI-powered tools.
            </p>
            <div class="footer-social">
              <a href="https://x.com/vipulawl" aria-label="Follow us on Twitter" class="social-link">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                </svg>
              </a>
              <a href="https://www.youtube.com/@changeimageto" aria-label="Subscribe on YouTube" class="social-link">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                </svg>
              </a>
              <a href="https://linkedin.com/company/make-a-video/" aria-label="Connect on LinkedIn" class="social-link">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                </svg>
              </a>
            </div>
          </div>

          <!-- Tools Section -->
          <div class="footer-section">
            <h3 class="footer-heading">Image Tools</h3>
            <ul class="footer-links">
              <li><a href="/remove-background-from-image.html">Remove Background</a></li>
              <li><a href="/change-image-background.html">Change Background</a></li>
              <li><a href="/change-color-of-image.html">Change Image Colors</a></li>
              <li><a href="/upscale-image.html">Upscale Image</a></li>
              <li><a href="/enhance-image.html">Enhance Image</a></li>
              <li><a href="/blur-background.html">Blur Background</a></li>
              <li><a href="/convert-image-format.html">Convert Image Format</a></li>
              <li><a href="/convert-image-to-pdf.html">Convert Image to PDF</a></li>
              <li><a href="/convert-image-to-text.html">Convert Image to Text</a></li>
              <li><a href="/remove-people-from-photo.html">Remove People / Objects</a></li>
              <li><a href="/remove-text-from-image.html">Remove Text / Watermark</a></li>
              <li><a href="/bulk-image-resizer.html">Bulk Image Resizer</a></li>
              <li><a href="/bulk-resize-images-online.html">Bulk Resize Images Online</a></li>
              <li><a href="/bulk-resize-images-without-losing-quality.html">Bulk Resize Without Losing Quality</a></li>
              <li><a href="/bulk-resize-images-for-ecommerce.html">Bulk Resize for E-commerce</a></li>
              <li><a href="/bulk-resize-images-for-social-media.html">Bulk Resize for Social Media</a></li>
              <li><a href="/image-quality-checker.html">Image Quality Checker</a></li>
              <li><a href="/real-estate-photo-enhancement.html">Real Estate Photo Enhancement</a></li>
            </ul>
          </div>

          <!-- Blog Section -->
          <div class="footer-section">
            <h3 class="footer-heading">Latest Blog Posts</h3>
            <ul class="footer-links">
              <li><a href="/blog/remove-background-from-image.html">Remove Background from Image</a></li>
              <li><a href="/blog/upscale-image.html">Upscale Image Quality</a></li>
              <li><a href="/blog/change-image-background-color.html">Change Image Background Color</a></li>
              <li><a href="/blog/remove-background-from-image-photoshop.html">Remove Background in Photoshop</a></li>
              <li><a href="/blog/change-image-background-color-online.html">Change Background Color Online</a></li>
              <li><a href="/blog/remove-background-from-image-canva.html">Remove Background in Canva</a></li>
              <li><a href="/blog/change-image-background-color-photoshop.html">Change Background in Photoshop</a></li>
              <li><a href="/blog/remove-background-from-image-android.html">Remove Background on Android</a></li>
            </ul>
          </div>

          <!-- Alternatives Section -->
          <div class="footer-section">
            <h3 class="footer-heading">Tool Alternatives</h3>
            <ul class="footer-links">
              <li><a href="/blog/photopea-vs-canva.html">Photopea vs Canva</a></li>
              <li><a href="/blog/canva-vs-photopea.html">Canva vs Photopea</a></li>
              <li><a href="/blog/capcut-vs-davinci-resolve.html">CapCut vs DaVinci Resolve</a></li>
              <li><a href="/blog/free-photoshop-alternatives.html">Free Photoshop Alternatives</a></li>
              <li><a href="/blog/ai-background-removers.html">AI Background Removers</a></li>
            </ul>
          </div>

          <!-- Company Section -->
          <div class="footer-section">
            <h3 class="footer-heading">Company</h3>
            <ul class="footer-links">
              <li><a href="/privacy-policy.html">Privacy Policy</a></li>
              <li><a href="/contact.html">Contact Us</a></li>
              <li><a href="/about.html">About</a></li>
            </ul>
          </div>
        </div>

        <!-- Bottom Bar -->
        <div class="footer-bottom">
          <div class="footer-bottom-content">
            <p class="footer-copyright">
              © 2024 ChangeImageTo.com. All rights reserved. Built for speed and quality.
            </p>
            <div class="footer-bottom-links">
              <a href="/privacy-policy.html">Privacy</a>
              <a href="#terms">Terms</a>
              <a href="#cookies">Cookies</a>
              <a href="#security">Security</a>
            </div>
          </div>
        </div>
      </div>
    </footer>"""
    return f"""<!doctype html><html lang=\"en\"><head>
<meta charset=\"utf-8\"/><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>
<title>{title}</title>
<meta name=\"description\" content=\"{title} – practical guide and tips.\"/>
<link rel=\"canonical\" href=\"https://www.changeimageto.com/blog/{slug}.html\"/>
<script type=\"application/ld+json\">{json.dumps(json_ld)}</script>
<link rel=\"preload\" as=\"style\" href=\"/styles.css?v=20250921-1\"/><link rel=\"stylesheet\" href=\"/styles.css?v=20250921-1\"/>
<style>
  /* Force readable white text on blog articles */
  body, .main, main.container.main, .seo, .seo p, .seo li, .seo h2, .seo h3, .seo details, .seo summary {{ color: var(--fg); }}
  .seo a {{ color: var(--accent); }}
  .seo a:hover {{ text-decoration: underline; }}
  .seo-links a {{ color: var(--fg); }}
  .header h1 {{ color: var(--fg); }}
  .header p {{ color: var(--muted); }}
  .top-nav a {{ color: var(--fg); }}
</style>
</head><body>
<header class=\"container header\"><a href=\"https://www.changeimageto.com/\" class=\"logo-link\"><img src=\"https://www.changeimageto.com/logo.png?v=20250921-1\" alt=\"ChangeImageTo\" class=\"logo-img\" loading=\"eager\" width=\"200\" height=\"68\" /></a><div style=\"display:flex;align-items:center;gap:16px;justify-content:space-between;width:100%\"><h1 style=\"margin:0\">{title}</h1><nav class=\"top-nav\"><a href=\"https://www.changeimageto.com/blog\" aria-label=\"Read our blog\">Blog</a></nav></div></header>
<main class=\"container main\">\n  <p class=\"seo\" style=\"margin:0 0 16px\"><strong>By:</strong> ChangeImageTo.com Team · <time datetime=\"{now_iso}\">{now_iso.replace('T',' ')[:19]} UTC</time></p>\n  {sections_html}\n  <p class=\"seo\" style=\"margin-top:24px\"><a href=\"https://www.changeimageto.com/blog\" style=\"color:#fff\">← Back to blog</a></p>\n</main>
{comprehensive_footer}
<script src=\"/script.js?v=20250921-1\" defer></script>
</body></html>"""

def build_sections(keyword: str) -> list:
    k = keyword.lower()
    sections = []

    def tools_list():
        return (
            "<section class=\"seo\"><h3>Try it online (free)</h3><ul>"
            "<li><a href=\"/remove-background-from-image.html\">Remove background from image</a></li>"
            "<li><a href=\"/change-image-background.html\">Change image background</a></li>"
            "<li><a href=\"/upscale-image.html\">AI Image Upscaler</a></li>"
            "<li><a href=\"/enhance-image.html\">Enhance image quality</a></li>"
            "<li><a href=\"/convert-image-format.html\">Convert image format</a></li>"
            "<li><a href=\"/convert-image-to-pdf.html\">Convert image to PDF</a></li>"
            "<li><a href=\"/convert-image-to-text.html\">Convert image to text (OCR)</a></li>"
            "</ul></section>"
        )

    if "powerpoint" in k:
        sections = [
            "<section class=\"seo\"><p>Yes, you can remove the background of a picture directly in Microsoft PowerPoint. Here is the exact sequence that works in Office 365 and PowerPoint 2019+.</p></section>",
            "<section class=\"seo\"><h2>Remove Background in PowerPoint (exact steps)</h2><ol>"
            "<li>Insert the picture: Insert → Pictures → choose your file.</li>"
            "<li>Select the picture, then go to Picture Format → Remove Background.</li>"
            "<li>Use Mark Areas to Keep/Remove to refine. Zoom in and click along edges.</li>"
            "<li>When satisfied, click Keep Changes. The background becomes transparent.</li>"
            "<li>Export: File → Save As → PNG → check Transparent background when available.</li>"
            "</ol><p>Older versions: the Remove Background button is under Picture Tools → Format.</p></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Tips</h2><ul><li>High-contrast images work best.</li><li>If edges look rough, add a soft shadow or export at higher resolution.</li></ul></section>",
        ]
    elif "photoshop" in k:
        sections = [
            "<section class=\"seo\"><p>This guide shows the precise Photoshop commands to remove the background.</p></section>",
            "<section class=\"seo\"><h2>Photoshop quick method</h2><ol>"
            "<li>Open the image. In Layers, unlock the Background layer.</li>"
            "<li>Select: Select → Subject. Then click Select and Mask.</li>"
            "<li>Refine edges with the Refine Edge Brush, Output: New Layer with Layer Mask.</li>"
            "<li>Hide or delete the background layer. Export: File → Export → Export As → PNG.</li>"
            "</ol></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Keyboard shortcuts</h2><p>Q toggles Quick Mask, Shift+F6 feathers a selection, and ⌘/Ctrl+J duplicates selection to a new layer.</p></section>",
        ]
    elif "iphone" in k or "ios" in k:
        sections = [
            "<section class=\"seo\"><p>On iPhone (iOS 16+), you can lift the subject from the background in Photos without any app.</p></section>",
            "<section class=\"seo\"><h2>Remove background on iPhone</h2><ol>"
            "<li>Open the photo in Photos.</li>"
            "<li>Press and hold the subject until a glow appears, choose Copy or Share → Save Image to export the cutout with a transparent background.</li>"
            "<li>For color backgrounds, upload the cutout to our Change Background tool and pick a color.</li>"
            "</ol></section>",
            tools_list(),
        ]
    elif "android" in k:
        sections = [
            "<section class=\"seo\"><p>Removing backgrounds from images on Android devices is easier than you might think. Here are the best methods using built-in Android features and popular apps.</p></section>",
            "<section class=\"seo\"><h2>Method 1: Google Photos (Built-in)</h2><ol>"
            "<li>Open Google Photos app on your Android device.</li>"
            "<li>Select the image you want to edit.</li>"
            "<li>Tap the \"Edit\" button (pencil icon).</li>"
            "<li>Scroll down and tap \"Magic Eraser\" or \"Background Blur\".</li>"
            "<li>Use your finger to highlight the background areas you want to remove.</li>"
            "<li>Tap \"Done\" and save the edited image.</li>"
            "</ol><p>Note: Magic Eraser is available on Google Pixel devices and newer Android phones with Google Photos.</p></section>",
            "<section class=\"seo\"><h2>Method 2: Samsung Gallery Editor</h2><ol>"
            "<li>Open Samsung Gallery app on your Samsung device.</li>"
            "<li>Select the image and tap \"Edit\".</li>"
            "<li>Look for \"Object Eraser\" or \"Background Eraser\" in the tools.</li>"
            "<li>Draw over the background areas you want to remove.</li>"
            "<li>Tap \"Apply\" and save your changes.</li>"
            "</ol><p>This feature is available on Samsung Galaxy devices with One UI 3.0 or later.</p></section>",
            "<section class=\"seo\"><h2>Method 3: Third-party Apps</h2><ol>"
            "<li><strong>Background Eraser:</strong> Download from Google Play Store, upload image, use auto-detection.</li>"
            "<li><strong>Remove.bg:</strong> Install the app, take or select photo, automatic background removal.</li>"
            "<li><strong>PhotoRoom:</strong> Professional-grade background removal with manual editing tools.</li>"
            "</ol></section>",
            "<section class=\"seo\"><h2>Method 4: Our Free Online Tool (Best Results)</h2><ol>"
            "<li>Open your Android browser and go to our <a href=\"/remove-background-from-image.html\">Remove Background</a> tool.</li>"
            "<li>Upload your image from your phone's gallery.</li>"
            "<li>Wait for automatic processing (5-10 seconds).</li>"
            "<li>Download the result with transparent background.</li>"
            "<li>Save to your Android device's gallery.</li>"
            "</ol><p>This method works on any Android device and produces professional-quality results.</p></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Tips for Android Background Removal</h2><ul>"
            "<li>Use high-resolution images (at least 1080p) for better results.</li>"
            "<li>Ensure good lighting when taking photos for background removal.</li>"
            "<li>Images with clear subject-background contrast work best.</li>"
            "<li>Save results as PNG format to preserve transparency.</li>"
            "<li>Use landscape mode for better editing precision on tablets.</li>"
            "</ul></section>",
            "<section class=\"seo\"><h2>FAQ</h2>"
            "<details><summary>Which Android version supports background removal?</summary><p>Most modern Android devices (Android 8.0+) support basic editing features. Advanced AI background removal requires Android 10+ or specific manufacturer apps.</p></details>"
            "<details><summary>Is it really free on Android?</summary><p>Yes, our online tools are completely free on Android with no watermarks or login required.</p></details>"
            "<details><summary>Best Android app for background removal?</summary><p>Google Photos (Pixel devices), Samsung Gallery (Samsung devices), or our free online tool for any Android device.</p></details>"
            "<details><summary>Can I remove backgrounds from videos on Android?</summary><p>Yes, some apps like CapCut and InShot support video background removal on Android.</p></details>"
            "</section>",
        ]
    elif "google slides" in k or "google-slides" in k or "google" in k and "slides" in k:
        sections = [
            "<section class=\"seo\"><p>This guide shows the exact steps to remove a picture background in Google Slides without third‑party add‑ons. You can mask, crop, and make backgrounds transparent using built‑in tools and a quick detour via Google Drawings.</p></section>",
            "<section class=\"seo\"><h2>Method 1 — Make white background transparent</h2><ol>"
            "<li>Insert the image: Insert → Image → Upload from computer.</li>"
            "<li>Select the image → Format options (right panel) → Adjustments.</li>"
            "<li>Increase Transparency until the white/solid background fades. Fine‑tune Brightness/Contrast to keep the subject visible.</li>"
            "<li>Optional: Add a colored shape behind the image (Insert → Shape) for a new background.</li>"
            "</ol><p>Works best when the background is plain white or a single color.</p></section>",
            "<section class=\"seo\"><h2>Method 2 — Background removal via Google Drawings (free)</h2><ol>"
            "<li>Right‑click the image → Open with → Google Drawings.</li>"
            "<li>In Drawings, use the <em>Format → Format options → Adjustments</em> slider and the <em>Image → Recolor</em> if needed to isolate the subject.</li>"
            "<li>For tighter edges, use the <em>Crop</em> tool with <em>Mask</em> (dropdown next to the crop icon) and choose a shape that matches your subject. Drag the control handles to refine.</li>"
            "<li>File → Download → PNG. This preserves transparency.</li>"
            "<li>Back in Slides, Insert → Image → Upload → choose the PNG you downloaded.</li>"
            "</ol><p>Tip: PNG is required to keep transparent backgrounds.</p></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Method 3 — Quick online cutout (best edges)</h2><ol>"
            "<li>Open our <a href=\"/remove-background-from-image.html\">Remove Background</a> tool.</li>"
            "<li>Upload your picture → download the PNG with transparent background.</li>"
            "<li>Insert the PNG into Google Slides and place a new background (Insert → Image → behind text).</li>"
            "</ol></section>",
            "<section class=\"seo\"><h2>Tips for Slides</h2><ul>"
            "<li>Use high‑resolution images (at least 1600×1200) to avoid pixelation on projectors.</li>"
            "<li>Add a soft shadow (Format options → Drop shadow) to make the cutout blend naturally.</li>"
            "<li>Lock background elements by setting them as the slide background image.</li>"
            "</ul></section>",
        ]
    elif "canva" in k:
        sections = [
            "<section class=\"seo\"><p>Canva has a built-in background remover that works well for simple images. Here's how to use it effectively.</p></section>",
            "<section class=\"seo\"><h2>Remove background in Canva (step-by-step)</h2><ol>"
            "<li>Upload your image to Canva or drag it from the Photos tab.</li>"
            "<li>Select the image and click <strong>Edit image</strong> (or Effects).</li>"
            "<li>Click <strong>Background remover</strong> in the left panel.</li>"
            "<li>Canva will automatically detect and remove the background.</li>"
            "<li>Use <strong>Restore</strong> and <strong>Erase</strong> brushes to refine edges.</li>"
            "<li>Click <strong>Apply</strong> when satisfied.</li>"
            "</ol><p>Note: Background remover is available on Canva Pro ($15/month) or with free trial.</p></section>",
            "<section class=\"seo\"><h2>Free alternative for Canva users</h2><ol>"
            "<li>Use our free <a href=\"/remove-background-from-image.html\">Remove Background</a> tool.</li>"
            "<li>Download the PNG with transparent background.</li>"
            "<li>Upload the PNG to Canva and use it in your designs.</li>"
            "</ol><p>This method is completely free and often produces better results than Canva's built-in tool.</p></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Tips for Canva</h2><ul>"
            "<li>Use high-resolution images (at least 1000px wide) for best results.</li>"
            "<li>Images with clear subject-background contrast work best.</li>"
            "<li>Add a subtle shadow or border to make cutouts blend naturally.</li>"
            "<li>PNG format preserves transparency when downloading.</li>"
            "</ul></section>",
        ]
    elif "free" in k and "online" in k:
        sections = [
            "<section class=\"seo\"><p>Here are completely free, watermark-free ways to remove image backgrounds online without any software installation.</p></section>",
            "<section class=\"seo\"><h2>Free online background removal methods</h2><ol>"
            "<li><strong>Our Remove Background tool</strong> - No login, no watermark, unlimited use.</li>"
            "<li><strong>Google Photos</strong> - Magic Eraser feature (Android/Google One users).</li>"
            "<li><strong>Photopea</strong> - Free Photoshop alternative with background removal.</li>"
            "<li><strong>GIMP</strong> - Free desktop software with powerful selection tools.</li>"
            "</ol></section>",
            "<section class=\"seo\"><h2>Best free online method</h2><ol>"
            "<li>Visit our <a href=\"/remove-background-from-image.html\">Remove Background</a> tool.</li>"
            "<li>Upload your image (JPG, PNG, or WebP up to 10MB).</li>"
            "<li>Wait 5-10 seconds for AI processing.</li>"
            "<li>Download the PNG with transparent background.</li>"
            "</ol><p>This method is completely free, requires no registration, and produces professional results.</p></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Why choose free online tools?</h2><ul>"
            "<li><strong>No software installation</strong> - Works in any web browser.</li>"
            "<li><strong>No watermarks</strong> - Download clean, professional results.</li>"
            "<li><strong>AI-powered</strong> - Automatic detection with manual refinement options.</li>"
            "<li><strong>Multiple formats</strong> - Support for JPG, PNG, and WebP images.</li>"
            "</ul></section>",
        ]
    elif "change" in k and "background" in k and "color" in k and "powerpoint" in k:
        sections = [
            "<section class=\"seo\"><p>PowerPoint offers several ways to change image background colors. Here are the most effective methods for different scenarios.</p></section>",
            "<section class=\"seo\"><h2>Method 1: Change background color of existing image</h2><ol>"
            "<li>Insert your image: Insert → Pictures → choose your file.</li>"
            "<li>Select the image and go to Picture Format → Color.</li>"
            "<li>Choose from preset color options or click Picture Color Options for custom colors.</li>"
            "<li>Adjust Brightness, Contrast, and Saturation as needed.</li>"
            "</ol><p>This method works best for images with solid or simple backgrounds.</p></section>",
            "<section class=\"seo\"><h2>Method 2: Remove background first, then add color</h2><ol>"
            "<li>Select your image → Picture Format → Remove Background.</li>"
            "<li>Use Mark Areas to Keep/Remove to refine the selection.</li>"
            "<li>Click Keep Changes to remove the background.</li>"
            "<li>Insert → Shapes → Rectangle, draw behind the image.</li>"
            "<li>Right-click the shape → Format Shape → Fill → choose your color.</li>"
            "</ol><p>This method gives you complete control over the background color.</p></section>",
            "<section class=\"seo\"><h2>Method 3: Use our online tool (best results)</h2><ol>"
            "<li>Visit our <a href=\"/change-image-background.html\">Change Background</a> tool.</li>"
            "<li>Upload your image and choose a background color.</li>"
            "<li>Download the result and insert it into PowerPoint.</li>"
            "</ol><p>This method produces the cleanest results and works with complex backgrounds.</p></section>",
            tools_list(),
            "<section class=\"seo\"><h2>Tips for PowerPoint backgrounds</h2><ul>"
            "<li>Use high-resolution images (at least 1920×1080) for presentations.</li>"
            "<li>Choose colors that contrast well with your text.</li>"
            "<li>Consider your presentation theme when selecting background colors.</li>"
            "<li>PNG format preserves transparency when importing into PowerPoint.</li>"
            "</ul></section>",
        ]
    elif "free" in k:
        sections = [
            "<section class=\"seo\"><p>Here are free, watermark‑free ways to remove image backgrounds online.</p></section>",
            "<section class=\"seo\"><h2>Completely free methods</h2><ol>"
            "<li>Use our Remove Background tool (no login, no watermark).</li>"
            "<li>For batch images, process one by one to keep best quality.</li>"
            "<li>Download as PNG to preserve transparency.</li>"
            "</ol></section>",
            tools_list(),
        ]
    else:
        sections = [
            f"<section class=\"seo\"><p>This tutorial explains how to {keyword} precisely using built‑in tools and our free online apps.</p></section>",
            "<section class=\"seo\"><h2>Exact steps (online)</h2><ol>"
            "<li>Open the Remove Background tool.</li>"
            "<li>Upload your image (JPG/PNG/WebP). Processing takes ~5–10s.</li>"
            "<li>Download the PNG with transparent background, or change the background color.</li>"
            "</ol></section>",
            tools_list(),
        ]

    sections.append(
        f"<section class=\"seo\"><h2>FAQ</h2><details><summary>Is it really free?</summary><p>Yes, our tools are free with no watermark and no login required.</p></details><details><summary>Best file format?</summary><p>Use PNG to keep transparency; use JPG for photos with solid backgrounds.</p></details></section>"
    )
    return sections

def list_existing_slugs(client) -> set:
    if not BLOG_BUCKET:
        return set()
    try:
        bucket = get_or_create_bucket(client)
        blobs = list(bucket.list_blobs(prefix="blog/"))
        slugs = set()
        for b in blobs:
            if b.name.endswith('.html'):
                s = os.path.basename(b.name).replace('.html','')
                slugs.add(s)
        return slugs
    except Exception:
        return set()

def save_article(client, slug: str, html: str):
    bucket = get_or_create_bucket(client)
    blob = bucket.blob(f"blog/{slug}.html")
    # Keep CDN/browser cache short so edits propagate quickly
    blob.cache_control = "public, max-age=600"
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")
    # update index json (append latest)
    idx = bucket.blob("blog/index.json")
    try:
        existing = json.loads(idx.download_as_text())
    except Exception:
        existing = {"posts": []}
    url = f"/blog/{slug}.html"
    existing["posts"] = ([{"slug": slug, "title": html.split('<title>')[1].split('</title>')[0], "url": url, "date": datetime.utcnow().isoformat()}] 
                           + [p for p in existing.get("posts", []) if p.get("slug") != slug])[:200]
    idx.upload_from_string(json.dumps(existing), content_type="application/json")

@app.post("/api/blog/regenerate-all")
async def blog_regenerate_all(token: str):
    """Re-render and overwrite all existing blog posts using current templates/content.

    Requires CRON_TOKEN for authorization.
    """
    if CRON_TOKEN and token != CRON_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not BLOG_BUCKET:
        raise HTTPException(status_code=400, detail="BLOG_BUCKET not configured in production")
    client = get_storage_client()
    bucket = get_or_create_bucket(client)
    # Determine slugs from index.json, fallback to listing blobs
    slugs: set[str] = set()
    try:
        idx = bucket.blob("blog/index.json")
        data = json.loads(idx.download_as_text())
        for p in data.get("posts", []):
            s = p.get("slug")
            if s:
                slugs.add(s)
    except Exception:
        pass
    if not slugs:
        for b in bucket.list_blobs(prefix="blog/"):
            if b.name.endswith(".html"):
                slugs.add(os.path.basename(b.name).replace(".html", ""))
    regenerated = []
    for slug in sorted(slugs):
        title = slug.replace('-', ' ').title()
        html = render_article_html(title, slug, build_sections(title))
        save_article(client, slug, html)
        regenerated.append(slug)
    return {"regenerated": regenerated, "count": len(regenerated)}

@app.post("/api/blog/regenerate-one")
async def blog_regenerate_one(slug: str, token: str):
    if CRON_TOKEN and token != CRON_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not BLOG_BUCKET:
        raise HTTPException(status_code=400, detail="BLOG_BUCKET not configured in production")
    client = get_storage_client()
    title = slug.replace('-', ' ').title()
    html = render_article_html(title, slug, build_sections(title))
    save_article(client, slug, html)
    return {"ok": True, "slug": slug}

def get_session_for_category(category: str):
    category = category.lower()
    model_name = MODEL_NAME_FOR_CATEGORY.get(category)
    if not model_name:
        raise HTTPException(status_code=400, detail=f"Unsupported category: {category}")
    if model_name not in _sessions_cache:
        _sessions_cache[model_name] = new_session(model_name)
    return _sessions_cache[model_name]


@app.on_event("startup")
async def warm_models_on_startup():
    """Kick off model session initialization in the background so the server
    can bind to the port immediately and avoid Render's port scan timeout.
    """
    try:
        # Run in threads to avoid blocking the event loop
        asyncio.create_task(asyncio.to_thread(get_session_for_category, "product"))
        asyncio.create_task(asyncio.to_thread(get_session_for_category, "portrait"))
        logger.info("Background model warmup tasks started")
    except Exception as e:
        logger.warning(f"Failed to start background warmup: {e}")

@app.post("/api/remove-bg")
async def remove_bg(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("product"),
    bg_color: Optional[str] = Form(None),
    background_image: Optional[UploadFile] = File(None),
    foreground_x: Optional[float] = Form(None),
    foreground_y: Optional[float] = Form(None),
    foreground_scale: Optional[float] = Form(None),
    background_blur: Optional[float] = Form(None),
):
    # Log the request start
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("request_started", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
        "content_type": file.content_type,
        "category": category,
        "bg_color": bg_color,
        "has_bg_color": bg_color is not None,
        "has_background_image": background_image is not None
    })

    try:
        session = get_session_for_category(category)
    except HTTPException as e:
        log_user_action("error", {
            "error_type": "model_init",
            "error_message": str(e.detail),
            "category": category
        })
        raise
    except Exception as e:
        log_user_action("error", {
            "error_type": "model_init_unexpected",
            "error_message": str(e),
            "category": category
        })
        raise HTTPException(status_code=500, detail=f"Model init error: {str(e)}")

    if not file.content_type or not file.content_type.startswith("image/"):
        log_user_action("error", {
            "error_type": "invalid_file_type",
            "content_type": file.content_type,
            "filename": file.filename
        })
        raise HTTPException(status_code=400, detail="File must be an image")

    try:
        contents = await file.read()
        file_size = len(contents)
        # Open image - rembg works best with RGB input, not RGBA
        # rembg will return RGBA with transparency, but it expects RGB input
        image = Image.open(io.BytesIO(contents))
        # Convert to RGB for rembg processing (rembg will add alpha channel)
        if image.mode != "RGB":
            if image.mode == "RGBA":
                # If input has transparency, convert to RGB (rembg will recreate transparency)
                image = image.convert("RGB")
            else:
                # Convert other modes to RGB
                image = image.convert("RGB")
        original_size = (image.width, image.height)
        
        # Log successful file processing
        log_user_action("file_processed", {
            "filename": file.filename,
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "image_dimensions": f"{image.width}x{image.height}",
            "category": category
        })
        
    except Exception as e:
        log_user_action("error", {
            "error_type": "file_processing",
            "error_message": str(e),
            "filename": file.filename,
            "file_size": len(contents) if 'contents' in locals() else 0
        })
        raise HTTPException(status_code=400, detail="Invalid image file")

    try:
        # Log processing start
        log_user_action("processing_started", {
            "category": category,
            "model": MODEL_NAME_FOR_CATEGORY.get(category.lower()),
            "bg_color": bg_color,
            "action_type": "change_background" if bg_color else "remove_background"
        })

        # Process with rembg - EXACT SAME CODE AS TEST ENDPOINT
        proc_image = downscale_image_if_needed(image)
        
        async with PROCESS_SEM:
            result = remove(
                proc_image,
                session=session,
                alpha_matting=True,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
                alpha_matting_erode_size=10,
                post_process_mask=True,
            )
        
        # For transparent output, use EXACT TEST ENDPOINT CODE
        if not background_image and not bg_color:
            # EXACT COPY FROM TEST ENDPOINT - return JSON with base64 like test endpoint
            output_io = io.BytesIO()
            if result.mode != "RGBA":
                result = result.convert("RGBA")
            result.save(output_io, format="PNG")
            output_bytes = output_io.getvalue()
            
            log_user_action("processing_completed", {
                "category": category,
                "action_type": "remove_background",
                "output_size_bytes": len(output_bytes),
                "processing_successful": True
            })
            
            # Return JSON with base64 EXACTLY like test endpoint
            import base64
            return {
                "test_image_base64": "data:image/png;base64," + base64.b64encode(output_bytes).decode(),
            }
        
        # Only do trimming/compositing if we have a background
        trimmed_result = result
        trim_offset_x = 0
        trim_offset_y = 0
        
        # Calculate scale factor if image was downscaled for processing
        proc_size = result.size
        scale_factor_x = original_size[0] / proc_size[0] if proc_size[0] > 0 else 1.0
        scale_factor_y = original_size[1] / proc_size[1] if proc_size[1] > 0 else 1.0
        
        if background_image or bg_color:
            try:
                alpha_channel = result.split()[-1]
                trim_bbox = alpha_channel.getbbox()
                if trim_bbox and trim_bbox != (0, 0, result.width, result.height):
                    trim_offset_x = trim_bbox[0]
                    trim_offset_y = trim_bbox[1]
                    trimmed_result = result.crop(trim_bbox)
                    
                    # Scale trim offsets to original size
                    trim_offset_x = int(trim_offset_x * scale_factor_x)
                    trim_offset_y = int(trim_offset_y * scale_factor_y)
                    
                    # Scale trimmed result back to original size
                    if scale_factor_x != 1.0 or scale_factor_y != 1.0:
                        new_trimmed_w = int(trimmed_result.width * scale_factor_x)
                        new_trimmed_h = int(trimmed_result.height * scale_factor_y)
                        trimmed_result = trimmed_result.resize((new_trimmed_w, new_trimmed_h), Image.LANCZOS)
                else:
                    # No trimming needed, but still scale result back to original size if it was downscaled
                    if scale_factor_x != 1.0 or scale_factor_y != 1.0:
                        trimmed_result = trimmed_result.resize(original_size, Image.LANCZOS)
            except Exception:
                # If trimming fails, still scale result back to original size if it was downscaled
                if scale_factor_x != 1.0 or scale_factor_y != 1.0:
                    trimmed_result = trimmed_result.resize(original_size, Image.LANCZOS)
            
        # Optional background compositing (image or solid color)
        if background_image:
            # Use background image
            try:
                bg_contents = await background_image.read()
                bg_image = Image.open(io.BytesIO(bg_contents)).convert("RGBA")
                
                # Resize background to match original image dimensions (cover mode - maintain aspect ratio, fill space)
                bg_w, bg_h = bg_image.size
                target_w, target_h = original_size
                
                # Calculate scaling to cover (maintain aspect ratio, fill entire space)
                scale_w = target_w / bg_w
                scale_h = target_h / bg_h
                scale = max(scale_w, scale_h)  # Use larger scale to ensure coverage
                
                new_bg_w = int(bg_w * scale)
                new_bg_h = int(bg_h * scale)
                
                # Resize background image
                bg_image = bg_image.resize((new_bg_w, new_bg_h), Image.LANCZOS)
                
                # Crop to exact target size (center crop)
                left = (new_bg_w - target_w) // 2
                top = (new_bg_h - target_h) // 2
                bg_image = bg_image.crop((left, top, left + target_w, top + target_h))
                
                # Apply blur to background if requested
                if background_blur and background_blur > 0:
                    bg_image = bg_image.convert("RGB")  # Convert to RGB for blur filter
                    bg_image = bg_image.filter(ImageFilter.GaussianBlur(radius=float(background_blur)))
                    bg_image = bg_image.convert("RGBA")  # Convert back to RGBA
                
                # Composite foreground onto background with position and scale
                # Apply scale if provided
                if foreground_scale and foreground_scale != 1.0:
                    new_width = int(trimmed_result.width * foreground_scale)
                    new_height = int(trimmed_result.height * foreground_scale)
                    trimmed_result = trimmed_result.resize((new_width, new_height), Image.LANCZOS)
                
                # Calculate position
                if foreground_x is not None and foreground_y is not None:
                    # Position is relative to center (as sent from frontend)
                    offset_x = int((bg_image.width / 2) + foreground_x - (trimmed_result.width / 2))
                    offset_y = int((bg_image.height / 2) + foreground_y - (trimmed_result.height / 2))
                else:
                    # Default: preserve original position instead of centering
                    # Scale the trim offset to match the original size (bg_image is already resized to original_size)
                    offset_x = trim_offset_x
                    offset_y = trim_offset_y
                
                # Ensure position is within bounds
                offset_x = max(0, min(offset_x, bg_image.width - trimmed_result.width))
                offset_y = max(0, min(offset_y, bg_image.height - trimmed_result.height))
                
                bg_image.paste(trimmed_result, (offset_x, offset_y), mask=trimmed_result.split()[-1])
                result = bg_image
                
                log_user_action("background_image_applied", {
                    "bg_image_size": f"{bg_image.width}x{bg_image.height}",
                    "original_size": f"{original_size[0]}x{original_size[1]}"
                })
            except Exception as e:
                log_user_action("error", {
                    "error_type": "background_image_processing",
                    "error_message": str(e)
                })
                # Fallback to transparent if background image fails
                # Preserve original position instead of centering
                canvas = Image.new('RGBA', original_size, (0, 0, 0, 0))
                offset_x = trim_offset_x
                offset_y = trim_offset_y
                canvas.paste(trimmed_result, (offset_x, offset_y), mask=trimmed_result.split()[-1])
                result = canvas
        elif bg_color:
            # Use solid background color
            log_user_action("bg_color_debug", {
                "bg_color_raw": bg_color,
                "bg_color_type": type(bg_color).__name__,
                "bg_color_repr": repr(bg_color)
            })
            col = bg_color.strip()
            if col.startswith('#'):
                col = col[1:]
            if len(col) in (3,4):
                col = ''.join(c*2 for c in col[:3])
            if len(col) != 6:
                log_user_action("error", {
                    "error_type": "invalid_bg_color",
                    "bg_color": bg_color,
                    "processed_color": col
                })
                raise HTTPException(status_code=400, detail="Invalid bg_color; use hex like #ffffff")
            r = int(col[0:2], 16); g = int(col[2:4], 16); b = int(col[4:6], 16)
            # Ensure output keeps the original input dimensions
            canvas = Image.new('RGBA', original_size, (r, g, b, 255))
            
            # Apply scale if provided
            if foreground_scale and foreground_scale != 1.0:
                new_width = int(trimmed_result.width * foreground_scale)
                new_height = int(trimmed_result.height * foreground_scale)
                trimmed_result = trimmed_result.resize((new_width, new_height), Image.LANCZOS)
            
            # Calculate position
            if foreground_x is not None and foreground_y is not None:
                # Position is relative to center (as sent from frontend)
                offset_x = int((canvas.width / 2) + foreground_x - (trimmed_result.width / 2))
                offset_y = int((canvas.height / 2) + foreground_y - (trimmed_result.height / 2))
            else:
                # Default: preserve original position instead of centering
                offset_x = trim_offset_x
                offset_y = trim_offset_y
            
            # Ensure position is within bounds
            offset_x = max(0, min(offset_x, canvas.width - trimmed_result.width))
            offset_y = max(0, min(offset_y, canvas.height - trimmed_result.height))
            
            canvas.paste(trimmed_result, (offset_x, offset_y), mask=trimmed_result.split()[-1])
            result = canvas
        # If we reach here, we have a background (bg_color or background_image)
        # Canvas creation only happens for backgrounds - transparent output already returned above
        output_io = io.BytesIO()
        if result.mode != "RGBA":
            result = result.convert("RGBA")
        result.save(output_io, format="PNG")
        output_bytes = output_io.getvalue()
        
        # Log for debugging
        log_user_action("output_prepared", {
            "result_mode": result.mode,
            "result_size": f"{result.width}x{result.height}",
            "original_size": f"{original_size[0]}x{original_size[1]}",
            "output_bytes": len(output_bytes),
            "has_bg_color": bg_color is not None,
            "has_background_image": background_image is not None
        })
        
        # Log successful processing
        log_user_action("processing_completed", {
            "category": category,
            "bg_color": bg_color,
            "action_type": "change_background" if bg_color else "remove_background",
            "output_size_bytes": len(output_bytes),
            "output_size_mb": round(len(output_bytes) / (1024 * 1024), 2),
            "processing_successful": True
        })
        
        return Response(content=output_bytes, media_type="image/png")
        
    except Exception as e:
        log_user_action("error", {
            "error_type": "processing_error",
            "error_message": str(e),
            "category": category,
            "bg_color": bg_color,
            "action_type": "change_background" if bg_color else "remove_background"
        })
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")


@app.post("/api/test-rembg")
async def test_rembg(file: UploadFile = File(...)):
    """
    Test endpoint to check what rembg.remove() actually returns
    Returns JSON with analysis of the rembg output
    """
    import numpy as np
    
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents))
        
        # Convert to RGB for rembg
        if image.mode != "RGB":
            image = image.convert("RGB")
        
        original_size = image.size
        
        # Process with rembg (same as production)
        session = get_session_for_category("product")
        proc_image = downscale_image_if_needed(image)
        
        async with PROCESS_SEM:
            result = remove(
                proc_image,
                session=session,
                alpha_matting=True,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
                alpha_matting_erode_size=10,
                post_process_mask=True,
            )
        
        # Analyze what rembg returned
        analysis = {
            "rembg_returned_mode": result.mode,
            "rembg_returned_size": f"{result.width}x{result.height}",
            "original_size": f"{original_size[0]}x{original_size[1]}",
            "was_downscaled": proc_image.size != original_size,
        }
        
        if result.mode == "RGBA":
            alpha = np.array(result.split()[-1])
            rgb_array = np.array(result)[:, :, :3]
            alpha_array = np.array(result)[:, :, 3]
            
            analysis["alpha_channel"] = {
                "min": int(alpha.min()),
                "max": int(alpha.max()),
                "mean": float(alpha.mean()),
                "transparent_pixels": int(np.sum(alpha < 10)),
                "opaque_pixels": int(np.sum(alpha > 250)),
            }
            
            # Check for white opaque pixels
            white_threshold = 240
            is_white = np.all(rgb_array > white_threshold, axis=2)
            is_opaque = alpha_array > 250
            opaque_white = is_white & is_opaque
            white_count = int(np.sum(opaque_white))
            
            analysis["white_background_check"] = {
                "opaque_white_pixels": white_count,
                "percentage": float(white_count / alpha.size * 100),
                "has_white_background": white_count > 0,
            }
            
            # Check edges (usually background)
            h, w = alpha_array.shape
            edge_width = min(20, w//10, h//10)
            edge_mask = np.zeros((h, w), dtype=bool)
            edge_mask[:edge_width, :] = True
            edge_mask[-edge_width:, :] = True
            edge_mask[:, :edge_width] = True
            edge_mask[:, -edge_width:] = True
            
            edge_alpha = alpha_array[edge_mask]
            edge_rgb = rgb_array[edge_mask]
            edge_is_white = np.all(edge_rgb > white_threshold, axis=1)
            edge_is_opaque = edge_alpha > 250
            
            analysis["edge_analysis"] = {
                "edge_pixels": int(np.sum(edge_mask)),
                "edge_white_pixels": int(np.sum(edge_is_white)),
                "edge_opaque_pixels": int(np.sum(edge_is_opaque)),
                "edge_opaque_white": int(np.sum(edge_is_white & edge_is_opaque)),
                "edge_mean_alpha": float(edge_alpha.mean()),
                "edge_mean_rgb": [float(edge_rgb[:, i].mean()) for i in range(3)],
            }
        elif result.mode == "RGB":
            analysis["warning"] = "rembg returned RGB instead of RGBA - no transparency!"
            rgb_array = np.array(result)
            white_pixels = np.all(rgb_array > 240, axis=2)
            analysis["white_background_check"] = {
                "white_pixels": int(np.sum(white_pixels)),
                "percentage": float(np.sum(white_pixels) / white_pixels.size * 100),
            }
        
        # Also save a test output image
        output_io = io.BytesIO()
        if result.mode != "RGBA":
            result = result.convert("RGBA")
        result.save(output_io, format="PNG")
        output_bytes = output_io.getvalue()
        
        analysis["output_png_size_bytes"] = len(output_bytes)
        
        import base64
        return {
            "analysis": analysis,
            "test_image_base64": "data:image/png;base64," + base64.b64encode(output_bytes).decode(),
        }
        
    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}


@app.post("/api/change-color")
async def change_color(
    request: Request,
    file: UploadFile = File(...),
    color_type: str = Form("hue"),
    hue_shift: float = Form(0),
    saturation: float = Form(100),
    brightness: float = Form(100),
    contrast: float = Form(100),
):
    """Change colors in an image"""
    # Log the request start
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("color_change_request_started", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
        "content_type": file.content_type,
        "color_type": color_type,
        "hue_shift": hue_shift,
        "saturation": saturation,
        "brightness": brightness,
        "contrast": contrast
    })

    if not file.content_type or not file.content_type.startswith("image/"):
        log_user_action("error", {
            "error_type": "invalid_file_type_color_change",
            "content_type": file.content_type,
            "filename": file.filename
        })
        raise HTTPException(status_code=400, detail="File must be an image")

    try:
        contents = await file.read()
        file_size = len(contents)
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        
        # Log successful file processing
        log_user_action("color_change_file_processed", {
            "filename": file.filename,
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "image_dimensions": f"{image.width}x{image.height}",
            "color_type": color_type
        })
        
    except Exception as e:
        log_user_action("error", {
            "error_type": "color_change_file_processing",
            "error_message": str(e),
            "filename": file.filename,
            "file_size": len(contents) if 'contents' in locals() else 0
        })
        raise HTTPException(status_code=400, detail="Invalid image file")

    try:
        # Log processing start
        log_user_action("color_change_processing_started", {
            "color_type": color_type,
            "hue_shift": hue_shift,
            "saturation": saturation,
            "brightness": brightness,
            "contrast": contrast
        })
        
        # Downscale first to reduce memory, then apply color changes
        image = downscale_image_if_needed(image)
        async with PROCESS_SEM:
            # Apply color changes based on type
            result = apply_color_changes(image, color_type, hue_shift, saturation, brightness, contrast)
        
        output_io = io.BytesIO()
        result.save(output_io, format="PNG")
        output_bytes = output_io.getvalue()
        
        # Log successful processing
        log_user_action("color_change_processing_completed", {
            "color_type": color_type,
            "hue_shift": hue_shift,
            "saturation": saturation,
            "brightness": brightness,
            "contrast": contrast,
            "output_size_bytes": len(output_bytes),
            "output_size_mb": round(len(output_bytes) / (1024 * 1024), 2),
            "processing_successful": True
        })
        
        return Response(content=output_bytes, media_type="image/png")
        
    except Exception as e:
        log_user_action("error", {
            "error_type": "color_change_processing_error",
            "error_message": str(e),
            "color_type": color_type,
            "hue_shift": hue_shift,
            "saturation": saturation,
            "brightness": brightness,
            "contrast": contrast
        })
        raise HTTPException(status_code=500, detail=f"Color change processing error: {str(e)}")


def apply_color_changes(image: Image.Image, color_type: str, hue_shift: float, saturation: float, brightness: float, contrast: float) -> Image.Image:
    """Apply color changes to an image"""
    result = image.copy()
    
    if color_type == "grayscale":
        # Convert to grayscale
        result = ImageOps.grayscale(result)
        result = result.convert("RGB")
    else:
        # Apply HSV adjustments
        if hue_shift != 0:
            result = apply_hue_shift(result, hue_shift)
        
        # Apply saturation
        if saturation != 100:
            enhancer = ImageEnhance.Color(result)
            result = enhancer.enhance(saturation / 100)
        
        # Apply brightness
        if brightness != 100:
            enhancer = ImageEnhance.Brightness(result)
            result = enhancer.enhance(brightness / 100)
        
        # Apply contrast
        if contrast != 100:
            enhancer = ImageEnhance.Contrast(result)
            result = enhancer.enhance(contrast / 100)
    
    return result


def apply_hue_shift(image: Image.Image, hue_shift: float) -> Image.Image:
    """Apply hue shift to an image"""
    # Convert to HSV
    hsv = image.convert("HSV")
    hsv_array = np.array(hsv)
    
    # Shift hue
    hsv_array[:, :, 0] = (hsv_array[:, :, 0] + hue_shift) % 360
    
    # Convert back to RGB
    result = Image.fromarray(hsv_array, "HSV").convert("RGB")
    return result


@app.post("/api/analytics")
async def log_analytics(request: Request):
    """Receive analytics data from frontend"""
    try:
        data = await request.json()
        log_user_action("frontend_analytics", data)
        return {"status": "logged"}
    except Exception as e:
        logger.error(f"Analytics logging error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/api/feedback")
async def submit_feedback(request: Request):
    """Store user feedback"""
    try:
        data = await request.json()
        rating = data.get('rating')
        comment = data.get('comment', '')
        page = data.get('page', '')
        operation = data.get('operation', '')
        user_agent = data.get('userAgent', '')
        
        if not rating or rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="Invalid rating")
        
        # Store in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_feedback (rating, comment, page, operation, user_agent)
            VALUES (?, ?, ?, ?, ?)
        ''', (rating, comment, page, operation, user_agent))
        
        # Log impression as submitted
        try:
            cursor.execute('''
                INSERT INTO feedback_impressions (page, operation, user_agent, action)
                VALUES (?, ?, ?, 'submitted')
            ''', (page, operation, user_agent))
        except Exception as e:
            logger.warning(f"Failed to log feedback impression: {e}")
        
        # Save all changes and sync to Cloud Storage
        save_db_changes(conn)
        conn.close()
        
        # Also log for analytics
        log_user_action("user_feedback", {
            "rating": rating,
            "has_comment": bool(comment),
            "comment_length": len(comment) if comment else 0,
            "page": page,
            "operation": operation
        })
        
        return {"status": "success", "message": "Feedback submitted"}
    except Exception as e:
        logger.error(f"Feedback submission error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/api/whats-missing")
async def submit_whats_missing(request: Request):
    """Store 'what's missing' feedback from users"""
    try:
        data = await request.json()
        feedback_text = data.get('feedback', '').strip()
        page = data.get('page', '')
        user_agent = data.get('userAgent', '')
        
        if not feedback_text:
            raise HTTPException(status_code=400, detail="Feedback text is required")
        
        # Store in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO whats_missing_feedback (feedback_text, page, user_agent)
            VALUES (?, ?, ?)
        ''', (feedback_text, page, user_agent))
        save_db_changes(conn)
        conn.close()
        
        # Also log for analytics
        log_user_action("whats_missing_feedback", {
            "has_feedback": bool(feedback_text),
            "feedback_length": len(feedback_text),
            "page": page
        })
        
        return {"status": "success", "message": "Feedback submitted"}
    except Exception as e:
        logger.error(f"What's missing feedback error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/api/christmas-backgrounds")
async def get_christmas_backgrounds():
    """Fetch Christmas background images from Pixabay API"""
    if not PIXABAY_API_KEY:
        # Return placeholder data if API key not configured
        return {
            "success": False,
            "message": "Pixabay API not configured",
            "backgrounds": []
        }
    
    try:
        # Search for Christmas backgrounds
        params = {
            "key": PIXABAY_API_KEY,
            "q": "christmas background",
            "image_type": "photo",
            "category": "backgrounds",
            "orientation": "all",
            "safesearch": "true",
            "per_page": 20,
            "min_width": 1920,
            "min_height": 1080
        }
        
        response = requests.get(PIXABAY_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Format results for frontend
        backgrounds = []
        for hit in data.get("hits", [])[:10]:  # Limit to 10 backgrounds
            backgrounds.append({
                "id": hit.get("id"),
                "preview_url": hit.get("previewURL"),
                "webformat_url": hit.get("webformatURL"),
                "large_image_url": hit.get("largeImageURL"),
                "full_hd_url": hit.get("fullHDURL") or hit.get("largeImageURL"),
                "tags": hit.get("tags", ""),
                "user": hit.get("user", "")
            })
        
        return {
            "success": True,
            "backgrounds": backgrounds
        }
        
    except Exception as e:
        logger.error(f"Pixabay API error: {str(e)}")
        return {
            "success": False,
            "message": str(e),
            "backgrounds": []
        }

@app.get("/api/whats-missing")
async def get_whats_missing(limit: int = 50):
    """Get 'what's missing' feedback entries"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='whats_missing_feedback'")
        if not cursor.fetchone():
            conn.close()
            return {"count": 0, "feedback": [], "message": "No feedback table found yet"}
        
        cursor.execute('''
            SELECT id, feedback_text, page, user_agent, created_at
            FROM whats_missing_feedback
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))
        
        feedback_list = []
        for row in cursor.fetchall():
            feedback_list.append({
                "id": row[0],
                "feedback": row[1],
                "page": row[2] or "",
                "user_agent": row[3] or "",
                "created_at": row[4]
            })
        
        cursor.execute("SELECT COUNT(*) FROM whats_missing_feedback")
        total_count = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "count": total_count,
            "returned": len(feedback_list),
            "feedback": feedback_list
        }
    except Exception as e:
        logger.error(f"What's missing retrieval error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/api/feedback/impression")
async def log_feedback_impression(request: Request):
    """Log when feedback modal is shown or interacted with"""
    try:
        data = await request.json()
        page = data.get('page', '')
        operation = data.get('operation', '')
        user_agent = data.get('userAgent', '')
        action = data.get('action', 'shown')  # 'shown', 'submitted', 'skipped', 'closed'
        
        # Store impression
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO feedback_impressions (page, operation, user_agent, action)
            VALUES (?, ?, ?, ?)
        ''', (page, operation, user_agent, action))
        save_db_changes(conn)
        conn.close()
        
        # Also log for analytics
        log_user_action("feedback_impression", {
            "action": action,
            "page": page,
            "operation": operation
        })
        
        return {"status": "success", "message": "Impression logged"}
    except Exception as e:
        logger.error(f"Feedback impression logging error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/api/feedback")
async def get_feedback(limit: int = 50, include_stats: bool = True):
    """Get user feedback entries and statistics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_feedback'")
        feedback_table_exists = cursor.fetchone() is not None
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_impressions'")
        impressions_table_exists = cursor.fetchone() is not None
        
        if not feedback_table_exists:
            conn.close()
            return {"count": 0, "feedback": [], "message": "No feedback table found yet"}
        
        # Get feedback entries
        cursor.execute('''
            SELECT id, rating, comment, page, operation, user_agent, created_at
            FROM user_feedback
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))
        
        feedback_list = []
        for row in cursor.fetchall():
            feedback_list.append({
                "id": row[0],
                "rating": row[1],
                "comment": row[2] or "",
                "page": row[3] or "",
                "operation": row[4] or "",
                "user_agent": row[5] or "",
                "created_at": row[6]
            })
        
        cursor.execute("SELECT COUNT(*) FROM user_feedback")
        total_count = cursor.fetchone()[0]
        
        stats = {}
        if include_stats and impressions_table_exists:
            # Get impression statistics
            cursor.execute("SELECT COUNT(*) FROM feedback_impressions WHERE action='shown'")
            impressions_shown = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM feedback_impressions WHERE action='submitted'")
            impressions_submitted = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM feedback_impressions WHERE action='skipped'")
            impressions_skipped = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM feedback_impressions WHERE action='closed'")
            impressions_closed = cursor.fetchone()[0]
            
            # Calculate conversion rate
            conversion_rate = (impressions_submitted / impressions_shown * 100) if impressions_shown > 0 else 0
            
            stats = {
                "impressions_shown": impressions_shown,
                "impressions_submitted": impressions_submitted,
                "impressions_skipped": impressions_skipped,
                "impressions_closed": impressions_closed,
                "conversion_rate": round(conversion_rate, 2)
            }
        
        conn.close()
        
        result = {
            "count": total_count,
            "returned": len(feedback_list),
            "feedback": feedback_list
        }
        
        if stats:
            result["stats"] = stats
        
        return result
    except Exception as e:
        logger.error(f"Feedback retrieval error: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


@app.head("/")
async def head_root():
    return Response(status_code=200)


# ----------------------------
# Image format conversion API
# ----------------------------
@app.post("/api/convert-format")
async def convert_format(
    request: Request,
    file: UploadFile = File(...),
    target_format: str = Form("png"),
    transparent: bool = Form(False),
    quality: Optional[int] = Form(90),
):
    """Convert an uploaded image to a different format.
    - target_format: png | jpg | jpeg | webp
    - transparent: keep/force transparency if supported by target format (PNG/WEBP)
    - quality: 1-100 for lossy formats
    """
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")

    target = (target_format or "png").lower()
    if target == "jpeg":
        target = "jpg"
    logger.info(f"Convert format request: target_format={target_format}, normalized_target={target}, filename={file.filename}")
    supported_targets = {"png", "jpg", "webp", "bmp", "gif", "tiff", "ico", "ppm", "pgm"}
    if target not in supported_targets:
        raise HTTPException(status_code=400, detail=f"Unsupported target_format. Use one of: {sorted(supported_targets)}")

    try:
        contents = await file.read()
        # Check if input is SVG
        if file.filename and file.filename.lower().endswith('.svg'):
            # Handle SVG input - convert SVG to raster first if target is not SVG
            if target == "svg":
                # SVG to SVG - just return as-is (or validate)
                return Response(content=contents, media_type="image/svg+xml")
            else:
                # SVG to raster - need to render SVG first
                try:
                    from PIL import Image as PILImage
                    import xml.etree.ElementTree as ET
                    # Try to parse and render SVG (basic approach)
                    # For production, consider using cairosvg or similar
                    raise HTTPException(status_code=400, detail="SVG to raster conversion requires additional libraries. Please convert SVG to PNG first using another tool.")
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"SVG input processing error: {str(e)}")
        image = Image.open(io.BytesIO(contents))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file")

    log_user_action("convert_request_started", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
        "content_type": file.content_type,
        "target_format": target,
        "transparent": transparent,
        "width": getattr(image, 'width', None),
        "height": getattr(image, 'height', None),
    })

    # Decide mode based on transparency setting and target
    supports_alpha = target in {"png", "webp", "tiff", "ico", "gif", "svg"}
    keep_alpha = bool(transparent and supports_alpha)

    try:
        # Downscale before conversion to control memory
        image = downscale_image_if_needed(image)
        if keep_alpha:
            converted = image.convert("RGBA")
            # Auto-trim fully transparent padding
            try:
                alpha = converted.split()[-1]
                bbox = alpha.getbbox()
                if bbox and bbox != (0, 0, converted.width, converted.height):
                    converted = converted.crop(bbox)
            except Exception:
                pass
        else:
            # Flatten onto opaque white background for formats w/o alpha or when transparency is false
            if image.mode in ("RGBA", "LA"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                converted = background
            else:
                converted = image.convert("RGB")

        params = {}
        if target == "jpg":
            params.update({"quality": max(1, min(int(quality or 90), 100)), "optimize": True})
            save_format = "JPEG"
        elif target == "webp":
            params.update({"quality": max(1, min(int(quality or 90), 100))})
            save_format = "WEBP"
        elif target == "bmp":
            save_format = "BMP"
        elif target == "gif":
            # Convert to palette-based; keep single frame
            if keep_alpha:
                # GIF transparency via palette index
                converted = converted.convert("RGBA")
                palette_image = converted.convert("P", palette=Image.ADAPTIVE, colors=255)
                # Pick a transparent color index
                alpha = converted.split()[-1]
                mask = Image.eval(alpha, lambda a: 255 if a <= 1 else 0)
                palette_image.info['transparency'] = 255  # last index
                palette_image.paste(255, None, mask)
                converted = palette_image
            else:
                converted = converted.convert("P", palette=Image.ADAPTIVE, colors=256)
            params.update({"optimize": True, "save_all": False})
            save_format = "GIF"
        elif target == "tiff":
            params.update({"compression": "tiff_lzw"})
            save_format = "TIFF"
        elif target == "ico":
            # ICO must be 256x256. Use cover-fit: fill the square with center-crop (no padding)
            size = 256
            img_rgba = converted.convert("RGBA")
            # scale so the shortest side is 256 (cover)
            min_side = min(img_rgba.width, img_rgba.height)
            scale = size / float(min_side)
            resized = img_rgba.resize((max(1, int(round(img_rgba.width * scale))),
                                       max(1, int(round(img_rgba.height * scale)))), Image.LANCZOS)
            # center crop to 256x256
            left = (resized.width - size) // 2
            top = (resized.height - size) // 2
            converted = resized.crop((left, top, left + size, top + size))
            save_format = "ICO"
            params.update({"sizes": [(256, 256)]})
        elif target in {"ppm", "pgm"}:
            # Ensure correct mode and explicit binary save
            if target == "ppm":
                converted = converted.convert("RGB")
            else:
                converted = converted.convert("L")
            save_format = "PPM"
            params.update({"bits": 8})
        else:
            save_format = "PNG"

        async with PROCESS_SEM:
            buf = io.BytesIO()
            converted.save(buf, format=save_format, **params)
            out = buf.getvalue()

        log_user_action("convert_completed", {
            "target_format": target,
            "transparent": keep_alpha,
            "output_size_bytes": len(out),
        })

        media = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "webp": "image/webp",
            "bmp": "image/bmp",
            "gif": "image/gif",
            "tiff": "image/tiff",
            "ico": "image/x-icon",
            "ppm": "image/x-portable-pixmap",
            "pgm": "image/x-portable-graymap",
        }[target]
        return Response(content=out, media_type=media)

    except Exception as e:
        log_user_action("convert_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Conversion error: {str(e)}")


# ----------------------------
# Blog endpoints
# ----------------------------
@app.post("/api/generate-blog-daily")
async def generate_blog_daily(token: str):
    """Legacy endpoint - now redirects to draft generation"""
    if CRON_TOKEN and token != CRON_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Redirect to the new draft generation system
    return await generate_blog_draft(token)


@app.get("/blog")
async def blog_index():
    # Local/dev fallback when BLOG_BUCKET is not configured
    items = []
    if BLOG_BUCKET:
        client = get_storage_client()
        bucket = get_or_create_bucket(client)
        idx = bucket.blob("blog/index.json")
        try:
            data = json.loads(idx.download_as_text())
        except Exception:
            data = {"posts": []}
        items = data.get("posts", [])
    else:
        items = [
            {"slug": "remove-background-from-image-in-powerpoint", "title": "Remove Background From Image In Powerpoint", "date": datetime.utcnow().isoformat()},
            {"slug": "remove-background-from-image-photoshop", "title": "Remove Background From Image Photoshop", "date": datetime.utcnow().isoformat()},
            {"slug": "remove-background-from-image-iphone", "title": "Remove Background From Image Iphone", "date": datetime.utcnow().isoformat()},
            {"slug": "remove-background-from-image-free", "title": "Remove Background From Image Free", "date": datetime.utcnow().isoformat()},
            {"slug": "remove-background-from-image", "title": "Remove Background From Image", "date": datetime.utcnow().isoformat()},
        ]

    # Canonicalize and deduplicate similar/alias posts (e.g., remove-background-from-image-* → remove-background-from-image)
    def canonicalize_slug(s: str) -> str:
        base = s.strip().lower()
        # Only collapse the exact near-duplicate variants, not tutorial-specific ones
        alias_map = {
            "remove-background-from-image": "remove-background-from-image",
            "remove-background-from-image-free": "remove-background-from-image",
            "remove-background-from-image-online": "remove-background-from-image",
            "remove-background-from-image-free-online": "remove-background-from-image",
            "remove-background-from-image-powerpoint": "remove-background-from-image",
            "remove-background-from-image-in-powerpoint": "remove-background-from-image",
        }
        return alias_map.get(base, base)

    dedup: dict[str, dict] = {}
    for p in items:
        slug = p.get("slug", "")
        canon = canonicalize_slug(slug)
        # Prefer an exact canonical entry if it exists; otherwise keep the most recent by date
        if canon not in dedup:
            dedup[canon] = {**p, "slug": canon, "title": p.get("title") or canon.replace('-', ' ').title()}
        else:
            # If existing is an alias but new one is truly canonical, replace
            if slug == canon and dedup[canon].get("slug") != canon:
                dedup[canon] = {**p, "slug": canon, "title": p.get("title") or canon.replace('-', ' ').title()}
            else:
                # Otherwise keep the one with the latest date
                try:
                    old_date = dedup[canon].get("date", "")
                    new_date = p.get("date", "")
                    if new_date and new_date > old_date:
                        dedup[canon] = {**p, "slug": canon, "title": p.get("title") or canon.replace('-', ' ').title()}
                except Exception:
                    pass
    items = list(dedup.values())
    # Extract snippets from blog posts
    import re
    from html import unescape
    
    def extract_snippet(slug: str, title: str) -> str:
        """Extract snippet from blog post HTML or generate from title"""
        if BLOG_BUCKET:
            try:
                client = get_storage_client()
                bucket = get_or_create_bucket(client)
                blob = bucket.blob(f"blog/{slug}.html")
                if blob.exists():
                    html_content = blob.download_as_text()
                    # Try to extract meta description
                    meta_desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
                    if meta_desc_match:
                        snippet = unescape(meta_desc_match.group(1))
                        # Remove the generic "– practical guide and tips." if present
                        snippet = snippet.replace(" – practical guide and tips.", "").strip()
                        if snippet and len(snippet) > 20:
                            return snippet[:150] + "..." if len(snippet) > 150 else snippet
                    # Fallback: extract first paragraph from content
                    p_match = re.search(r'<p[^>]*>([^<]+)</p>', html_content, re.IGNORECASE | re.DOTALL)
                    if p_match:
                        snippet = unescape(re.sub(r'\s+', ' ', p_match.group(1)).strip())
                        if snippet and len(snippet) > 20:
                            return snippet[:150] + "..." if len(snippet) > 150 else snippet
            except Exception:
                pass
        # Fallback: generate snippet from title
        return f"Learn how to {title.lower()}. Step-by-step guide with practical tips and examples."
    
    # Generate blog cards with snippets
    blog_cards = []
    for p in items:
        snippet = extract_snippet(p['slug'], p['title'])
        date_str = p.get('date', '')[:10] if p.get('date') else ''
        blog_cards.append(f"""
      <article class="blog-card">
        <div class="blog-card-content">
          <h2 class="blog-card-title"><a href="/blog/{p['slug']}.html">{p['title']}</a></h2>
          <p class="blog-card-snippet">{snippet}</p>
          <div class="blog-card-meta">
            <time datetime="{p.get('date', '')}">{date_str}</time>
          </div>
        </div>
      </article>""")
    
    blog_grid = "".join(blog_cards)
    page_title = "Image Editing Blog | Tutorials, Tips & How‑To Guides"
    page_desc = "Learn image editing: remove background, change colors, upscale, blur, enhance images. Free tutorials and guides."
    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": page_title,
        "description": page_desc,
        "url": "https://www.changeimageto.com/blog"
    })
    html = f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{page_title}</title>
<meta name='description' content='{page_desc}'>
<link rel='canonical' href='https://www.changeimageto.com/blog'>
<script type='application/ld+json'>{json_ld}</script>
<link rel='preload' as='style' href='/styles.css?v=20250916-3'/>
<link rel='stylesheet' href='/styles.css?v=20250916-3'/>
<link rel='stylesheet' href='https://www.changeimageto.com/styles.css?v=20250916-3'/>
<style>
  .blog-wrap{{max-width:1200px;margin:0 auto;padding:24px}}
  .blog-title{{margin:0 0 12px}}
  .blog-sub{{color:var(--muted);margin:0 0 32px;font-size:18px}}
  .blog-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:24px;margin-top:32px}}
  .blog-card{{background:var(--card,#12171d);border:1px solid var(--border,#1e2630);border-radius:12px;padding:24px;transition:transform 0.2s,box-shadow 0.2s}}
  .blog-card:hover{{transform:translateY(-4px);box-shadow:0 8px 24px rgba(0,0,0,0.3)}}
  .blog-card-content{{display:flex;flex-direction:column;gap:12px}}
  .blog-card-title{{margin:0;font-size:20px;line-height:1.4}}
  .blog-card-title a{{color:var(--fg);text-decoration:none;font-weight:600}}
  .blog-card-title a:hover{{color:var(--accent,#6aa7ff);text-decoration:underline}}
  .blog-card-snippet{{color:var(--muted,#9aa7b2);margin:0;line-height:1.6;font-size:15px;flex:1}}
  .blog-card-meta{{display:flex;align-items:center;gap:8px;margin-top:8px;padding-top:12px;border-top:1px solid var(--border,#1e2630)}}
  .blog-card-meta time{{color:var(--muted,#9aa7b2);font-size:14px}}
  @media(max-width:768px){{
    .blog-grid{{grid-template-columns:1fr;gap:16px}}
    .blog-card{{padding:20px}}
  }}
</style>
</head>
<body>
  <header class='container header'>
    <a href='/' class='logo-link'><img src='https://www.changeimageto.com/logo.png?v=20250916-2' class='logo-img' alt='ChangeImageTo'/></a>
    <div style='display:flex;align-items:center;gap:16px;justify-content:space-between;width:100%'>
      <h1 style='margin:0'>Image Editing Blog</h1>
      <nav class='top-nav'><a href='/blog' aria-label='Read our blog'>Blog</a></nav>
    </div>
  </header>
  <main class='blog-wrap'>
    <p class='blog-sub'>Guides for removing backgrounds, changing colors, upscaling, blurring, and enhancing images.</p>
    <div class='blog-grid'>{blog_grid}
    </div>
  </main>
  <nav class='seo-links'><a href='/remove-background-from-image.html'>Remove Background</a><a href='/change-image-background.html'>Change Background</a><a href='/blur-background.html'>Blur Background</a><a href='/grayscale-background.html'>Black & White Image Background</a><a href='/change-color-of-image.html'>Change Color</a><a href='/upscale-image.html'>AI Image Upscaler</a><a href='/enhance-image.html'>Enhance Image</a></nav>
  <footer class='container footer'><p>Built for speed and quality. <a href='#' rel='nofollow'>Contact</a></p></footer>
  <script src='/script.js?v=20250916-3' defer></script>
</body></html>"""
    return Response(content=html, media_type="text/html")


@app.get("/blog/{slug}.html")
async def blog_article(slug: str):
    # Redirect aliases to canonical to avoid duplicate content
    def canonicalize_slug(s: str) -> str:
        base = s.strip().lower()
        alias_map = {
            "remove-background-from-image-free": "remove-background-from-image",
            "remove-background-from-image-online": "remove-background-from-image",
            "remove-background-from-image-free-online": "remove-background-from-image",
        }
        return alias_map.get(base, base)

    canon = canonicalize_slug(slug)
    if canon != slug:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/blog/{canon}.html", status_code=301)

    # Serve hand-crafted static articles directly if they exist on disk
    static_path = os.path.join(FRONTEND_DIR, "blog", f"{slug}.html")
    if os.path.exists(static_path):
        return FileResponse(static_path, media_type="text/html")

    # Always render fresh from current template/sections
    # Use proper title formatting for specific slugs
    if slug == "remove-background-from-image-android":
        title = "How to Remove Background From Image on Android"
    else:
        title = slug.replace('-', ' ').title()
    fresh_html = render_article_html(title, slug, build_sections(title))
    # If in production with bucket, compare and update stored HTML if changed
    if BLOG_BUCKET:
        try:
            client = get_storage_client()
            bucket = get_or_create_bucket(client)
            blob = bucket.blob(f"blog/{slug}.html")
            old_html = blob.download_as_text() if blob.exists() else ""
            if old_html != fresh_html:
                save_article(client, slug, fresh_html)
        except Exception:
            # Non-fatal: still serve fresh content even if bucket update fails
            pass
    return Response(content=fresh_html, media_type="text/html")


# ----------------------------
# Blog Management System
# ----------------------------

class BlogStatus(Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"

# Database configuration - must be defined before functions that use it
# Use absolute path in production to avoid issues with working directory
if os.getenv("DB_FILE"):
    DB_FILE = os.getenv("DB_FILE")
elif os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production":
    # Cloud Run: /tmp (synced to GCS). Hetzner: set DB_FILE=/data/blog_management.db instead.
    DB_FILE = os.path.join(os.getenv("TMPDIR", "/tmp"), "blog_management.db")
else:
    DB_FILE = 'blog_management.db'
DB_BUCKET_PATH = 'data/blog_management.db'  # Path in Cloud Storage

def init_blog_db():
    """Initialize SQLite database for blog management"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blog_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP NULL,
            author TEXT DEFAULT 'system',
            notes TEXT,
            metadata TEXT  -- JSON for additional data
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blog_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            action TEXT NOT NULL,  -- 'approve', 'reject', 'request_changes'
            approver TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES blog_posts (id)
        )
    ''')
    
    # Create feedback table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rating INTEGER NOT NULL,
            comment TEXT,
            page TEXT,
            operation TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create feedback impressions table to track modal views
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback_impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page TEXT,
            operation TEXT,
            user_agent TEXT,
            action TEXT DEFAULT 'shown',  -- 'shown', 'submitted', 'skipped', 'closed'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create "what's missing" feedback table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS whats_missing_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feedback_text TEXT NOT NULL,
            page TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
def sync_db_from_storage():
    """Download database from Cloud Storage if it exists"""
    if not BLOG_BUCKET or storage is None:
        logger.warning("⚠️  Cloud Storage not configured, using local database only")
        logger.warning(f"   BLOG_BUCKET={BLOG_BUCKET}, storage={storage is not None}")
        logger.warning("   ⚠️  WARNING: In Cloud Run, local database will be LOST on restart!")
        return False
    
    try:
        client = get_storage_client()
        bucket = get_or_create_bucket(client)
        blob = bucket.blob(DB_BUCKET_PATH)
        
        if blob.exists():
            logger.info(f"📥 Downloading database from Cloud Storage: {DB_BUCKET_PATH}")
            blob.download_to_filename(DB_FILE)
            logger.info("✅ Database downloaded successfully from Cloud Storage")
            return True
        else:
            logger.info("ℹ️  No existing database in Cloud Storage, starting fresh")
            return False
    except Exception as e:
        logger.error(f"❌ Failed to sync database from Cloud Storage: {e}")
        logger.error("   ⚠️  Using local database - data may be lost on restart!")
        return False

def sync_db_to_storage():
    """Upload database to Cloud Storage"""
    if not BLOG_BUCKET or storage is None:
        logger.warning("⚠️  CRITICAL: Cloud Storage not configured! Database changes will be LOST on restart!")
        logger.warning(f"   BLOG_BUCKET={BLOG_BUCKET}, storage={storage is not None}")
        return False
    
    try:
        if not os.path.exists(DB_FILE):
            logger.warning(f"Database file {DB_FILE} does not exist, cannot sync")
            return False
        
        client = get_storage_client()
        bucket = get_or_create_bucket(client)
        blob = bucket.blob(DB_BUCKET_PATH)
        
        blob.upload_from_filename(DB_FILE)
        logger.info(f"✅ Database synced to Cloud Storage: {DB_BUCKET_PATH}")
        return True
    except Exception as e:
        logger.error(f"❌ CRITICAL: Failed to sync database to Cloud Storage: {e}")
        logger.error(f"   Database changes may be LOST on restart!")
        return False

def get_db_connection():
    """Get database connection with auto-sync to Cloud Storage"""
    conn = sqlite3.connect(DB_FILE)
    return conn

def save_db_changes(conn):
    """Commit changes and sync to Cloud Storage"""
    conn.commit()
    # Sync to Cloud Storage (this is important for persistence!)
    try:
        sync_db_to_storage()
    except Exception as e:
        logger.warning(f"Failed to sync database to Cloud Storage: {e}")
        # Still commit locally even if sync fails

def check_local_only():
    """Check if we're in local development mode"""
    # Check if we're running on Cloud Run (production)
    if os.getenv("K_SERVICE") or os.getenv("ENVIRONMENT") == "production":
        raise HTTPException(status_code=404, detail="Admin interface not found")

# Initialize database on startup and sync from Cloud Storage
# This runs AFTER the app starts listening, so it won't block startup
@app.on_event("startup")
async def init_database():
    """Initialize database and sync from Cloud Storage on startup (completely non-blocking)"""
    # Schedule in background - don't wait for it
    async def _init_db_async():
        try:
            logger.info("Starting database initialization in background...")
            
            # CRITICAL: Download database FROM Cloud Storage FIRST (before any table creation)
            # This ensures we don't overwrite existing data
            db_downloaded = False
            try:
                db_downloaded = await asyncio.to_thread(sync_db_from_storage)
                if db_downloaded:
                    logger.info("✅ Downloaded existing database from Cloud Storage")
                else:
                    logger.info("ℹ️  No existing database in Cloud Storage, will create new one")
            except Exception as e:
                logger.error(f"❌ Failed to sync from Cloud Storage: {e}")
                logger.error("   Will start with fresh database - existing data may be lost!")
            
            # Initialize/create tables (safe - uses CREATE TABLE IF NOT EXISTS)
            try:
                await asyncio.to_thread(init_blog_db)
                logger.info("Database tables initialized")
            except Exception as e:
                logger.error(f"Failed to initialize database tables: {e}")
            
            # Only sync BACK to Cloud Storage if:
            # 1. We didn't download an existing database (new database created)
            # 2. OR we successfully downloaded and want to ensure it's backed up
            # This prevents overwriting good data with empty database
            if not db_downloaded:
                # New database created - sync it to Cloud Storage
                try:
                    sync_success = await asyncio.to_thread(sync_db_to_storage)
                    if not sync_success:
                        logger.error("🚨 CRITICAL: Initial database sync failed! Check BLOG_BUCKET configuration!")
                    else:
                        logger.info("✅ New database synced to Cloud Storage")
                except Exception as e:
                    logger.error(f"❌ CRITICAL: Could not sync to Cloud Storage: {e}")
                    logger.error("   Database changes will be LOST on restart in Cloud Run!")
            else:
                # Database was downloaded - verify it's still there and optionally re-sync
                # (in case tables were added/updated)
                try:
                    sync_success = await asyncio.to_thread(sync_db_to_storage)
                    if sync_success:
                        logger.info("✅ Database verified and synced to Cloud Storage")
                    else:
                        logger.warning("⚠️  Could not verify database sync, but data should be safe")
                except Exception as e:
                    logger.warning(f"⚠️  Could not verify database sync: {e} (data should still be safe)")
                
        except Exception as e:
            logger.error(f"Database initialization error (non-fatal): {e}")
    
    # Fire and forget - don't await, don't block
    asyncio.create_task(_init_db_async())
    logger.info("Database initialization scheduled (non-blocking)")

@app.get("/api/blog/admin/drafts")
async def get_blog_drafts():
    """Get all draft, pending, approved, and rejected blog posts"""
    check_local_only()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, slug, title, status, created_at, updated_at, notes
        FROM blog_posts 
        WHERE status IN ('draft', 'pending_approval', 'approved', 'rejected')
        ORDER BY created_at DESC
    ''')
    
    drafts = []
    for row in cursor.fetchall():
        drafts.append({
            'id': row[0],
            'slug': row[1],
            'title': row[2],
            'status': row[3],
            'created_at': row[4],
            'updated_at': row[5],
            'notes': row[6]
        })
    
    conn.close()
    return {"drafts": drafts}

@app.get("/api/blog/admin/post/{post_id}")
async def get_blog_post(post_id: int):
    """Get specific blog post for editing"""
    check_local_only()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, slug, title, content, status, created_at, updated_at, 
               published_at, author, notes, metadata
        FROM blog_posts 
        WHERE id = ?
    ''', (post_id,))
    
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")
    
    post = {
        'id': row[0],
        'slug': row[1],
        'title': row[2],
        'content': row[3],
        'status': row[4],
        'created_at': row[5],
        'updated_at': row[6],
        'published_at': row[7],
        'author': row[8],
        'notes': row[9],
        'metadata': json.loads(row[10]) if row[10] else {}
    }
    
    conn.close()
    return post

@app.post("/api/blog/admin/post/{post_id}/update")
async def update_blog_post(post_id: int, request: Request):
    """Update blog post content"""
    check_local_only()
    data = await request.json()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Update post
    cursor.execute('''
        UPDATE blog_posts 
        SET title = ?, content = ?, updated_at = CURRENT_TIMESTAMP, notes = ?
        WHERE id = ?
    ''', (data.get('title'), data.get('content'), data.get('notes'), post_id))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found")
    
    save_db_changes(conn)
    conn.close()
    
    return {"success": True, "message": "Post updated successfully"}

@app.post("/api/blog/admin/post/{post_id}/approve")
async def approve_blog_post(post_id: int, request: Request):
    """Approve blog post for publishing"""
    check_local_only()
    data = await request.json()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Update post status
    cursor.execute('''
        UPDATE blog_posts 
        SET status = 'approved', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (post_id,))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Add approval record
    cursor.execute('''
        INSERT INTO blog_approvals (post_id, action, approver, notes)
        VALUES (?, 'approve', ?, ?)
    ''', (post_id, data.get('approver', 'admin'), data.get('notes', '')))
    
    save_db_changes(conn)
    conn.close()
    
    return {"success": True, "message": "Post approved successfully"}

@app.post("/api/blog/admin/post/{post_id}/reject")
async def reject_blog_post(post_id: int, request: Request):
    """Reject blog post"""
    check_local_only()
    data = await request.json()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Update post status
    cursor.execute('''
        UPDATE blog_posts 
        SET status = 'rejected', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (post_id,))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Add rejection record
    cursor.execute('''
        INSERT INTO blog_approvals (post_id, action, approver, notes)
        VALUES (?, 'reject', ?, ?)
    ''', (post_id, data.get('approver', 'admin'), data.get('notes', '')))
    
    save_db_changes(conn)
    conn.close()
    
    return {"success": True, "message": "Post rejected"}

@app.post("/api/blog/admin/post/{post_id}/publish")
async def publish_blog_post(post_id: int):
    """Publish approved blog post to live site"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get post data
    cursor.execute('''
        SELECT slug, title, content FROM blog_posts 
        WHERE id = ? AND status = 'approved'
    ''', (post_id,))
    
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Post not found or not approved")
    
    slug, title, content = row
    
    # Regenerate content with current title to ensure consistency
    fresh_content = render_article_html(title, slug, build_sections(title))
    
    # Publish to Google Cloud Storage (if configured)
    if BLOG_BUCKET:
        try:
            client = get_storage_client()
            save_article(client, slug, fresh_content)
            
            # Update post status
            cursor.execute('''
                UPDATE blog_posts 
                SET status = 'published', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (post_id,))
            
            save_db_changes(conn)
            conn.close()
            
            # Submit to IndexNow if configured
            if INDEXNOW_AVAILABLE and INDEXNOW_KEY and INDEXNOW_SITE_DOMAIN:
                try:
                    submit_blog_post(INDEXNOW_SITE_DOMAIN, slug, INDEXNOW_KEY)
                    logger.info(f"Submitted blog post {slug} to IndexNow")
                except Exception as e:
                    logger.warning(f"Failed to submit to IndexNow: {str(e)}")
                    # Don't fail the publish if IndexNow fails
            
            return {"success": True, "message": "Post published successfully"}
        except Exception as e:
            conn.close()
            raise HTTPException(status_code=500, detail=f"Publishing failed: {str(e)}")
    else:
        # For local development, try to publish to production automatically
        try:
            # Try to get storage client and publish to production
            if BLOG_BUCKET:
                client = get_storage_client()
                save_article(client, slug, fresh_content)
                message = "Post published to production successfully"
            else:
                message = "Post marked as published locally (BLOG_BUCKET not configured)"
            
            # Update post status regardless
            cursor.execute('''
                UPDATE blog_posts 
                SET content = ?, status = 'published', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (fresh_content, post_id))
            
            save_db_changes(conn)
            conn.close()
            
            return {"success": True, "message": message}
        except Exception as e:
            # If production publishing fails, still mark as published locally
            cursor.execute('''
                UPDATE blog_posts 
                SET content = ?, status = 'published', published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (fresh_content, post_id))
            
            save_db_changes(conn)
            conn.close()
            
            return {"success": True, "message": f"Post marked as published locally (production publishing failed: {str(e)})"}

@app.post("/api/blog/admin/generate-draft")
async def generate_blog_draft(token: str = None):
    """Generate new blog post as draft (requires approval)"""
    check_local_only()
    # For local testing, allow without token
    if CRON_TOKEN and token != CRON_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Handle case when BLOG_BUCKET is not configured (local development)
    existing = set()
    if BLOG_BUCKET:
        try:
            client = get_storage_client()
            existing = list_existing_slugs(client)
        except Exception:
            existing = set()
    
    # Get existing draft slugs from database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT slug FROM blog_posts')
    db_slugs = {row[0] for row in cursor.fetchall()}
    conn.close()
    
    # Combine existing slugs
    all_existing = existing.union(db_slugs)
    
    seeds = [
        "remove background from image canva",
        "remove background from image for free online",
        "remove background from image in google slides",
        "remove background from image on iphone",
        "remove background from image in photoshop",
        "change image background color powerpoint",
    ]
    
    # Use exact keywords instead of generating variations
    picks = []
    for seed in seeds:
        slug = normalize_slug(seed)
        if slug not in all_existing:
            picks.append((seed, slug))
    
    created = []
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for kw, slug in picks:
        title = kw.title()
        sections = build_sections(kw)
        html = render_article_html(title, slug, sections)
        
        # Save as draft in database
        cursor.execute('''
            INSERT INTO blog_posts (slug, title, content, status, author, notes)
            VALUES (?, ?, ?, 'draft', 'system', 'Auto-generated draft')
        ''', (slug, title, html))
        
        created.append({"slug": slug, "title": title, "status": "draft"})
    
    save_db_changes(conn)
    conn.close()
    
    return {"created": created, "message": "Drafts created - awaiting approval"}

@app.get("/api/blog/admin/published")
async def get_published_posts():
    """Get all published blog posts"""
    check_local_only()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, slug, title, status, created_at, published_at, author
        FROM blog_posts 
        WHERE status = 'published'
        ORDER BY published_at DESC
    ''')
    
    posts = []
    for row in cursor.fetchall():
        posts.append({
            'id': row[0],
            'slug': row[1],
            'title': row[2],
            'status': row[3],
            'created_at': row[4],
            'published_at': row[5],
            'author': row[6]
        })
    
    conn.close()
    return {"posts": posts}


@app.delete("/api/blog/admin/delete/{slug}")
async def delete_blog_post(slug: str):
    """Delete a blog post from Google Cloud Storage and update the blog index"""
    check_local_only()
    try:
        # Get storage client
        client = get_storage_client()
        bucket = client.bucket(BLOG_BUCKET)
        
        # Delete the HTML file
        blob_name = f"blog/{slug}.html"
        blob = bucket.blob(blob_name)
        
        if blob.exists():
            blob.delete()
            print(f"Deleted blog post: {slug}")
        else:
            print(f"Blog post not found: {slug}")
            return {"message": f"Blog post '{slug}' not found", "deleted": False}
        
        # Update the blog index by regenerating it
        await update_blog_index()
        
        return {"message": f"Blog post '{slug}' deleted successfully", "deleted": True}
        
    except Exception as e:
        print(f"Error deleting blog post {slug}: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting blog post: {str(e)}")


async def update_blog_index():
    """Regenerate the blog index page after deleting posts"""
    try:
        client = get_storage_client()
        bucket = client.bucket(BLOG_BUCKET)
        
        # List all blog posts
        blobs = bucket.list_blobs(prefix="blog/")
        blog_posts = []
        
        for blob in blobs:
            if blob.name.endswith('.html') and blob.name != 'blog/index.html':
                # Extract slug from filename
                slug = blob.name.replace('blog/', '').replace('.html', '')
                
                # Get metadata
                blob.reload()
                metadata = blob.metadata or {}
                
                blog_posts.append({
                    'slug': slug,
                    'title': metadata.get('title', slug.replace('-', ' ').title()),
                    'date': metadata.get('date', '2025-09-20')
                })
        
        # Sort by date (newest first)
        blog_posts.sort(key=lambda x: x['date'], reverse=True)
        
        # Extract snippets from blog posts
        import re
        from html import unescape
        
        def extract_snippet(slug: str, title: str) -> str:
            """Extract snippet from blog post HTML or generate from title"""
            try:
                blob = bucket.blob(f"blog/{slug}.html")
                if blob.exists():
                    html_content = blob.download_as_text()
                    # Try to extract meta description
                    meta_desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
                    if meta_desc_match:
                        snippet = unescape(meta_desc_match.group(1))
                        # Remove the generic "– practical guide and tips." if present
                        snippet = snippet.replace(" – practical guide and tips.", "").strip()
                        if snippet and len(snippet) > 20:
                            return snippet[:150] + "..." if len(snippet) > 150 else snippet
                    # Fallback: extract first paragraph from content
                    p_match = re.search(r'<p[^>]*>([^<]+)</p>', html_content, re.IGNORECASE | re.DOTALL)
                    if p_match:
                        snippet = unescape(re.sub(r'\s+', ' ', p_match.group(1)).strip())
                        if snippet and len(snippet) > 20:
                            return snippet[:150] + "..." if len(snippet) > 150 else snippet
            except Exception:
                pass
            # Fallback: generate snippet from title
            return f"Learn how to {title.lower()}. Step-by-step guide with practical tips and examples."
        
        # Generate blog cards with snippets
        blog_cards_html = ""
        for post in blog_posts:
            snippet = extract_snippet(post['slug'], post['title'])
            date_str = post.get('date', '')[:10] if post.get('date') else ''
            blog_cards_html += f"""
      <article class="blog-card">
        <div class="blog-card-content">
          <h2 class="blog-card-title"><a href="/blog/{post['slug']}.html">{post['title']}</a></h2>
          <p class="blog-card-snippet">{snippet}</p>
          <div class="blog-card-meta">
            <time datetime="{post.get('date', '')}">{date_str}</time>
          </div>
        </div>
      </article>"""
        
        # Generate new index HTML
        index_html = f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Image Editing Blog | Tutorials, Tips & How‑To Guides</title>
<meta name='description' content='Learn image editing: remove background, change colors, upscale, blur, enhance images. Free tutorials and guides.'>                                                  
<link rel='canonical' href='https://www.changeimageto.com/blog'>
<script type='application/ld+json'>{{"@context": "https://schema.org", "@type": "Blog", "name": "Image Editing Blog | Tutorials, Tips & How‑To Guides", "description": "Learn image editing: remove background, change colors, upscale, blur, enhance images. Free tutorials and guides.", "url": "https://www.changeimageto.com/blog"}}</script>                                                        
<link rel='preload' as='style' href='/styles.css?v=20250916-3'/>
<link rel='stylesheet' href='/styles.css?v=20250916-3'/>
<link rel='stylesheet' href='https://www.changeimageto.com/styles.css?v=20250916-3'/>
<style>
  .blog-wrap{{max-width:1200px;margin:0 auto;padding:24px}}
  .blog-title{{margin:0 0 12px}}
  .blog-sub{{color:var(--muted);margin:0 0 32px;font-size:18px}}
  .blog-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:24px;margin-top:32px}}
  .blog-card{{background:var(--card,#12171d);border:1px solid var(--border,#1e2630);border-radius:12px;padding:24px;transition:transform 0.2s,box-shadow 0.2s}}
  .blog-card:hover{{transform:translateY(-4px);box-shadow:0 8px 24px rgba(0,0,0,0.3)}}
  .blog-card-content{{display:flex;flex-direction:column;gap:12px}}
  .blog-card-title{{margin:0;font-size:20px;line-height:1.4}}
  .blog-card-title a{{color:var(--fg);text-decoration:none;font-weight:600}}
  .blog-card-title a:hover{{color:var(--accent,#6aa7ff);text-decoration:underline}}
  .blog-card-snippet{{color:var(--muted,#9aa7b2);margin:0;line-height:1.6;font-size:15px;flex:1}}
  .blog-card-meta{{display:flex;align-items:center;gap:8px;margin-top:8px;padding-top:12px;border-top:1px solid var(--border,#1e2630)}}
  .blog-card-meta time{{color:var(--muted,#9aa7b2);font-size:14px}}
  @media(max-width:768px){{
    .blog-grid{{grid-template-columns:1fr;gap:16px}}
    .blog-card{{padding:20px}}
  }}
</style>
</head>
<body>
  <header class='container header'>
    <a href='/' class='logo-link'><img src='https://www.changeimageto.com/logo.png?v=20250916-2' class='logo-img' alt='ChangeImageTo'/></a>                                                           
    <div style='display:flex;align-items:center;gap:16px;justify-content:space-between;width:100%'>
      <h1 style='margin:0'>Image Editing Blog</h1>
      <nav class='top-nav'><a href='/blog' aria-label='Read our blog'>Blog</a></nav>
    </div>
  </header>
  <main class='blog-wrap'>
    <p class='blog-sub'>Guides for removing backgrounds, changing colors, upscaling, blurring, and enhancing images.</p>                                                                              
    <div class='blog-grid'>{blog_cards_html}
    </div>"""
        
        # Extract snippets from blog posts
        import re
        from html import unescape
        
        def extract_snippet(slug: str, title: str) -> str:
            """Extract snippet from blog post HTML or generate from title"""
            try:
                blob = bucket.blob(f"blog/{slug}.html")
                if blob.exists():
                    html_content = blob.download_as_text()
                    # Try to extract meta description
                    meta_desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
                    if meta_desc_match:
                        snippet = unescape(meta_desc_match.group(1))
                        # Remove the generic "– practical guide and tips." if present
                        snippet = snippet.replace(" – practical guide and tips.", "").strip()
                        if snippet and len(snippet) > 20:
                            return snippet[:150] + "..." if len(snippet) > 150 else snippet
                    # Fallback: extract first paragraph from content
                    p_match = re.search(r'<p[^>]*>([^<]+)</p>', html_content, re.IGNORECASE | re.DOTALL)
                    if p_match:
                        snippet = unescape(re.sub(r'\s+', ' ', p_match.group(1)).strip())
                        if snippet and len(snippet) > 20:
                            return snippet[:150] + "..." if len(snippet) > 150 else snippet
            except Exception:
                pass
            # Fallback: generate snippet from title
            return f"Learn how to {title.lower()}. Step-by-step guide with practical tips and examples."
        
        # Generate blog cards with snippets
        for post in blog_posts:
            snippet = extract_snippet(post['slug'], post['title'])
            date_str = post.get('date', '')[:10] if post.get('date') else ''
            index_html += f"""
      <article class="blog-card">
        <div class="blog-card-content">
          <h2 class="blog-card-title"><a href="/blog/{post['slug']}.html">{post['title']}</a></h2>
          <p class="blog-card-snippet">{snippet}</p>
          <div class="blog-card-meta">
            <time datetime="{post.get('date', '')}">{date_str}</time>
          </div>
        </div>
      </article>"""
        
        index_html += """
    </div>                                                                                                
  </main>
  <nav class='seo-links'><a href='/remove-background-from-image.html'>Remove Background</a><a href='/change-image-background.html'>Change Background</a><a href='/blur-background.html'>Blur Background</a><a href='/grayscale-background.html'>Black & White Image Background</a><a href='/change-color-of-image.html'>Change Color</a><a href='/upscale-image.html'>AI Image Upscaler</a><a href='/enhance-image.html'>Enhance Image</a></nav>                                   
  <footer class='container footer'><p>Built for speed and quality. <a href='#' rel='nofollow'>Contact</a></p></footer>                                                                                
  <script src='/script.js?v=20250916-3' defer></script>
</body></html>"""
        
        # Upload updated index
        index_blob = bucket.blob("blog/index.html")
        index_blob.upload_from_string(index_html, content_type="text/html")
        
        print(f"Updated blog index with {len(blog_posts)} posts")
        
    except Exception as e:
        print(f"Error updating blog index: {e}")
        raise


@app.get("/blog-admin")
async def blog_admin():
    """Serve blog admin interface - Local development only"""
    # Only allow access in local development
    if os.getenv("ENVIRONMENT") == "production":
        raise HTTPException(status_code=404, detail="Admin interface not found")
    
    try:
        with open("frontend/blog-admin.html", "r", encoding="utf-8") as f:
            content = f.read()
        return Response(content=content, media_type="text/html")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Admin interface not found")


@app.post("/api/upscale-image")
async def upscale_image(
    file: UploadFile = File(...),
    scale_factor: int = Form(2),
    method: str = Form("lanczos")
):
    """
    Upscale an image using AI-powered interpolation methods.
    
    Args:
        file: Image file to upscale
        scale_factor: Upscaling factor (2, 3, or 4)
        method: Upscaling method ('lanczos', 'cubic', 'nearest', 'area')
    
    Returns:
        Upscaled image as PNG
    """
    try:
        # Validate scale factor
        if scale_factor not in [2, 3, 4]:
            raise HTTPException(status_code=400, detail="Scale factor must be 2, 3, or 4")
        
        # Validate method
        valid_methods = ['lanczos', 'cubic', 'nearest', 'area']
        if method not in valid_methods:
            raise HTTPException(status_code=400, detail=f"Method must be one of: {valid_methods}")
        
        # Read and validate image
        image_data = await file.read()
        image = Image.open(io.BytesIO(image_data))
        
        # Convert to RGB if necessary
        if image.mode in ('RGBA', 'LA', 'P'):
            # Create white background for transparent images
            background = Image.new('RGB', image.size, (255, 255, 255))
            if image.mode == 'P':
                image = image.convert('RGBA')
            background.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
            image = background
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Get original dimensions
        original_width, original_height = image.size
        new_width = original_width * scale_factor
        new_height = original_height * scale_factor
        
        # Apply upscaling using PIL
        if method == 'lanczos':
            # Use PIL's LANCZOS resampling for high-quality upscaling
            upscaled_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        elif method == 'cubic':
            # Use PIL's BICUBIC resampling
            upscaled_image = image.resize((new_width, new_height), Image.Resampling.BICUBIC)
        elif method == 'nearest':
            # Use PIL's NEAREST resampling
            upscaled_image = image.resize((new_width, new_height), Image.Resampling.NEAREST)
        elif method == 'area':
            # Use PIL's BOX resampling (similar to area)
            upscaled_image = image.resize((new_width, new_height), Image.Resampling.BOX)
        else:
            # Default to LANCZOS
            upscaled_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Apply additional sharpening for better quality
        enhancer = ImageEnhance.Sharpness(upscaled_image)
        upscaled_image = enhancer.enhance(1.2)  # Slight sharpening
        
        # Save as PNG
        output_buffer = io.BytesIO()
        upscaled_image.save(output_buffer, format='PNG', optimize=True)
        output_data = output_buffer.getvalue()
        
        # Log the action
        log_user_action("upscale_completed", {
            "original_size": f"{original_width}x{original_height}",
            "upscaled_size": f"{new_width}x{new_height}",
            "scale_factor": scale_factor,
            "method": method,
            "output_size_bytes": len(output_data)
        })
        
        return Response(content=output_data, media_type="image/png")
        
    except Exception as e:
        log_user_action("upscale_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Upscaling error: {str(e)}")


# ----------------------------
# New simple image tools
# ----------------------------

@app.post("/api/blur-background")
async def blur_background(
    request: Request,
    file: UploadFile = File(...),
    blur_radius: float = Form(12.0),
    category: str = Form("portrait"),
):
    """Blur the background while keeping the subject sharp using existing segmentation."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGBA")
        # Downscale for processing efficiency
        proc_image = downscale_image_if_needed(image)
        # Segment foreground
        session = get_session_for_category(category)
        async with PROCESS_SEM:
            cutout = remove(
                proc_image,
                session=session,
                alpha_matting=True,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
                alpha_matting_erode_size=10,
                post_process_mask=True,
            )
        # Build mask from alpha
        if cutout.mode != "RGBA":
            cutout = cutout.convert("RGBA")
        mask = cutout.split()[-1]
        # Prepare blurred background from original
        base_rgb = image.convert("RGB")
        blurred = base_rgb.filter(ImageFilter.GaussianBlur(radius=max(0.0, float(blur_radius))))
        blurred_rgba = blurred.convert("RGBA")
        # Composite: paste sharp subject onto blurred bg
        # Resize cutout to original size if we downscaled
        if cutout.size != image.size:
            cutout = cutout.resize(image.size, Image.LANCZOS)
            mask = mask.resize(image.size, Image.LANCZOS)
        output = blurred_rgba.copy()
        output.paste(cutout, (0, 0), mask=mask)
        buf = io.BytesIO()
        output.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("blur_background_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Blur background error: {str(e)}")


@app.post("/api/grayscale-background")
async def grayscale_background(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("portrait"),
):
    """Convert the background to grayscale while keeping the foreground in color using U-2-Net segmentation."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGBA")
        original_size = image.size
        
        # Downscale for processing efficiency
        proc_image = downscale_image_if_needed(image)
        
        # Segment foreground using rembg (which uses U-2-Net)
        session = get_session_for_category(category)
        async with PROCESS_SEM:
            cutout = remove(
                proc_image,
                session=session,
                alpha_matting=True,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
                alpha_matting_erode_size=10,
                post_process_mask=True,
            )
        
        # Build mask from alpha channel
        if cutout.mode != "RGBA":
            cutout = cutout.convert("RGBA")
        mask = cutout.split()[-1]
        
        # Prepare grayscale background from original image
        base_rgb = image.convert("RGB")
        grayscale_bg = ImageOps.grayscale(base_rgb)
        # Convert back to RGBA for compositing
        grayscale_rgba = grayscale_bg.convert("RGBA")
        
        # Resize cutout and mask to original size if we downscaled
        if cutout.size != image.size:
            cutout = cutout.resize(image.size, Image.LANCZOS)
            mask = mask.resize(image.size, Image.LANCZOS)
        
        # Composite: paste color foreground onto grayscale background
        output = grayscale_rgba.copy()
        output.paste(cutout, (0, 0), mask=mask)
        
        buf = io.BytesIO()
        output.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("grayscale_background_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Grayscale background error: {str(e)}")


@app.post("/api/enhance-image")
async def enhance_image(
    file: UploadFile = File(...),
    sharpen: float = Form(1.0),
    contrast: float = Form(105.0),
    brightness: float = Form(100.0),
    deblur: bool = Form(True),
    denoise: bool = Form(True),
    deblur_strength: float = Form(1.5),
    denoise_strength: float = Form(1.0),
):
    """Enhanced photo processing with de-blur, noise removal, and traditional enhancements."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    try:
        contents = await file.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        original_size = img.size
        img = downscale_image_if_needed(img)
        
        # Step 1: Noise/Grain Removal (apply first, before sharpening)
        if denoise:
            # Apply median filter for salt-and-pepper noise removal
            if denoise_strength >= 0.5:
                img = img.filter(ImageFilter.MedianFilter(size=3))
            
            # Additional denoising: slight Gaussian blur then sharpen (removes grain)
            if denoise_strength >= 1.0:
                # Very light Gaussian blur to smooth grain
                img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        
        # Step 2: De-blur (unsharp mask for de-blurring)
        if deblur:
            # Apply unsharp mask filter for de-blurring
            unsharp_percent = int(100 + (deblur_strength * 50))  # 100-200% range
            img = img.filter(ImageFilter.UnsharpMask(
                radius=2,
                percent=unsharp_percent,
                threshold=3
            ))
        
        # Step 3: Traditional enhancements (sharpness, contrast, brightness)
        if sharpen != 1.0:
            sharp_enh = ImageEnhance.Sharpness(img)
            img = sharp_enh.enhance(max(0.0, float(sharpen)))
        
        if contrast != 100.0:
            cont_enh = ImageEnhance.Contrast(img)
            img = cont_enh.enhance(max(0.0, float(contrast)) / 100.0)
        
        if brightness != 100.0:
            bri_enh = ImageEnhance.Brightness(img)
            img = bri_enh.enhance(max(0.0, float(brightness)) / 100.0)
        
        # Resize back to original if downscaled
        if img.size != original_size:
            img = img.resize(original_size, Image.Resampling.LANCZOS)
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("enhance_image_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Enhance image error: {str(e)}")


@app.post("/api/test-enhance")
async def test_enhance(
    file: UploadFile = File(...),
    sharpen: float = Form(1.0),
    contrast: float = Form(105.0),
    brightness: float = Form(100.0),
    deblur: bool = Form(True),
    denoise: bool = Form(True),
    deblur_strength: float = Form(1.5),
    denoise_strength: float = Form(1.0),
):
    """Test endpoint for enhanced image processing with de-blur and noise removal"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    try:
        contents = await file.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        original_size = img.size
        img = downscale_image_if_needed(img)
        
        # Step 1: Noise/Grain Removal (apply first, before sharpening)
        if denoise:
            # Apply median filter for salt-and-pepper noise removal
            if denoise_strength >= 0.5:
                # Use MedianFilter for noise removal
                img = img.filter(ImageFilter.MedianFilter(size=3))
            
            # Additional denoising: slight Gaussian blur then sharpen (removes grain)
            if denoise_strength >= 1.0:
                # Very light Gaussian blur to smooth grain
                img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        
        # Step 2: De-blur (unsharp mask for de-blurring)
        if deblur:
            # Apply unsharp mask filter for de-blurring
            # UnsharpMask parameters: radius, percent, threshold
            # Higher percent = stronger de-blur effect
            unsharp_percent = int(100 + (deblur_strength * 50))  # 100-200% range
            img = img.filter(ImageFilter.UnsharpMask(
                radius=2,
                percent=unsharp_percent,
                threshold=3
            ))
        
        # Step 3: Traditional enhancements (sharpness, contrast, brightness)
        if sharpen != 1.0:
            sharp_enh = ImageEnhance.Sharpness(img)
            img = sharp_enh.enhance(max(0.0, float(sharpen)))
        
        if contrast != 100.0:
            cont_enh = ImageEnhance.Contrast(img)
            img = cont_enh.enhance(max(0.0, float(contrast)) / 100.0)
        
        if brightness != 100.0:
            bri_enh = ImageEnhance.Brightness(img)
            img = bri_enh.enhance(max(0.0, float(brightness)) / 100.0)
        
        # Resize back to original if downscaled
        if img.size != original_size:
            img = img.resize(original_size, Image.Resampling.LANCZOS)
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Test enhance error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Enhance image error: {str(e)}")


def detect_damage_v2(img_array: np.ndarray, method: str = "conservative") -> np.ndarray:
    """
    Improved automatic damage detection with multiple strategies.
    
    Args:
        img_array: RGB image array
        method: "conservative" (safer, fewer false positives) or "aggressive" (more detection)
    
    Returns:
        Binary mask where white (255) indicates damage
    """
    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    damage_mask = np.zeros((h, w), dtype=np.uint8)
    
    # Strategy 1: Frequency domain filtering for scratches (FFT-based)
    # Scratches appear as strong lines in frequency domain
    f_transform = np.fft.fft2(gray)
    f_shift = np.fft.fftshift(f_transform)
    magnitude = np.abs(f_shift)
    
    # Create a mask to filter out strong linear patterns (scratches)
    # This is a simplified approach - in practice, we'll use spatial methods
    
    # Strategy 2: Improved scratch detection using morphological operations
    # Use adaptive thresholds based on image statistics
    mean_intensity = np.mean(gray)
    std_intensity = np.std(gray)
    median_intensity = np.median(gray)
    
    # Adjust thresholds based on detection mode
    if method == "aggressive":
        bright_multiplier = 0.8  # Much more sensitive for scratches
        dark_multiplier = 0.8
        # Also use percentile-based thresholds for better detection
        bright_percentile = 85  # Top 15% intensity
        dark_percentile = 15   # Bottom 15% intensity
    else:
        bright_multiplier = 1.2  # More sensitive than before
        dark_multiplier = 1.2
        bright_percentile = 90
        dark_percentile = 10
    
    # Method 1: Statistical thresholding
    bright_thresh = mean_intensity + bright_multiplier * std_intensity
    _, bright_mask1 = cv2.threshold(gray, int(min(255, bright_thresh)), 255, cv2.THRESH_BINARY)
    
    dark_thresh = mean_intensity - dark_multiplier * std_intensity
    _, dark_mask1 = cv2.threshold(gray, int(max(0, dark_thresh)), 255, cv2.THRESH_BINARY_INV)
    
    # Method 2: Percentile-based thresholding (catches more scratches)
    bright_percentile_val = np.percentile(gray, bright_percentile)
    dark_percentile_val = np.percentile(gray, dark_percentile)
    _, bright_mask2 = cv2.threshold(gray, int(bright_percentile_val), 255, cv2.THRESH_BINARY)
    _, dark_mask2 = cv2.threshold(gray, int(dark_percentile_val), 255, cv2.THRESH_BINARY_INV)
    
    # Method 3: Detect scratches as lines that differ significantly from local median
    # This catches scratches that might not be extreme outliers globally
    local_median = cv2.medianBlur(gray, 15)  # Local median over larger area
    diff_from_local = cv2.absdiff(gray, local_median)
    _, local_diff_mask = cv2.threshold(diff_from_local, int(std_intensity * 0.8), 255, cv2.THRESH_BINARY)
    
    # Combine all methods
    scratch_candidates = cv2.bitwise_or(bright_mask1, dark_mask1)
    scratch_candidates = cv2.bitwise_or(scratch_candidates, bright_mask2)
    scratch_candidates = cv2.bitwise_or(scratch_candidates, dark_mask2)
    scratch_candidates = cv2.bitwise_or(scratch_candidates, local_diff_mask)
    
    # Use morphological operations to find linear structures
    # Make kernels longer to catch more scratches
    # Horizontal scratches
    h_kernel_size = max(20, int(w / 20))  # Longer kernels for better detection
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_size, 1))
    h_lines = cv2.morphologyEx(scratch_candidates, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
    h_lines = cv2.dilate(h_lines, horizontal_kernel, iterations=2)
    
    # Vertical scratches
    v_kernel_size = max(20, int(h / 20))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_kernel_size))
    v_lines = cv2.morphologyEx(scratch_candidates, cv2.MORPH_OPEN, vertical_kernel, iterations=2)
    v_lines = cv2.dilate(v_lines, vertical_kernel, iterations=2)
    
    # Diagonal scratches (45 and 135 degrees)
    diag_kernel_size = max(15, int(min(h, w) / 25))
    # Create diagonal kernels
    diag_kernel_45 = np.zeros((diag_kernel_size, diag_kernel_size), np.uint8)
    cv2.line(diag_kernel_45, (0, diag_kernel_size-1), (diag_kernel_size-1, 0), 255, 1)
    diag_lines_45 = cv2.morphologyEx(scratch_candidates, cv2.MORPH_OPEN, diag_kernel_45, iterations=1)
    
    diag_kernel_135 = np.zeros((diag_kernel_size, diag_kernel_size), np.uint8)
    cv2.line(diag_kernel_135, (0, 0), (diag_kernel_size-1, diag_kernel_size-1), 255, 1)
    diag_lines_135 = cv2.morphologyEx(scratch_candidates, cv2.MORPH_OPEN, diag_kernel_135, iterations=1)
    
    # Diagonal scratches using Hough line detection (more sensitive)
    # Use lower thresholds to catch more scratches
    edges = cv2.Canny(gray, 30, 100)  # Lower thresholds for more edge detection
    hough_threshold = max(20, int(min(h, w) / 30))  # Lower threshold
    min_line_length = max(15, int(min(h, w) / 20))  # Shorter lines
    max_line_gap = 8  # Allow larger gaps
    
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=hough_threshold, 
                            minLineLength=min_line_length, maxLineGap=max_line_gap)
    
    scratch_mask = np.zeros_like(gray)
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            length = np.sqrt((x2-x1)**2 + (y2-y1)**2)
            # Accept shorter lines too (scratches can be short)
            if length > max(20, min(h, w) / 15):
                # Check if this line area differs from surroundings
                line_mask = np.zeros_like(gray)
                cv2.line(line_mask, (x1, y1), (x2, y2), 255, 3)  # Thicker line for region check
                line_region = gray[line_mask > 0]
                if len(line_region) > 0:
                    # Check if line is significantly different from local area
                    line_mean = np.mean(line_region)
                    # Get surrounding area
                    kernel = np.ones((15, 15), np.float32) / 225
                    local_mean = cv2.filter2D(gray.astype(np.float32), -1, kernel)
                    # Check if line differs from local mean
                    if abs(line_mean - local_mean[y1, x1]) > std_intensity * 0.5:
                        cv2.line(scratch_mask, (x1, y1), (x2, y2), 255, 2)  # Thicker for better coverage
    
    # Combine all scratch detections
    scratch_mask = cv2.bitwise_or(scratch_mask, h_lines)
    scratch_mask = cv2.bitwise_or(scratch_mask, v_lines)
    scratch_mask = cv2.bitwise_or(scratch_mask, diag_lines_45)
    scratch_mask = cv2.bitwise_or(scratch_mask, diag_lines_135)
    
    # Strategy 3: Dust spot detection (small isolated spots)
    # Use median filter to create a "clean" version
    median_filtered = cv2.medianBlur(gray, 5)
    diff = cv2.absdiff(gray, median_filtered)
    
    # Threshold the difference to find spots
    _, spot_mask = cv2.threshold(diff, int(mean_intensity * 0.3), 255, cv2.THRESH_BINARY)
    
    # Find contours and filter by size (dust spots are small)
    contours, _ = cv2.findContours(spot_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    dust_mask = np.zeros_like(gray)
    min_dust_area = 3
    max_dust_area = int(min(h, w) * min(h, w) * 0.001)  # Adaptive max area
    
    for contour in contours:
        area = cv2.contourArea(contour)
        if min_dust_area < area < max_dust_area:
            # Check circularity (dust spots are roughly circular)
            perimeter = cv2.arcLength(contour, True)
            if perimeter > 0:
                circularity = 4 * np.pi * area / (perimeter * perimeter)
                if circularity > 0.3:  # Roughly circular
                    cv2.drawContours(dust_mask, [contour], -1, 255, -1)
    
    # Strategy 4: Statistical outlier detection (conservative)
    # Only detect extreme outliers that are clearly damage
    if method == "aggressive":
        outlier_threshold = 2.5
    else:
        outlier_threshold = 3.5  # More conservative
    
    outlier_mask = ((gray > mean_intensity + outlier_threshold * std_intensity) | 
                    (gray < mean_intensity - outlier_threshold * std_intensity))
    outlier_mask = outlier_mask.astype(np.uint8) * 255
    
    # Only keep outliers that are isolated (not part of image structure)
    # Use opening to remove outliers that are part of larger structures
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    outlier_mask = cv2.morphologyEx(outlier_mask, cv2.MORPH_OPEN, kernel, iterations=2)
    
    # Combine all damage types
    damage_mask = cv2.bitwise_or(scratch_mask, dust_mask)
    damage_mask = cv2.bitwise_or(damage_mask, outlier_mask)
    
    # Clean up: remove very small isolated pixels
    damage_mask = cv2.morphologyEx(damage_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    # Close small gaps
    damage_mask = cv2.morphologyEx(damage_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    
    # Final cleanup: remove damage that's too large (likely image content, not damage)
    contours, _ = cv2.findContours(damage_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    final_mask = np.zeros_like(damage_mask)
    max_damage_area = int(h * w * 0.01)  # Max 1% of image area
    
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < max_damage_area:
            cv2.drawContours(final_mask, [contour], -1, 255, -1)
    
    # Slight dilation to ensure full coverage
    final_mask = cv2.dilate(final_mask, kernel, iterations=1)
    
    return final_mask


def detect_damage(img_array: np.ndarray) -> np.ndarray:
    """Wrapper for backward compatibility - uses improved v2 detection"""
    return detect_damage_v2(img_array, method="conservative")


@app.post("/api/test-restore")
async def test_restore(
    file: UploadFile = File(...),
    remove_damage: bool = Form(True),
    detection_mode: str = Form("conservative"),
    deblur: bool = Form(True),
    denoise: bool = Form(True),
    sharpen: float = Form(1.3),
    contrast: float = Form(110.0),
    brightness: float = Form(102.0),
    deblur_strength: float = Form(1.5),
    denoise_strength: float = Form(1.0),
):
    """Test endpoint for photo restoration: automatic damage removal, de-blur, and denoise"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    try:
        contents = await file.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        original_size = img.size
        img = downscale_image_if_needed(img)
        
        # Convert to numpy array for OpenCV processing
        img_array = np.array(img)
        
        # Step 1: Automatic damage removal (scratches, dust)
        if remove_damage:
            # Use the specified detection mode
            detection_method = detection_mode if detection_mode in ["conservative", "aggressive"] else "conservative"
            
            # Try multiple detection strategies
            damage_mask_conservative = detect_damage_v2(img_array, method="conservative")
            damage_mask_aggressive = detect_damage_v2(img_array, method="aggressive")
            
            # Use user-selected mode, or auto-select based on detection
            if detection_method == "aggressive":
                damage_mask = damage_mask_aggressive
                logger.info(f"Using aggressive detection: {np.sum(damage_mask > 0)} pixels")
            else:
                damage_pixels_conservative = np.sum(damage_mask_conservative > 0)
                damage_pixels_aggressive = np.sum(damage_mask_aggressive > 0)
                
                # If conservative finds very little, use aggressive
                if damage_pixels_conservative < (img_array.shape[0] * img_array.shape[1] * 0.001):
                    damage_mask = damage_mask_aggressive
                    logger.info(f"Auto-switched to aggressive detection: {damage_pixels_aggressive} pixels")
                else:
                    damage_mask = damage_mask_conservative
                    logger.info(f"Using conservative detection: {damage_pixels_conservative} pixels")
            
            # Check if we found any damage
            damage_pixel_count = np.sum(damage_mask > 0)
            total_pixels = damage_mask.shape[0] * damage_mask.shape[1]
            damage_percentage = (damage_pixel_count / total_pixels) * 100
            
            logger.info(f"Damage detection: {damage_pixel_count} pixels ({damage_percentage:.2f}% of image)")
            
            if damage_pixel_count > 0:
                # Convert to BGR for OpenCV
                img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                
                # Calculate dynamic radius based on image size and damage area
                max_side = max(img_bgr.shape[:2])
                damage_ratio = damage_pixel_count / total_pixels
                
                logger.info(f"Damage ratio: {damage_ratio:.4f}, Image size: {img_bgr.shape[:2]}")
                
                # Calculate radius based on damage type and size
                # Scratches need larger radius because they're thin but long
                # Check if we have mostly linear damage (scratches) vs spots (dust)
                # Count connected components to estimate scratch vs spot ratio
                contours, _ = cv2.findContours(damage_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    # Calculate aspect ratio of damage regions
                    aspect_ratios = []
                    for contour in contours:
                        if cv2.contourArea(contour) > 10:  # Ignore tiny noise
                            x, y, w_cont, h_cont = cv2.boundingRect(contour)
                            if min(w_cont, h_cont) > 0:
                                aspect = max(w_cont, h_cont) / min(w_cont, h_cont)
                                aspect_ratios.append(aspect)
                    
                    avg_aspect = np.mean(aspect_ratios) if aspect_ratios else 1.0
                    # High aspect ratio = linear (scratches), low = circular (dust)
                    is_mostly_scratches = avg_aspect > 3.0
                    
                    if is_mostly_scratches:
                        # Scratches: use larger radius to cover the width
                        dynamic_radius = int(max(5, min(12, max_side / 100)))  # Much larger for scratches
                        logger.info(f"Detected mostly scratches (avg aspect ratio: {avg_aspect:.2f}), using larger radius")
                    else:
                        # Spots/dust: smaller radius
                        if damage_ratio < 0.01:
                            dynamic_radius = int(max(3, min(6, max_side / 200)))
                        else:
                            dynamic_radius = int(max(4, min(8, max_side / 150)))
                        logger.info(f"Detected mostly spots (avg aspect ratio: {avg_aspect:.2f})")
                else:
                    # Fallback
                    dynamic_radius = int(max(5, min(10, max_side / 150)))
                
                logger.info(f"Using inpainting radius: {dynamic_radius}")
                
                # Try LaMa first (best quality) if available
                if _get_lama_manager() is not None:
                    inpainted = lama_inpaint_torch(img_bgr, damage_mask)
                    logger.info("Used LaMa for damage removal")
                elif _get_lama_session() is not None:
                    inpainted = lama_inpaint_onnx(img_bgr, damage_mask)
                    logger.info("Used LaMa ONNX for damage removal")
                else:
                    # Enhanced OpenCV inpainting with better post-processing
                    # First pass: Telea (faster, good for small areas)
                    inpainted_telea = cv2.inpaint(img_bgr, damage_mask, dynamic_radius, cv2.INPAINT_TELEA)
                    # Second pass: NS (better structure preservation)
                    inpainted_ns = cv2.inpaint(img_bgr, damage_mask, dynamic_radius, cv2.INPAINT_NS)
                    
                    # Blend the two results (NS is better for structure, Telea for texture)
                    inpainted = cv2.addWeighted(inpainted_telea, 0.4, inpainted_ns, 0.6, 0)
                    
                    # Apply post-processing with soft blending (like remove-painted-areas)
                    inpainted = post_process_inpainted_result(img_bgr, inpainted, damage_mask)
                    
                    # Additional soft blending to reduce artifacts
                    soft_mask = damage_mask.astype(np.float32) / 255.0
                    soft_mask = cv2.GaussianBlur(soft_mask, (0, 0), sigmaX=4, sigmaY=4)
                    soft_mask_3 = np.repeat(soft_mask[:, :, None], 3, axis=2)
                    inpainted = (soft_mask_3 * inpainted.astype(np.float32) + 
                                (1.0 - soft_mask_3) * img_bgr.astype(np.float32)).astype(np.uint8)
                    
                    logger.info(f"Used OpenCV for damage removal (radius={dynamic_radius})")
                
                # Convert back to RGB PIL Image
                img = Image.fromarray(cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB))
                logger.info(f"Damage removal completed. Processed {damage_pixel_count} pixels.")
            else:
                logger.warning(f"No damage detected in image. Try 'aggressive' detection mode or check if image actually has visible damage.")
        
        # Step 2: Noise/Grain Removal
        if denoise:
            if denoise_strength >= 0.5:
                img = img.filter(ImageFilter.MedianFilter(size=3))
            if denoise_strength >= 1.0:
                img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        
        # Step 3: De-blur
        if deblur:
            unsharp_percent = int(100 + (deblur_strength * 50))
            img = img.filter(ImageFilter.UnsharpMask(
                radius=2,
                percent=unsharp_percent,
                threshold=3
            ))
        
        # Step 4: Traditional enhancements
        if sharpen != 1.0:
            sharp_enh = ImageEnhance.Sharpness(img)
            img = sharp_enh.enhance(max(0.0, float(sharpen)))
        
        if contrast != 100.0:
            cont_enh = ImageEnhance.Contrast(img)
            img = cont_enh.enhance(max(0.0, float(contrast)) / 100.0)
        
        if brightness != 100.0:
            bri_enh = ImageEnhance.Brightness(img)
            img = bri_enh.enhance(max(0.0, float(brightness)) / 100.0)
        
        # Resize back to original if downscaled
        if img.size != original_size:
            img = img.resize(original_size, Image.Resampling.LANCZOS)
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Test restore error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Photo restoration error: {str(e)}")


@app.post("/api/test-upscayl")
async def test_upscayl(
    file: UploadFile = File(...),
    scale: int = Form(4),
    model: str = Form("realesrgan-x4plus"),
):
    """
    Test endpoint for Upscayl (Real-ESRGAN NCNN) image upscaling.
    Tests if Upscayl can restore/upscale images as claimed.
    
    Args:
        file: Image file to upscale
        scale: Upscale factor (2, 3, or 4). Default is 4.
        model: Model name. Options: realesrgan-x4plus (default), realesrnet-x4plus, 
               realesrgan-x4plus-anime, realesr-animevideov3
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    if scale not in [2, 3, 4]:
        raise HTTPException(status_code=400, detail="Scale must be 2, 3, or 4")
    
    # Validate model name - must match available model files
    valid_models = [
        "realesrgan-x4plus",
        "realesrgan-x4plus-anime",
        "realesr-animevideov3-x2",
        "realesr-animevideov3-x3",
        "realesr-animevideov3-x4",
    ]
    
    # Auto-correct common typos
    model_corrections = {
        "realesrganplus": "realesrgan-x4plus",
        "realesrgan-x4": "realesrgan-x4plus",
        "realesrgan": "realesrgan-x4plus",
    }
    if model in model_corrections:
        logger.info(f"Auto-correcting model name: {model} -> {model_corrections[model]}")
        model = model_corrections[model]
    
    if model not in valid_models:
        logger.warning(f"Invalid model name received: {model}. Valid models: {valid_models}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model name: '{model}'. Valid models: {', '.join(valid_models)}"
        )
    
    # For realesr-animevideov3 models, ensure scale matches model name
    if model.startswith("realesr-animevideov3-"):
        model_scale = int(model.split("-x")[-1])
        if scale != model_scale:
            logger.warning(f"Scale mismatch: model {model} requires scale {model_scale}, but got {scale}")
            scale = model_scale  # Auto-correct the scale
    
    binary_path = _check_upscayl_ncnn_available()
    if not binary_path:
        raise HTTPException(
            status_code=503,
            detail="Upscayl NCNN binary not available. Download from: https://github.com/xinntao/Real-ESRGAN/releases"
        )
    
    try:
        contents = await file.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        original_size = img.size
        
        # Create temporary files for input and output
        tmp_dir = tempfile.gettempdir()
        input_path = os.path.join(tmp_dir, f"upscayl_input_{os.getpid()}.png")
        output_path = os.path.join(tmp_dir, f"upscayl_output_{os.getpid()}.png")
        
        # Save input image
        img.save(input_path, format="PNG")
        logger.info(f"Saved input image to: {input_path} ({img.size})")
        
        try:
            # Build command for realesrgan-ncnn-vulkan (Upscayl backend)
            cmd = [
                binary_path,
                "-i", input_path,
                "-o", output_path,
                "-n", model,
                "-s", str(scale),
                "-f", "png",
                "-t", "0",  # Auto tile size
            ]
            
            # Add GPU ID if specified
            gpu_id = os.getenv("UPSCAYL_GPU_ID", "0")
            if gpu_id != "auto":
                cmd.extend(["-g", str(gpu_id)])
            
            logger.info(f"Running Upscayl NCNN: {' '.join(cmd)}")
            
            # Set working directory to where models are located
            binary_dir = os.path.dirname(os.path.abspath(binary_path)) or os.getcwd()
            models_dir = os.path.join(binary_dir, "models")
            
            # Ensure models are accessible
            if os.path.exists(UPSCAYL_MODELS_PATH) and os.path.isdir(UPSCAYL_MODELS_PATH):
                if not os.path.exists(models_dir) or UPSCAYL_MODELS_PATH != models_dir:
                    try:
                        os.makedirs(models_dir, exist_ok=True)
                        import shutil
                        for model_file in os.listdir(UPSCAYL_MODELS_PATH):
                            src = os.path.join(UPSCAYL_MODELS_PATH, model_file)
                            dst = os.path.join(models_dir, model_file)
                            if os.path.isfile(src) and (not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst)):
                                shutil.copy2(src, dst)
                                logger.debug(f"Copied model file: {model_file}")
                    except Exception as e:
                        logger.warning(f"Could not set up models directory: {e}")
            
            # Run the command
            logger.info(f"Working directory: {binary_dir}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                cwd=binary_dir,
            )
            
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                logger.error(f"Upscayl NCNN failed (return code {result.returncode}): {error_msg}")
                logger.error(f"Command was: {' '.join(cmd)}")
                logger.error(f"Model: {model}, Scale: {scale}")
                
                # Provide more helpful error messages
                if result.returncode == -11:  # SIGSEGV (segmentation fault)
                    detail_msg = f"Model '{model}' not found or invalid. Available models: {', '.join(valid_models)}"
                elif "model" in error_msg.lower() or "not found" in error_msg.lower():
                    detail_msg = f"Model '{model}' not found. Check that model files exist in {models_dir}"
                else:
                    detail_msg = f"Upscaling failed: {error_msg[:500]}"
                
                raise HTTPException(
                    status_code=500,
                    detail=detail_msg
                )
            
            # Check if output file exists and is valid
            if not os.path.exists(output_path):
                logger.error(f"Output file not created at {output_path}")
                logger.error(f"Command stdout: {result.stdout}")
                logger.error(f"Command stderr: {result.stderr}")
                raise HTTPException(status_code=500, detail="Output file was not created. Check logs for details.")
            
            # Check file size
            file_size = os.path.getsize(output_path)
            if file_size == 0:
                logger.error(f"Output file is empty: {output_path}")
                raise HTTPException(status_code=500, detail="Output file is empty. Processing may have failed.")
            
            logger.info(f"Output file created successfully: {output_path} ({file_size} bytes)")
            
            # Load the upscaled image
            try:
                upscaled_img = Image.open(output_path).convert("RGB")
                logger.info(f"Loaded upscaled image: {upscaled_img.size} (original: {original_size})")
            except Exception as e:
                logger.error(f"Failed to load output image: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to load output image: {str(e)}")
            
            # Verify the output size is reasonable
            expected_size = (original_size[0] * scale, original_size[1] * scale)
            if upscaled_img.size != expected_size:
                logger.info(f"Output size {upscaled_img.size} != expected {expected_size}, resizing")
                upscaled_img = upscaled_img.resize(expected_size, Image.Resampling.LANCZOS)
            
            buf = io.BytesIO()
            upscaled_img.save(buf, format="PNG", optimize=False)
            return Response(content=buf.getvalue(), media_type="image/png")
            
        finally:
            # Clean up temporary files
            try:
                os.unlink(input_path)
            except Exception:
                pass
            try:
                os.unlink(output_path)
            except Exception:
                pass
        
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Upscaling timed out after 5 minutes")
    except Exception as e:
        logger.error(f"Test upscayl error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upscayl error: {str(e)}")


@app.post("/api/create-payment-checkout")
async def create_payment_checkout(request: Request):
    """Create a Dodo Payments checkout session for text removal."""
    dodo_api_key = os.getenv("DODO_PAYMENTS_API_KEY")
    dodo_product_id = os.getenv("DODO_PAYMENTS_PRODUCT_ID")
    
    # Log for debugging (first 20 chars only for security)
    logger.info(f"Dodo API Key from env: {'SET' if dodo_api_key else 'NOT SET'}, length: {len(dodo_api_key) if dodo_api_key else 0}, starts with: {dodo_api_key[:20] if dodo_api_key else 'N/A'}...")
    logger.info(f"Dodo Product ID from env: {dodo_product_id}")
    
    if not dodo_api_key:
        raise HTTPException(status_code=500, detail="DODO_PAYMENTS_API_KEY environment variable not set")
    if not dodo_product_id:
        raise HTTPException(status_code=500, detail="DODO_PAYMENTS_PRODUCT_ID environment variable not set")
    
    try:
        import dodopayments
        
        # Get the return URL from request or use default
        try:
            request_data = await request.json()
            return_url = request_data.get("return_url", "http://localhost:8080/test-replicate-text-removal.html")
        except:
            return_url = "http://localhost:8080/test-replicate-text-removal.html"
        
        # Initialize Dodo Payments client
        # Make sure there are no extra spaces or newlines in the key
        clean_api_key = dodo_api_key.strip()
        
        # Use test_mode for local development, live_mode for production
        # Check if we're running in Cloud Run (production) vs local
        is_production = IS_PRODUCTION
        dodo_env = "live_mode" if is_production else "test_mode"
        
        logger.info(f"Creating Dodo Payments client with {dodo_env}, API key length: {len(clean_api_key)}")
        client = dodopayments.DodoPayments(
            bearer_token=clean_api_key,
            environment=dodo_env
        )
        
        logger.info(f"Creating checkout session for product: {dodo_product_id}")
        
        # Create checkout session with return URL
        # Dodo Payments will replace {CHECKOUT_SESSION_ID} with the actual session ID
        checkout_session = client.checkout_sessions.create(
            product_cart=[
                {
                    "product_id": dodo_product_id,
                    "quantity": 1,
                }
            ],
            return_url=f"{return_url}?session_id={{CHECKOUT_SESSION_ID}}"
        )
        
        # Extract session ID from checkout URL
        # The session ID is the last part of the URL path: .../session/cks_xxxxx
        session_id = None
        if hasattr(checkout_session, 'id'):
            session_id = checkout_session.id
        elif hasattr(checkout_session, 'checkout_url'):
            # Extract session ID from URL path (last segment after /session/)
            url_parts = checkout_session.checkout_url.rstrip('/').split('/')
            if 'session' in url_parts:
                session_idx = url_parts.index('session')
                if session_idx + 1 < len(url_parts):
                    session_id = url_parts[session_idx + 1]
        
        return {
            "checkout_url": checkout_session.checkout_url,
            "session_id": session_id
        }
        
    except Exception as e:
        logger.error(f"Error creating Dodo Payments checkout: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating checkout session: {str(e)}")

@app.post("/api/verify-payment")
async def verify_payment(
    request: Request,
    session_id: str = Form(...),
):
    """Verify if payment was successful before processing."""
    dodo_api_key = os.getenv("DODO_PAYMENTS_API_KEY")
    
    if not dodo_api_key:
        raise HTTPException(status_code=500, detail="DODO_PAYMENTS_API_KEY environment variable not set")
    
    logger.info(f"Verifying payment for session_id: {session_id}")
    
    try:
        import dodopayments
        
        # Initialize Dodo Payments client
        # Use test_mode for local development, live_mode for production
        is_production = IS_PRODUCTION
        dodo_env = "live_mode" if is_production else "test_mode"
        
        client = dodopayments.DodoPayments(
            bearer_token=dodo_api_key,
            environment=dodo_env
        )
        
        # Get checkout session status using retrieve method
        # Session ID should be in format: cks_xxxxx (extracted from URL)
        checkout_session = client.checkout_sessions.retrieve(session_id)
        
        # Check payment status - the checkout session has payment_status attribute
        payment_status = getattr(checkout_session, 'payment_status', None)
        logger.info(f"Checkout session payment_status: {payment_status}")
        
        # Payment is verified if payment_status indicates success
        if payment_status and payment_status.lower() in ['succeeded', 'completed', 'paid']:
            return {"verified": True, "message": "Payment verified"}
        
        return {"verified": False, "message": f"Payment not completed. Status: {payment_status}", "payment_status": payment_status}
        
    except Exception as e:
        logger.error(f"Error verifying payment: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error verifying payment: {str(e)}")

@app.post("/api/test-replicate-text-removal")
async def test_replicate_text_removal(
    request: Request,
    file: UploadFile = File(...),
    payment_session_id: Optional[str] = Form(None),
):
    """Test text removal using Replicate's FLUX Kontext API. Requires payment verification."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    replicate_api_token = os.getenv("REPLICATE_API_TOKEN")
    if not replicate_api_token:
        raise HTTPException(status_code=500, detail="REPLICATE_API_TOKEN environment variable not set")
    
    tmp_file_path = None
    try:
        import replicate
        
        # Read image file
        contents = await file.read()
        
        # Create a temporary file for Replicate API
        # Replicate accepts file paths, file-like objects, or URLs
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Replicate client
        client = replicate.Client(api_token=replicate_api_token)
        
        # Run the Nano Banana Pro model for text removal
        # Try passing the file path directly, or as a file object
        logger.info(f"Running Nano Banana Pro model with image: {tmp_file_path}")
        
        # Try using file path directly - Replicate should handle file paths
        # If that doesn't work, we can try uploading to get a URL first
        with open(tmp_file_path, 'rb') as img_file:
            output = client.run(
                "google/nano-banana-pro",
                input={
                    "prompt": "A clean version of this image with all text, words, watermarks, captions, and labels removed. All visual elements, colors, objects, layout, and composition remain exactly the same. Only text is removed.",
                    "image_input": [img_file],
                    "aspect_ratio": "match_input_image",
                    "output_format": "png",
                    "resolution": "2K",
                    "safety_filter_level": "block_only_high"
                }
            )
        
        logger.info(f"Nano Banana Pro output type: {type(output)}")
        
        # Handle output - could be FileOutput, URL string, or list
        result_bytes = None
        
        if isinstance(output, str):
            # If output is a URL string, download it
            logger.info(f"Output is URL: {output}")
            response = requests.get(output, timeout=120)
            response.raise_for_status()
            result_bytes = response.content
        elif isinstance(output, (list, tuple)) and len(output) > 0:
            # Handle list of outputs
            file_output = output[0]
            if isinstance(file_output, str):
                # URL in list
                response = requests.get(file_output, timeout=120)
                response.raise_for_status()
                result_bytes = response.content
            elif hasattr(file_output, 'read'):
                result_bytes = file_output.read()
            else:
                raise HTTPException(status_code=500, detail=f"Unexpected output format in list: {type(file_output)}")
        elif hasattr(output, 'read'):
            # Handle FileOutput object
            logger.info("Output is FileOutput, reading bytes...")
            result_bytes = output.read()
        else:
            logger.error(f"Unexpected output format: {type(output)}, value: {output}")
            raise HTTPException(status_code=500, detail=f"Unexpected output format from Replicate: {type(output)}")
        
        if not result_bytes or len(result_bytes) == 0:
            raise HTTPException(status_code=500, detail="No image content found in response")
        
        logger.info(f"Successfully got {len(result_bytes)} bytes from Nano Banana Pro")
        return Response(content=result_bytes, media_type="image/png")
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Replicate Nano Banana Pro: {error_msg}", exc_info=True)
        
        # Provide more helpful error messages
        if "No image content" in error_msg or "ModelError" in str(type(e)):
            raise HTTPException(
                status_code=500, 
                detail=f"Model did not return an image. This might mean: 1) The model doesn't support this use case, 2) The prompt needs adjustment, or 3) The image format isn't supported. Error: {error_msg}"
            )
        else:
            raise HTTPException(status_code=500, detail=f"Error processing image with Replicate: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

async def _fallback_to_replicate_text_removal(image_contents: bytes, logger) -> bytes:
    """Fallback function to use Replicate's flux-kontext-apps/text-removal model."""
    replicate_api_token = os.getenv("REPLICATE_API_TOKEN")
    if not replicate_api_token:
        raise Exception("REPLICATE_API_TOKEN not available for fallback")
    
    import replicate
    import tempfile
    import requests
    
    tmp_file_path = None
    try:
        # Create a temporary file for Replicate API
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Replicate client
        client = replicate.Client(api_token=replicate_api_token)
        
        logger.info("Falling back to Replicate flux-kontext-apps/text-removal model")
        
        # Use flux-kontext-apps/text-removal model with input_image parameter
        with open(tmp_file_path, 'rb') as img_file:
            output = client.run(
                "flux-kontext-apps/text-removal",
                input={
                    "input_image": img_file,
                }
            )
        
        logger.info(f"Replicate output type: {type(output)}")
        
        # Handle output - could be FileOutput, URL string, or list
        result_bytes = None
        
        if isinstance(output, str):
            # If output is a URL string, download it
            logger.info(f"Output is URL: {output}")
            response = requests.get(output, timeout=120)
            response.raise_for_status()
            result_bytes = response.content
        elif isinstance(output, (list, tuple)) and len(output) > 0:
            # Handle list of outputs
            file_output = output[0]
            if isinstance(file_output, str):
                # URL in list
                response = requests.get(file_output, timeout=120)
                response.raise_for_status()
                result_bytes = response.content
            elif hasattr(file_output, 'read'):
                result_bytes = file_output.read()
            else:
                raise Exception(f"Unexpected output format in list: {type(file_output)}")
        elif hasattr(output, 'read'):
            # Handle FileOutput object
            logger.info("Output is FileOutput, reading bytes...")
            result_bytes = output.read()
        else:
            raise Exception(f"Unexpected output format from Replicate: {type(output)}")
        
        if not result_bytes or len(result_bytes) == 0:
            raise Exception("No image content found in Replicate response")
        
        logger.info(f"Successfully got {len(result_bytes)} bytes from Replicate fallback")
        return result_bytes
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/test-google-text-removal")
async def test_google_text_removal(
    request: Request,
    file: UploadFile = File(...),
    payment_session_id: Optional[str] = Form(None),
):
    """Test text removal using Google's Gemini image editing API with Replicate fallback. Requires payment verification."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Verify payment session and prevent reuse
    if not payment_session_id:
        raise HTTPException(status_code=400, detail="Payment session ID is required")
    
    # Check if this payment session has already been used
    if payment_session_id in _USED_PAYMENT_SESSIONS:
        raise HTTPException(
            status_code=403, 
            detail="This payment session has already been used. Please make a new payment to process another image."
        )
    
    # Verify payment is actually successful
    try:
        import dodopayments
        dodo_api_key = os.getenv("DODO_PAYMENTS_API_KEY")
        if dodo_api_key:
            # Use same environment as checkout creation (test_mode for local, live_mode for production)
            is_production = IS_PRODUCTION
            dodo_env = "live_mode" if is_production else "test_mode"
            
            client = dodopayments.DodoPayments(
                bearer_token=dodo_api_key.strip(),
                environment=dodo_env
            )
            checkout_session = client.checkout_sessions.retrieve(payment_session_id)
            payment_status = getattr(checkout_session, 'payment_status', None)
            if not payment_status or payment_status.lower() not in ['succeeded', 'completed', 'paid']:
                raise HTTPException(status_code=403, detail="Payment not verified or incomplete")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not verify payment status (proceeding anyway): {str(e)}")
    
    # Try both environment variable names for compatibility
    google_api_key = os.getenv("GOOGLE_GENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not google_api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_GENAI_API_KEY or GEMINI_API_KEY environment variable not set")
    
    # Read and save image file contents before processing (needed for fallback)
    image_contents = await file.read()
    
    tmp_file_path = None
    try:
        from google import genai
        from google.genai import types
        from PIL import Image
        import importlib.metadata
        try:
            genai_version = importlib.metadata.version("google-genai")
            logger.info(f"google-genai SDK version: {genai_version}")
        except Exception:
            logger.warning("Could not determine google-genai version")
        
        # Create a temporary file for PIL to open
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Google GenAI client
        # Pass API key explicitly as per documentation: https://ai.google.dev/gemini-api/docs/api-key
        client = genai.Client(api_key=google_api_key)
        
        # Open image with PIL
        image = Image.open(tmp_file_path)
        
        # Create prompt for text removal
        prompt = "Provide a clean version of this image with only the background pattern visible, maintaining the exact style but without any text or overlays."
        
        logger.info(f"Running Google Gemini image editing model (gemini-3-pro-image-preview) for text removal")
        
        # Call Google Gemini API for image editing with retry logic for quota limits
        # According to docs: https://ai.google.dev/gemini-api/docs/image-generation
        # For image editing (text-and-image-to-image), pass both prompt and image to generate_content
        # Using gemini-3-pro-image-preview for better quality and professional asset production
        import time
        max_retries = 3
        retry_delay = 5  # Start with 5 seconds
        
        response = None
        for attempt in range(max_retries):
            try:
                # For gemini-3-pro-image-preview, we must explicitly specify response_modalities
                # to allow IMAGE output. Without this, the model may only return text.
                # Configure with response_modalities and safety settings to prevent blocking
                # Safety settings set to BLOCK_NONE to allow image editing requests
                # Use dictionary format to avoid AttributeError with SDK types
                config = {
                    "response_modalities": ["TEXT", "IMAGE"],
                    "safety_settings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                    ]
                }
                
                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt, image],
                    config=config
                )
                
                logger.info(f"Response received. Type: {type(response)}")
                if hasattr(response, 'candidates') and response.candidates:
                    logger.info(f"Response has {len(response.candidates)} candidates")
                
                break  # Success, exit retry loop
            except Exception as e:
                error_str = str(e)
                # Check if it's a quota/rate limit error (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    if attempt < max_retries - 1:
                        # Extract retry delay from error if available, otherwise use exponential backoff
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Quota exceeded, retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        # Last attempt failed, raise the error
                        raise HTTPException(
                            status_code=429,
                            detail=f"Quota exceeded. Please wait and try again later, or upgrade your Google AI plan. Error: {error_str}"
                        )
                else:
                    # Not a quota error, raise immediately
                    raise
        
        # Extract image from response with safe checking to prevent NoneType errors
        # Based on Google GenAI library, response should have candidates[0].content.parts
        result_bytes = None
        
        try:
            # Safe checking: verify response structure before accessing parts
            if not response:
                raise HTTPException(status_code=500, detail="No response received from Google Gemini API")
            
            # Check if candidates exist and have content
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.error(f"Response has no candidates. Response type: {type(response)}")
                raise HTTPException(
                    status_code=500, 
                    detail="Google Gemini API returned no candidates. This might indicate the request was blocked by safety filters or the API call failed."
                )
            
            candidate = response.candidates[0]
            logger.info(f"Candidate type: {type(candidate)}")
            logger.info(f"Candidate attributes: {[attr for attr in dir(candidate) if not attr.startswith('_')]}")
            logger.info(f"Candidate has content: {hasattr(candidate, 'content')}")
            if hasattr(candidate, 'finish_reason'):
                logger.info(f"Finish reason: {candidate.finish_reason}")
            if hasattr(candidate, 'safety_ratings'):
                logger.info(f"Safety ratings: {candidate.safety_ratings}")
            
            if not hasattr(candidate, 'content') or not candidate.content:
                finish_reason = getattr(candidate, 'finish_reason', None)
                logger.error(f"Candidate has no content. Finish reason: {finish_reason}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Google Gemini API candidate has no content. Finish reason: {finish_reason}"
                )
            
            # Safely get parts - check if parts exists and is not None or empty
            parts = getattr(candidate.content, 'parts', None)
            logger.info(f"Parts from candidate.content: {parts}, type: {type(parts)}, length: {len(parts) if parts else 'N/A'}")
            
            if not parts:
                # Fallback to direct parts access (as shown in some examples)
                parts = getattr(response, 'parts', None)
                logger.info(f"Parts from response: {parts}, type: {type(parts)}, length: {len(parts) if parts else 'N/A'}")
            
            if not parts or (isinstance(parts, list) and len(parts) == 0):
                # Log the actual structure for debugging
                finish_reason = getattr(candidate, 'finish_reason', None)
                safety_ratings = getattr(candidate, 'safety_ratings', None)
                logger.error(f"Response type: {type(response)}, attributes: {[attr for attr in dir(response) if not attr.startswith('_')]}")
                logger.error(f"Candidate type: {type(candidate)}, content type: {type(candidate.content) if candidate.content else None}")
                logger.error(f"Finish reason: {finish_reason}")
                logger.error(f"Safety ratings: {safety_ratings}")
                logger.error(f"Candidate attributes: {[attr for attr in dir(candidate) if not attr.startswith('_')]}")
                if candidate.content:
                    logger.error(f"Content attributes: {[attr for attr in dir(candidate.content) if not attr.startswith('_')]}")
                
                error_detail = "Response contained no parts. This might indicate the request was blocked or failed."
                if finish_reason:
                    error_detail += f" Finish reason: {finish_reason}"
                if safety_ratings:
                    error_detail += f" Safety ratings: {safety_ratings}"
                
                raise HTTPException(status_code=500, detail=error_detail)
            
            all_text_parts = []
            # Now safely iterate over parts (guaranteed to be not None)
            for part in parts:
                logger.info(f"Part type: {type(part)}, attributes: {[attr for attr in dir(part) if not attr.startswith('_')]}")
                
                if hasattr(part, 'text') and part.text:
                    logger.info(f"Text response from model: {part.text}")
                    all_text_parts.append(part.text)
                elif hasattr(part, 'inline_data') and part.inline_data:
                    # Extract image data from inline_data
                    inline_data = part.inline_data
                    logger.info(f"inline_data type: {type(inline_data)}, attributes: {[attr for attr in dir(inline_data) if not attr.startswith('_')]}")
                    
                    # The inline_data should have mime_type and data attributes
                    # Data is base64 encoded string
                    if hasattr(inline_data, 'data'):
                        data_value = inline_data.data
                        logger.info(f"Data type: {type(data_value)}, length: {len(data_value) if isinstance(data_value, (str, bytes)) else 'N/A'}")
                        
                        # Check if it's already bytes or needs decoding
                        if isinstance(data_value, bytes):
                            result_bytes = data_value
                        elif isinstance(data_value, str):
                            # Decode base64 string
                            image_bytes = base64.b64decode(data_value)
                            result_bytes = image_bytes
                        else:
                            logger.error(f"Unexpected data type: {type(data_value)}")
                            raise HTTPException(status_code=500, detail=f"Unexpected inline_data.data type: {type(data_value)}")
                        
                        logger.info(f"Successfully got {len(result_bytes)} bytes from Google Gemini")
                        break  # Found image, exit loop
                    elif hasattr(inline_data, 'bytes'):
                        # Alternative: if data is already bytes
                        result_bytes = inline_data.bytes
                        logger.info(f"Successfully got {len(result_bytes)} bytes from Google Gemini")
                        break  # Found image, exit loop
                    else:
                        logger.error(f"inline_data has no 'data' or 'bytes' attribute")
                        logger.error(f"inline_data structure: {type(inline_data)}, attributes: {[attr for attr in dir(inline_data) if not attr.startswith('_')]}")
                        raise HTTPException(status_code=500, detail="Could not extract image data from inline_data")
                else:
                    logger.warning(f"Part has no text or inline_data: {part}")
            
            # If no image was found but we have text, log it as an error
            if not result_bytes and all_text_parts:
                combined_text = "\n".join(all_text_parts)
                logger.error(f"Model returned text instead of image. Response text: {combined_text}")
                raise HTTPException(
                    status_code=500, 
                    detail=f"Google Gemini model returned text instead of an image. This might indicate the model doesn't support image editing or the prompt needs adjustment. Response: {combined_text[:200]}"
                )
        except AttributeError as e:
            logger.error(f"Error accessing response structure: {e}")
            logger.error(f"Response type: {type(response)}, attributes: {[attr for attr in dir(response) if not attr.startswith('_')]}")
            raise HTTPException(status_code=500, detail=f"Error parsing Google Gemini response: {str(e)}")
        
        if not result_bytes or len(result_bytes) == 0:
            # Log the full response structure for debugging
            logger.error(f"No image content found. Response structure: {type(response)}")
            logger.error(f"Response has 'text' attribute: {hasattr(response, 'text')}")
            if hasattr(response, 'text'):
                logger.error(f"Response text: {response.text}")
            if hasattr(response, 'candidates') and response.candidates:
                logger.error(f"Number of candidates: {len(response.candidates)}")
                for idx, candidate in enumerate(response.candidates):
                    logger.error(f"Candidate {idx} type: {type(candidate)}")
                    if hasattr(candidate, 'content'):
                        logger.error(f"Candidate {idx} content type: {type(candidate.content)}")
                        if hasattr(candidate.content, 'parts'):
                            logger.error(f"Candidate {idx} parts count: {len(candidate.content.parts)}")
                            for part_idx, part in enumerate(candidate.content.parts):
                                logger.error(f"  Part {part_idx}: {type(part)}, has text: {hasattr(part, 'text')}, has inline_data: {hasattr(part, 'inline_data')}")
                                if hasattr(part, 'text') and part.text:
                                    logger.error(f"    Text content: {part.text[:200]}")
            error_detail = "No image content found in response from Google Gemini"
            if hasattr(response, 'text') and response.text:
                error_detail += f". Model returned text: {response.text[:200]}"
            raise HTTPException(status_code=500, detail=error_detail)
        
        # Log the first few bytes to verify it's actually image data
        if len(result_bytes) < 1000:
            logger.warning(f"Received very small response ({len(result_bytes)} bytes). First 100 bytes (hex): {result_bytes[:100].hex()}")
            logger.warning(f"First 100 bytes (ascii): {result_bytes[:100]}")
        
        # Verify it's a valid image by checking magic bytes
        # PNG starts with \x89PNG, JPEG starts with \xff\xd8
        is_png = result_bytes[:4] == b'\x89PNG'
        is_jpeg = result_bytes[:2] == b'\xff\xd8'
        
        if not is_png and not is_jpeg:
            logger.error(f"Response does not appear to be a valid image. First bytes: {result_bytes[:20]}")
            raise HTTPException(
                status_code=500, 
                detail=f"Response from Google Gemini does not appear to be image data. Received {len(result_bytes)} bytes."
            )
        
        # Determine media type based on image format
        media_type = "image/png" if is_png else "image/jpeg"
        logger.info(f"Successfully validated {media_type} image ({len(result_bytes)} bytes)")
        
        # Mark this payment session as used to prevent reuse (only after successful processing)
        _USED_PAYMENT_SESSIONS.add(payment_session_id)
        logger.info(f"Payment session {payment_session_id} marked as used")
        
        return Response(content=result_bytes, media_type=media_type)
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Google Gemini image editing: {error_msg}", exc_info=True)
        
        # Fallback to Replicate if Gemini fails (only in production)
        # Skip fallback for payment/auth errors or if already tried Replicate
        is_production = IS_PRODUCTION
        if is_production and not any(err in error_msg.lower() for err in ['payment', 'auth', 'session', '403', '401', '400']):
            try:
                logger.info("Attempting fallback to Replicate flux-kontext-apps/text-removal")
                result_bytes = await _fallback_to_replicate_text_removal(image_contents, logger)
                
                # Verify it's a valid image
                is_png = result_bytes[:4] == b'\x89PNG'
                is_jpeg = result_bytes[:2] == b'\xff\xd8'
                if not is_png and not is_jpeg:
                    raise Exception("Replicate fallback did not return a valid image")
                
                media_type = "image/png" if is_png else "image/jpeg"
                
                # Mark payment session as used after successful fallback processing
                _USED_PAYMENT_SESSIONS.add(payment_session_id)
                logger.info(f"Payment session {payment_session_id} marked as used (Replicate fallback)")
                
                logger.info(f"Successfully processed image using Replicate fallback ({len(result_bytes)} bytes)")
                return Response(content=result_bytes, media_type=media_type)
            except Exception as fallback_error:
                logger.error(f"Replicate fallback also failed: {str(fallback_error)}", exc_info=True)
                # If fallback fails, raise original Gemini error
                raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}. Fallback to Replicate also failed: {str(fallback_error)}")
        
        # No fallback attempted or fallback not available - raise original error
        raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/remove-gemini-watermark")
async def remove_gemini_watermark(
    request: Request,
    file: UploadFile = File(...),
):
    """Remove Gemini watermark from images using LaMa inpainting. Targets bottom-right corner area."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("gemini_watermark_removal_request", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
    })
    
    try:
        contents = await file.read()
        file_size_mb = len(contents) / (1024 * 1024)
        
        # Enforce file size limit (10MB) to prevent excessive resource usage
        MAX_FILE_SIZE_MB = 10.0
        if file_size_mb > MAX_FILE_SIZE_MB:
            raise HTTPException(
                status_code=400, 
                detail=f"File size ({file_size_mb:.1f}MB) exceeds maximum allowed size ({MAX_FILE_SIZE_MB}MB). Please compress or resize your image first."
            )
        
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        original_size = image.size
        
        # Process at original size - no downscaling/upscaling to preserve perfect quality
        # LaMa inpainting can handle large images, and watermark removal only processes a small corner area
        # This ensures zero quality loss from resampling
        img_array = np.array(image)
        
        # Convert RGB to BGR for OpenCV
        img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        h, w = img_bgr.shape[:2]
        
        # Create mask for bottom-right corner where Gemini watermark typically appears
        # Gemini watermarks are usually small, so we'll mask a region in the bottom-right
        # Typically around 5-10% of image width/height
        mask_width = max(60, int(w * 0.15))  # At least 60px or 15% of width
        mask_height = max(40, int(h * 0.12))  # At least 40px or 12% of height
        
        # Create binary mask (white = area to inpaint, black = keep)
        watermark_mask = np.zeros((h, w), dtype=np.uint8)
        
        # Fill bottom-right corner region
        watermark_mask[h - mask_height:, w - mask_width:] = 255
        
        # Optionally expand the mask slightly to ensure we get the entire watermark
        # Apply small dilation to capture watermark edges
        kernel = np.ones((3, 3), np.uint8)
        watermark_mask = cv2.dilate(watermark_mask, kernel, iterations=1)
        
        logger.info(f"Created watermark mask: {mask_width}x{mask_height} region at bottom-right corner")
        
        # Try LaMa first (best quality) if available
        # Use conservative_blend=True to minimize blur on subject areas
        if _get_lama_manager() is not None:
            inpainted = lama_inpaint_torch(img_bgr, watermark_mask, conservative_blend=True)
            logger.info("Used LaMa (PyTorch) for watermark removal with conservative blending")
        elif _get_lama_session() is not None:
            inpainted = lama_inpaint_onnx(img_bgr, watermark_mask)
            logger.info("Used LaMa ONNX for watermark removal")
        else:
            # Fallback to OpenCV inpainting
            max_side = max(h, w)
            dynamic_radius = int(max(5, min(15, max_side / 200)))
            
            # Use Telea algorithm first
            inpainted_telea = cv2.inpaint(img_bgr, watermark_mask, dynamic_radius, cv2.INPAINT_TELEA)
            # Then NS for better structure
            inpainted_ns = cv2.inpaint(img_bgr, watermark_mask, dynamic_radius, cv2.INPAINT_NS)
            # Blend the two results
            inpainted = cv2.addWeighted(inpainted_telea, 0.4, inpainted_ns, 0.6, 0)
            
            # Apply soft blending to reduce artifacts - use smaller blur for less aggressive blending
            soft_mask = watermark_mask.astype(np.float32) / 255.0
            soft_mask = cv2.GaussianBlur(soft_mask, (0, 0), sigmaX=1.5, sigmaY=1.5)  # Reduced from 3 to 1.5
            soft_mask_3 = np.repeat(soft_mask[:, :, None], 3, axis=2)
            inpainted = (soft_mask_3 * inpainted.astype(np.float32) + 
                        (1.0 - soft_mask_3) * img_bgr.astype(np.float32)).astype(np.uint8)
            
            logger.info(f"Used OpenCV inpainting for watermark removal (radius={dynamic_radius})")
        
        # Convert back to RGB PIL Image
        result_rgb = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)
        result_image = Image.fromarray(result_rgb)
        
        # No upscaling needed - image was processed at original size
        
        # Save to bytes with maximum quality (PNG is lossless)
        buf = io.BytesIO()
        # Use optimize=False to ensure fastest encoding, quality is already lossless for PNG
        result_image.save(buf, format="PNG", optimize=False)
        
        log_user_action("gemini_watermark_removal_success", {
            "original_size": f"{original_size[0]}x{original_size[1]}",
            "processed_size": f"{w}x{h}",
            "mask_size": f"{mask_width}x{mask_height}",
            "file_size_mb": round(file_size_mb, 2),
            "was_downscaled": False
        })
        
        return Response(content=buf.getvalue(), media_type="image/png")
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error removing Gemini watermark: {error_msg}", exc_info=True)
        log_user_action("gemini_watermark_removal_error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Error removing watermark: {error_msg}")

@app.post("/api/test-remove-gemini-watermark")
async def test_remove_gemini_watermark(
    request: Request,
    file: UploadFile = File(...),
    mask_width_percent: float = Form(15.0),
    mask_height_percent: float = Form(12.0),
):
    """Test endpoint: Remove Gemini watermark from images using LaMa inpainting. Targets bottom-right corner area.
    Uses EXACT same inpainting logic as production endpoint, only allows mask size adjustment.
    
    Parameters:
    - mask_width_percent: Width of mask as percentage of image width (default 15%)
    - mask_height_percent: Height of mask as percentage of image height (default 12%)
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        original_size = image.size
        
        # Process at original size - no downscaling/upscaling to preserve perfect quality (EXACT same as production)
        img_array = np.array(image)
        
        # Convert RGB to BGR for OpenCV
        img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        h, w = img_bgr.shape[:2]
        
        # Create mask for bottom-right corner where Gemini watermark typically appears
        # Use adjustable parameters from form
        mask_width = max(60, int(w * mask_width_percent / 100.0))  # At least 60px or specified % of width
        mask_height = max(40, int(h * mask_height_percent / 100.0))  # At least 40px or specified % of height
        
        # Create binary mask (white = area to inpaint, black = keep)
        watermark_mask = np.zeros((h, w), dtype=np.uint8)
        
        # Fill bottom-right corner region
        watermark_mask[h - mask_height:, w - mask_width:] = 255
        
        # Optionally expand the mask slightly to ensure we get the entire watermark
        # Apply small dilation to capture watermark edges
        kernel = np.ones((3, 3), np.uint8)
        watermark_mask = cv2.dilate(watermark_mask, kernel, iterations=1)
        
        logger.info(f"Test endpoint: Created watermark mask: {mask_width}x{mask_height} region at bottom-right corner (width={mask_width_percent}%, height={mask_height_percent}%)")
        
        # Try LaMa first (best quality) if available
        # Use EXACT same logic as production endpoint
        if _get_lama_manager() is not None:
            inpainted = lama_inpaint_torch(img_bgr, watermark_mask, conservative_blend=True)
            logger.info("Test endpoint: Used LaMa (PyTorch) for watermark removal with conservative blending")
        elif _get_lama_session() is not None:
            inpainted = lama_inpaint_onnx(img_bgr, watermark_mask)
            logger.info("Test endpoint: Used LaMa ONNX for watermark removal")
        else:
            # Fallback to OpenCV inpainting - EXACT same as production
            max_side = max(h, w)
            dynamic_radius = int(max(5, min(15, max_side / 200)))
            
            # Use Telea algorithm first
            inpainted_telea = cv2.inpaint(img_bgr, watermark_mask, dynamic_radius, cv2.INPAINT_TELEA)
            # Then NS for better structure
            inpainted_ns = cv2.inpaint(img_bgr, watermark_mask, dynamic_radius, cv2.INPAINT_NS)
            # Blend the two results
            inpainted = cv2.addWeighted(inpainted_telea, 0.4, inpainted_ns, 0.6, 0)
            
            # Apply soft blending to reduce artifacts - use smaller blur for less aggressive blending (EXACT same as production)
            soft_mask = watermark_mask.astype(np.float32) / 255.0
            soft_mask = cv2.GaussianBlur(soft_mask, (0, 0), sigmaX=1.5, sigmaY=1.5)  # Same as production
            soft_mask_3 = np.repeat(soft_mask[:, :, None], 3, axis=2)
            inpainted = (soft_mask_3 * inpainted.astype(np.float32) + 
                        (1.0 - soft_mask_3) * img_bgr.astype(np.float32)).astype(np.uint8)
            
            logger.info(f"Test endpoint: Used OpenCV inpainting for watermark removal (radius={dynamic_radius})")
        
        # Convert back to RGB PIL Image
        result_rgb = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)
        result_image = Image.fromarray(result_rgb)
        
        # No upscaling needed - image was processed at original size (EXACT same as production)
        
        # Save to bytes (same as production)
        buf = io.BytesIO()
        result_image.save(buf, format="PNG", optimize=False)
        return Response(content=buf.getvalue(), media_type="image/png")
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error removing Gemini watermark: {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error removing watermark: {error_msg}")

@app.post("/api/test-google-professional-headshot")
async def test_google_professional_headshot(
    request: Request,
    file: UploadFile = File(...),
):
    """Test professional headshot creation using Google's Gemini gemini-3-pro-image-preview model."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Try both environment variable names for compatibility
    google_api_key = os.getenv("GOOGLE_GENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not google_api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_GENAI_API_KEY or GEMINI_API_KEY environment variable not set")
    
    # Read image file contents
    image_contents = await file.read()
    
    tmp_file_path = None
    try:
        from google import genai
        from PIL import Image
        import importlib.metadata
        import base64
        try:
            genai_version = importlib.metadata.version("google-genai")
            logger.info(f"google-genai SDK version: {genai_version}")
        except Exception:
            logger.warning("Could not determine google-genai version")
        
        # Create a temporary file for PIL to open
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=google_api_key)
        
        # Open image with PIL
        image = Image.open(tmp_file_path)
        
        # Create prompt for professional headshot - maintain the person's identity
        prompt = (
            "Edit this image to create a professional corporate headshot.\n\n"
            "IDENTITY LOCK: Directly copy and maintain the 100% exact facial structure, identity, and key features (eyes, nose, mouth, and skin texture) of the person in the reference image. DO NOT interpret, reimagine, or beautify the face.\n\n"
            "STYLING:\n"
            "- CLOTHING: Replace the person's current outfit with a premium, tailored navy blue business suit, a crisp white dress shirt, and a subtle silk tie. Ensure the suit texture is sharp and detailed.\n"
            "- BACKGROUND: Replace the background with a neutral, out-of-focus (#141414) professional studio backdrop. Remove all background distractions.\n"
            "- LIGHTING: Apply soft, diffused 'Rembrandt' studio lighting with a subtle catchlight in the eyes.\n\n"
            "COMPOSITION: Chest-up framing with centered alignment and shallow depth of field (85mm lens effect).\n"
            "OUTPUT: Hyper-realistic 4K resolution, preserving natural skin pores and hair strands without AI-smoothing."
        )
        
        logger.info(f"Running Google Gemini image editing model (gemini-3-pro-image-preview) for professional headshot")
        
        # Call Google Gemini API for image editing with retry logic for quota limits
        import time
        max_retries = 3
        retry_delay = 5  # Start with 5 seconds
        
        response = None
        for attempt in range(max_retries):
            try:
                # Configure with response_modalities and safety settings
                config = {
                    "response_modalities": ["TEXT", "IMAGE"],
                    "safety_settings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                    ]
                }
                
                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt, image],
                    config=config
                )
                
                logger.info(f"Response received. Type: {type(response)}")
                if hasattr(response, 'candidates') and response.candidates:
                    logger.info(f"Response has {len(response.candidates)} candidates")
                
                break  # Success, exit retry loop
            except Exception as e:
                error_str = str(e)
                # Check if it's a quota/rate limit error (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Quota exceeded, retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail=f"Quota exceeded. Please wait and try again later, or upgrade your Google AI plan. Error: {error_str}"
                        )
                else:
                    raise
        
        # Extract image from response
        result_bytes = None
        
        try:
            if not response:
                raise HTTPException(status_code=500, detail="No response received from Google Gemini API")
            
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.error(f"Response has no candidates. Response type: {type(response)}")
                raise HTTPException(
                    status_code=500, 
                    detail="Google Gemini API returned no candidates."
                )
            
            candidate = response.candidates[0]
            
            if not hasattr(candidate, 'content') or not candidate.content:
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(
                    status_code=500,
                    detail=f"Google Gemini API candidate has no content. Finish reason: {finish_reason}"
                )
            
            parts = getattr(candidate.content, 'parts', None)
            if not parts:
                parts = getattr(response, 'parts', None)
            
            if not parts or (isinstance(parts, list) and len(parts) == 0):
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(status_code=500, detail=f"Response contained no parts. Finish reason: {finish_reason}")
            
            # Iterate over parts to find image data
            for part in parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    inline_data = part.inline_data
                    
                    if hasattr(inline_data, 'data'):
                        data_value = inline_data.data
                        if isinstance(data_value, bytes):
                            result_bytes = data_value
                        elif isinstance(data_value, str):
                            result_bytes = base64.b64decode(data_value)
                        else:
                            raise HTTPException(status_code=500, detail=f"Unexpected inline_data.data type: {type(data_value)}")
                        break
                    elif hasattr(inline_data, 'bytes'):
                        result_bytes = inline_data.bytes
                        break
            
            if not result_bytes:
                raise HTTPException(status_code=500, detail="No image content found in response from Google Gemini")
        
        except AttributeError as e:
            logger.error(f"Error accessing response structure: {e}")
            raise HTTPException(status_code=500, detail=f"Error parsing Google Gemini response: {str(e)}")
        
        # Verify it's a valid image by checking magic bytes
        is_png = result_bytes[:4] == b'\x89PNG'
        is_jpeg = result_bytes[:2] == b'\xff\xd8'
        
        if not is_png and not is_jpeg:
            logger.error(f"Response does not appear to be a valid image. First bytes: {result_bytes[:20]}")
            raise HTTPException(
                status_code=500, 
                detail=f"Response from Google Gemini does not appear to be image data. Received {len(result_bytes)} bytes."
            )
        
        # Determine media type based on image format
        media_type = "image/png" if is_png else "image/jpeg"
        logger.info(f"Successfully validated {media_type} image ({len(result_bytes)} bytes)")
        
        return Response(content=result_bytes, media_type=media_type)
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Google Gemini professional headshot: {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/test-google-edit-text")
async def test_google_edit_text(
    request: Request,
    file: UploadFile = File(...),
    text_to_find: str = Form(...),
    text_to_replace: str = Form(...),
):
    """Test text editing in images using Google's Gemini gemini-3-pro-image-preview model."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    if not text_to_find or not text_to_replace:
        raise HTTPException(status_code=400, detail="Both text_to_find and text_to_replace are required")
    
    # Try both environment variable names for compatibility
    google_api_key = os.getenv("GOOGLE_GENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not google_api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_GENAI_API_KEY or GEMINI_API_KEY environment variable not set")
    
    # Read image file contents
    image_contents = await file.read()
    
    tmp_file_path = None
    try:
        from google import genai
        from PIL import Image
        import importlib.metadata
        import base64
        try:
            genai_version = importlib.metadata.version("google-genai")
            logger.info(f"google-genai SDK version: {genai_version}")
        except Exception:
            logger.warning("Could not determine google-genai version")
        
        # Create a temporary file for PIL to open
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=google_api_key)
        
        # Open image with PIL
        image = Image.open(tmp_file_path)
        
        # Create prompt for text editing
        prompt = (
            f"Edit this image to change text in the image.\n\n"
            f"TEXT EDITING INSTRUCTIONS:\n"
            f"- Find and locate the text: '{text_to_find}'\n"
            f"- Replace it with: '{text_to_replace}'\n"
            f"- Maintain the exact same font style, size, color, and position as the original text\n"
            f"- Keep all other elements of the image exactly the same\n"
            f"- Ensure the new text blends seamlessly with the image background\n"
            f"- Preserve the original text's visual appearance (font, color, effects) as closely as possible\n\n"
            f"OUTPUT: Return the edited image with only the specified text changed."
        )
        
        logger.info(f"Running Google Gemini image editing model (gemini-3-pro-image-preview) for text editing: '{text_to_find}' -> '{text_to_replace}'")
        
        # Call Google Gemini API for image editing with retry logic for quota limits
        import time
        max_retries = 3
        retry_delay = 5  # Start with 5 seconds
        
        response = None
        for attempt in range(max_retries):
            try:
                # Configure with response_modalities and safety settings
                config = {
                    "response_modalities": ["TEXT", "IMAGE"],
                    "safety_settings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                    ]
                }
                
                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt, image],
                    config=config
                )
                
                logger.info(f"Response received. Type: {type(response)}")
                if hasattr(response, 'candidates') and response.candidates:
                    logger.info(f"Response has {len(response.candidates)} candidates")
                
                break  # Success, exit retry loop
            except Exception as e:
                error_str = str(e)
                # Check if it's a quota/rate limit error (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Quota exceeded, retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail=f"Quota exceeded. Please wait and try again later, or upgrade your Google AI plan. Error: {error_str}"
                        )
                else:
                    raise
        
        # Extract image from response
        result_bytes = None
        
        try:
            if not response:
                raise HTTPException(status_code=500, detail="No response received from Google Gemini API")
            
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.error(f"Response has no candidates. Response type: {type(response)}")
                raise HTTPException(
                    status_code=500, 
                    detail="Google Gemini API returned no candidates."
                )
            
            candidate = response.candidates[0]
            
            if not hasattr(candidate, 'content') or not candidate.content:
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(
                    status_code=500,
                    detail=f"Google Gemini API candidate has no content. Finish reason: {finish_reason}"
                )
            
            parts = getattr(candidate.content, 'parts', None)
            if not parts:
                parts = getattr(response, 'parts', None)
            
            if not parts or (isinstance(parts, list) and len(parts) == 0):
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(status_code=500, detail=f"Response contained no parts. Finish reason: {finish_reason}")
            
            # Iterate over parts to find image data
            for part in parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    inline_data = part.inline_data
                    
                    if hasattr(inline_data, 'data'):
                        data_value = inline_data.data
                        if isinstance(data_value, bytes):
                            result_bytes = data_value
                        elif isinstance(data_value, str):
                            result_bytes = base64.b64decode(data_value)
                        else:
                            raise HTTPException(status_code=500, detail=f"Unexpected inline_data.data type: {type(data_value)}")
                        break
                    elif hasattr(inline_data, 'bytes'):
                        result_bytes = inline_data.bytes
                        break
            
            if not result_bytes:
                raise HTTPException(status_code=500, detail="No image content found in response from Google Gemini")
        
        except AttributeError as e:
            logger.error(f"Error accessing response structure: {e}")
            raise HTTPException(status_code=500, detail=f"Error parsing Google Gemini response: {str(e)}")
        
        # Verify it's a valid image by checking magic bytes
        is_png = result_bytes[:4] == b'\x89PNG'
        is_jpeg = result_bytes[:2] == b'\xff\xd8'
        
        if not is_png and not is_jpeg:
            logger.error(f"Response does not appear to be a valid image. First bytes: {result_bytes[:20]}")
            raise HTTPException(
                status_code=500, 
                detail=f"Response from Google Gemini does not appear to be image data. Received {len(result_bytes)} bytes."
            )
        
        # Determine media type based on image format
        media_type = "image/png" if is_png else "image/jpeg"
        logger.info(f"Successfully validated {media_type} image ({len(result_bytes)} bytes)")
        
        return Response(content=result_bytes, media_type=media_type)
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Google Gemini text editing: {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/edit-text-in-image")
async def edit_text_in_image(
    request: Request,
    file: UploadFile = File(...),
    text_to_find: str = Form(...),
    text_to_replace: str = Form(...),
    payment_session_id: Optional[str] = Form(None),
):
    """Edit text in images using Google's Gemini gemini-3-pro-image-preview model. Free to use."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    if not text_to_find or not text_to_replace:
        raise HTTPException(status_code=400, detail="Both text_to_find and text_to_replace are required")
    
    # Payment verification is optional - if payment_session_id is provided, verify it
    if payment_session_id:
        # Check if this payment session has already been used
        if payment_session_id in _USED_PAYMENT_SESSIONS:
            raise HTTPException(
                status_code=403, 
                detail="This payment session has already been used. Please make a new payment to process another image."
            )
        
        # Verify payment is actually successful
        try:
            import dodopayments
            dodo_api_key = os.getenv("DODO_PAYMENTS_API_KEY")
            if dodo_api_key:
                # Use same environment as checkout creation (test_mode for local, live_mode for production)
                is_production = IS_PRODUCTION
                dodo_env = "live_mode" if is_production else "test_mode"
                
                client = dodopayments.DodoPayments(
                    bearer_token=dodo_api_key.strip(),
                    environment=dodo_env
                )
                checkout_session = client.checkout_sessions.retrieve(payment_session_id)
                payment_status = getattr(checkout_session, 'payment_status', None)
                if not payment_status or payment_status.lower() not in ['succeeded', 'completed', 'paid']:
                    raise HTTPException(status_code=403, detail="Payment not verified or incomplete")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not verify payment status (proceeding anyway): {str(e)}")
        
        # Mark payment session as used
        _USED_PAYMENT_SESSIONS.add(payment_session_id)
    
    # Try both environment variable names for compatibility
    google_api_key = os.getenv("GOOGLE_GENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not google_api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_GENAI_API_KEY or GEMINI_API_KEY environment variable not set")
    
    # Read image file contents
    image_contents = await file.read()
    
    tmp_file_path = None
    try:
        from google import genai
        from PIL import Image
        import importlib.metadata
        import base64
        try:
            genai_version = importlib.metadata.version("google-genai")
            logger.info(f"google-genai SDK version: {genai_version}")
        except Exception:
            logger.warning("Could not determine google-genai version")
        
        # Create a temporary file for PIL to open
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=google_api_key)
        
        # Open image with PIL
        image = Image.open(tmp_file_path)
        
        # Create prompt for text editing
        prompt = (
            f"Edit this image to change text in the image.\n\n"
            f"TEXT EDITING INSTRUCTIONS:\n"
            f"- Find and locate the text: '{text_to_find}'\n"
            f"- Replace it with: '{text_to_replace}'\n"
            f"- Maintain the exact same font style, size, color, and position as the original text\n"
            f"- Keep all other elements of the image exactly the same\n"
            f"- Ensure the new text blends seamlessly with the image background\n"
            f"- Preserve the original text's visual appearance (font, color, effects) as closely as possible\n\n"
            f"OUTPUT: Return the edited image with only the specified text changed."
        )
        
        logger.info(f"Running Google Gemini image editing model (gemini-3-pro-image-preview) for text editing: '{text_to_find}' -> '{text_to_replace}'")
        
        # Call Google Gemini API for image editing with retry logic for quota limits
        import time
        max_retries = 3
        retry_delay = 5  # Start with 5 seconds
        
        response = None
        for attempt in range(max_retries):
            try:
                # Configure with response_modalities and safety settings
                config = {
                    "response_modalities": ["TEXT", "IMAGE"],
                    "safety_settings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                    ]
                }
                
                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt, image],
                    config=config
                )
                
                logger.info(f"Response received. Type: {type(response)}")
                if hasattr(response, 'candidates') and response.candidates:
                    logger.info(f"Response has {len(response.candidates)} candidates")
                
                break  # Success, exit retry loop
            except Exception as e:
                error_str = str(e)
                # Check if it's a quota/rate limit error (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Quota exceeded, retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail=f"Quota exceeded. Please wait and try again later, or upgrade your Google AI plan. Error: {error_str}"
                        )
                else:
                    raise
        
        # Extract image from response
        result_bytes = None
        
        try:
            if not response:
                raise HTTPException(status_code=500, detail="No response received from Google Gemini API")
            
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.error(f"Response has no candidates. Response type: {type(response)}")
                raise HTTPException(
                    status_code=500, 
                    detail="Google Gemini API returned no candidates."
                )
            
            candidate = response.candidates[0]
            
            if not hasattr(candidate, 'content') or not candidate.content:
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(
                    status_code=500,
                    detail=f"Google Gemini API candidate has no content. Finish reason: {finish_reason}"
                )
            
            parts = getattr(candidate.content, 'parts', None)
            if not parts:
                parts = getattr(response, 'parts', None)
            
            if not parts or (isinstance(parts, list) and len(parts) == 0):
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(status_code=500, detail=f"Response contained no parts. Finish reason: {finish_reason}")
            
            # Iterate over parts to find image data
            for part in parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    inline_data = part.inline_data
                    
                    if hasattr(inline_data, 'data'):
                        data_value = inline_data.data
                        if isinstance(data_value, bytes):
                            result_bytes = data_value
                        elif isinstance(data_value, str):
                            result_bytes = base64.b64decode(data_value)
                        else:
                            raise HTTPException(status_code=500, detail=f"Unexpected inline_data.data type: {type(data_value)}")
                        break
                    elif hasattr(inline_data, 'bytes'):
                        result_bytes = inline_data.bytes
                        break
            
            if not result_bytes:
                raise HTTPException(status_code=500, detail="No image content found in response from Google Gemini")
        
        except AttributeError as e:
            logger.error(f"Error accessing response structure: {e}")
            raise HTTPException(status_code=500, detail=f"Error parsing Google Gemini response: {str(e)}")
        
        # Verify it's a valid image by checking magic bytes
        is_png = result_bytes[:4] == b'\x89PNG'
        is_jpeg = result_bytes[:2] == b'\xff\xd8'
        
        if not is_png and not is_jpeg:
            logger.error(f"Response does not appear to be a valid image. First bytes: {result_bytes[:20]}")
            raise HTTPException(
                status_code=500, 
                detail=f"Response from Google Gemini does not appear to be image data. Received {len(result_bytes)} bytes."
            )
        
        # Determine media type based on image format
        media_type = "image/png" if is_png else "image/jpeg"
        logger.info(f"Successfully validated {media_type} image ({len(result_bytes)} bytes)")
        
        return Response(content=result_bytes, media_type=media_type)
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Google Gemini text editing: {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/test-replicate-professional-headshot")
async def test_replicate_professional_headshot(
    request: Request,
    file: UploadFile = File(...),
):
    """Test professional headshot creation using Replicate's Stable Diffusion models."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    replicate_api_token = os.getenv("REPLICATE_API_TOKEN")
    if not replicate_api_token:
        raise HTTPException(status_code=500, detail="REPLICATE_API_TOKEN environment variable not set")
    
    tmp_file_path = None
    try:
        import replicate
        
        # Read image file
        contents = await file.read()
        
        # Create a temporary file for Replicate API
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Replicate client
        client = replicate.Client(api_token=replicate_api_token)
        
        # Use Stable Diffusion XL for professional headshot generation
        # This model can do image-to-image generation with control
        logger.info(f"Running Stable Diffusion XL for professional headshot with image: {tmp_file_path}")
        
        with open(tmp_file_path, 'rb') as img_file:
            # Try using google/nano-banana-pro which supports image-to-image with prompts
            # Or use stability-ai/sdxl for professional headshots
            output = client.run(
                "google/nano-banana-pro",
                input={
                    "prompt": "A professional corporate headshot portrait. The person is wearing a premium navy blue business suit, crisp white dress shirt, and subtle silk tie. Professional studio lighting with soft Rembrandt lighting and subtle catchlight in the eyes. Neutral, out-of-focus dark professional studio background. Chest-up framing, centered alignment, shallow depth of field. Hyper-realistic 4K resolution, preserving natural skin texture and hair details. Maintain the exact facial features, identity, and appearance of the person in the input image.",
                    "image_input": [img_file],
                    "aspect_ratio": "match_input_image",
                    "output_format": "png",
                    "resolution": "2K",
                    "safety_filter_level": "block_only_high"
                }
            )
        
        logger.info(f"Stable Diffusion output type: {type(output)}")
        
        # Handle output - could be FileOutput, URL string, or list
        result_bytes = None
        
        if isinstance(output, str):
            # If output is a URL string, download it
            logger.info(f"Output is URL: {output}")
            response = requests.get(output, timeout=120)
            response.raise_for_status()
            result_bytes = response.content
        elif isinstance(output, (list, tuple)) and len(output) > 0:
            # Handle list of outputs
            file_output = output[0]
            if isinstance(file_output, str):
                # URL in list
                response = requests.get(file_output, timeout=120)
                response.raise_for_status()
                result_bytes = response.content
            elif hasattr(file_output, 'read'):
                result_bytes = file_output.read()
            else:
                raise HTTPException(status_code=500, detail=f"Unexpected output format in list: {type(file_output)}")
        elif hasattr(output, 'read'):
            # Handle FileOutput object
            logger.info("Output is FileOutput, reading bytes...")
            result_bytes = output.read()
        else:
            logger.error(f"Unexpected output format: {type(output)}, value: {output}")
            raise HTTPException(status_code=500, detail=f"Unexpected output format from Replicate: {type(output)}")
        
        if not result_bytes or len(result_bytes) == 0:
            raise HTTPException(status_code=500, detail="No image content found in response")
        
        logger.info(f"Successfully got {len(result_bytes)} bytes from Stable Diffusion")
        return Response(content=result_bytes, media_type="image/png")
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Replicate professional headshot: {error_msg}", exc_info=True)
        
        # Provide more helpful error messages
        if "No image content" in error_msg or "ModelError" in str(type(e)):
            raise HTTPException(
                status_code=500, 
                detail=f"Model did not return an image. This might mean: 1) The model doesn't support this use case, 2) The prompt needs adjustment, or 3) The image format isn't supported. Error: {error_msg}"
            )
        else:
            raise HTTPException(status_code=500, detail=f"Error processing image with Replicate: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/test-google-enhance")
async def test_google_enhance(
    request: Request,
    file: UploadFile = File(...),
):
    """Test image enhancement/restoration using Google's Gemini gemini-3-pro-image-preview model."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Try both environment variable names for compatibility
    google_api_key = os.getenv("GOOGLE_GENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not google_api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_GENAI_API_KEY or GEMINI_API_KEY environment variable not set")
    
    # Read image file contents
    image_contents = await file.read()
    
    tmp_file_path = None
    try:
        from google import genai
        from PIL import Image
        import importlib.metadata
        import base64
        try:
            genai_version = importlib.metadata.version("google-genai")
            logger.info(f"google-genai SDK version: {genai_version}")
        except Exception:
            logger.warning("Could not determine google-genai version")
        
        # Create a temporary file for PIL to open
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=google_api_key)
        
        # Open image with PIL
        image = Image.open(tmp_file_path)
        
        # Create prompt for image enhancement/restoration
        prompt = (
            "Enhance and restore this image: improve clarity, reduce noise, sharpen details, "
            "fix any blur or artifacts, enhance colors and contrast, and improve overall image quality "
            "while maintaining the original composition, style, and content."
        )
        
        logger.info(f"Running Google Gemini image editing model (gemini-3-pro-image-preview) for image enhancement")
        
        # Call Google Gemini API for image editing with retry logic for quota limits
        import time
        max_retries = 3
        retry_delay = 5  # Start with 5 seconds
        
        response = None
        for attempt in range(max_retries):
            try:
                # Configure with response_modalities and safety settings
                config = {
                    "response_modalities": ["TEXT", "IMAGE"],
                    "safety_settings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                    ]
                }
                
                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt, image],
                    config=config
                )
                
                logger.info(f"Response received. Type: {type(response)}")
                if hasattr(response, 'candidates') and response.candidates:
                    logger.info(f"Response has {len(response.candidates)} candidates")
                
                break  # Success, exit retry loop
            except Exception as e:
                error_str = str(e)
                # Check if it's a quota/rate limit error (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Quota exceeded, retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail=f"Quota exceeded. Please wait and try again later, or upgrade your Google AI plan. Error: {error_str}"
                        )
                else:
                    raise
        
        # Extract image from response
        result_bytes = None
        
        try:
            if not response:
                raise HTTPException(status_code=500, detail="No response received from Google Gemini API")
            
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.error(f"Response has no candidates. Response type: {type(response)}")
                raise HTTPException(
                    status_code=500, 
                    detail="Google Gemini API returned no candidates."
                )
            
            candidate = response.candidates[0]
            
            if not hasattr(candidate, 'content') or not candidate.content:
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(
                    status_code=500,
                    detail=f"Google Gemini API candidate has no content. Finish reason: {finish_reason}"
                )
            
            parts = getattr(candidate.content, 'parts', None)
            if not parts:
                parts = getattr(response, 'parts', None)
            
            if not parts or (isinstance(parts, list) and len(parts) == 0):
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(status_code=500, detail=f"Response contained no parts. Finish reason: {finish_reason}")
            
            # Iterate over parts to find image data
            for part in parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    inline_data = part.inline_data
                    
                    if hasattr(inline_data, 'data'):
                        data_value = inline_data.data
                        if isinstance(data_value, bytes):
                            result_bytes = data_value
                        elif isinstance(data_value, str):
                            result_bytes = base64.b64decode(data_value)
                        else:
                            raise HTTPException(status_code=500, detail=f"Unexpected inline_data.data type: {type(data_value)}")
                        break
                    elif hasattr(inline_data, 'bytes'):
                        result_bytes = inline_data.bytes
                        break
            
            if not result_bytes:
                raise HTTPException(status_code=500, detail="No image content found in response from Google Gemini")
        
        except AttributeError as e:
            logger.error(f"Error accessing response structure: {e}")
            raise HTTPException(status_code=500, detail=f"Error parsing Google Gemini response: {str(e)}")
        
        # Verify it's a valid image by checking magic bytes
        is_png = result_bytes[:4] == b'\x89PNG'
        is_jpeg = result_bytes[:2] == b'\xff\xd8'
        
        if not is_png and not is_jpeg:
            logger.error(f"Response does not appear to be a valid image. First bytes: {result_bytes[:20]}")
            raise HTTPException(
                status_code=500, 
                detail=f"Response from Google Gemini does not appear to be image data. Received {len(result_bytes)} bytes."
            )
        
        # Determine media type based on image format
        media_type = "image/png" if is_png else "image/jpeg"
        logger.info(f"Successfully validated {media_type} image ({len(result_bytes)} bytes)")
        
        return Response(content=result_bytes, media_type=media_type)
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Google Gemini image enhancement: {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/test-google-enhance-payment")
async def test_google_enhance_payment(
    request: Request,
    file: UploadFile = File(...),
    payment_session_id: Optional[str] = Form(None),
):
    """Test image enhancement/restoration using Google's Gemini gemini-3-pro-image-preview model with payment verification."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Verify payment session and prevent reuse
    if not payment_session_id:
        raise HTTPException(status_code=400, detail="Payment session ID is required")
    
    # Check if this payment session has already been used
    if payment_session_id in _USED_PAYMENT_SESSIONS:
        raise HTTPException(
            status_code=403, 
            detail="This payment session has already been used. Please make a new payment to process another image."
        )
    
    # Verify payment is actually successful
    try:
        import dodopayments
        dodo_api_key = os.getenv("DODO_PAYMENTS_API_KEY")
        if dodo_api_key:
            # Use same environment as checkout creation (test_mode for local, live_mode for production)
            is_production = IS_PRODUCTION
            dodo_env = "live_mode" if is_production else "test_mode"
            
            client = dodopayments.DodoPayments(
                bearer_token=dodo_api_key.strip(),
                environment=dodo_env
            )
            checkout_session = client.checkout_sessions.retrieve(payment_session_id)
            payment_status = getattr(checkout_session, 'payment_status', None)
            if not payment_status or payment_status.lower() not in ['succeeded', 'completed', 'paid']:
                raise HTTPException(status_code=403, detail="Payment not verified or incomplete")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not verify payment status (proceeding anyway): {str(e)}")
    
    # Try both environment variable names for compatibility
    google_api_key = os.getenv("GOOGLE_GENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not google_api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_GENAI_API_KEY or GEMINI_API_KEY environment variable not set")
    
    # Read image file contents
    image_contents = await file.read()
    
    tmp_file_path = None
    try:
        from google import genai
        from PIL import Image
        import importlib.metadata
        import base64
        try:
            genai_version = importlib.metadata.version("google-genai")
            logger.info(f"google-genai SDK version: {genai_version}")
        except Exception:
            logger.warning("Could not determine google-genai version")
        
        # Create a temporary file for PIL to open
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
            tmp_file.write(image_contents)
            tmp_file_path = tmp_file.name
        
        # Initialize Google GenAI client
        client = genai.Client(api_key=google_api_key)
        
        # Open image with PIL
        image = Image.open(tmp_file_path)
        
        # Create prompt for image enhancement/restoration
        prompt = (
            "Enhance and restore this image: improve clarity, reduce noise, sharpen details, "
            "fix any blur or artifacts, enhance colors and contrast, and improve overall image quality "
            "while maintaining the original composition, style, and content."
        )
        
        logger.info(f"Running Google Gemini image editing model (gemini-3-pro-image-preview) for image enhancement (premium)")
        
        # Call Google Gemini API for image editing with retry logic for quota limits
        import time
        max_retries = 3
        retry_delay = 5  # Start with 5 seconds
        
        response = None
        for attempt in range(max_retries):
            try:
                # Configure with response_modalities and safety settings
                config = {
                    "response_modalities": ["TEXT", "IMAGE"],
                    "safety_settings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "threshold": "BLOCK_NONE"
                        },
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "threshold": "BLOCK_NONE"
                        },
                    ]
                }
                
                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt, image],
                    config=config
                )
                
                logger.info(f"Response received. Type: {type(response)}")
                if hasattr(response, 'candidates') and response.candidates:
                    logger.info(f"Response has {len(response.candidates)} candidates")
                
                break  # Success, exit retry loop
            except Exception as e:
                error_str = str(e)
                # Check if it's a quota/rate limit error (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "quota" in error_str.lower():
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Quota exceeded, retrying in {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail=f"Quota exceeded. Please wait and try again later, or upgrade your Google AI plan. Error: {error_str}"
                        )
                else:
                    raise
        
        # Extract image from response
        result_bytes = None
        
        try:
            if not response:
                raise HTTPException(status_code=500, detail="No response received from Google Gemini API")
            
            if not hasattr(response, 'candidates') or not response.candidates:
                logger.error(f"Response has no candidates. Response type: {type(response)}")
                raise HTTPException(
                    status_code=500, 
                    detail="Google Gemini API returned no candidates."
                )
            
            candidate = response.candidates[0]
            
            if not hasattr(candidate, 'content') or not candidate.content:
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(
                    status_code=500,
                    detail=f"Google Gemini API candidate has no content. Finish reason: {finish_reason}"
                )
            
            parts = getattr(candidate.content, 'parts', None)
            if not parts:
                parts = getattr(response, 'parts', None)
            
            if not parts or (isinstance(parts, list) and len(parts) == 0):
                finish_reason = getattr(candidate, 'finish_reason', None)
                raise HTTPException(status_code=500, detail=f"Response contained no parts. Finish reason: {finish_reason}")
            
            # Iterate over parts to find image data
            for part in parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    inline_data = part.inline_data
                    
                    if hasattr(inline_data, 'data'):
                        data_value = inline_data.data
                        if isinstance(data_value, bytes):
                            result_bytes = data_value
                        elif isinstance(data_value, str):
                            result_bytes = base64.b64decode(data_value)
                        else:
                            raise HTTPException(status_code=500, detail=f"Unexpected inline_data.data type: {type(data_value)}")
                        break
                    elif hasattr(inline_data, 'bytes'):
                        result_bytes = inline_data.bytes
                        break
            
            if not result_bytes:
                raise HTTPException(status_code=500, detail="No image content found in response from Google Gemini")
        
        except AttributeError as e:
            logger.error(f"Error accessing response structure: {e}")
            raise HTTPException(status_code=500, detail=f"Error parsing Google Gemini response: {str(e)}")
        
        # Verify it's a valid image by checking magic bytes
        is_png = result_bytes[:4] == b'\x89PNG'
        is_jpeg = result_bytes[:2] == b'\xff\xd8'
        
        if not is_png and not is_jpeg:
            logger.error(f"Response does not appear to be a valid image. First bytes: {result_bytes[:20]}")
            raise HTTPException(
                status_code=500, 
                detail=f"Response from Google Gemini does not appear to be image data. Received {len(result_bytes)} bytes."
            )
        
        # Determine media type based on image format
        media_type = "image/png" if is_png else "image/jpeg"
        logger.info(f"Successfully validated {media_type} image ({len(result_bytes)} bytes)")
        
        # Mark this payment session as used to prevent reuse (only after successful processing)
        _USED_PAYMENT_SESSIONS.add(payment_session_id)
        logger.info(f"Payment session {payment_session_id} marked as used")
        
        return Response(content=result_bytes, media_type=media_type)
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in Google Gemini image enhancement (premium): {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing image with Google Gemini: {error_msg}")
    
    finally:
        # Clean up temporary file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

@app.post("/api/remove-text")
async def remove_text(
    request: Request,
    file: UploadFile = File(...),
):
    """Remove text from images using OCR detection and inpainting."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("text_removal_request", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
    })
    
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        
        # Downscale for processing efficiency
        proc_image = downscale_image_if_needed(image)
        
        # Convert PIL to OpenCV format
        opencv_image = cv2.cvtColor(np.array(proc_image), cv2.COLOR_RGB2BGR)
        
        # Create mask for text regions
        text_mask = np.zeros(opencv_image.shape[:2], dtype=np.uint8)
        
        try:
            if not pytesseract:
                raise HTTPException(status_code=500, detail="Text detection not available")
                
            # Use Tesseract for text detection (more memory efficient)
            # Get text regions using Tesseract
            data = pytesseract.image_to_data(proc_image, output_type=pytesseract.Output.DICT)
            
            for i in range(len(data['text'])):
                if int(data['conf'][i]) > 50:  # Confidence threshold
                    x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                    if w > 10 and h > 10:  # Filter out very small detections
                        cv2.rectangle(text_mask, (x, y), (x + w, y + h), 255, -1)
                
        except Exception as e:
            log_user_action("text_detection_error", {"error": str(e)})
            raise HTTPException(status_code=500, detail=f"Text detection failed: {str(e)}")
        
        # Check if any text was detected
        if np.sum(text_mask) == 0:
            log_user_action("no_text_detected", {})
            # Return original image if no text detected
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        
        # Gently expand and smooth the mask to avoid hard edges
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        text_mask = cv2.dilate(text_mask, kernel, iterations=2)
        text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        
        # Use OpenCV inpainting to remove text with a larger radius for better blending
        try:
            # Apply inpainting using Telea algorithm
            # Radius scaled to image size to avoid under/over inpainting
            max_side = max(opencv_image.shape[:2])
            dynamic_radius = int(max(5, min(15, max_side / 200)))
            inpainted = cv2.inpaint(opencv_image, text_mask, inpaintRadius=dynamic_radius, flags=cv2.INPAINT_TELEA)
            # Optional refinement pass with NS to improve structure continuity
            inpainted = cv2.inpaint(inpainted, text_mask, dynamic_radius, cv2.INPAINT_NS)

            # Feathered edge blend to reduce seams
            soft_mask = text_mask.copy().astype(np.float32) / 255.0
            soft_mask = cv2.GaussianBlur(soft_mask, (0, 0), sigmaX=3, sigmaY=3)
            inner = cv2.erode(text_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
            soft_mask[inner == 255] = 1.0
            soft_mask = np.clip(soft_mask, 0.0, 1.0)
            soft_mask_3 = np.repeat(soft_mask[:, :, None], 3, axis=2)
            blended = (soft_mask_3 * inpainted.astype(np.float32) + (1.0 - soft_mask_3) * opencv_image.astype(np.float32)).astype(np.uint8)
            inpainted = blended
            
            # Convert back to PIL Image
            result_image = Image.fromarray(cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB))
            
            # Resize back to original size if we downscaled
            if result_image.size != image.size:
                result_image = result_image.resize(image.size, Image.LANCZOS)
            
            log_user_action("text_removal_success", {
                "text_pixels_removed": int(np.sum(text_mask > 0))
            })
            
            buf = io.BytesIO()
            result_image.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
            
        except Exception as e:
            log_user_action("inpainting_error", {"error": str(e)})
            raise HTTPException(status_code=500, detail=f"Text removal failed: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("text_removal_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Text removal error: {str(e)}")


@app.post("/api/test-unscribe")
async def test_unscribe(
    request: Request,
    file: UploadFile = File(...),
):
    """Test endpoint for unscribe library to remove text from images."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    try:
        import unscribe
        
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        
        logger.info(f"Testing unscribe on image: {image.size}")
        
        # Convert PIL Image to numpy array for unscribe
        import numpy as np
        image_array = np.array(image)
        
        # Use unscribe to remove text
        # Based on PyPI documentation: unscribe.remove_text(image)
        result_array = unscribe.remove_text(image_array)
        
        # Convert back to PIL Image
        result_image = Image.fromarray(result_array)
        
        # Save to bytes
        buf = io.BytesIO()
        result_image.save(buf, format="PNG")
        
        logger.info(f"Unscribe test successful: output size={len(buf.getvalue())} bytes")
        
        return Response(content=buf.getvalue(), media_type="image/png")
        
    except ImportError:
        raise HTTPException(status_code=500, detail="unscribe library not installed. Install with: pip install unscribe")
    except Exception as e:
        logger.error(f"Unscribe test error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unscribe error: {str(e)}")


@app.post("/api/image-to-pdf")
async def image_to_pdf(
    request: Request,
    file: UploadFile = File(...),
):
    """Convert an image to PDF format."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("image_to_pdf_request", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
    })
    
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents))
        
        # Convert to RGB if necessary (PDF doesn't support transparency directly)
        if image.mode in ('RGBA', 'LA', 'P'):
            # Create white background for transparent images
            rgb_image = Image.new('RGB', image.size, (255, 255, 255))
            if image.mode == 'P':
                image = image.convert('RGBA')
            rgb_image.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
            image = rgb_image
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Save to PDF
        buf = io.BytesIO()
        image.save(buf, format="PDF", resolution=100.0)
        buf.seek(0)
        
        log_user_action("image_to_pdf_success", {
            "filename": file.filename,
            "output_size": len(buf.getvalue()),
        })
        
        return Response(
            content=buf.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{file.filename.rsplit(".", 1)[0] if "." in (file.filename or "") else "image"}.pdf"'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("image_to_pdf_error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Error converting image to PDF: {str(e)}")


@app.post("/api/image-to-text")
async def image_to_text(
    request: Request,
    file: UploadFile = File(...),
):
    """Extract text from an image using OCR (Optical Character Recognition)."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    if not pytesseract:
        raise HTTPException(status_code=500, detail="OCR functionality not available. Tesseract OCR is not installed.")
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("image_to_text_request", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
    })
    
    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents))
        
        # Convert to RGB for OCR
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Downscale if too large for better OCR performance
        max_dimension = 2000
        if max(image.size) > max_dimension:
            ratio = max_dimension / max(image.size)
            new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
            image = image.resize(new_size, Image.LANCZOS)
        
        # Extract text using Tesseract OCR
        try:
            extracted_text = pytesseract.image_to_string(image, lang='eng')
            
            # Clean up the text
            extracted_text = extracted_text.strip()
            
            if not extracted_text:
                log_user_action("no_text_extracted", {})
                return Response(
                    content=json.dumps({"text": "", "message": "No text could be extracted from this image."}),
                    media_type="application/json"
                )
            
            log_user_action("image_to_text_success", {
                "filename": file.filename,
                "text_length": len(extracted_text),
            })
            
            return Response(
                content=json.dumps({"text": extracted_text}),
                media_type="application/json"
            )
            
        except Exception as e:
            log_user_action("ocr_error", {"error": str(e)})
            raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")
        
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("image_to_text_error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Error processing image for OCR: {str(e)}")


@app.post("/api/remove-painted-areas")
async def remove_painted_areas(
    request: Request,
    file: UploadFile = File(...),
    mask_data: str = Form(...),
):
    """Remove areas painted by user using inpainting."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    log_user_action("painted_areas_removal_request", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "filename": file.filename,
    })
    
    try:
        import base64
        
        # Load original image
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
        
        # Decode mask from base64
        try:
            # Remove data URL prefix if present
            if ',' in mask_data:
                mask_data = mask_data.split(',')[1]
            mask_bytes = base64.b64decode(mask_data)
            mask = Image.open(io.BytesIO(mask_bytes)).convert("L")  # Grayscale
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid mask data: {str(e)}")
        
        # Ensure mask and image are same size
        if mask.size != image.size:
            mask = mask.resize(image.size, Image.LANCZOS)
        
        # Downscale for processing efficiency
        proc_image = downscale_image_if_needed(image)
        proc_mask = mask.resize(proc_image.size, Image.LANCZOS)
        
        # Convert to OpenCV format
        opencv_image = cv2.cvtColor(np.array(proc_image), cv2.COLOR_RGB2BGR)
        opencv_mask = np.array(proc_mask)
        
        # Create binary mask (white = remove, black = keep)
        # Lower threshold since our red overlay with 50% opacity becomes darker when converted to grayscale
        binary_mask = (opencv_mask > 50).astype(np.uint8) * 255
        
        # Check if any areas are marked for removal
        if np.sum(binary_mask) == 0:
            log_user_action("no_areas_marked_for_removal", {})
            # Return original image if no areas marked
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")
        
        # Use improved mask preprocessing
        binary_mask = improve_mask_quality(binary_mask)
        
        # Radius scaled to image size and mask size
        max_side = max(opencv_image.shape[:2])
        mask_area = float(np.sum(binary_mask > 0))
        area_ratio = mask_area / float(binary_mask.shape[0] * binary_mask.shape[1] + 1e-6)
        base_radius = max(5, min(18, max_side / 180))
        # If very large region, increase radius slightly
        dynamic_radius = int(max(base_radius, 8 if area_ratio > 0.08 else base_radius))

        use_lama = os.getenv("USE_LAMA", "true").lower() in ("1","true","yes")
        # More aggressive LaMa usage - use it for smaller areas too for better quality
        large_hole = area_ratio > float(os.getenv("LAMA_MASK_THRESHOLD", "0.01"))  # Reduced from 0.03 to 0.01
        
        # Debug logging
        log_user_action("mask_debug", {
            "mask_shape": opencv_mask.shape,
            "mask_min": int(opencv_mask.min()),
            "mask_max": int(opencv_mask.max()),
            "mask_mean": float(opencv_mask.mean()),
            "binary_mask_sum": int(np.sum(binary_mask)),
            "binary_mask_pixels": int(np.sum(binary_mask > 0)),
            "use_lama": use_lama,
            "large_hole": large_hole,
            "area_ratio": area_ratio,
            "lama_available": _get_lama_session() is not None
        })
        # Try LaMa PyTorch first (best quality)
        if use_lama and large_hole and _get_lama_manager() is not None:
            result_base = lama_inpaint_torch(opencv_image, binary_mask)
            log_user_action("inpaint_method", {"method": "lama_torch"})
        # Fallback to LaMa ONNX if available
        elif use_lama and large_hole and _get_lama_session() is not None:
            result_base = lama_inpaint_onnx(opencv_image, binary_mask)
            log_user_action("inpaint_method", {"method": "lama_onnx"})
        else:
            # Enhanced OpenCV inpainting with dual algorithm approach
            # First pass: Telea algorithm
            result_telea = cv2.inpaint(opencv_image, binary_mask, dynamic_radius, cv2.INPAINT_TELEA)
            # Second pass: Navier-Stokes for structure preservation
            result_ns = cv2.inpaint(result_telea, binary_mask, dynamic_radius, cv2.INPAINT_NS)
            # Blend the results
            result_base = cv2.addWeighted(result_telea, 0.6, result_ns, 0.4, 0)
            log_user_action("inpaint_method", {"method": "opencv_enhanced", "radius": dynamic_radius})

        # Enhanced blending with post-processing
        # Only apply additional post-processing if we used LaMa (already post-processed)
        if use_lama and (_get_lama_manager() is not None or _get_lama_session() is not None):
            result = result_base  # LaMa result is already post-processed
        else:
            # For OpenCV results, apply post-processing
            result = post_process_inpainted_result(opencv_image, result_base, binary_mask)
            
            # Additional soft blending for OpenCV results
            soft = binary_mask.astype(np.float32) / 255.0
            soft = cv2.GaussianBlur(soft, (0, 0), sigmaX=6, sigmaY=6)  # Reduced blur for sharper edges
            soft3 = np.repeat(soft[:, :, None], 3, axis=2)
            result = (soft3 * result.astype(np.float32) + (1.0 - soft3) * opencv_image.astype(np.float32)).astype(np.uint8)
        
        # Convert back to PIL
        result_image = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))
        
        # Resize back to original size if we downscaled
        if result_image.size != image.size:
            result_image = result_image.resize(image.size, Image.LANCZOS)
        
        log_user_action("painted_areas_removal_success", {
            "painted_pixels_removed": int(np.sum(binary_mask > 0))
        })
        
        buf = io.BytesIO()
        result_image.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
        
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("painted_areas_removal_error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Painted areas removal failed: {str(e)}")


@app.post("/api/bulk-resize")
async def bulk_resize(
    request: Request,
    files: List[UploadFile] = File(...),
    width: int = Form(800),
    height: int = Form(600),
    maintain_aspect: bool = Form(True),
    quality: int = Form(90),
):
    """Resize multiple images and return as a ZIP file."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # Validate inputs
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 images allowed per batch")
    
    if width < 1 or width > 4000 or height < 1 or height > 4000:
        raise HTTPException(status_code=400, detail="Width and height must be between 1 and 4000 pixels")
    
    if quality < 1 or quality > 100:
        raise HTTPException(status_code=400, detail="Quality must be between 1 and 100")
    
    log_user_action("bulk_resize_started", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "file_count": len(files),
        "target_width": width,
        "target_height": height,
        "maintain_aspect": maintain_aspect,
        "quality": quality
    })
    
    processed_images = []
    errors = []
    
    async def process_single_resize(i, file):
        """Process a single image resize and return result or error."""
        try:
            # Validate file type
            if not file.content_type or not file.content_type.startswith("image/"):
                return f"File {i+1} ({file.filename}): Not an image file"
            
            # Read and process image
            contents = await file.read()
            if len(contents) > 10 * 1024 * 1024:  # 10MB limit per file
                return f"File {i+1} ({file.filename}): File too large (max 10MB)"
            
            image = Image.open(io.BytesIO(contents))
            
            # Convert to RGB if necessary
            if image.mode in ('RGBA', 'LA', 'P'):
                # Create white background for transparent images
                background = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                background.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
                image = background
            elif image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Resize image to exact dimensions
            original_width, original_height = image.size
            target_aspect = width / height
            original_aspect = original_width / original_height
            
            if maintain_aspect:
                # Fit within target dimensions while maintaining aspect ratio
                if original_aspect > target_aspect:
                    # Image is wider - fit to width
                    new_width = width
                    new_height = int(width / original_aspect)
                else:
                    # Image is taller - fit to height
                    new_height = height
                    new_width = int(height * original_aspect)
                
                # Resize maintaining aspect ratio
                resized = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Create canvas with exact target dimensions and center the image
                canvas = Image.new('RGB', (width, height), (255, 255, 255))
                offset_x = (width - new_width) // 2
                offset_y = (height - new_height) // 2
                canvas.paste(resized, (offset_x, offset_y))
                resized = canvas
            else:
                # Crop and resize to exact dimensions (may distort)
                resized = image.resize((width, height), Image.Resampling.LANCZOS)
            
            # Save to bytes
            output_buffer = io.BytesIO()
            resized.save(output_buffer, format='JPEG', quality=quality, optimize=True)
            output_data = output_buffer.getvalue()
            
            # Store filename and data
            filename = file.filename or f"image_{i+1}.jpg"
            if not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                filename += '.jpg'
            
            return {
                'filename': filename,
                'data': output_data,
                'original_size': f"{image.width}x{image.height}",
                'new_size': f"{new_width}x{new_height}"
            }
            
        except Exception as e:
            return f"File {i+1} ({file.filename}): {str(e)}"
    
    try:
        # Process images in parallel with bounded concurrency
        tasks = [process_single_resize(i, file) for i, file in enumerate(files)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Separate successful results from errors
        for result in results:
            if isinstance(result, str):  # Error message
                errors.append(result)
            elif isinstance(result, dict):  # Successful resize
                processed_images.append(result)
            else:  # Exception
                errors.append(f"Unexpected error: {str(result)}")
        
        if not processed_images:
            raise HTTPException(status_code=400, detail="No images could be processed. " + "; ".join(errors))
        
        # Create ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for img_data in processed_images:
                zip_file.writestr(img_data['filename'], img_data['data'])
        
        zip_data = zip_buffer.getvalue()
        
        log_user_action("bulk_resize_completed", {
            "processed_count": len(processed_images),
            "error_count": len(errors),
            "zip_size_bytes": len(zip_data),
            "zip_size_mb": round(len(zip_data) / (1024 * 1024), 2)
        })
        
        # Return ZIP file
        headers = {
            "Content-Disposition": "attachment; filename=resized_images.zip",
            "Content-Type": "application/zip"
        }
        
        return Response(content=zip_data, headers=headers, media_type="application/zip")
        
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("bulk_resize_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Bulk resize error: {str(e)}")


@app.post("/api/bulk-convert-format")
async def bulk_convert_format(
    request: Request,
    files: List[UploadFile] = File(...),
    target_format: str = Form("png"),
    transparent: bool = Form(False),
    quality: int = Form(90),
):
    """Convert multiple images to a different format and return as a ZIP file."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # Validate inputs
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 images allowed per batch")
    
    if quality < 1 or quality > 100:
        raise HTTPException(status_code=400, detail="Quality must be between 1 and 100")
    
    supported_targets = {"png", "jpg", "webp", "bmp", "gif", "tiff", "ico", "ppm", "pgm"}
    if target_format not in supported_targets:
        raise HTTPException(status_code=400, detail=f"Unsupported target_format. Use one of: {sorted(supported_targets)}")
    
    log_user_action("bulk_convert_started", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "file_count": len(files),
        "target_format": target_format,
        "transparent": transparent,
        "quality": quality
    })
    
    processed_images = []
    errors = []
    
    async def process_single_convert(i, file):
        """Process a single image format conversion and return result or error."""
        try:
            # Validate file type
            if not file.content_type or not file.content_type.startswith("image/"):
                return f"File {i+1} ({file.filename}): Not an image file"
            
            # Read and process image
            contents = await file.read()
            if len(contents) > 10 * 1024 * 1024:  # 10MB limit per file
                return f"File {i+1} ({file.filename}): File too large (max 10MB)"
            
            image = Image.open(io.BytesIO(contents))
            
            # Decide mode based on transparency setting and target
            supports_alpha = target_format in {"png", "webp", "tiff", "ico", "gif"}
            keep_alpha = bool(transparent and supports_alpha)
            
            # Convert image based on format requirements
            if keep_alpha:
                converted = image.convert("RGBA")
                # Auto-trim fully transparent padding
                try:
                    alpha = converted.split()[-1]
                    bbox = alpha.getbbox()
                    if bbox and bbox != (0, 0, converted.width, converted.height):
                        converted = converted.crop(bbox)
                except Exception:
                    pass
            else:
                # Flatten onto opaque white background for formats w/o alpha
                if image.mode in ("RGBA", "LA"):
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    background.paste(image, mask=image.split()[-1])
                    converted = background
                else:
                    converted = image.convert("RGB")
            
            # Prepare save parameters
            params = {}
            if target_format == "jpg":
                params.update({"quality": max(1, min(int(quality), 100)), "optimize": True})
                save_format = "JPEG"
            elif target_format == "webp":
                params.update({"quality": max(1, min(int(quality), 100))})
                save_format = "WEBP"
            elif target_format == "bmp":
                save_format = "BMP"
            elif target_format == "gif":
                if keep_alpha:
                    converted = converted.convert("RGBA")
                    palette_image = converted.convert("P", palette=Image.ADAPTIVE, colors=255)
                    alpha = converted.split()[-1]
                    mask = Image.eval(alpha, lambda a: 255 if a <= 1 else 0)
                    palette_image.info['transparency'] = 255
                    palette_image.paste(255, None, mask)
                    converted = palette_image
                else:
                    converted = converted.convert("P", palette=Image.ADAPTIVE, colors=256)
                params.update({"optimize": True, "save_all": False})
                save_format = "GIF"
            elif target_format == "tiff":
                params.update({"compression": "tiff_lzw"})
                save_format = "TIFF"
            elif target_format == "ico":
                # ICO must be 256x256
                size = 256
                img_rgba = converted.convert("RGBA")
                min_side = min(img_rgba.width, img_rgba.height)
                scale = size / float(min_side)
                resized = img_rgba.resize((max(1, int(round(img_rgba.width * scale))),
                                           max(1, int(round(img_rgba.height * scale)))), Image.LANCZOS)
                left = (resized.width - size) // 2
                top = (resized.height - size) // 2
                converted = resized.crop((left, top, left + size, top + size))
                save_format = "ICO"
                params.update({"sizes": [(256, 256)]})
            elif target_format in {"ppm", "pgm"}:
                if target_format == "ppm":
                    converted = converted.convert("RGB")
                else:
                    converted = converted.convert("L")
                save_format = "PPM"
                params.update({"bits": 8})
            else:
                save_format = "PNG"
            
            # Save to bytes
            output_buffer = io.BytesIO()
            converted.save(output_buffer, format=save_format, **params)
            output_data = output_buffer.getvalue()
            
            # Store filename and data
            filename = file.filename or f"image_{i+1}.{target_format}"
            if not filename.lower().endswith(f'.{target_format}'):
                name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                filename = f"{name_without_ext}.{target_format}"
            
            return {
                'filename': filename,
                'data': output_data,
                'original_size': f"{image.width}x{image.height}",
                'new_format': target_format
            }
            
        except Exception as e:
            return f"File {i+1} ({file.filename}): {str(e)}"
    
    try:
        # Process images in parallel with bounded concurrency
        tasks = [process_single_convert(i, file) for i, file in enumerate(files)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Separate successful results from errors
        for result in results:
            if isinstance(result, str):  # Error message
                errors.append(result)
            elif isinstance(result, dict):  # Successful conversion
                processed_images.append(result)
            else:  # Exception
                errors.append(f"Unexpected error: {str(result)}")
        
        if not processed_images:
            raise HTTPException(status_code=400, detail="No images could be processed. " + "; ".join(errors))
        
        # Create ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for img_data in processed_images:
                zip_file.writestr(img_data['filename'], img_data['data'])
        
        zip_data = zip_buffer.getvalue()
        
        log_user_action("bulk_convert_completed", {
            "processed_count": len(processed_images),
            "error_count": len(errors),
            "target_format": target_format,
            "zip_size_bytes": len(zip_data),
            "zip_size_mb": round(len(zip_data) / (1024 * 1024), 2)
        })
        
        # Return ZIP file
        headers = {
            "Content-Disposition": f"attachment; filename=converted_images_{target_format}.zip",
            "Content-Type": "application/zip"
        }
        
        return Response(content=zip_data, headers=headers, media_type="application/zip")
        
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("bulk_convert_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Bulk convert error: {str(e)}")


def detect_image_quality(image_array):
    """Analyze image quality and return detailed metrics."""
    try:
        # Convert to grayscale for analysis
        if len(image_array.shape) == 3:
            gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = image_array
        
        quality_metrics = {}
        issues = []
        overall_score = 100
        
        # Common stats
        height, width = gray.shape
        total_pixels = float(height * width)
        gray_float = gray.astype(np.float64)
        gray_std = float(np.std(gray_float))
        gray_mean = float(np.mean(gray_float))

        # 1) Blur detection (normalized Laplacian + Tenengrad + depth-of-field analysis)
        lap = cv2.Laplacian(gray_float, cv2.CV_64F)
        lap_var = float(lap.var())
        sobel_x = cv2.Sobel(gray_float, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray_float, cv2.CV_64F, 0, 1, ksize=3)
        tenengrad = np.hypot(sobel_x, sobel_y)
        ten_var = float(tenengrad.var())
        texture_var = float(np.var(gray_float)) + 1e-6
        lap_norm = lap_var / texture_var
        ten_norm = ten_var / texture_var
        blur_index = 0.5 * lap_norm + 0.5 * ten_norm  # higher is sharper
        
        # Depth-of-field detection: check if center is sharper than edges (intentional blur)
        center_h, center_w = height // 2, width // 2
        center_size = min(height, width) // 3  # Center region size
        y1, y2 = center_h - center_size//2, center_h + center_size//2
        x1, x2 = center_w - center_size//2, center_w + center_size//2
        y1, y2 = max(0, y1), min(height, y2)
        x1, x2 = max(0, x1), min(width, x2)
        
        # Calculate sharpness in center vs edges
        center_lap = lap[y1:y2, x1:x2]
        center_ten = tenengrad[y1:y2, x1:x2]
        center_sharpness = float(np.mean(center_lap)) + float(np.mean(center_ten))
        
        # Edge regions (corners and borders)
        edge_regions = []
        if y1 > 0: edge_regions.append(lap[:y1, :])  # top
        if y2 < height: edge_regions.append(lap[y2:, :])  # bottom
        if x1 > 0: edge_regions.append(lap[:, :x1])  # left
        if x2 < width: edge_regions.append(lap[:, x2:])  # right
        
        if edge_regions:
            edge_sharpness = float(np.mean([np.mean(region) for region in edge_regions]))
            # If center is significantly sharper than edges, likely intentional DOF blur
            dof_ratio = center_sharpness / (edge_sharpness + 1e-6)
            if dof_ratio > 2.0:  # Center is 2x sharper than edges
                # Boost blur score for intentional depth-of-field
                blur_index *= 1.3
                blur_score = max(0.0, min(100.0, (blur_index / 1.2) * 100.0))
            else:
                blur_score = max(0.0, min(100.0, (blur_index / 1.2) * 100.0))
        else:
            blur_score = max(0.0, min(100.0, (blur_index / 1.2) * 100.0))
        
        if blur_score < 40:  # More lenient threshold for decent images
            issues.append("blur")
        quality_metrics["blur_score"] = float(round(blur_score, 1))

        # 2) Noise detection (resolution-aware + texture-aware filtering)
        hp_kernel = np.array([[-1, -1, -1],
                              [-1,  8, -1],
                              [-1, -1, -1]], dtype=np.float64)
        residual = cv2.filter2D(gray_float, -1, hp_kernel, borderType=cv2.BORDER_REFLECT)
        
        # Resolution-aware noise threshold (higher res = more detail = higher noise threshold)
        resolution_factor = min(2.0, max(0.5, total_pixels / 500000.0))  # Scale based on resolution
        
        # Build flat-region mask: low edges and low local variance
        edges_mask = cv2.Canny(gray.astype(np.uint8), 50, 150) == 0
        mean = cv2.blur(gray_float, (5, 5))
        sqmean = cv2.blur(gray_float * gray_float, (5, 5))
        local_var = np.maximum(0.0, sqmean - mean * mean)
        
        # Adjust texture threshold based on resolution
        texture_threshold = 50.0 * resolution_factor
        flats_mask = (local_var < texture_threshold) & edges_mask
        
        if np.any(flats_mask):
            residual_std = float(np.std(residual[flats_mask]))
        else:
            residual_std = float(np.std(residual))
        
        snr = (gray_mean + 1e-6) / (gray_std + 1e-6)
        
        # Contrast-aware SNR scoring (high contrast = good, not noise)
        # Calculate contrast ratio to distinguish good contrast from noise
        contrast_ratio = gray_std / (gray_mean + 1e-6)
        
        # For high-contrast images (product photos), adjust SNR scoring
        if contrast_ratio > 0.3:  # High contrast image
            # Boost SNR score for high-contrast images (good contrast, not noise)
            snr_score = max(0.0, min(100.0, (snr / 8.0) * 100.0))  # More lenient threshold
        else:
            # Normal SNR scoring for low-contrast images
            snr_score = max(0.0, min(100.0, (snr / 10.0) * 100.0))
        # Adjust residual penalty based on resolution
        residual_threshold = 50.0 * resolution_factor
        residual_penalty = max(0.0, min(100.0, (residual_std / residual_threshold) * 100.0))
        
        # Combine scores with resolution awareness
        noise_score = max(0.0, min(100.0, 0.7 * snr_score + 0.3 * (100.0 - residual_penalty)))
        
        # Adjust threshold based on resolution (high-res images get more lenient threshold)
        noise_threshold = max(25.0, 35.0 - (resolution_factor - 1.0) * 10.0)
        
        if noise_score < noise_threshold:
            issues.append("noise")
        quality_metrics["noise_score"] = float(round(noise_score, 1))

        # 3) Pixelation detection (FFT grid peaks + edge density + SSIM smoothness check)
        # Downsample to speed up FFT while keeping grid artifacts
        max_side = 512
        scale = max(1.0, max(height, width) / max_side)
        if scale > 1.0:
            small = cv2.resize(gray_float, (int(round(width / scale)), int(round(height / scale))), interpolation=cv2.INTER_AREA)
        else:
            small = gray_float
        # FFT magnitude
        f = np.fft.fftshift(np.fft.fft2(small))
        mag = np.abs(f)
        # Ignore DC and very low frequencies
        h, w = small.shape
        cy, cx = h // 2, w // 2
        low_radius = max(3, int(0.02 * min(h, w)))
        yy, xx = np.ogrid[:h, :w]
        mask_low = (yy - cy) ** 2 + (xx - cx) ** 2 <= low_radius ** 2
        mag_filt = mag.copy()
        mag_filt[mask_low] = 0
        # Estimate grid energy along 8-pixel periodicity harmonics
        # Translate pixel-domain 8px blocks to frequency-domain ~w/8 and h/8 bands
        bands = []
        for k in (1, 2, 3):
            vx = int(round((w / 8.0) * k))
            vy = int(round((h / 8.0) * k))
            bands.append((vx, 0))
            bands.append((0, vy))
        band_radius = max(1, int(0.01 * min(h, w)))
        grid_energy = 0.0
        for bx, by in bands:
            if bx != 0:
                grid_energy += float(mag_filt[:, max(0, cx - bx - band_radius):min(w, cx - bx + band_radius)].sum())
                grid_energy += float(mag_filt[:, max(0, cx + bx - band_radius):min(w, cx + bx + band_radius)].sum())
            if by != 0:
                grid_energy += float(mag_filt[max(0, cy - by - band_radius):min(h, cy - by + band_radius), :].sum())
                grid_energy += float(mag_filt[max(0, cy + by - band_radius):min(h, cy + by + band_radius), :].sum())
        total_hf_energy = float(mag_filt.sum()) + 1e-6
        grid_ratio = grid_energy / total_hf_energy
        # Edge density
        edges = cv2.Canny(gray.astype(np.uint8), 50, 150)
        edge_density = float(np.sum(edges > 0) / total_pixels)
        # Pixelation score: penalize when grid_ratio high and edges sparse
        pixelation_penalty = max(0.0, min(100.0, (grid_ratio / 0.3) * 100.0))  # 0.3 ~ strong grid
        # SSIM smoothness check via 2x bicubic upsample then downsample
        try:
            up2 = cv2.resize(gray.astype(np.uint8), (width * 2, height * 2), interpolation=cv2.INTER_CUBIC)
            down2 = cv2.resize(up2, (width, height), interpolation=cv2.INTER_AREA)
            ssim_val = float(ssim(gray.astype(np.uint8), down2, data_range=255))
        except Exception:
            ssim_val = 1.0
        ssim_change = 1.0 - ssim_val
        if edge_density < 0.06 and grid_ratio > 0.12 and ssim_change > 0.15:
            issues.append("pixelation")
        else:
            # dampen penalty if SSIM does not indicate strong blockiness reduction
            pixelation_penalty *= 0.5
        pixelation_score = max(0.0, min(100.0, 100.0 - pixelation_penalty))
        quality_metrics["pixelation_score"] = float(round(pixelation_score, 1))

        # 4) Exposure & lighting (histogram balance + white background detection)
        hist, _ = np.histogram(gray, bins=256, range=(0, 256))
        hist = hist.astype(np.float64)
        hist /= (hist.sum() + 1e-6)
        dark_pixels = float(np.sum(hist[:85]))
        mid_pixels = float(np.sum(hist[85:170]))
        bright_pixels = float(np.sum(hist[170:]))
        
        # White background detection (common in product photos)
        very_bright_pixels = float(np.sum(hist[200:]))  # Very bright pixels (200-255)
        white_background_detected = very_bright_pixels > 0.4  # 40%+ very bright pixels
        
        # balance score
        exposure_score = 100.0
        if dark_pixels > 0.60:  # More lenient threshold for decent images
            issues.append("underexposed")
            exposure_score = max(0.0, 100.0 - (dark_pixels - 0.50) * 200.0)
        elif bright_pixels > 0.60:  # More lenient threshold for decent images
            # Special handling for white background product photos
            if white_background_detected:
                # For white backgrounds, only flag if extremely overexposed
                if bright_pixels > 0.80:  # 80%+ bright pixels
                    issues.append("overexposed")
                    exposure_score = max(0.0, 100.0 - (bright_pixels - 0.70) * 150.0)
                else:
                    # White background is acceptable, don't penalize heavily
                    exposure_score = max(80.0, 100.0 - (bright_pixels - 0.60) * 50.0)
            else:
                issues.append("overexposed")
                exposure_score = max(0.0, 100.0 - (bright_pixels - 0.50) * 200.0)
        elif mid_pixels < 0.25:
            exposure_score = max(0.0, mid_pixels * 400.0)
        # entropy (0..1)
        nz = hist[hist > 0]
        entropy = float(-(nz * np.log2(nz)).sum() / np.log2(256))
        entropy_score = max(0.0, min(100.0, entropy * 100.0))
        # RMS contrast
        contrast_score = max(0.0, min(100.0, (gray_std / 255.0) * 100.0))
        # Clipping measure at 0/255
        zeros_frac = float(np.mean(gray == 0))
        fulls_frac = float(np.mean(gray == 255))
        clip_frac = zeros_frac + fulls_frac
        if clip_frac > 0.15:  # More lenient threshold for decent images
            issues.append("clipped")
            exposure_score = min(exposure_score, max(0.0, 100.0 - (clip_frac - 0.10) * 300.0))
        exposure_combined = 0.5 * exposure_score + 0.25 * entropy_score + 0.25 * contrast_score
        quality_metrics["exposure_score"] = float(round(exposure_combined, 1))

        # 5) Resolution & usability (with aspect ratio factor)
        if total_pixels < 100000:  # ~316x316
            issues.append("low_resolution")
            resolution_score = max(0.0, (total_pixels / 100000.0) * 100.0)
        elif total_pixels < 400000:  # ~632x632
            resolution_score = 80.0
        else:
            resolution_score = 100.0
        aspect_ratio = max(width / float(height), height / float(width))
        if aspect_ratio > 3.0:
            # penalize extreme panoramas
            penalty = min(30.0, (aspect_ratio - 3.0) * 10.0)
            resolution_score = max(0.0, resolution_score - penalty)
        # Couple resolution with blur (high res but soft images shouldn't get full credit)
        if resolution_score > 80.0 and quality_metrics["blur_score"] < 50.0:
            res_cap = quality_metrics["blur_score"] + 20.0
            resolution_score = min(resolution_score, res_cap)
        quality_metrics["resolution_score"] = float(round(resolution_score, 1))

        # 6) Compression artifacts (blockiness across 8x8 borders)
        # Compute border differences at multiples of 8
        block = 8
        # vertical borders energy
        vb = 0.0
        for x in range(block, width, block):
            vb += float(np.sum(np.abs(gray_float[:, x-1] - gray_float[:, x % width])))
        # horizontal borders energy
        hb = 0.0
        for y in range(block, height, block):
            hb += float(np.sum(np.abs(gray_float[y-1, :] - gray_float[y % height, :])))
        border_energy = vb + hb
        # baseline intra-block energy (differences away from borders)
        # use shifted differences to approximate
        intra_h = float(np.sum(np.abs(gray_float[:, 1:] - gray_float[:, :-1]))) + 1e-6
        intra_v = float(np.sum(np.abs(gray_float[1:, :] - gray_float[:-1, :]))) + 1e-6
        intra_total = intra_h + intra_v
        blockiness_ratio = float(border_energy) / float(intra_total)
        compression_score = max(0.0, min(100.0, 100.0 - (blockiness_ratio / 0.12) * 100.0))  # 0.12 is noticeable blockiness
        # Texture-aware damping for flat logos/solids
        if entropy_score < 20.0:
            compression_score = 100.0
            issues = [iss for iss in issues if iss != "compression_artifacts"]
        elif compression_score < 50.0:  # More lenient threshold for decent images
            issues.append("compression_artifacts")
        quality_metrics["compression_score"] = float(round(compression_score, 1))

        # Tiny image adjustments: soften harsh penalties for very small images
        min_side = min(height, width)
        if min_side < 128:
            # Relax blur/exposure for icons/thumbnails where metrics are unreliable
            quality_metrics["blur_score"] = float(max(quality_metrics["blur_score"], 40.0))
            quality_metrics["exposure_score"] = float(max(quality_metrics["exposure_score"], 40.0))
            # Do not flag exposure issues for tiny images
            issues = [iss for iss in issues if iss not in ("underexposed", "overexposed")]

        # Entropy-based blur relaxation for flat backgrounds
        if entropy_score < 30.0:
            pre = quality_metrics["blur_score"]
            quality_metrics["blur_score"] = float(max(quality_metrics["blur_score"], 60.0))
            if pre < 60.0 and "blur" in issues and quality_metrics["blur_score"] >= 60.0:
                issues = [iss for iss in issues if iss != "blur"]

        # Overall scoring (rebalanced for new metrics)
        weights = {
            'blur_score': 0.25,
            'resolution_score': 0.25,
            'exposure_score': 0.15,  # Reduced weight since it's often subjective
            'noise_score': 0.15,
            'compression_score': 0.15,  # Increased weight
            'pixelation_score': 0.05,
        }
        overall_score = (
            quality_metrics['blur_score'] * weights['blur_score'] +
            quality_metrics['resolution_score'] * weights['resolution_score'] +
            quality_metrics['exposure_score'] * weights['exposure_score'] +
            quality_metrics['noise_score'] * weights['noise_score'] +
            quality_metrics['compression_score'] * weights['compression_score'] +
            quality_metrics['pixelation_score'] * weights['pixelation_score']
        )
        quality_metrics["overall_score"] = round(float(overall_score), 1)
        quality_metrics["issues"] = issues
        quality_metrics["dimensions"] = f"{width}x{height}"
        
        return quality_metrics
        
    except Exception as e:
        return {
            "overall_score": 0,
            "issues": ["analysis_failed"],
            "error": str(e),
            "blur_score": 0,
            "noise_score": 0,
            "pixelation_score": 0,
            "exposure_score": 0,
            "resolution_score": 0,
            "compression_score": 0
        }


@app.post("/api/bulk-quality-check")
async def bulk_quality_check(
    request: Request,
    files: List[UploadFile] = File(...),
):
    """Analyze quality of multiple images and return detailed reports."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # Validate inputs
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 images allowed per batch")
    
    log_user_action("bulk_quality_check_started", {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "file_count": len(files)
    })
    
    analyzed_images = []
    errors = []
    
    async def process_single_image(i, file):
        """Process a single image file and return result or error."""
        try:
            # Validate file type
            if not file.content_type or not file.content_type.startswith("image/"):
                return f"File {i+1} ({file.filename}): Not an image file"
            
            # Read and process image
            contents = await file.read()
            if len(contents) > 10 * 1024 * 1024:  # 10MB limit per file
                return f"File {i+1} ({file.filename}): File too large (max 10MB)"
            
            # Convert to OpenCV format
            nparr = np.frombuffer(contents, np.uint8)
            image_array = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if image_array is None:
                return f"File {i+1} ({file.filename}): Could not decode image"
            
            # Convert BGR to RGB for analysis
            image_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
            
            # Analyze image quality (CPU-bound, run in thread pool)
            async with PROCESS_SEM:
                quality_metrics = await asyncio.to_thread(detect_image_quality, image_array)
                
                # Determine quality category
                overall_score = quality_metrics["overall_score"]
                if overall_score >= 80:
                    category = "good"
                elif overall_score >= 60:
                    category = "fair"
                else:
                    category = "poor"
                
                # Generate repair suggestions
                repair_links = []
                for issue in quality_metrics.get("issues", []):
                    if issue == "blur":
                        repair_links.append({"tool": "enhance", "url": "/enhance-image.html", "description": "Enhance Image"})
                    elif issue == "noise":
                        repair_links.append({"tool": "enhance", "url": "/enhance-image.html", "description": "Enhance Image"})
                    elif issue == "pixelation":
                        repair_links.append({"tool": "upscale", "url": "/upscale-image.html", "description": "AI Image Upscaler"})
                    elif issue == "overexposed":
                        repair_links.append({"tool": "enhance", "url": "/enhance-image.html", "description": "Enhance Image"})
                    elif issue == "underexposed":
                        repair_links.append({"tool": "enhance", "url": "/enhance-image.html", "description": "Enhance Image"})
                    elif issue == "low_resolution":
                        repair_links.append({"tool": "upscale", "url": "/upscale-image.html", "description": "AI Image Upscaler"})
                    elif issue == "compression_artifacts":
                        repair_links.append({"tool": "enhance", "url": "/enhance-image.html", "description": "Enhance Image"})
                
                return {
                    'filename': file.filename or f"image_{i+1}",
                    'quality_metrics': quality_metrics,
                    'category': category,
                    'repair_links': repair_links,
                    'file_size_mb': round(len(contents) / (1024 * 1024), 2)
                }
                
        except Exception as e:
            return f"File {i+1} ({file.filename}): {str(e)}"
    
    try:
        # Process images in parallel with bounded concurrency
        tasks = [process_single_image(i, file) for i, file in enumerate(files)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Separate successful results from errors
        for result in results:
            if isinstance(result, str):  # Error message
                errors.append(result)
            elif isinstance(result, dict):  # Successful analysis
                analyzed_images.append(result)
            else:  # Exception
                errors.append(f"Unexpected error: {str(result)}")
        
        if not analyzed_images:
            raise HTTPException(status_code=400, detail="No images could be analyzed. " + "; ".join(errors))
        
        # Categorize images
        good_images = [img for img in analyzed_images if img['category'] == 'good']
        fair_images = [img for img in analyzed_images if img['category'] == 'fair']
        poor_images = [img for img in analyzed_images if img['category'] == 'poor']
        
        log_user_action("bulk_quality_check_completed", {
            "total_analyzed": len(analyzed_images),
            "good_count": len(good_images),
            "fair_count": len(fair_images),
            "poor_count": len(poor_images),
            "error_count": len(errors)
        })
        
        return {
            "total_images": len(analyzed_images),
            "good_images": len(good_images),
            "fair_images": len(fair_images),
            "poor_images": len(poor_images),
            "error_count": len(errors),
            "images": analyzed_images,
            "errors": errors
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log_user_action("bulk_quality_check_error", {"message": str(e)})
        raise HTTPException(status_code=500, detail=f"Quality check error: {str(e)}")


