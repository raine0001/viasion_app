# Unified visaion app.py — optimized for dual model use, cleaned init, and removed /detect_video_init

from flask import (
    Flask,
    request,
    Response,
    jsonify,
    send_from_directory,
    send_file,
    abort,
    url_for,
    redirect,
    Blueprint,
    current_app,
    session,
)
from flask_cors import CORS
from werkzeug.utils import secure_filename
import numpy as np
import requests
import cv2
import os
import shlex

try:
    import torch  # type: ignore
    from ultralytics.nn.tasks import DetectionModel  # type: ignore
    from ultralytics import YOLO  # type: ignore

    TORCH_AVAILABLE = True
except Exception:
    TORCH_AVAILABLE = False
import base64
from openai import OpenAI
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import (
    UniqueConstraint,
    Index,
    String,
    Integer,
    Float,
    Boolean,
    Date,
    DateTime,
    Text,
    JSON as MyJSON,
    BigInteger,
    Column,
    ForeignKey,
    create_engine,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.types import JSON as MyJSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import traceback
import re
import csv
import shutil
import subprocess
import json
import glob
import time
import hashlib
import secrets
import smtplib
import ssl
import html
from pathlib import Path
import io
import wave
import mimetypes
from email.message import EmailMessage
from datetime import date, datetime, timezone, timedelta
from collections import defaultdict
import random
import threading
from queue import Queue
from PIL import Image
from urllib.parse import urlencode
from arcmm_api import arcmm_api
from support.engine import detect_intent, generate_general_reply, run_intent_handler


# SQLAlchemy models are defined later inside _try_init_db()
SQLA_AVAILABLE = True


def _truthy(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


try:
    if TORCH_AVAILABLE:
        torch.serialization.add_safe_globals([DetectionModel])
except Exception:
    pass

# Make CV/Torch single-threaded on Windows (prevents deadlocks / resets)
cv2.setNumThreads(0)
os.environ.setdefault("OMP_NUM_THREADS", "1")
try:
    if TORCH_AVAILABLE:
        torch.set_num_threads(1)
except Exception:
    pass

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

app.register_blueprint(arcmm_api)

# Secret key for session cookies (load from .env if present)
try:
    load_dotenv()
    app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)
except Exception:
    app.secret_key = os.urandom(24)

_env_app = (os.getenv("APP_ENV") or "").strip().lower()
_env_flask = (os.getenv("FLASK_ENV") or "").strip().lower()
DEV_MODE = _truthy(os.getenv("DEV_MODE", "0")) or _env_flask in (
    "development",
    "dev",
) or _env_app in ("development", "dev")
DB_REQUIRED = _truthy(os.getenv("REQUIRE_DATABASE"), default=not DEV_MODE)

# Stub auth fallback is disabled by default when a DB URI is present.
_has_db_uri = any(
    os.getenv(key)
    for key in ("SQLALCHEMY_DATABASE_URI", "DATABASE_URI", "DATABASE_URL")
)
_default_stub = "0" if _has_db_uri else "1"
ALLOW_STUB_AUTH = _truthy(
    os.getenv("ALLOW_STUB_AUTH", _default_stub), default=(_default_stub == "1")
)
try:
    PASSWORD_RESET_TOKEN_TTL_MINUTES = int(
        os.getenv("PASSWORD_RESET_TOKEN_TTL_MINUTES", "60")
    )
except Exception:
    PASSWORD_RESET_TOKEN_TTL_MINUTES = 60
if PASSWORD_RESET_TOKEN_TTL_MINUTES < 10:
    PASSWORD_RESET_TOKEN_TTL_MINUTES = 10

# Simple trace flag (enable with visaion_TRACE=1; legacy DOACH_TRACE still honored)
try:
    _TRACE = os.getenv("visaion_TRACE") or os.getenv("DOACH_TRACE") or "1"
    TRACE_ON = _TRACE.lower() not in ("0", "false", "no", "off", "")
except Exception:
    TRACE_ON = True


def _trace(*args, **kwargs):
    try:
        if TRACE_ON:
            print(*args, **kwargs)
    except Exception:
        pass


PARTIAL_CHUNK_BYTES = 512 * 1024  # 512 KB initial streaming chunk
FFMPEG_BIN = os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFMPEG_TRANSCODE_PRESET = os.getenv("FFMPEG_TRANSCODE_PRESET", "veryfast")
FFMPEG_TRANSCODE_CRF = os.getenv("FFMPEG_TRANSCODE_CRF", "23")


def _remux_to_mp4(src: Path) -> Path | None:
    """Remux a WebM clip to fast-start MP4 for smoother streaming."""
    try:
        src_path = Path(src)
    except Exception:
        return None
    if not src_path.exists():
        return None
    dst_path = src_path.with_suffix(".mp4")
    try:
        if dst_path.exists() and dst_path.stat().st_mtime >= src_path.stat().st_mtime:
            return dst_path
    except Exception:
        # If we cannot stat the files, attempt conversion anyway.
        pass

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(src_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dst_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        _trace("[clip:ffmpeg] remux ok", {"src": str(src_path), "dst": str(dst_path)})
        return dst_path
    except FileNotFoundError:
        _trace("[clip:ffmpeg] binary not found", {"cmd": cmd})
    except subprocess.CalledProcessError as exc:
        stderr_text = ""
        try:
            stderr_text = (
                exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
            )
        except Exception:
            stderr_text = str(exc.stderr)
        _trace(
            "[clip:ffmpeg] remux failed",
            {"cmd": cmd, "code": exc.returncode, "stderr": stderr_text[-400:]},
        )
        try:
            if dst_path.exists():
                dst_path.unlink()
        except Exception:
            pass
        transcode_cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            str(src_path),
            "-c:v",
            "libx264",
            "-preset",
            FFMPEG_TRANSCODE_PRESET,
            "-crf",
            FFMPEG_TRANSCODE_CRF,
            "-an",
            str(dst_path),
        ]
        try:
            subprocess.run(
                transcode_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            _trace(
                "[clip:ffmpeg] transcode ok",
                {"src": str(src_path), "dst": str(dst_path), "cmd": transcode_cmd},
            )
            return dst_path
        except Exception as sub_exc:
            sub_err = ""
            if isinstance(sub_exc, subprocess.CalledProcessError):
                try:
                    sub_err = (
                        sub_exc.stderr.decode("utf-8", errors="ignore")
                        if sub_exc.stderr
                        else ""
                    )
                except Exception:
                    sub_err = str(sub_exc.stderr)
            else:
                sub_err = str(sub_exc)
            _trace(
                "[clip:ffmpeg] transcode failed",
                {"cmd": transcode_cmd, "error": sub_err[-400:]},
            )
            try:
                if dst_path.exists():
                    dst_path.unlink()
            except Exception:
                pass
    except Exception as exc:
        _trace("[clip:ffmpeg] remux exception", exc)
        try:
            if dst_path.exists():
                dst_path.unlink()
        except Exception:
            pass
    return None


REQUIRED_LABELS = {"basketball", "hoop", "net", "backboard", "player"}
CONFIDENCE_THRESHOLD = 0.01  # Lowered from 0.75 to 0.01 for improved detection
SKIPPED_LOG_PATH = "skipped_frames.json"
UPLOAD_FOLDER = "uploads"
FRAME_FOLDER = "frame_cache"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(FRAME_FOLDER, exist_ok=True)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
PRESET_FILE = DATA_DIR / "voice_presets.json"

PROJECT_MANIFEST_PATH = os.path.join(app.root_path, "static", "config", "projects.json")
_PROJECT_MANIFEST_CACHE = None
_PROJECT_MANIFEST_MTIME = None
_DEFAULT_DATASET_FALLBACK = {
    "project": "basketball",
    "slug": "basketball_pose",
    "root": "datasets/visaion_seg",
    "frameCacheRoot": "frame_cache",
    "framesRoot": "frames",
    "labelTrainRoot": "datasets/visaion_seg/labels/train",
    "imagesTrainRoot": "datasets/visaion_seg/images/train",
}


def _get_project_manifest():
    global _PROJECT_MANIFEST_CACHE, _PROJECT_MANIFEST_MTIME
    try:
        mtime = os.path.getmtime(PROJECT_MANIFEST_PATH)
    except OSError:
        return {"projects": {}, "defaultProject": None}
    if _PROJECT_MANIFEST_CACHE is not None and _PROJECT_MANIFEST_MTIME == mtime:
        return _PROJECT_MANIFEST_CACHE
    try:
        with open(PROJECT_MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"projects": {}, "defaultProject": None}
    _PROJECT_MANIFEST_CACHE = data
    _PROJECT_MANIFEST_MTIME = mtime
    return data


def _normalize_dataset(project_slug, dataset_cfg):
    cfg = dict(dataset_cfg or {})
    cfg["project"] = project_slug or cfg.get("project") or "basketball"
    cfg["slug"] = cfg.get("slug") or f"{cfg['project']}_pose"
    cfg["root"] = cfg.get("root") or "datasets/visaion_seg"
    cfg["frameCacheRoot"] = cfg.get("frameCacheRoot") or "frame_cache"
    cfg["framesRoot"] = cfg.get("framesRoot") or "frames"
    if not cfg.get("labelTrainRoot"):
        cfg["labelTrainRoot"] = os.path.join(cfg["root"], "labels", "train")
    if not cfg.get("imagesTrainRoot"):
        cfg["imagesTrainRoot"] = os.path.join(cfg["root"], "images", "train")
    detector_path = cfg.get("detector")
    if detector_path:
        cfg["detector"] = detector_path
    else:
        cfg.pop("detector", None)
    labels = cfg.get("labels")
    if isinstance(labels, list):
        cfg["labels"] = labels
    elif labels is None:
        cfg["labels"] = []
    else:
        cfg["labels"] = list(labels)
    return cfg


def _resolve_dataset(dataset_slug=None):
    manifest = _get_project_manifest()
    target_slug = dataset_slug
    projects = manifest.get("projects") or {}

    if target_slug:
        for project_slug, project_cfg in projects.items():
            for dataset_cfg in project_cfg.get("datasets") or []:
                if dataset_cfg.get("slug") == target_slug:
                    return _normalize_dataset(project_slug, dataset_cfg)

    default_project = manifest.get("defaultProject")
    if default_project:
        project_cfg = projects.get(default_project, {})
        datasets = project_cfg.get("datasets") or []
        if datasets:
            return _normalize_dataset(default_project, datasets[0])

    return dict(_DEFAULT_DATASET_FALLBACK)


def _dataset_abs_path(dataset_cfg, key, *parts):
    root = dataset_cfg.get(key)
    if not root:
        return None
    base = (
        root
        if os.path.isabs(root)
        else os.path.abspath(os.path.join(app.root_path, root))
    )
    return os.path.abspath(os.path.join(base, *parts))


def _send_dataset_label(dataset_cfg, filename):
    label_root = _dataset_abs_path(dataset_cfg, "labelTrainRoot")
    if not label_root:
        return None
    candidate = os.path.abspath(os.path.join(label_root, filename))
    if not candidate.startswith(label_root):
        return None
    if not os.path.exists(candidate):
        return None
    return send_file(candidate, mimetype="text/plain")


def _ensure_dataset_dirs(dataset_cfg):
    for key in (
        "root",
        "frameCacheRoot",
        "framesRoot",
        "labelTrainRoot",
        "imagesTrainRoot",
    ):
        path = dataset_cfg.get(key)
        if not path:
            continue
        abs_path = _dataset_abs_path(dataset_cfg, key)
        if abs_path:
            os.makedirs(abs_path, exist_ok=True)


def _collect_image_files(root_path):
    total = 0
    by_folder = []
    if not root_path or not os.path.exists(root_path):
        return total, by_folder
    if os.path.isdir(root_path):
        for entry in sorted(os.listdir(root_path)):
            full = os.path.join(root_path, entry)
            if os.path.isdir(full):
                count = sum(
                    1
                    for name in os.listdir(full)
                    if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
                )
                by_folder.append({"folder": entry, "images": count})
                total += count
            elif os.path.isfile(full):
                ext = os.path.splitext(entry)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    total += 1
    return total, by_folder


def _dataset_summary(dataset_cfg):
    summary = {
        "slug": dataset_cfg.get("slug"),
        "train_images": 0,
        "train_labels": 0,
        "label_distribution": [],
        "frame_cache_total": 0,
        "frame_sets": [],
    }
    frames_root = _dataset_abs_path(dataset_cfg, "frameCacheRoot")
    images_root = _dataset_abs_path(dataset_cfg, "imagesTrainRoot")
    labels_root = _dataset_abs_path(dataset_cfg, "labelTrainRoot")
    labels = dataset_cfg.get("labels") or []

    total_frames, frame_sets = _collect_image_files(frames_root)
    summary["frame_cache_total"] = total_frames
    summary["frame_sets"] = frame_sets

    if images_root and os.path.exists(images_root):
        summary["train_images"] = sum(
            1
            for name in os.listdir(images_root)
            if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
        )

    distribution = defaultdict(int)
    label_files = 0
    if labels_root and os.path.exists(labels_root):
        for name in os.listdir(labels_root):
            if not name.lower().endswith(".txt"):
                continue
            label_files += 1
            try:
                with open(os.path.join(labels_root, name), "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split()
                        if not parts:
                            continue
                        try:
                            cid = int(float(parts[0]))
                        except Exception:
                            continue
                        if labels and 0 <= cid < len(labels):
                            key = labels[cid]
                        else:
                            key = str(cid)
                        distribution[key] += 1
            except Exception:
                continue
    summary["train_labels"] = label_files
    summary["label_distribution"] = [
        {"label": key, "count": count} for key, count in sorted(distribution.items())
    ]
    summary["train_pairs"] = min(summary["train_images"], summary["train_labels"])
    return summary


_LEGACY_SUBSCRIPTIONS_CONFIG_PATH = os.path.join(
    app.root_path, "static", "config", "subscriptions.json"
)
_SUBSCRIPTIONS_CACHE = None
_SUBSCRIPTIONS_MTIME = None
_SUBSCRIPTIONS_PATH = None
_DEFAULT_SUBSCRIPTIONS = {
    "plans": {
        "basketball_monthly": {
            "id": "basketball_monthly",
            "name": "Basketball Coaching",
            "dataset": "basketball_pose",
            "description": "Full access to the basketball shot coaching experience.",
            "price": 19.99,
            "period": "monthly",
            "trial_days": 7,
            "active": True,
        },
        "golf_range_monthly": {
            "id": "golf_range_monthly",
            "name": "Golf Range Analyzer",
            "dataset": "g_pose",
            "description": "Pose and object analysis tailored for golf swing sessions at the range.",
            "price": 19.99,
            "period": "monthly",
            "trial_days": 7,
            "active": True,
        },
    },
    "userSubscriptions": {},
    "specials": {},
    "userSpecials": {},
}


def _get_subscriptions_config_path():
    override = (os.getenv("SUBSCRIPTIONS_CONFIG_PATH") or "").strip()
    if override:
        if os.path.isabs(override):
            return os.path.abspath(override)
        return os.path.abspath(os.path.join(app.root_path, override))
    base_dir = globals().get("SESSIONS_DIR")
    if not base_dir:
        try:
            base_dir = _resolve_sessions_dir()
        except Exception:
            base_dir = os.path.join(app.root_path, "sessions")
    return os.path.join(base_dir, "_config", "subscriptions.json")


def _maybe_migrate_subscriptions_config(target_path):
    legacy_path = _LEGACY_SUBSCRIPTIONS_CONFIG_PATH
    try:
        if os.path.abspath(target_path) == os.path.abspath(legacy_path):
            return
    except Exception:
        pass
    if os.path.exists(target_path) or not os.path.exists(legacy_path):
        return
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(legacy_path, target_path)
    except Exception:
        pass


def _ensure_subscription_defaults(data):
    if not isinstance(data, dict):
        data = {}
    data.setdefault("plans", {})
    data.setdefault("userSubscriptions", {})
    data.setdefault("specials", {})
    data.setdefault("userSpecials", {})
    return data


def _normalize_special_code(value):
    return str(value or "").strip().lower()


def _slugify_special_id(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return cleaned


def _normalize_special_payload(payload):
    if not isinstance(payload, dict):
        payload = {}
    raw_id = (
        payload.get("id")
        or payload.get("special_id")
        or payload.get("offer_id")
        or payload.get("slug")
        or ""
    )
    title = (payload.get("title") or payload.get("name") or "").strip()
    company = (payload.get("company") or payload.get("organization") or "").strip()
    code = (payload.get("code") or payload.get("promo") or payload.get("promo_code") or "").strip()
    signup_url = (payload.get("signup_url") or payload.get("signup") or payload.get("link") or "").strip()
    if not raw_id:
        raw_id = title or company or code
    special_id = _slugify_special_id(raw_id)
    if not special_id:
        raise ValueError("special id required")
    if not title:
        title = special_id.replace("_", " ").title()
    try:
        amount = float(payload.get("amount", payload.get("price", 0.0)) or 0.0)
    except Exception:
        amount = 0.0
    term_value_raw = payload.get("term_value", payload.get("term", 0))
    try:
        term_value = int(term_value_raw or 0)
    except Exception:
        term_value = 0
    term_unit = (payload.get("term_unit") or payload.get("termUnit") or "").strip().lower()
    unit_map = {
        "day": "days",
        "days": "days",
        "d": "days",
        "month": "months",
        "months": "months",
        "m": "months",
        "year": "years",
        "years": "years",
        "y": "years",
    }
    term_unit = unit_map.get(term_unit, term_unit)
    if term_value <= 0:
        term_value = 0
        term_unit = ""
    modules = payload.get("modules") or payload.get("projects") or payload.get("project") or []
    if isinstance(modules, str):
        modules = [m.strip() for m in modules.split(",")]
    if not isinstance(modules, (list, tuple, set)):
        modules = []
    module_list = []
    for module in modules:
        if not module:
            continue
        module_list.append(str(module).strip())
    active = bool(payload.get("active", True))
    return {
        "id": special_id,
        "title": title,
        "company": company,
        "code": code,
        "signup_url": signup_url,
        "term_value": max(0, term_value),
        "term_unit": term_unit,
        "amount": max(0.0, amount),
        "modules": sorted(set(module_list)),
        "active": active,
    }


def _project_slug_for_dataset(dataset_slug):
    if not dataset_slug:
        return None
    manifest = _get_project_manifest()
    projects = manifest.get("projects") or {}
    for project_slug, project_cfg in projects.items():
        for dataset_cfg in project_cfg.get("datasets") or []:
            if dataset_cfg.get("slug") == dataset_slug:
                return project_slug
    return None


def _best_plan_for_modules(modules, plans):
    if not modules:
        return {}, []
    best = {}
    for plan_id, plan in (plans or {}).items():
        if not isinstance(plan, dict):
            continue
        resolved_project = (plan.get("project") or plan.get("project_slug") or "").strip()
        if not resolved_project:
            resolved_project = _project_slug_for_dataset(plan.get("dataset"))
        if not resolved_project:
            continue
        active = plan.get("active", True)
        try:
            price = float(plan.get("price", 0.0) or 0.0)
        except Exception:
            price = 0.0
        key = (0 if active else 1, price, plan_id)
        current = best.get(resolved_project)
        if not current or key < current["key"]:
            best[resolved_project] = {"id": plan_id, "key": key}
    assigned = {}
    missing = []
    for module in modules:
        if module in best:
            assigned[module] = best[module]["id"]
        else:
            missing.append(module)
    return assigned, missing


def _load_subscriptions_config():
    global _SUBSCRIPTIONS_CACHE, _SUBSCRIPTIONS_MTIME, _SUBSCRIPTIONS_PATH
    path = _get_subscriptions_config_path()
    if _SUBSCRIPTIONS_PATH != path:
        _SUBSCRIPTIONS_PATH = path
        _SUBSCRIPTIONS_CACHE = None
        _SUBSCRIPTIONS_MTIME = None
    _maybe_migrate_subscriptions_config(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        data = json.loads(json.dumps(_DEFAULT_SUBSCRIPTIONS))
        _write_subscriptions_config(data)
        return data

    if _SUBSCRIPTIONS_CACHE is not None and _SUBSCRIPTIONS_MTIME == mtime:
        return _SUBSCRIPTIONS_CACHE

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = json.loads(json.dumps(_DEFAULT_SUBSCRIPTIONS))

    data = _ensure_subscription_defaults(data)
    _SUBSCRIPTIONS_CACHE = data
    _SUBSCRIPTIONS_MTIME = mtime
    return data


def _write_subscriptions_config(data):
    global _SUBSCRIPTIONS_CACHE, _SUBSCRIPTIONS_MTIME, _SUBSCRIPTIONS_PATH
    path = _get_subscriptions_config_path()
    if _SUBSCRIPTIONS_PATH != path:
        _SUBSCRIPTIONS_PATH = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)
    _SUBSCRIPTIONS_CACHE = data
    try:
        _SUBSCRIPTIONS_MTIME = os.path.getmtime(path)
    except OSError:
        _SUBSCRIPTIONS_MTIME = None


def _prepare_training_split(dataset_cfg, val_ratio=0.1):
    """Build a fresh train/val split under dataset_root/_build/* and return metadata."""
    image_src = _dataset_abs_path(dataset_cfg, "imagesTrainRoot")
    label_src = _dataset_abs_path(dataset_cfg, "labelTrainRoot")
    root_abs = _dataset_abs_path(dataset_cfg, "root")
    if not root_abs:
        raise RuntimeError("Dataset root is not configured")
    if not image_src or not os.path.exists(image_src):
        raise RuntimeError("Dataset has no images to train on yet")
    if not label_src or not os.path.exists(label_src):
        raise RuntimeError("Dataset has no labels to train on yet")

    supported_exts = (".jpg", ".jpeg", ".png", ".bmp", ".JPG", ".JPEG", ".PNG", ".BMP")
    pairs = []
    for label_name in sorted(os.listdir(label_src)):
        if not label_name.lower().endswith(".txt"):
            continue
        base = os.path.splitext(label_name)[0]
        label_path = os.path.join(label_src, label_name)
        image_path = None
        for ext in supported_exts:
            cand = os.path.join(image_src, base + ext)
            if os.path.exists(cand):
                image_path = cand
                break
        if not image_path:
            _trace("[prepare_split] missing image for label", label_name)
            continue
        pairs.append((image_path, label_path))

    if not pairs:
        raise RuntimeError("No paired images/labels found for training")

    # deterministic shuffle -> hold out val_ratio (default 10%)
    rng = random.Random(42)
    indices = list(range(len(pairs)))
    rng.shuffle(indices)
    try:
        val_ratio = float(val_ratio)
    except Exception:
        val_ratio = 0.1
    val_ratio = min(max(val_ratio, 0.0), 0.5)
    val_count = int(round(len(pairs) * val_ratio))
    if val_count <= 0 and len(pairs) > 1:
        val_count = 1
    val_indices = set(indices[:val_count])

    build_root = os.path.join(root_abs, "_build")
    shutil.rmtree(build_root, ignore_errors=True)

    train_img_dir = os.path.join(build_root, "images", "train")
    val_img_dir = os.path.join(build_root, "images", "val")
    train_lbl_dir = os.path.join(build_root, "labels", "train")
    val_lbl_dir = os.path.join(build_root, "labels", "val")
    for path in (train_img_dir, val_img_dir, train_lbl_dir, val_lbl_dir):
        os.makedirs(path, exist_ok=True)

    manifest = {
        "dataset": dataset_cfg.get("slug"),
        "total": len(pairs),
        "train": [],
        "val": [],
        "val_ratio_requested": val_ratio,
    }

    for idx, (img_path, label_path) in enumerate(pairs):
        target_img_dir, target_lbl_dir, bucket = (
            (val_img_dir, val_lbl_dir, manifest["val"])
            if idx in val_indices
            else (train_img_dir, train_lbl_dir, manifest["train"])
        )
        img_name = os.path.basename(img_path)
        lbl_name = os.path.basename(label_path)
        shutil.copy2(img_path, os.path.join(target_img_dir, img_name))
        shutil.copy2(label_path, os.path.join(target_lbl_dir, lbl_name))
        bucket.append({"image": img_name, "label": lbl_name})

    yaml_path = os.path.join(build_root, "data.yaml")
    rel_train = os.path.relpath(train_img_dir, root_abs).replace(os.sep, "/")
    rel_val = os.path.relpath(val_img_dir, root_abs).replace(os.sep, "/")
    names = dataset_cfg.get("labels") or sorted(REQUIRED_LABELS)
    yaml_lines = [
        f"path: {root_abs.replace(os.sep, '/')}",
        f"train: {rel_train}",
        f"val: {rel_val}",
        f"names: {json.dumps(names)}",
    ]
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write("\n".join(yaml_lines) + "\n")

    manifest_path = os.path.join(build_root, "split_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)

    _trace(
        "[prepare_split]",
        dataset_cfg.get("slug"),
        "total=",
        len(pairs),
        "train=",
        len(manifest["train"]),
        "val=",
        len(manifest["val"]),
    )

    return {
        "yaml_path": yaml_path,
        "train_dir": train_img_dir,
        "val_dir": val_img_dir,
        "train_count": len(manifest["train"]),
        "val_count": len(manifest["val"]),
        "build_root": build_root,
        "manifest_path": manifest_path,
    }


def _write_project_manifest(data):
    global _PROJECT_MANIFEST_CACHE, _PROJECT_MANIFEST_MTIME
    os.makedirs(os.path.dirname(PROJECT_MANIFEST_PATH), exist_ok=True)
    tmp_path = PROJECT_MANIFEST_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, PROJECT_MANIFEST_PATH)
    _PROJECT_MANIFEST_CACHE = data
    try:
        _PROJECT_MANIFEST_MTIME = os.path.getmtime(PROJECT_MANIFEST_PATH)
    except OSError:
        _PROJECT_MANIFEST_MTIME = None


@app.get("/api/projects")
def api_list_projects():
    return jsonify(_get_project_manifest())


@app.post("/api/projects")
def api_create_project():
    payload = request.get_json(force=True, silent=True) or {}
    slug = (payload.get("slug") or "").strip().lower()
    if not slug:
        return jsonify({"error": "slug is required"}), 400
    manifest = _get_project_manifest()
    projects = manifest.setdefault("projects", {})
    if slug in projects:
        return jsonify({"error": "project already exists"}), 400

    name = (payload.get("name") or slug.replace("_", " ").title()).strip()
    description = (payload.get("description") or "").strip()

    raw_datasets = payload.get("datasets")
    if not raw_datasets:
        dataset_slug = (payload.get("dataset_slug") or f"{slug}_pose").strip().lower()
        raw_datasets = [
            {
                "slug": dataset_slug,
                "label": payload.get("dataset_label") or f"{name} Dataset",
                "root": payload.get("dataset_root") or f"datasets/{dataset_slug}",
                "frameCacheRoot": payload.get("frameCacheRoot")
                or f"frame_cache/{dataset_slug}",
                "framesRoot": payload.get("framesRoot") or f"frames/{dataset_slug}",
                "labelTrainRoot": payload.get("labelTrainRoot")
                or f"datasets/{dataset_slug}/labels/train",
                "imagesTrainRoot": payload.get("imagesTrainRoot")
                or f"datasets/{dataset_slug}/images/train",
                "labels": payload.get("labels") or [],
            }
        ]

    datasets = []
    for ds in raw_datasets:
        norm = _normalize_dataset(slug, ds)
        datasets.append(norm)
        _ensure_dataset_dirs(norm)

    modules = payload.get("modules") or {}

    project = {
        "name": name,
        "description": description,
        "datasets": datasets,
        "modules": modules,
    }

    projects[slug] = project
    if not manifest.get("defaultProject"):
        manifest["defaultProject"] = slug

    _write_project_manifest(manifest)
    return jsonify({"project": project, "slug": slug})


@app.patch("/api/projects/<slug>")
def api_update_project(slug):
    manifest = _get_project_manifest()
    projects = manifest.setdefault("projects", {})
    project = projects.get(slug)
    if not project:
        return jsonify({"error": "project not found"}), 404

    payload = request.get_json(force=True, silent=True) or {}
    if "name" in payload:
        project["name"] = (payload.get("name") or project.get("name") or slug).strip()
    if "description" in payload:
        project["description"] = (payload.get("description") or "").strip()
    if "modules" in payload and isinstance(payload.get("modules"), dict):
        project["modules"] = payload["modules"]
    if "datasets" in payload and isinstance(payload.get("datasets"), list):
        new_datasets = []
        for ds in payload["datasets"]:
            norm = _normalize_dataset(slug, ds)
            new_datasets.append(norm)
            _ensure_dataset_dirs(norm)
        if new_datasets:
            project["datasets"] = new_datasets

    _write_project_manifest(manifest)
    return jsonify({"project": project, "slug": slug})


@app.post("/api/projects/<slug>/detector")
def api_set_project_detector(slug):
    manifest = _get_project_manifest()
    projects = manifest.get("projects") or {}
    project = projects.get(slug)
    if not project:
        return jsonify({"error": "project not found"}), 404

    payload = request.get_json(force=True, silent=True) or {}
    dataset_slug = (payload.get("dataset") or "").strip()
    detector_path = (payload.get("detector") or "").strip()
    if not dataset_slug:
        return jsonify({"error": "dataset slug required"}), 400

    datasets = project.get("datasets") or []
    target = next((ds for ds in datasets if ds.get("slug") == dataset_slug), None)
    if not target:
        return jsonify({"error": "dataset not found"}), 404

    old_path = target.get("detector")

    if detector_path:
        # normalise path relative to app root when possible
        detector_path = detector_path.replace("\\", "/")
        abs_candidate = (
            detector_path
            if os.path.isabs(detector_path)
            else os.path.abspath(os.path.join(app.root_path, detector_path))
        )
        abs_candidate = os.path.abspath(abs_candidate)
        if abs_candidate.startswith(os.path.abspath(app.root_path)):
            rel = os.path.relpath(abs_candidate, app.root_path).replace("\\", "/")
            target["detector"] = rel
            new_abs = os.path.abspath(os.path.join(app.root_path, rel))
        else:
            target["detector"] = detector_path
            new_abs = abs_candidate
    else:
        target.pop("detector", None)
        new_abs = None

    _write_project_manifest(manifest)

    # invalidate caches so next detection loads fresh weights
    if old_path:
        old_abs = (
            old_path
            if os.path.isabs(old_path)
            else os.path.abspath(os.path.join(app.root_path, old_path))
        )
        _DETECTOR_CACHE.pop(os.path.abspath(old_abs), None)
    if new_abs:
        _DETECTOR_CACHE.pop(os.path.abspath(new_abs), None)

    return jsonify(
        {
            "project": slug,
            "dataset": dataset_slug,
            "detector": target.get("detector"),
        }
    )


# dataset summaries ---------------------------------------------------------
@app.get("/api/datasets/<dataset_slug>/summary")
def api_dataset_summary(dataset_slug):
    dataset = _resolve_dataset(dataset_slug)
    summary = _dataset_summary(dataset)
    return jsonify({"dataset": dataset.get("slug"), **summary})


@app.get("/api/subscriptions")
def api_subscriptions_list():
    return jsonify(_load_subscriptions_config())


def _normalize_plan_payload(payload):
    plan_id = (payload.get("id") or payload.get("plan_id") or "").strip()
    if not plan_id:
        raise ValueError("plan id required")
    name = (payload.get("name") or plan_id.replace("_", " ").title()).strip()
    dataset = (payload.get("dataset") or "").strip()
    if not dataset:
        raise ValueError("dataset slug required for plan")
    description = (payload.get("description") or "").strip()
    try:
        price = float(payload.get("price", 0.0))
    except Exception:
        price = 0.0
    period = (payload.get("period") or "monthly").strip().lower() or "monthly"
    try:
        trial_days = int(payload.get("trial_days", 0))
    except Exception:
        trial_days = 0
    active = bool(payload.get("active", True))
    return {
        "id": plan_id,
        "name": name,
        "dataset": dataset,
        "description": description,
        "price": price,
        "period": period,
        "trial_days": max(0, trial_days),
        "active": active,
    }


@app.post("/api/subscriptions/plan")
def api_subscriptions_upsert_plan():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        plan = _normalize_plan_payload(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    data = _load_subscriptions_config()
    data.setdefault("plans", {})
    data["plans"][plan["id"]] = plan
    _write_subscriptions_config(data)
    return jsonify({"plan": plan})


@app.delete("/api/subscriptions/plan/<plan_id>")
def api_subscriptions_delete_plan(plan_id):
    data = _load_subscriptions_config()
    plans = data.setdefault("plans", {})
    plan = plans.get(plan_id)
    if not plan:
        return jsonify({"error": "plan not found"}), 404
    plan["active"] = False
    _write_subscriptions_config(data)
    return jsonify({"plan": plan, "status": "deactivated"})


def _find_special_by_code(specials, code):
    code_norm = _normalize_special_code(code)
    if not code_norm:
        return None
    for special in (specials or {}).values():
        if not isinstance(special, dict):
            continue
        if _normalize_special_code(special.get("code")) == code_norm:
            return special
    return None


@app.post("/api/subscriptions/special")
def api_subscriptions_upsert_special():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        special = _normalize_special_payload(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data = _load_subscriptions_config()
    data.setdefault("specials", {})
    data["specials"][special["id"]] = special
    _write_subscriptions_config(data)
    return jsonify({"special": special})


@app.delete("/api/subscriptions/special/<special_id>")
def api_subscriptions_delete_special(special_id):
    data = _load_subscriptions_config()
    specials = data.setdefault("specials", {})
    special = specials.get(special_id)
    if not special:
        return jsonify({"error": "special not found"}), 404
    special["active"] = False
    _write_subscriptions_config(data)
    return jsonify({"special": special, "status": "deactivated"})


def _ensure_user_subscription_key(user_id):
    if user_id is None:
        return None
    if isinstance(user_id, int):
        return str(user_id)
    if isinstance(user_id, str) and user_id.strip():
        return user_id.strip()
    return None


@app.post("/api/subscriptions/assign")
def api_subscriptions_assign():
    payload = request.get_json(force=True, silent=True) or {}
    user_key = _ensure_user_subscription_key(
        payload.get("user_id") or payload.get("user")
    )
    if not user_key:
        if ALLOW_STUB_AUTH:
            user_key = str(session.get("user_id") or "demo")
        else:
            return jsonify({"error": "user_id required"}), 400

    plan_id = (payload.get("plan_id") or payload.get("id") or "").strip()
    if not plan_id:
        return jsonify({"error": "plan_id required"}), 400

    data = _load_subscriptions_config()
    plans = data.setdefault("plans", {})
    if plan_id not in plans:
        return jsonify({"error": "plan not found"}), 404
    users = data.setdefault("userSubscriptions", {})
    current = set(users.get(user_key, []))
    current.add(plan_id)
    users[user_key] = sorted(current)
    _write_subscriptions_config(data)
    return jsonify({"user": user_key, "plans": users[user_key]})


@app.post("/api/subscriptions/unassign")
def api_subscriptions_unassign():
    payload = request.get_json(force=True, silent=True) or {}
    user_key = _ensure_user_subscription_key(
        payload.get("user_id") or payload.get("user")
    )
    if not user_key:
        return jsonify({"error": "user_id required"}), 400
    plan_id = (payload.get("plan_id") or payload.get("id") or "").strip()
    if not plan_id:
        return jsonify({"error": "plan_id required"}), 400
    data = _load_subscriptions_config()
    users = data.setdefault("userSubscriptions", {})
    if user_key not in users:
        return jsonify({"user": user_key, "plans": []})
    current = set(users.get(user_key, []))
    if plan_id in current:
        current.remove(plan_id)
        users[user_key] = sorted(current)
        _write_subscriptions_config(data)
    return jsonify({"user": user_key, "plans": users.get(user_key, [])})


@app.post("/api/subscriptions/redeem")
def api_subscriptions_redeem_special():
    payload = request.get_json(force=True, silent=True) or {}
    user_key = _ensure_user_subscription_key(
        payload.get("user_id") or payload.get("user")
    )
    if not user_key:
        if ALLOW_STUB_AUTH:
            user_key = str(session.get("user_id") or "demo")
        else:
            return jsonify({"error": "user_id required"}), 400

    special_id = (payload.get("special_id") or payload.get("offer_id") or payload.get("id") or "").strip()
    code = (payload.get("code") or payload.get("promo") or payload.get("promo_code") or "").strip()
    if not special_id and not code:
        return jsonify({"error": "special id or code required"}), 400

    data = _load_subscriptions_config()
    specials = data.setdefault("specials", {})
    special = specials.get(special_id) if special_id else None
    if not special and code:
        special = _find_special_by_code(specials, code)
    if not special:
        return jsonify({"error": "special not found"}), 404
    if special.get("active") is False:
        return jsonify({"error": "special inactive"}), 400

    user_specials = data.setdefault("userSpecials", {})
    current_specials = set(user_specials.get(user_key, []))
    current_specials.add(special["id"])
    user_specials[user_key] = sorted(current_specials)

    modules = special.get("modules") or []
    assigned_plan_ids = []
    missing_modules = []
    plans = data.get("plans", {})
    if modules:
        plan_matches, missing_modules = _best_plan_for_modules(modules, plans)
        if plan_matches:
            users = data.setdefault("userSubscriptions", {})
            current_plans = set(users.get(user_key, []))
            for plan_id in plan_matches.values():
                current_plans.add(plan_id)
            users[user_key] = sorted(current_plans)
            assigned_plan_ids = sorted(plan_matches.values())

    _write_subscriptions_config(data)
    return jsonify(
        {
            "user": user_key,
            "special": special,
            "special_ids": user_specials[user_key],
            "assigned_plan_ids": assigned_plan_ids,
            "missing_modules": missing_modules,
        }
    )


@app.get("/api/me/profile")
def api_my_profile():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "auth"}), 401
    profile = _load_user_profile(uid)
    return jsonify(
        {
            "ok": True,
            "profile": profile,
            "profile_complete": _profile_is_complete(profile),
        }
    )


@app.post("/api/me/profile")
def api_update_profile():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"ok": False, "error": "auth"}), 401
    payload = request.get_json(force=True, silent=True) or {}
    profile = _normalize_profile_payload(payload)
    try:
        saved = _save_user_profile(uid, profile)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "profile": saved,
            "profile_complete": _profile_is_complete(saved),
        }
    )


@app.get("/api/me/subscriptions")
def api_my_subscriptions():
    uid = session.get("user_id")
    if uid is None and ALLOW_STUB_AUTH:
        uid = session.setdefault("user_id", "demo")
    key = _ensure_user_subscription_key(uid)
    data = _load_subscriptions_config()
    plans = data.get("plans", {})
    user_plans = data.get("userSubscriptions", {}).get(key or "", [])
    specials = data.get("specials", {})
    user_special_ids = data.get("userSpecials", {}).get(key or "", [])
    return jsonify(
        {
            "user_id": key,
            "plan_ids": user_plans,
            "plans": [plans[p] for p in user_plans if p in plans],
            "special_ids": user_special_ids,
            "specials": [specials[s] for s in user_special_ids if s in specials],
        }
    )


# Track active users (lightweight, in-memory). Production should use Redis.
app.active_users = {}
app.observer_frames = {}


@app.before_request
def _track_active_user():
    try:
        uid = session.get("user_id")
        if uid:
            app.active_users[uid] = {
                "user_id": uid,
                "last_seen": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "path": request.path,
                "ip": request.headers.get("X-Forwarded-For") or request.remote_addr,
            }
            # prune very old ( > 2h )
            if len(app.active_users) > 500:
                to_del = []
                for k, v in app.active_users.items():
                    try:
                        dt = datetime.fromisoformat(v["last_seen"].rstrip("Z"))
                        if (datetime.utcnow() - dt).total_seconds() > 7200:
                            to_del.append(k)
                    except Exception:
                        to_del.append(k)
                for k in to_del:
                    app.active_users.pop(k, None)
    except Exception:
        pass


client = None


def get_openai_client():
    global client

    if client is not None:
        return client

    load_dotenv()  # Ensure .env is loaded

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set in environment or .env file.")

    client = OpenAI(api_key=api_key)
    return client


LABEL_TO_CLASS = {"basketball": 0, "hoop": 1, "net": 2, "backboard": 3, "player": 4}

# --- Shared paths used by ONNX export + training monitor ---
BASE_DIR = (
    Path(__file__).resolve().parent
)  # (you already set this later; ok to keep here)

RUNS_DETECT_DIR = os.path.join(app.root_path, "runs", "detect")
STATIC_DIR = os.path.join(app.root_path, "static")
STATIC_MODELS_DIR = os.path.join(STATIC_DIR, "models")
STATIC_CONFIG_DIR = os.path.join(STATIC_DIR, "config")
DETECTOR_CFG_PATH = os.path.join(STATIC_CONFIG_DIR, "detector.json")

os.makedirs(STATIC_MODELS_DIR, exist_ok=True)
os.makedirs(STATIC_CONFIG_DIR, exist_ok=True)

# 🧠 In-memory state
frame_memory = {"ball_path": [], "frame_id": 0}

# load object mapping
LABELS_PATH = os.path.join(app.root_path, "static", "models", "labels.json")
try:
    with open(LABELS_PATH, "r", encoding="utf-8") as _f:
        _LABELS = json.load(_f)
    CANON_NAMES = _LABELS.get("classes", [])
except Exception:
    CANON_NAMES = ["basketball", "hoop", "net", "backboard", "player"]  # fallback


# --- Canonical class names (must be defined BEFORE model_det is created) ---
LABELS_PATH = os.path.join(app.root_path, "static", "models", "labels.json")


def _load_canon_names():
    try:
        with open(LABELS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # support either {"classes":[...]} or {"names":[...]}
        names = data.get("classes") or data.get("names") or []
        if not names:
            raise ValueError("labels.json has no classes/names")
        return names
    except Exception:
        # safe fallback to your basketball profile
        return ["basketball", "hoop", "net", "backboard", "player"]


CANON_NAMES = _load_canon_names()

# 🔄 Load both models
BASE_DIR = Path(__file__).resolve().parent
model_det = None
TRAINING_NAMES = None
predict_lock = threading.Lock()
_DETECTOR_CACHE: dict[str, "YOLO"] = {}
if TORCH_AVAILABLE:
    try:
        model_det = YOLO(BASE_DIR / "weights/best.pt")
        TRAINING_NAMES = getattr(getattr(model_det, "model", None), "names", None)
        print("[detector] training names:", TRAINING_NAMES)
        print("✅ Model loaded")
    except Exception as e:
        print("⚠️ YOLO model load failed:", e)
else:
    print(
        "⚠️ Torch/Ultralytics not available. Server will run with local ONNX (front-end) only."
    )


def _get_dataset_detector(dataset_cfg):
    """Return (YOLO model, class names) for a dataset; fallback to default detector."""
    if not TORCH_AVAILABLE:
        raise RuntimeError("Torch runtime not available for detectors")

    cfg = dataset_cfg or {}
    path = cfg.get("detector")

    # Allow environment overrides per dataset/project (e.g., visaion_DETECTOR_G_POSE=/models/best.pt)
    def _norm_env_key(value):
        return re.sub(r"[^A-Z0-9]+", "_", str(value).upper())

    env_candidates = []
    slug = cfg.get("slug")
    proj = cfg.get("project")
    if slug:
        key = _norm_env_key(slug)
        env_candidates.extend(
            [
                f"visaion_DETECTOR_{key}",
                f"visaion_DATASET_DETECTOR__{key}",
            ]
        )
    if proj:
        key = _norm_env_key(proj)
        env_candidates.extend(
            [
                f"visaion_DETECTOR_{key}",
                f"visaion_PROJECT_DETECTOR__{key}",
            ]
        )

    for env_key in env_candidates:
        override = os.getenv(env_key)
        if override and override.strip():
            path = override.strip()
            print(f"[detector] override from {env_key} -> {path}")
            break

    if not path:
        return model_det, (TRAINING_NAMES or [])

    abs_path = path if os.path.isabs(path) else os.path.join(app.root_path, path)
    if not os.path.exists(abs_path):
        print(f"[detector] missing {abs_path}; falling back to default detector")
        return model_det, (TRAINING_NAMES or [])

    cached = _DETECTOR_CACHE.get(abs_path)
    if cached is None:
        try:
            cached = YOLO(abs_path)
            _DETECTOR_CACHE[abs_path] = cached
            print(f"[detector] loaded custom model: {abs_path}")
        except Exception as exc:
            print(f"⚠️ Failed to load detector {abs_path}: {exc}")
            return model_det, (TRAINING_NAMES or [])

    names = getattr(getattr(cached, "model", None), "names", None) or []
    return cached, names


# ----------- video routes -----------
UPLOAD_DIR = os.path.join(app.root_path, "static", "videos")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


def _is_video(fname):
    return os.path.splitext(fname)[1].lower() in ALLOWED_EXTS


@app.get("/videos")
def list_videos():
    items = []
    for fname in sorted(os.listdir(UPLOAD_DIR)):
        path = os.path.join(UPLOAD_DIR, fname)
        if os.path.isfile(path) and _is_video(fname):
            st = os.stat(path)
            items.append(
                {
                    "name": fname,
                    "url": f"/static/videos/{fname}",  # direct static path
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                }
            )
    return jsonify({"items": items})


# Optional: allow uploads from the UI
@app.post("/videos")
def upload_video():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "missing file"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({"error": "unsupported format"}), 400
    fname = secure_filename(f.filename)
    dest = os.path.join(UPLOAD_DIR, fname)
    f.save(dest)
    return jsonify({"ok": True, "name": fname, "url": f"/static/videos/{fname}"})


# ------------html routes ------------
@app.route("/")
def index():
    return redirect(url_for("my_sessions_page"))


@app.route("/start_session")
def start_session_page():
    return send_from_directory("static", "index.html")


@app.route("/frame_extract")
def frame_extract():
    return send_from_directory("static", "frame_extract.html")


@app.route("/shot_summary")
def shot_summary():
    return send_from_directory("static", "shot_summary.html")


# ---------------------- Session API (demo-friendly) ----------------------
def _resolve_sessions_dir():
    override = (os.getenv("SESSIONS_DIR") or "").strip()
    if override:
        return os.path.abspath(override)
    data_root = (os.getenv("DATA_DIR") or "").strip()
    if data_root:
        return os.path.abspath(os.path.join(data_root, "sessions"))
    output_dir = (os.getenv("OUTPUT_DIRECTORY") or "").strip()
    if output_dir:
        if os.path.isabs(output_dir):
            return os.path.abspath(output_dir)
        return os.path.abspath(os.path.join(app.root_path, output_dir))
    mount_candidates = [
        ("/app/sesions", False),
        ("/app/sessions", False),
        ("/var/data", True),
        ("/data", True),
        ("/mnt/data", True),
    ]
    for base, append_sessions in mount_candidates:
        try:
            if os.path.ismount(base):
                return os.path.abspath(
                    os.path.join(base, "sessions") if append_sessions else base
                )
        except OSError:
            pass
    for candidate in ("/app/sesions", "/app/sessions"):
        try:
            if os.path.isdir(candidate):
                return os.path.abspath(candidate)
        except OSError:
            pass
    for base in ("/var/data", "/data", "/mnt/data"):
        try:
            if os.path.isdir(base):
                return os.path.abspath(os.path.join(base, "sessions"))
        except OSError:
            pass
    return os.path.join(app.root_path, "sessions")


SESSIONS_DIR = _resolve_sessions_dir()
os.makedirs(SESSIONS_DIR, exist_ok=True)
COMMUNITY_DIR = os.path.join(SESSIONS_DIR, "_community")
COMMUNITY_FEED_PATH = os.path.join(COMMUNITY_DIR, "feed.json")
os.makedirs(COMMUNITY_DIR, exist_ok=True)

MICROCLIP_HEADER_PATH = os.path.join(SESSIONS_DIR, "_microclip_header.bin")
MICROCLIP_HEADER_MAX = 2 * 1024 * 1024  # allow up to 2 MiB for header capture
MICROCLIP_CLUSTER_MARKER = b"\x1f\x43\xb6\x75"


def _is_valid_webm_header(data: bytes) -> bool:
    if not data or len(data) < 4:
        return False
    # WebM/Matroska files start with 0x1A45DFA3 (EBML)
    return data[0:4] == b"\x1a\x45\xdf\xa3"


def _trim_to_webm_header(data: bytes) -> bytes:
    """Return only the EBML header section up to (but not including) the first Cluster."""
    if not data:
        return data
    idx = data.find(MICROCLIP_CLUSTER_MARKER, 4)
    if idx != -1:
        return data[:idx]
    return data


def _load_cached_microclip_header() -> bytes | None:
    if os.path.exists(MICROCLIP_HEADER_PATH):
        try:
            with open(MICROCLIP_HEADER_PATH, "rb") as f:
                return f.read()
        except OSError:
            return None
    return None


def _maybe_cache_microclip_header(sample: bytes) -> None:
    if not sample or not _is_valid_webm_header(sample):
        return
    sample = sample[:MICROCLIP_HEADER_MAX]
    if MICROCLIP_CLUSTER_MARKER not in sample:
        return
    header_only = _trim_to_webm_header(sample)
    if not header_only:
        return
    cached = _load_cached_microclip_header()
    if cached and len(header_only) == len(cached) and cached.startswith(header_only):
        return
    try:
        with open(MICROCLIP_HEADER_PATH, "wb") as f:
            f.write(header_only)
    except OSError:
        pass


def _repair_microclip_header(path: Path) -> bool:
    header = _load_cached_microclip_header()
    if not header:
        return False
    header = _trim_to_webm_header(header[:MICROCLIP_HEADER_MAX])
    if not header:
        return False
    try:
        file_size = path.stat().st_size
        if file_size < 4:
            return False
        with open(path, "r+b") as f:
            original = f.read(min(len(header), MICROCLIP_HEADER_MAX))
            if _is_valid_webm_header(original):
                return False
            f.seek(0)
            f.write(header[: min(len(header), file_size)])
        return True
    except OSError:
        return False


def _session_path(sid: str):
    d = os.path.join(SESSIONS_DIR, sid)
    os.makedirs(d, exist_ok=True)
    return d


def _session_json_path(sid: str):
    return os.path.join(_session_path(sid), "session.json")


def _read_session_file(sid: str):
    p = _session_json_path(sid)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _write_session_file(sid: str, data: dict):
    p = _session_json_path(sid)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_session(sid: str):
    db = _db_get()
    if db:
        data = _db_read_session_payload(sid)
        if data is not None:
            return data
        fs_data = _read_session_file(sid)
        if isinstance(fs_data, dict):
            try:
                _db_write_session_payload(sid, fs_data)
            except Exception:
                if DB_REQUIRED:
                    raise
            return fs_data
        return None
    return _read_session_file(sid)


def _write_session(sid: str, data: dict):
    db = _db_get()
    if db:
        ok = _db_write_session_payload(sid, data)
        if ok:
            return
        if DB_REQUIRED:
            raise RuntimeError("Failed to write session payload to database.")
        return
    _write_session_file(sid, data)


def _db_read_session_payload(sid: str):
    db = _db_get()
    if not db:
        return None
    Row = db.get("SessionPayloadRow")
    if not Row:
        return None
    try:
        with db["Session"]() as s:
            row = s.get(Row, sid)
            if not row:
                return None
            data = row.data
            if isinstance(data, dict):
                return data
            if isinstance(data, str):
                try:
                    return json.loads(data)
                except Exception:
                    return None
    except Exception as exc:
        _trace("db_read_session_payload", exc)
        if DB_REQUIRED:
            raise
    return None


def _db_write_session_payload(sid: str, data: dict) -> bool:
    db = _db_get()
    if not db:
        return False
    Row = db.get("SessionPayloadRow")
    if not Row:
        return False
    try:
        with db["Session"]() as s:
            values = {
                "sid": sid,
                "data": data,
                "updated_at": datetime.utcnow(),
            }
            stmt = pg_insert(Row).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["sid"],
                set_={
                    "data": values["data"],
                    "updated_at": values["updated_at"],
                },
            )
            s.execute(stmt)
            s.commit()
        return True
    except Exception as exc:
        _trace("db_write_session_payload", exc)
        if DB_REQUIRED:
            raise
        return False


def _db_build_session_payload(sid: str) -> dict | None:
    db = _db_get()
    if not db:
        return None
    try:
        from sqlalchemy import select

        with db["Session"]() as s:
            SessionRow = db["SessionRow"]
            ShotRow = db["ShotRow"]
            sess_row = s.get(SessionRow, sid)
            if not sess_row:
                return None
            shots = []
            rows = (
                s.execute(
                    select(ShotRow)
                    .where(ShotRow.sid == sid)
                    .order_by(ShotRow.idx.asc())
                )
                .scalars()
                .all()
            )
            for r in rows:
                base = r.data if isinstance(r.data, dict) else {}
                shot = dict(base)
                shot.setdefault("idx", r.idx)
                if r.made is not None:
                    shot.setdefault("made", r.made)
                if r.entry_angle is not None:
                    shot.setdefault("entryAngle", r.entry_angle)
                if r.release_angle is not None:
                    shot.setdefault("releaseAngle", r.release_angle)
                if r.arc_height is not None:
                    shot.setdefault("arcHeight", r.arc_height)
                if r.miss_reason is not None:
                    shot.setdefault("missReason", r.miss_reason)
                if r.pose_score is not None:
                    shot.setdefault("poseScore", r.pose_score)
                shots.append(shot)

            attempts = len(shots)
            makes = sum(1 for shot in shots if shot.get("made") is True)
            accuracy = int(round((makes / attempts) * 100)) if attempts else 0
            if sess_row.shots_count is not None:
                try:
                    attempts = max(attempts, int(sess_row.shots_count))
                except Exception:
                    pass
            if sess_row.makes is not None:
                try:
                    makes = max(makes, int(sess_row.makes))
                except Exception:
                    pass
            if sess_row.accuracy is not None:
                try:
                    accuracy = int(sess_row.accuracy)
                except Exception:
                    pass

            payload = {
                "id": sid,
                "startedAt": _to_epoch_ms(sess_row.created_at),
                "endedAt": _to_epoch_ms(sess_row.ended_at),
                "shots": shots,
                "totals": {"attempts": attempts, "made": makes, "accuracy": accuracy},
            }
            if sess_row.user_id is not None:
                payload["userId"] = sess_row.user_id
            return payload
    except Exception as exc:
        _trace("db_build_session_payload", exc)
        if DB_REQUIRED:
            raise
        return None


def _merge_session_payload(sess: dict, db_payload: dict) -> dict:
    if not isinstance(sess, dict) or not isinstance(db_payload, dict):
        return sess
    base_shots = list(sess.get("shots") or [])
    db_shots = list(db_payload.get("shots") or [])
    if not db_shots:
        return sess
    merged = []
    seen = set()
    for shot in base_shots:
        if not isinstance(shot, dict):
            continue
        try:
            idx = int(shot.get("idx"))
        except Exception:
            idx = None
        if idx is not None:
            seen.add(idx)
        merged.append(shot)
    for shot in db_shots:
        if not isinstance(shot, dict):
            continue
        try:
            idx = int(shot.get("idx"))
        except Exception:
            idx = None
        if idx is not None and idx in seen:
            continue
        merged.append(shot)
        if idx is not None:
            seen.add(idx)
    try:
        merged.sort(key=lambda s: int(s.get("idx")) if isinstance(s, dict) and s.get("idx") is not None else 0)
    except Exception:
        pass
    sess["shots"] = merged
    totals = sess.get("totals")
    db_totals = db_payload.get("totals") if isinstance(db_payload.get("totals"), dict) else None
    if db_totals:
        if not isinstance(totals, dict):
            sess["totals"] = dict(db_totals)
        else:
            for key in ("attempts", "made", "accuracy"):
                if totals.get(key) is None and db_totals.get(key) is not None:
                    totals[key] = db_totals.get(key)
    return sess


def _persist_coach_feedback_fs(sid: str, shot_idx: int, text: str, score_val: float | None) -> bool:
    try:
        sess = _read_session(sid)
    except Exception:
        sess = None
    if not isinstance(sess, dict):
        return False
    shots = list(sess.get("shots") or [])
    updated = False
    for shot in shots:
        if isinstance(shot, dict) and shot.get("idx") == shot_idx:
            shot["coachNote"] = text
            shot["coach_summary"] = text
            if score_val is not None:
                shot["poseScore"] = score_val
            updated = True
            break
    if not updated and 0 <= shot_idx < len(shots):
        shot = shots[shot_idx]
        if isinstance(shot, dict):
            shot["coachNote"] = text
            shot["coach_summary"] = text
            if score_val is not None:
                shot["poseScore"] = score_val
            updated = True
    if not updated:
        return False
    sess["shots"] = shots
    try:
        _write_session(sid, sess)
    except Exception:
        return False
    return True


def _normalize_user_id(value):
    if value is None:
        return None
    return str(value)


def _session_owner_id(sess: dict) -> str | None:
    if not isinstance(sess, dict):
        return None
    owner = sess.get("userId")
    if owner is None:
        owner = sess.get("user_id")
    return _normalize_user_id(owner)


def _summarize_session_file(sid: str, sess: dict) -> dict | None:
    if not isinstance(sess, dict):
        return None
    shots = list(sess.get("shots") or [])
    totals_raw = sess.get("totals")
    totals = totals_raw if isinstance(totals_raw, dict) else {}
    attempts = totals.get("attempts")
    if attempts is None:
        attempts = len(shots)
    try:
        attempts = int(attempts)
    except Exception:
        attempts = len(shots)
    made = totals.get("made")
    if made is None:
        made = sum(
            1 for s in shots if isinstance(s, dict) and s.get("made") is True
        )
    try:
        made = int(made)
    except Exception:
        made = 0
    accuracy = totals.get("accuracy")
    if accuracy is None:
        accuracy = int(round((made / max(1, attempts)) * 100)) if attempts else 0
    try:
        accuracy = int(accuracy)
    except Exception:
        accuracy = 0
    last_ms = None
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        t = shot.get("t") or shot.get("time") or shot.get("ts")
        if isinstance(t, (int, float)):
            last_ms = t if last_ms is None else max(last_ms, t)
    last_iso = None
    if last_ms is not None:
        try:
            last_iso = datetime.fromtimestamp(last_ms / 1000, timezone.utc).isoformat()
        except Exception:
            last_iso = None
    totals_norm = {"attempts": attempts, "made": made, "accuracy": accuracy}
    return {
        "sid": sid,
        "startedAt": sess.get("startedAt"),
        "endedAt": sess.get("endedAt"),
        "totals": totals_norm,
        "shots": attempts,
        "makes": made,
        "accuracy": accuracy,
        "last_shot_at": last_iso,
        "user_id": _session_owner_id(sess),
    }


def _load_fs_session_summaries() -> dict:
    summaries = {}
    try:
        for sid in sorted(os.listdir(SESSIONS_DIR)):
            p = os.path.join(SESSIONS_DIR, sid, "session.json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    sdat = json.load(f)
            except Exception:
                continue
            summary = _summarize_session_file(sid, sdat)
            if summary:
                summaries[sid] = summary
    except Exception:
        pass
    return summaries


def _db_list_session_items(
    current_uid, include_unowned: bool, include_archived: bool
) -> dict:
    db = _db_get()
    if not db:
        return {}
    from sqlalchemy import select, or_

    current_uid_db = current_uid
    if current_uid_db is not None:
        try:
            current_uid_db = int(current_uid_db)
        except Exception:
            pass
    with db["Session"]() as s:
        query = select(db["SessionRow"])
        if not include_archived:
            query = query.where(
                or_(
                    db["SessionRow"].status.is_(None),
                    db["SessionRow"].status.notin_(("archived", "deleted")),
                )
            )
        if current_uid_db is not None:
            if include_unowned:
                query = query.where(
                    or_(
                        db["SessionRow"].user_id == current_uid_db,
                        db["SessionRow"].user_id.is_(None),
                    )
                )
            else:
                query = query.where(db["SessionRow"].user_id == current_uid_db)
        rows = s.execute(query).scalars().all()
        items_by_sid = {}
        for row in rows:
            shots_count = int(row.shots_count or 0)
            makes = int(row.makes or 0)
            accuracy = int(row.accuracy or 0)
            items_by_sid[row.sid] = {
                "id": row.sid,
                "startedAt": _to_epoch_ms(row.created_at),
                "endedAt": _to_epoch_ms(row.ended_at),
                "totals": {
                    "attempts": shots_count,
                    "made": makes,
                    "accuracy": accuracy,
                },
                "shots": shots_count,
            }
        return items_by_sid


def _db_import_session_from_fs(sid: str, sess: dict) -> bool:
    if not isinstance(sess, dict):
        return False
    db = _db_get()
    if not db:
        return False
    try:
        _db_write_session_payload(sid, sess)
    except Exception as exc:
        _trace("db_import_session_payload", exc)
        if DB_REQUIRED:
            raise
        return False

    shots = list(sess.get("shots") or [])
    totals = sess.get("totals") if isinstance(sess.get("totals"), dict) else {}
    attempts = totals.get("attempts")
    if attempts is None:
        attempts = len(shots)
    try:
        attempts = int(attempts)
    except Exception:
        attempts = len(shots)

    def _is_made(value):
        if value is True:
            return True
        if isinstance(value, (int, float)):
            return value >= 1
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "made", "yes", "y")
        return False

    makes = totals.get("made")
    if makes is None:
        makes = sum(1 for shot in shots if _is_made(shot.get("made")))
    try:
        makes = int(makes)
    except Exception:
        makes = 0

    accuracy = totals.get("accuracy")
    if accuracy is None:
        accuracy = int(round((makes / max(1, attempts)) * 100)) if attempts else 0
    else:
        try:
            accuracy = int(accuracy)
        except Exception:
            accuracy = None

    start_ms = sess.get("startedAt") or sess.get("started_at")
    end_ms = sess.get("endedAt") or sess.get("ended_at")
    user_id = sess.get("userId") or sess.get("user_id")
    try:
        user_id = int(user_id) if user_id is not None else None
    except Exception:
        user_id = None

    try:
        with db["Session"]() as s:
            SessionRow = db["SessionRow"]
            row = s.get(SessionRow, sid)
            if not row:
                row = SessionRow(sid=sid)
                s.add(row)
            if isinstance(start_ms, (int, float)):
                start_dt = datetime.utcfromtimestamp(start_ms / 1000.0)
                if row.created_at is None or row.created_at > start_dt:
                    row.created_at = start_dt
            if isinstance(end_ms, (int, float)):
                end_dt = datetime.utcfromtimestamp(end_ms / 1000.0)
                if row.ended_at is None or row.ended_at < end_dt:
                    row.ended_at = end_dt
            if user_id is not None and row.user_id is None:
                row.user_id = user_id
            try:
                current_attempts = int(row.shots_count or 0)
            except Exception:
                current_attempts = 0
            row.shots_count = max(current_attempts, attempts or 0)
            try:
                current_makes = int(row.makes or 0)
            except Exception:
                current_makes = 0
            row.makes = max(current_makes, makes or 0)
            if accuracy is not None:
                row.accuracy = accuracy
            s.commit()
    except Exception as exc:
        _trace("db_import_session_row", exc)
        if DB_REQUIRED:
            raise
        return False

    try:
        _db_backfill_session_shots(sid)
    except Exception as exc:
        _trace("db_import_session_shots", exc)

    return True


def _maybe_backfill_db_sessions_from_fs() -> None:
    if app.config.get("FS_DB_MIGRATED"):
        return
    db = _db_get()
    if not db:
        return
    fs_summaries = _load_fs_session_summaries()
    if not fs_summaries:
        app.config["FS_DB_MIGRATED"] = True
        return
    try:
        from sqlalchemy import select

        with db["Session"]() as s:
            existing = set(
                s.execute(select(db["SessionRow"].sid)).scalars().all()
            )
    except Exception as exc:
        _trace("db session list error", exc)
        if DB_REQUIRED:
            raise
        return
    missing = [sid for sid in fs_summaries.keys() if sid not in existing]
    for sid in missing:
        sess = _read_session_file(sid)
        if isinstance(sess, dict):
            _db_import_session_from_fs(sid, sess)
    app.config["FS_DB_MIGRATED"] = True


def _should_include_owner(owner_id: str | None, current_uid, include_unowned: bool) -> bool:
    if current_uid is None:
        return True
    current_norm = _normalize_user_id(current_uid)
    if owner_id == current_norm:
        return True
    if include_unowned and owner_id in (None, "", "null"):
        return True
    return False


def _to_epoch_ms(dt) -> int | None:
    if not dt:
        return None
    try:
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _community_detail_path(sid: str) -> str:
    safe = secure_filename(sid) or sid
    return os.path.join(COMMUNITY_DIR, f"{safe}.json")


def _load_community_feed_fs() -> list[dict]:
    try:
        with open(COMMUNITY_FEED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            posts = data.get("posts", [])
        else:
            posts = data
    except FileNotFoundError:
        return []
    except Exception as exc:
        _trace("community:feed load error", exc)
        return []
    if not isinstance(posts, list):
        return []
    posts.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return posts


def _load_community_feed() -> list[dict]:
    db = _db_get()
    if db:
        db_posts = _db_load_community_feed()
        if db_posts:
            return db_posts
        posts = _load_community_feed_fs()
        if posts:
            try:
                _db_backfill_community_from_fs(posts)
            except Exception:
                pass
        return posts
    return _load_community_feed_fs()


def _write_community_feed(posts: list[dict]):
    db = _db_get()
    if db:
        _db_upsert_community_summary(posts)
        return
    tmp_path = COMMUNITY_FEED_PATH + ".tmp"
    payload = {"version": 1, "posts": posts}
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, COMMUNITY_FEED_PATH)


def _db_load_community_feed() -> list[dict]:
    db = _db_get()
    if not db:
        return []
    Row = db.get("CommunityPostRow")
    if not Row:
        return []
    try:
        from sqlalchemy import select

        with db["Session"]() as s:
            rows = s.execute(select(Row)).scalars().all()
        posts: list[dict] = []
        for row in rows:
            summary = row.summary
            if isinstance(summary, str):
                try:
                    summary = json.loads(summary)
                except Exception:
                    summary = None
            if isinstance(summary, dict):
                if not summary.get("id"):
                    summary["id"] = row.sid
                if not summary.get("sessionId"):
                    summary["sessionId"] = row.sid
                posts.append(summary)
        posts.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
        return posts
    except Exception as exc:
        _trace("community:db feed load error", exc)
        if DB_REQUIRED:
            raise
        return []


def _db_get_community_detail(sid: str):
    db = _db_get()
    if not db:
        return None
    Row = db.get("CommunityPostRow")
    if not Row:
        return None
    try:
        with db["Session"]() as s:
            row = s.get(Row, sid)
            if not row:
                return None
            detail = row.detail
            if isinstance(detail, dict):
                return detail
            if isinstance(detail, str):
                try:
                    return json.loads(detail)
                except Exception:
                    return None
    except Exception as exc:
        _trace("community:db detail load error", exc)
        if DB_REQUIRED:
            raise
    return None


def _db_upsert_community_post(sid: str, summary: dict, detail: dict | None) -> bool:
    db = _db_get()
    if not db:
        return False
    Row = db.get("CommunityPostRow")
    if not Row:
        return False
    try:
        with db["Session"]() as s:
            values = {
                "sid": sid,
                "summary": summary,
                "detail": detail,
                "updated_at": datetime.utcnow(),
            }
            stmt = pg_insert(Row).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["sid"],
                set_={
                    "summary": values["summary"],
                    "detail": values["detail"],
                    "updated_at": values["updated_at"],
                },
            )
            s.execute(stmt)
            s.commit()
        return True
    except Exception as exc:
        _trace("community:db upsert error", exc)
        if DB_REQUIRED:
            raise
        return False


def _db_upsert_community_summary(posts: list[dict]) -> bool:
    db = _db_get()
    if not db:
        return False
    Row = db.get("CommunityPostRow")
    if not Row:
        return False
    try:
        with db["Session"]() as s:
            for post in posts:
                sid = _community_post_id(post)
                if not sid:
                    continue
                values = {
                    "sid": sid,
                    "summary": post,
                    "updated_at": datetime.utcnow(),
                }
                stmt = pg_insert(Row).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["sid"],
                    set_={
                        "summary": values["summary"],
                        "updated_at": values["updated_at"],
                    },
                )
                s.execute(stmt)
            s.commit()
        return True
    except Exception as exc:
        _trace("community:db feed sync error", exc)
        if DB_REQUIRED:
            raise
        return False


def _db_delete_community_post(sid: str) -> bool:
    db = _db_get()
    if not db:
        return False
    Row = db.get("CommunityPostRow")
    if not Row:
        return False
    try:
        with db["Session"]() as s:
            row = s.get(Row, sid)
            if row:
                s.delete(row)
                s.commit()
        return True
    except Exception as exc:
        _trace("community:db delete error", exc)
        if DB_REQUIRED:
            raise
        return False


def _db_backfill_community_from_fs(posts: list[dict]) -> None:
    if not posts:
        return
    db = _db_get()
    if not db:
        return
    Row = db.get("CommunityPostRow")
    if not Row:
        return
    existing = _db_load_community_feed()
    if existing:
        return
    for post in posts:
        sid = _community_post_id(post)
        if not sid:
            continue
        detail = None
        detail_path = _community_detail_path(sid)
        if os.path.exists(detail_path):
            try:
                with open(detail_path, "r", encoding="utf-8") as f:
                    detail = json.load(f)
            except Exception:
                detail = None
        _db_upsert_community_post(sid, post, detail)


def _community_post_id(post: dict) -> str | None:
    if not isinstance(post, dict):
        return None
    return post.get("sessionId") or post.get("id")


def _find_community_post(posts: list[dict], sid: str):
    for idx, post in enumerate(posts):
        if _community_post_id(post) == sid:
            return idx, post
    return None, None


def _set_community_post_visibility(
    sid: str, hide_flag: bool, actor: str | None = None, reason: str | None = None
):
    posts = _load_community_feed()
    idx, post = _find_community_post(posts, sid)
    if post is None:
        return None
    updated = dict(post)
    if hide_flag:
        updated["hidden"] = True
        updated["hiddenAt"] = int(time.time() * 1000)
        updated["hiddenBy"] = str(actor or "user")
        if reason:
            updated["hiddenReason"] = reason
    else:
        updated.pop("hidden", None)
        updated.pop("hiddenAt", None)
        updated.pop("hiddenBy", None)
        updated.pop("hiddenReason", None)
    posts[idx] = updated
    _write_community_feed(posts)

    detail, detail_path = _load_community_detail_payload(sid)
    db = _db_get()
    if isinstance(detail, dict):
        if hide_flag:
            detail["hidden"] = True
            detail["hiddenAt"] = updated.get("hiddenAt")
            detail["hiddenBy"] = updated.get("hiddenBy")
            if updated.get("hiddenReason"):
                detail["hiddenReason"] = updated.get("hiddenReason")
        else:
            detail.pop("hidden", None)
            detail.pop("hiddenAt", None)
            detail.pop("hiddenBy", None)
            detail.pop("hiddenReason", None)
        if not db and detail_path:
            try:
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(detail, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        if db:
            try:
                _db_upsert_community_post(sid, updated, detail)
            except Exception:
                pass
    elif db:
        try:
            _db_upsert_community_summary([updated])
        except Exception:
            pass

    return updated


def _safe_count(value, default=0) -> int:
    if value is None:
        return default
    try:
        return max(0, int(value))
    except Exception:
        return default


def _normalize_comment_entry(entry: dict | str | None) -> dict | None:
    if not entry:
        return None
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return None
        return {
            "id": f"c{int(time.time() * 1000)}{random.randint(100, 999)}",
            "author": "Community member",
            "text": text[:180],
            "ts": int(time.time() * 1000),
        }
    if not isinstance(entry, dict):
        return None
    text = str(entry.get("text") or entry.get("comment") or entry.get("body") or "").strip()
    if not text:
        return None
    author = str(entry.get("author") or entry.get("user") or entry.get("name") or "Community member").strip()
    if not author:
        author = "Community member"
    ts_raw = entry.get("ts") or entry.get("createdAt") or entry.get("created_at")
    try:
        ts_val = int(ts_raw)
    except Exception:
        ts_val = int(time.time() * 1000)
    cid = entry.get("id") or entry.get("cid")
    if not cid:
        cid = f"c{ts_val}{random.randint(100, 999)}"
    return {
        "id": str(cid)[:32],
        "author": author[:80],
        "text": text[:180],
        "ts": ts_val,
    }


def _sanitize_comment_list(entries) -> list[dict]:
    out = []
    for entry in entries or []:
        normalized = _normalize_comment_entry(entry)
        if normalized:
            out.append(normalized)
    return out[-200:]


def _ensure_community_action_fields(summary: dict, detail: dict | None):
    changed = False
    summary = dict(summary or {})
    detail_copy = dict(detail) if isinstance(detail, dict) else None

    comments = []
    if detail_copy is not None:
        raw_comments = detail_copy.get("comments") if isinstance(detail_copy.get("comments"), list) else []
        comments = _sanitize_comment_list(raw_comments)
        if raw_comments != comments:
            detail_copy["comments"] = comments
            changed = True

    like_count = _safe_count(summary.get("likeCount") or (detail_copy or {}).get("likeCount"), 0)
    share_count = _safe_count(summary.get("shareCount") or (detail_copy or {}).get("shareCount"), 0)
    subscriber_count = _safe_count(summary.get("subscriberCount") or (detail_copy or {}).get("subscriberCount"), 0)
    comment_count = _safe_count(summary.get("commentCount") or (detail_copy or {}).get("commentCount"), 0)
    if comments:
        comment_count = max(comment_count, len(comments))

    if summary.get("likeCount") != like_count:
        summary["likeCount"] = like_count
        changed = True
    if summary.get("shareCount") != share_count:
        summary["shareCount"] = share_count
        changed = True
    if summary.get("subscriberCount") != subscriber_count:
        summary["subscriberCount"] = subscriber_count
        changed = True
    if summary.get("commentCount") != comment_count:
        summary["commentCount"] = comment_count
        changed = True

    if detail_copy is not None:
        if detail_copy.get("likeCount") != like_count:
            detail_copy["likeCount"] = like_count
            changed = True
        if detail_copy.get("shareCount") != share_count:
            detail_copy["shareCount"] = share_count
            changed = True
        if detail_copy.get("subscriberCount") != subscriber_count:
            detail_copy["subscriberCount"] = subscriber_count
            changed = True
        if detail_copy.get("commentCount") != comment_count:
            detail_copy["commentCount"] = comment_count
            changed = True
        if "comments" not in detail_copy:
            detail_copy["comments"] = comments
            changed = True

    return summary, detail_copy, changed


def _community_actions_payload(summary: dict, detail: dict | None) -> dict:
    safe_summary = summary or {}
    comments = []
    if isinstance(detail, dict) and isinstance(detail.get("comments"), list):
        comments = detail.get("comments")
    return {
        "likeCount": _safe_count(safe_summary.get("likeCount"), 0),
        "commentCount": _safe_count(safe_summary.get("commentCount"), len(comments)),
        "shareCount": _safe_count(safe_summary.get("shareCount"), 0),
        "subscriberCount": _safe_count(safe_summary.get("subscriberCount"), 0),
        "comments": comments,
    }


def _summary_from_detail(detail: dict, sid: str) -> dict:
    summary = {"id": sid, "sessionId": sid}
    for key in (
        "title",
        "summary",
        "author",
        "tags",
        "createdAt",
        "project",
        "projectName",
        "dataset",
        "attemptLabel",
        "attemptsLabel",
        "readyPrompt",
        "stats",
        "highlights",
        "preview",
        "previewRev",
        "likeCount",
        "commentCount",
        "shareCount",
        "subscriberCount",
        "hidden",
        "hiddenAt",
        "hiddenBy",
        "hiddenReason",
    ):
        if key in detail:
            summary[key] = detail.get(key)
    return summary


def _load_community_detail_payload(sid: str):
    detail_path = _community_detail_path(sid)
    db = _db_get()
    if db:
        detail = _db_get_community_detail(sid)
        if isinstance(detail, dict):
            return detail, detail_path
        if os.path.exists(detail_path):
            try:
                with open(detail_path, "r", encoding="utf-8") as f:
                    detail = json.load(f)
            except Exception:
                return None, detail_path
            if isinstance(detail, dict):
                try:
                    summary = None
                    for post in _load_community_feed_fs():
                        if _community_post_id(post) == sid:
                            summary = post
                            break
                    if summary is None:
                        summary = _summary_from_detail(detail, sid)
                    _db_upsert_community_post(sid, summary, detail)
                except Exception:
                    pass
                return detail, detail_path
        return None, detail_path
    if os.path.exists(detail_path):
        try:
            with open(detail_path, "r", encoding="utf-8") as f:
                return json.load(f), detail_path
        except Exception:
            return None, detail_path
    return None, detail_path


def _init_community_detail_from_summary(sid: str, summary: dict) -> dict:
    detail = {
        "id": sid,
        "title": summary.get("title"),
        "summary": summary.get("summary"),
        "author": summary.get("author"),
        "tags": summary.get("tags"),
        "createdAt": summary.get("createdAt"),
        "project": summary.get("project"),
        "projectName": summary.get("projectName"),
        "dataset": summary.get("dataset"),
        "attemptLabel": summary.get("attemptLabel"),
        "attemptsLabel": summary.get("attemptsLabel"),
        "readyPrompt": summary.get("readyPrompt"),
        "stats": summary.get("stats"),
        "shots": summary.get("shots") or [],
        "comments": [],
    }
    for key in ("preview", "previewRev", "hidden", "hiddenAt", "hiddenBy", "hiddenReason"):
        if key in summary:
            detail[key] = summary.get(key)
    return detail


def _extract_preview_with_ffmpeg(src: Path, dest: Path) -> bool:
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(dest),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:  # pragma: no cover - best effort
        _trace(
            "[community:preview ffmpeg failed]", {"clip": str(src), "error": str(exc)}
        )
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        return False


def _ensure_preview_image(sid: str) -> str | None:
    base_dir = os.path.join(SESSIONS_DIR, sid)
    if not os.path.isdir(base_dir):
        return None
    preview_path = os.path.join(base_dir, "preview.jpg")
    if os.path.exists(preview_path):
        return preview_path
    clips_dir = Path(base_dir) / "clips"
    if not clips_dir.exists():
        return None
    first_clip = None
    for candidate in sorted(clips_dir.glob("shot-*")):
        if candidate.is_file():
            first_clip = candidate
            break
    if not first_clip:
        return None
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception:
        pass

    clip_path = Path(first_clip)
    preview_file = Path(preview_path)

    if _extract_preview_with_ffmpeg(clip_path, preview_file):
        return preview_path
    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(str(clip_path))
        success, frame = cap.read()
        cap.release()
        if not success or frame is None:
            return None
        cv2.imwrite(preview_path, frame)
        return preview_path
    except Exception as exc:
        _trace("community:preview error", exc)
        return None


# ---------------------- ArcMM Auto-processing ----------------------
ARCMM_AUTO_PROCESS = os.getenv("ARCMM_AUTO_PROCESS", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
ARCMM_RUNNER_CMD = os.getenv("ARCMM_RUNNER_CMD", "").strip()
ARCMM_MAX_ATTEMPTS = int(os.getenv("ARCMM_MAX_ATTEMPTS", "6"))
ARCMM_RETRY_DELAY = float(os.getenv("ARCMM_RETRY_DELAY", "1.5"))
ARCMM_RUNNER_TIMEOUT = float(os.getenv("ARCMM_RUNNER_TIMEOUT", "120"))
ARCMM_WORKER_COUNT = max(1, int(os.getenv("ARCMM_WORKERS", "1")))
ARCMM_PROCESSED_DIRNAME = "processed"

_ARCMM_QUEUE: "Queue[dict]" = Queue()
_ARCMM_JOB_KEYS: set[str] = set()
_ARCMM_LOCK = threading.Lock()
_ARCMM_WORKER_THREADS: list[threading.Thread] = []
_ARCMM_SESSION_LOCKS: dict[str, threading.Lock] = {}
_ARCMM_SESSION_LOCKS_LOCK = threading.Lock()


def _arcmm_processed_dir(sid: str) -> Path:
    return Path(_session_path(sid)) / ARCMM_PROCESSED_DIRNAME


def _arcmm_clip_candidates(sid: str, idx: int) -> Path | None:
    clips_dir = Path(_session_path(sid)) / "clips"
    if not clips_dir.exists():
        return None
    names = [
        f"shot-{idx}.mp4",
        f"shot_{idx}.mp4",
        f"shot-{idx}.webm",
        f"shot_{idx}.webm",
    ]
    for name in names:
        p = clips_dir / name
        if p.exists():
            return p
    for pattern in (f"shot-{idx}.*", f"shot_{idx}.*"):
        for p in clips_dir.glob(pattern):
            if p.is_file():
                return p
    return None


def _arcmm_acquire_session_lock(sid: str) -> threading.Lock:
    with _ARCMM_SESSION_LOCKS_LOCK:
        lock = _ARCMM_SESSION_LOCKS.get(sid)
        if lock is None:
            lock = threading.Lock()
            _ARCMM_SESSION_LOCKS[sid] = lock
    lock.acquire()
    return lock


def _arcmm_apply_make_fallback(summary: dict | None) -> dict | None:
    if not summary:
        return summary
    try:
        if summary.get("made") is True:
            return summary
        result = (summary.get("result") or "").lower()
        if result != "miss":
            return summary
        miss_reason = (summary.get("missReason") or "").lower()
        candidate_reasons = {"rim out"}
        trail = summary.get("trail") or []
        if not trail:
            return summary
        y_values: list[float] = []
        for point in trail:
            try:
                y = float(point.get("y"))
            except (TypeError, ValueError):
                continue
            y_values.append(y)
        if not y_values:
            return summary
        span_y = max(y_values) - min(y_values)
        max_y = max(y_values)
        # Heuristic: significant downward travel through the hoop cylinder implies a make.
        if miss_reason in candidate_reasons:
            release_angle = summary.get("releaseAngle") or 0.0
            arc_height = summary.get("arcHeight") or 0.0
            hoop = summary.get("hoop") or {}
            try:
                hoop_x = float(hoop.get("x"))
                hoop_y = float(hoop.get("y"))
            except (TypeError, ValueError):
                hoop_x = hoop_y = None
            tail = trail[-5:] if len(trail) >= 5 else trail
            within_cylinder = True
            passes_through = True
            if hoop_x is not None and hoop_y is not None:
                within_cylinder = all(
                    abs(float(pt.get("x", hoop_x)) - hoop_x) <= 110
                    and abs(float(pt.get("y", hoop_y)) - hoop_y) <= 110
                    for pt in tail
                )
                passes_through = any(
                    float(pt.get("y", hoop_y)) >= hoop_y - 15 for pt in tail
                )
            else:
                passes_through = max_y >= 420

            override = False
            if span_y >= 350 and passes_through and within_cylinder:
                override = True
            elif (
                span_y >= 260
                and passes_through
                and within_cylinder
                and release_angle >= 65
            ):
                override = True
            elif (
                span_y >= 220
                and passes_through
                and within_cylinder
                and arc_height >= 120
            ):
                override = True

            if override:
                summary = dict(summary)
                summary["made"] = True
                summary["result"] = "HIT"
                summary["fallbackOverride"] = "make_from_trail"
                summary["fallbackOriginalReason"] = miss_reason
                summary["missReason"] = None
        return summary
    except Exception as exc:
        _trace("arcmm fallback error:", exc)
        return summary


def _arcmm_update_shot_status(
    sid: str,
    idx: int,
    *,
    status: str,
    message: str | None = None,
    summary: dict | None = None,
    clip_url: str | None = None,
) -> None:
    db = getattr(app, "db", None)
    if not db or "ShotRow" not in db:
        return
    from sqlalchemy import select  # local import to keep module import light

    with db["Session"]() as s:
        ShotRow = db["ShotRow"]
        row = s.execute(
            select(ShotRow).where(ShotRow.sid == sid, ShotRow.idx == idx)
        ).scalar_one_or_none()
        if not row:
            return

        payload = dict(row.data or {})
        arcmm = dict(payload.get("arcmm") or {})
        arcmm["status"] = status
        arcmm["updated_at"] = datetime.utcnow().isoformat(timespec="seconds")
        if clip_url:
            arcmm["processed_clip"] = clip_url
        if summary is not None:
            summary = _arcmm_apply_make_fallback(summary)
            if summary.get("fallbackOverride"):
                message = (
                    f"{message} (fallback make override)"
                    if message
                    else "fallback make override"
                )
            arcmm["summary"] = summary
            try:
                if "entryAngle" in summary and summary["entryAngle"] is not None:
                    row.entry_angle = float(summary["entryAngle"])
                if "releaseAngle" in summary and summary["releaseAngle"] is not None:
                    row.release_angle = float(summary["releaseAngle"])
                if "arcHeight" in summary and summary["arcHeight"] is not None:
                    row.arc_height = float(summary["arcHeight"])
                if "made" in summary and summary["made"] is not None:
                    row.made = bool(summary["made"])
                if "missReason" in summary:
                    row.miss_reason = summary.get("missReason")
            except Exception as exc:
                _trace("arcmm: summary->column sync failed", exc)

        if message is not None:
            arcmm["message"] = message

        payload["arcmm"] = arcmm
        row.data = payload
        try:
            s.commit()
        except Exception:
            s.rollback()
            raise

    try:
        if summary is not None:
            sess = _read_session(sid)
            if sess and isinstance(sess.get("shots"), list):
                updated = False
                for shot in sess["shots"]:
                    try:
                        if int(shot.get("idx")) == int(idx):
                            shot.setdefault("arcmm", {})
                            shot["arcmm"].update(arcmm)
                            # mirror key metrics
                            if summary.get("made") is not None:
                                shot["made"] = bool(summary["made"])
                            if "entryAngle" in summary:
                                shot["entryAngle"] = summary["entryAngle"]
                            if "releaseAngle" in summary:
                                shot["releaseAngle"] = summary["releaseAngle"]
                            if "arcHeight" in summary:
                                shot["arcHeight"] = summary["arcHeight"]
                            updated = True
                            break
                    except Exception:
                        continue
                if updated:
                    _write_session(sid, sess)
    except Exception as exc:
        _trace("arcmm: session sync failed", exc)


def _arcmm_requeue(job: dict, delay: float) -> None:
    time.sleep(delay)
    job["attempt"] = int(job.get("attempt", 0)) + 1
    key = f"{job['sid']}:{job['idx']}"
    with _ARCMM_LOCK:
        _ARCMM_JOB_KEYS.add(key)
    _ARCMM_QUEUE.put(job)


def _run_arcmm_runner(sid: str, idx: int, clip_path: Path) -> dict:
    if not ARCMM_RUNNER_CMD:
        raise RuntimeError("ARCMM_RUNNER_CMD is not configured")
    processed_dir = _arcmm_processed_dir(sid)
    processed_dir.mkdir(parents=True, exist_ok=True)
    cmd = shlex.split(ARCMM_RUNNER_CMD)
    cmd += [str(clip_path), str(processed_dir), str(idx)]
    _trace("[arcmm] runner", cmd)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=ARCMM_RUNNER_TIMEOUT
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise RuntimeError(stderr or stdout or f"runner exit {result.returncode}")
    summary_path = processed_dir / f"shot-{idx}.summary.json"
    summary = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    clip_url = None
    for ext in (".results.webm", ".results.mp4"):
        processed_file = processed_dir / f"shot-{idx}{ext}"
        if processed_file.exists():
            clip_url = (
                f"/sessions/{sid}/{ARCMM_PROCESSED_DIRNAME}/{processed_file.name}"
            )
            break
    stdout_lines = (result.stdout or "").splitlines()
    message = None
    if stdout_lines:
        flat = [ln.strip() for ln in stdout_lines if ln.strip()]
        non_json = [ln for ln in flat if not (ln.startswith("{") and ln.endswith("}"))]
        if non_json:
            message = non_json[-1]
    if not message:
        message = "ArcMM runner completed"
    if len(message) > 240:
        message = message[:237] + "…"
    return {"summary": summary, "clip_url": clip_url, "message": message}


def _arcmm_worker_loop(worker_id: int) -> None:
    while True:
        job = _ARCMM_QUEUE.get()
        sid = job["sid"]
        idx = int(job["idx"])
        attempt = int(job.get("attempt", 0))
        key = f"{sid}:{idx}"
        session_lock = _arcmm_acquire_session_lock(sid)
        requeued = False
        try:
            clip = _arcmm_clip_candidates(sid, idx)
            if clip is None:
                if attempt < ARCMM_MAX_ATTEMPTS:
                    _arcmm_update_shot_status(
                        sid,
                        idx,
                        status="waiting_clip",
                        message="clip not found; retrying",
                    )
                    _arcmm_requeue(job, ARCMM_RETRY_DELAY)
                    requeued = True
                else:
                    _arcmm_update_shot_status(
                        sid, idx, status="missing_clip", message="clip not found"
                    )
                continue
            try:
                _arcmm_update_shot_status(sid, idx, status="processing")
                runner_out = _run_arcmm_runner(sid, idx, clip)
                summary = runner_out.get("summary")
                clip_url = runner_out.get("clip_url")
                if summary is None:
                    _arcmm_update_shot_status(
                        sid,
                        idx,
                        status="error",
                        message="runner did not produce summary",
                    )
                else:
                    _arcmm_update_shot_status(
                        sid,
                        idx,
                        status="complete",
                        summary=summary,
                        clip_url=clip_url,
                        message=runner_out.get("message"),
                    )
            except Exception as exc:
                _arcmm_update_shot_status(sid, idx, status="error", message=str(exc))
        except Exception as exc:
            _trace("arcmm worker error", exc)
            try:
                _arcmm_update_shot_status(sid, idx, status="error", message=str(exc))
            except Exception:
                pass
        finally:
            session_lock.release()
            if not requeued:
                with _ARCMM_LOCK:
                    _ARCMM_JOB_KEYS.discard(key)
            _ARCMM_QUEUE.task_done()


def _ensure_arcmm_worker_started() -> None:
    global _ARCMM_WORKER_THREADS
    if not ARCMM_AUTO_PROCESS:
        return
    alive_threads: list[threading.Thread] = []
    for t in _ARCMM_WORKER_THREADS:
        if t.is_alive():
            alive_threads.append(t)
    _ARCMM_WORKER_THREADS = alive_threads
    if len(_ARCMM_WORKER_THREADS) >= ARCMM_WORKER_COUNT:
        return
    # Start additional workers to reach desired count
    for i in range(len(_ARCMM_WORKER_THREADS), ARCMM_WORKER_COUNT):
        worker_name = f"arcmm-worker-{i + 1}"
        t = threading.Thread(
            target=_arcmm_worker_loop, args=(i + 1,), name=worker_name, daemon=True
        )
        _ARCMM_WORKER_THREADS.append(t)
        t.start()


def _arcmm_queue_shot(sid: str, idx: int) -> None:
    if not ARCMM_AUTO_PROCESS:
        return
    if not ARCMM_RUNNER_CMD:
        _arcmm_update_shot_status(
            sid, int(idx), status="skipped", message="ARCMM_RUNNER_CMD not configured"
        )
        return
    key = f"{sid}:{int(idx)}"
    with _ARCMM_LOCK:
        if key in _ARCMM_JOB_KEYS:
            return
        _ARCMM_JOB_KEYS.add(key)
    _ensure_arcmm_worker_started()
    job = {"sid": sid, "idx": int(idx), "attempt": 0, "enqueued_at": time.time()}
    _ARCMM_QUEUE.put(job)
    _arcmm_update_shot_status(
        sid, int(idx), status="queued", message="awaiting processing"
    )


def _new_session_id():
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")


@app.post("/api/sessions/start")
def api_session_start():
    b = request.get_json(silent=True) or {}
    sid = b.get("id") or _new_session_id()
    now = int(time.time() * 1000)
    event_slug = (b.get("event") or request.args.get("event") or "").strip()
    user_id = session.get("user_id")
    challenge_mode = bool(event_slug)
    if challenge_mode and not user_id:
        return jsonify({"ok": False, "err": "auth"}), 401
    sess = {
        "id": sid,
        "startedAt": now,
        "endedAt": None,
        "device": b.get("device") or request.headers.get("User-Agent", "unknown"),
        "video": b.get("video") or None,
        "shots": [],
        "totals": {"attempts": 0, "made": 0, "accuracy": 0},
    }
    if user_id:
        sess["userId"] = user_id
    extra_fields = {
        "project": (b.get("project") or "").strip() or None,
        "projectName": (b.get("projectName") or "").strip() or None,
        "dataset": (b.get("dataset") or "").strip() or None,
    }
    attempt_label = b.get("attemptLabel")
    if isinstance(attempt_label, str):
        attempt_label = attempt_label.strip() or None
        extra_fields["attemptLabel"] = attempt_label
    attempts_label = b.get("attemptsLabel")
    if isinstance(attempts_label, str):
        attempts_label = attempts_label.strip() or None
        extra_fields["attemptsLabel"] = attempts_label
    ready_prompt = b.get("readyPrompt")
    if isinstance(ready_prompt, str):
        ready_prompt = ready_prompt.strip() or None
        extra_fields["readyPrompt"] = ready_prompt
    tags_val = b.get("tags")
    if isinstance(tags_val, list):
        safe_tags = []
        for tag in tags_val:
            if not isinstance(tag, str):
                continue
            t = tag.strip()
            if t:
                safe_tags.append(t[:48])
        if safe_tags:
            extra_fields["tags"] = safe_tags
    for key, val in extra_fields.items():
        if val:
            sess[key] = val
    if challenge_mode:
        sess["event"] = event_slug
        sess["challenge"] = True
    event_payload = None
    try:
        _db_add_session(sid, now)
    except Exception as e:
        _trace("db add session error:", e)
    if challenge_mode:
        try:
            res = _db_event_start_challenge_session(sid, user_id, event_slug, now)
        except Exception as e:
            _db_delete_session_row(sid)
            return jsonify(
                {"ok": False, "err": "challenge start failed", "detail": str(e)}
            ), 500
        if not res.get("ok"):
            _db_delete_session_row(sid)
            err = res.get("err") or "challenge start failed"
            return jsonify(
                {
                    "ok": False,
                    "err": err,
                    **{k: v for k, v in res.items() if k not in {"ok", "err"}},
                }
            ), 400
        event_payload = res
    _write_session(sid, sess)
    payload = {"ok": True, "id": sid, "startedAt": now}
    if event_payload:
        payload["event"] = event_payload
    _trace(
        "api:sessions/start",
        {"sid": sid, "device": sess.get("device"), "challenge": bool(event_payload)},
    )
    return jsonify(payload)


@app.post("/api/sessions/<sid>/shot")
def api_session_add_shot(sid):
    data = request.get_json(force=True) or {}
    sess = _read_session(sid)
    user_id = session.get("user_id")
    if not sess:
        try:
            sess = {
                "id": sid,
                "startedAt": int(time.time() * 1000),
                "endedAt": None,
                "device": request.headers.get("User-Agent", "unknown"),
                "video": None,
                "shots": [],
                "totals": {"attempts": 0, "made": 0, "accuracy": 0},
            }
            if user_id:
                sess["userId"] = user_id
            _write_session(sid, sess)
        except Exception as exc:
            _trace("api:sid/shot:create-missing-session", exc)
            return jsonify({"error": "session not ready"}), 404
    elif user_id and not sess.get("userId"):
        sess["userId"] = user_id
        try:
            _write_session(sid, sess)
        except Exception:
            pass
    shots = list(sess.get("shots", []))
    client_idx = data.get("idx") if isinstance(data.get("idx"), int) else None
    replace = bool(data.get("replace"))
    # Determine next server index from both file and DB to avoid duplicate 0s
    next_idx = len(shots)
    try:
        db = _db_get()
        if db:
            from sqlalchemy import select, func

            with db["Session"]() as s:
                ShotRow = db["ShotRow"]
                mx = s.execute(
                    select(func.max(ShotRow.idx)).where(ShotRow.sid == sid)
                ).scalar_one_or_none()
                if mx is not None:
                    next_idx = max(next_idx, int(mx) + 1)
    except Exception as e:
        _trace("api:sid/shot: max idx probe failed:", e)
    _trace(
        "api:sid/shot",
        {
            "sid": sid,
            "idx": client_idx,
            "replace": replace,
            "serverNext": next_idx,
            "keys": list(data.keys()),
        },
    )
    # Append by default; only replace when explicitly requested
    found_idx = None
    if replace and client_idx is not None:
        for i, srow in enumerate(shots):
            if isinstance(srow, dict) and srow.get("idx") == client_idx:
                found_idx = i
                break
    if found_idx is not None:
        data["idx"] = client_idx
        shots[found_idx] = data
    else:
        data["idx"] = next_idx
        shots.append(data)
    sess["shots"] = shots
    # update totals
    attempts = len(shots)
    made = sum(1 for s in shots if s.get("made") is True)
    acc = int(round((made / attempts) * 100)) if attempts else 0
    sess["totals"] = {"attempts": attempts, "made": made, "accuracy": acc}
    _write_session(sid, sess)
    try:
        _db_add_shot(sid, data["idx"], data)
    except Exception as e:
        _trace("db add shot error:", e)
    return jsonify({"ok": True, "idx": data["idx"], "totals": sess["totals"]})


@app.post("/api/microclip/upload")
def api_microclip_upload():
    clip = request.files.get("clip")
    if not clip:
        return jsonify(ok=False, error="no file"), 400
    session_id = request.form.get("sessionId") or "sess_unknown"
    shot_id = request.form.get("shotId") or "0"
    ts_raw = request.form.get("ts") or ""
    safe_sid = secure_filename(session_id) or "sess_unknown"
    safe_shot = "".join(ch for ch in str(shot_id) if ch.isdigit()) or "0"
    safe_ts = "".join(ch for ch in str(ts_raw) if ch.isdigit())
    if not safe_ts:
        safe_ts = str(int(time.time() * 1000))
    clips_dir = Path(_session_path(safe_sid)) / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    filename = f"shot-{safe_shot}-{safe_ts}.webm"
    dest_path = clips_dir / filename
    clip.save(dest_path)
    try:
        with open(dest_path, "rb") as f:
            sample = f.read(MICROCLIP_HEADER_MAX)
        head_bytes = [b for b in sample[:4]]
        size_bytes = dest_path.stat().st_size
        print(
            f"[microclip] saved {dest_path} size={size_bytes} head={head_bytes}",
            flush=True,
        )
    except Exception as exc:
        print(f"[microclip] head inspect failed for {dest_path}: {exc}", flush=True)
        sample = b""
    if _is_valid_webm_header(sample):
        try:
            _maybe_cache_microclip_header(sample)
        except Exception as exc:
            print(f"[microclip] cache header failed: {exc}", flush=True)
    else:
        if _repair_microclip_header(dest_path):
            try:
                with open(dest_path, "rb") as f:
                    repaired_head = [b for b in f.read(4)]
                print(
                    f"[microclip] repaired header for {dest_path}; new head={repaired_head}",
                    flush=True,
                )
            except Exception:
                print(f"[microclip] repaired header for {dest_path}", flush=True)
        else:
            print(
                f"[microclip] repair skipped (no cached header) for {dest_path}",
                flush=True,
            )
    stream_filename = filename
    remuxed = None
    try:
        remuxed = _remux_to_mp4(dest_path)
        if remuxed:
            stream_filename = remuxed.name
    except Exception as exc:
        _trace("[microclip] remux error", exc)
    rel_path = f"sessions/{safe_sid}/clips/{stream_filename}"
    payload = {"ok": True, "path": rel_path}
    if remuxed:
        payload["mp4"] = rel_path
        payload["source"] = f"sessions/{safe_sid}/clips/{filename}"
    # enqueue background job here (fbf worker reads this path)
    return jsonify(payload)


@app.post("/api/microclip/result")
def api_microclip_result():
    payload = request.get_json(silent=True) or {}
    # persist as you like (jsonl, db, etc.)
    # then notify the frontend through polling, SSE, or websockets as needed
    return jsonify(ok=True)


@app.post("/api/sessions/<sid>/shot_video")
def api_session_shot_video(sid):
    if "file" not in request.files:
        return jsonify({"error": "file field required"}), 400
    idx = request.args.get("index", "0")
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1] or ".webm"
    d = _session_path(sid)
    name = f"shot_{idx}{ext}"
    dst = os.path.join(d, name)
    f.save(dst)
    stream_name = name
    try:
        converted = _remux_to_mp4(Path(dst))
        if converted:
            stream_name = converted.name
    except Exception as exc:
        _trace("[shot_video] remux error", exc)
    url = f"/sessions/{sid}/{stream_name}"
    payload = {"ok": True, "url": url, "name": stream_name}
    if stream_name != name:
        payload["source"] = f"/sessions/{sid}/{name}"
    return jsonify(payload)


@app.post("/api/sessions/<sid>/end")
def api_session_end(sid):
    sess = _read_session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    user_id = session.get("user_id")
    if user_id and not sess.get("userId"):
        sess["userId"] = user_id
    sess["endedAt"] = int(time.time() * 1000)
    # Recompute totals
    shots = sess.get("shots", [])
    attempts = len(shots)
    made = sum(1 for s in shots if s.get("made") is True)
    acc = int(round((made / attempts) * 100)) if attempts else 0
    sess["totals"] = {"attempts": attempts, "made": made, "accuracy": acc}
    _write_session(sid, sess)
    try:
        _db_finalize_session(sid)
    except Exception as e:
        _trace("db finalize error:", e)
    # Backfill DB shots from session.json to guarantee coverage
    try:
        _db_backfill_session_shots(sid)
    except Exception as e:
        _trace("db backfill error:", e)
    _trace("api:sessions/end", {"sid": sid, "totals": sess["totals"]})
    return jsonify({"ok": True, "id": sid, "totals": sess["totals"]})


@app.get("/api/sessions")
def api_sessions_list():
    current_uid = session.get("user_id")
    include_unowned = _truthy(request.args.get("include_unowned"), default=False)
    include_archived = _truthy(request.args.get("include_archived"), default=False)
    db = _db_get()
    items_by_sid = {}
    if db:
        _maybe_backfill_db_sessions_from_fs()
        items_by_sid = _db_list_session_items(
            current_uid, include_unowned, include_archived
        )
    else:
        fs_summaries = _load_fs_session_summaries()
        for sid, summary in fs_summaries.items():
            owner_id = summary.get("user_id")
            if not _should_include_owner(owner_id, current_uid, include_unowned):
                continue
            items_by_sid[sid] = {
                "id": sid,
                "startedAt": summary.get("startedAt"),
                "endedAt": summary.get("endedAt"),
                "totals": summary.get("totals") or {},
                "shots": summary.get("shots") or 0,
            }
    items = list(items_by_sid.values())
    items.sort(key=lambda x: x.get("startedAt") or 0, reverse=True)
    return jsonify({"sessions": items})


@app.get("/api/sessions/<sid>")
def api_session_get(sid):
    sess = _read_session(sid)
    if sess:
        db_payload = _db_build_session_payload(sid)
        if db_payload:
            sess = _merge_session_payload(sess, db_payload)
    else:
        sess = _db_build_session_payload(sid)
    if not sess:
        if _truthy(request.args.get("allow_empty"), default=False):
            return (
                jsonify(
                    {
                        "id": sid,
                        "startedAt": None,
                        "endedAt": None,
                        "shots": [],
                        "totals": {"attempts": 0, "made": 0, "accuracy": 0},
                        "missing": True,
                    }
                ),
                200,
            )
        return jsonify({"error": "session not found"}), 404
    if isinstance(sess, dict) and "communityHidden" not in sess:
        try:
            posts = _load_community_feed()
            _, post = _find_community_post(posts, sid)
            if post is not None:
                sess["communityHidden"] = bool(post.get("hidden"))
        except Exception:
            pass
    try:
        shots = sess.get("shots") if isinstance(sess, dict) else None
        if isinstance(shots, list) and _ensure_shot_clip_urls(sid, shots):
            sess["shots"] = shots
            try:
                _write_session(sid, sess)
            except Exception:
                pass
    except Exception:
        pass
    return jsonify(sess)


def _session_user_can_edit(sid: str, user_id) -> bool:
    if not user_id:
        return False
    db = _db_get()
    if db:
        try:
            with db["Session"]() as s:
                row = s.get(db["SessionRow"], sid)
                if not row:
                    return False
                owner = row.user_id
                if owner is None:
                    return True
                return str(owner) == str(user_id)
        except Exception as exc:
            _trace("session_edit: db lookup failed", exc)
            if DB_REQUIRED:
                raise
            return False
    sess = _read_session(sid)
    owner = _session_owner_id(sess) if isinstance(sess, dict) else None
    if owner in (None, "", "null"):
        return True
    return str(owner) == str(user_id)


@app.post("/api/sessions/<sid>/archive")
def api_session_archive(sid):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "auth required"}), 401
    if not _session_user_can_edit(sid, user_id):
        return jsonify({"error": "forbidden"}), 403
    db = _db_get()
    if db:
        try:
            with db["Session"]() as s:
                row = s.get(db["SessionRow"], sid)
                if not row:
                    return jsonify({"error": "session not found"}), 404
                row.status = "archived"
                s.commit()
        except Exception as exc:
            _trace("session_archive db", exc)
            return jsonify({"error": "archive failed"}), 500
    sess = _read_session(sid)
    if isinstance(sess, dict):
        sess["archived"] = True
        try:
            _write_session(sid, sess)
        except Exception:
            pass
    return jsonify({"ok": True, "archived": True})


@app.delete("/api/sessions/<sid>")
@app.post("/api/sessions/<sid>/delete")
def api_session_delete(sid):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "auth required"}), 401
    if not _session_user_can_edit(sid, user_id):
        return jsonify({"error": "forbidden"}), 403
    db = _db_get()
    if db:
        try:
            with db["Session"]() as s:
                ShotRow = db.get("ShotRow")
                PoseRow = db.get("PoseSnapshotRow")
                FBRow = db.get("CoachFeedbackRow")
                PayloadRow = db.get("SessionPayloadRow")
                CommunityRow = db.get("CommunityPostRow")
                if ShotRow:
                    s.query(ShotRow).filter(ShotRow.sid == sid).delete()
                if PoseRow:
                    s.query(PoseRow).filter(PoseRow.sid == sid).delete()
                if FBRow:
                    s.query(FBRow).filter(FBRow.sid == sid).delete()
                if PayloadRow:
                    s.query(PayloadRow).filter(PayloadRow.sid == sid).delete()
                if CommunityRow:
                    s.query(CommunityRow).filter(CommunityRow.sid == sid).delete()
                row = s.get(db["SessionRow"], sid)
                if row:
                    s.delete(row)
                s.commit()
        except Exception as exc:
            _trace("session_delete db", exc)
            return jsonify({"error": "delete failed"}), 500
    # Best-effort filesystem cleanup for dev or legacy data.
    try:
        session_dir = os.path.join(SESSIONS_DIR, sid)
        if os.path.isdir(session_dir):
            shutil.rmtree(session_dir, ignore_errors=True)
    except Exception:
        pass
    return jsonify({"ok": True, "deleted": True})


@app.post("/api/sessions/<sid>/privacy")
def api_session_privacy(sid):
    user_id = session.get("user_id")
    if not _session_user_can_edit(sid, user_id):
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    if "hidden" in payload:
        hide_flag = bool(payload.get("hidden"))
    elif "share" in payload:
        hide_flag = not bool(payload.get("share"))
    elif "private" in payload:
        hide_flag = bool(payload.get("private"))
    else:
        return jsonify({"error": "missing privacy flag"}), 400

    sess = _read_session(sid)
    if not isinstance(sess, dict):
        sess = _db_build_session_payload(sid) or {"id": sid}
    sess["communityHidden"] = bool(hide_flag)
    try:
        _write_session(sid, sess)
    except Exception as exc:
        _trace("session privacy write failed", exc)
        if DB_REQUIRED:
            raise

    updated = _set_community_post_visibility(sid, hide_flag, actor=user_id)
    return jsonify({"ok": True, "hidden": bool(hide_flag), "post": updated})


def _append_trial_email_request(payload: dict) -> bool:
    try:
        path = os.path.join(SESSIONS_DIR, "trial_email_requests.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        _trace("trial_email_request_fs", exc)
        return False


def _clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    val = str(value).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        val = val[1:-1].strip()
    return val or None


def _get_smtp_config() -> dict:
    host = _clean_env_value(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER"))
    port_raw = _clean_env_value(os.getenv("SMTP_PORT")) or "587"
    try:
        port = int(port_raw)
    except Exception:
        port = 587
    username = _clean_env_value(os.getenv("SMTP_USERNAME"))
    password = _clean_env_value(os.getenv("SMTP_PASSWORD"))
    sender = _clean_env_value(os.getenv("SMTP_SENDER")) or username
    use_tls = _truthy(os.getenv("SMTP_USE_TLS"), default=True)
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "sender": sender,
        "use_tls": use_tls,
    }


def _build_summary_email(payload: dict) -> tuple[str, str, str | None]:
    project = payload.get("projectName") or payload.get("project") or "Visaion"
    project = str(project).strip()
    sid = payload.get("sid") or "unknown"
    summary = (payload.get("summary") or "").strip()
    lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    subject = f"{project} session summary"
    safe_project = html.escape(project)
    safe_sid = html.escape(str(sid))
    safe_summary = html.escape(summary) if summary else ""
    text_lines = [
        "Thanks for training with Visaion.",
        "",
        f"Session ID: {sid}",
        f"Project: {project}",
    ]
    if summary:
        text_lines.extend(["", "Summary:", summary])
    if lines:
        text_lines.append("")
        text_lines.append("Highlights:")
        for line in lines[:5]:
            if not isinstance(line, str):
                continue
            text_lines.append(f"- {line.strip()}")
    text_lines.append("")
    text_lines.append("Log in to view more sessions at https://www.visaion.app/static/login.html")
    text_body = "\n".join(text_lines)

    html_lines = [
        "<p>Thanks for training with Visaion.</p>",
        f"<p><strong>Session ID:</strong> {safe_sid}<br><strong>Project:</strong> {safe_project}</p>",
    ]
    if summary:
        html_lines.append(f"<p><strong>Summary:</strong> {safe_summary}</p>")
    if lines:
        items = "".join(
            f"<li>{html.escape(line.strip())}</li>"
            for line in lines[:5]
            if isinstance(line, str) and line.strip()
        )
        if items:
            html_lines.append(f"<p><strong>Highlights:</strong></p><ul>{items}</ul>")
    html_lines.append(
        '<p>Log in to view more sessions at <a href="https://www.visaion.app/static/login.html">visaion.app</a></p>'
    )
    html_body = "\n".join(html_lines)
    return subject, text_body, html_body


def _send_summary_email(to_addr: str, payload: dict) -> tuple[bool, str | None]:
    cfg = _get_smtp_config()
    if not cfg.get("host") or not cfg.get("sender"):
        return False, "smtp_not_configured"
    if not cfg.get("username") or not cfg.get("password"):
        return False, "smtp_credentials_missing"
    subject, text_body, html_body = _build_summary_email(payload)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = to_addr
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.ehlo()
            if cfg.get("use_tls"):
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return True, None
    except Exception as exc:
        _trace("smtp_send_error", exc)
        return False, "smtp_send_failed"


def _build_password_reset_link(token: str, email: str | None = None) -> str:
    params = {"reset": "1", "token": token}
    if email:
        params["email"] = email
    query = urlencode(params)
    base = _clean_env_value(
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("SITE_URL")
        or os.getenv("APP_BASE_URL")
        or os.getenv("BASE_URL")
    )
    if base:
        if not base.endswith("/"):
            base = f"{base}/"
        return f"{base}static/login.html?{query}"
    try:
        base = url_for("static", filename="login.html", _external=True)
        return f"{base}?{query}"
    except Exception:
        return f"/static/login.html?{query}"


def _build_password_reset_email(payload: dict) -> tuple[str, str, str | None]:
    reset_url = payload.get("reset_url") or ""
    name = (payload.get("name") or "").strip() or "there"
    ttl_minutes = payload.get("ttl_minutes") or PASSWORD_RESET_TOKEN_TTL_MINUTES
    subject = "Reset your Visaion password"
    safe_name = html.escape(name)
    safe_url = html.escape(reset_url)
    text_lines = [
        f"Hi {name},",
        "",
        "We received a request to reset your Visaion password.",
        f"This link expires in {ttl_minutes} minutes:",
        reset_url,
        "",
        "If you did not request this, you can ignore this email.",
    ]
    text_body = "\n".join([line for line in text_lines if line is not None])
    html_body = "\n".join(
        [
            f"<p>Hi {safe_name},</p>",
            "<p>We received a request to reset your Visaion password.</p>",
            f"<p>This link expires in {ttl_minutes} minutes:</p>",
            f'<p><a href="{safe_url}">{safe_url}</a></p>',
            "<p>If you did not request this, you can ignore this email.</p>",
        ]
    )
    return subject, text_body, html_body


def _send_password_reset_email(to_addr: str, payload: dict) -> tuple[bool, str | None]:
    cfg = _get_smtp_config()
    if not cfg.get("host") or not cfg.get("sender"):
        return False, "smtp_not_configured"
    if not cfg.get("username") or not cfg.get("password"):
        return False, "smtp_credentials_missing"
    subject, text_body, html_body = _build_password_reset_email(payload)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["sender"]
    msg["To"] = to_addr
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.ehlo()
            if cfg.get("use_tls"):
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        return True, None
    except Exception as exc:
        _trace("smtp_reset_send_error", exc)
        return False, "smtp_send_failed"


@app.post("/api/sessions/<sid>/email_summary")
def api_session_email_summary(sid):
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "valid email required"}), 400
    payload = {
        "sid": sid,
        "email": email,
        "summary": (data.get("summary") or "").strip(),
        "lines": data.get("lines") if isinstance(data.get("lines"), list) else [],
        "project": data.get("project") or None,
        "projectName": data.get("projectName") or None,
        "cap": data.get("cap"),
        "trial": bool(data.get("trial")),
        "created_at": datetime.utcnow().isoformat(),
    }
    db_saved = False
    db = _db_get()
    if db and "TrialEmailRequestRow" in db:
        try:
            with db["Session"]() as s:
                row = db["TrialEmailRequestRow"](
                    sid=sid, email=email, data=payload, created_at=datetime.utcnow()
                )
                s.add(row)
                s.commit()
                db_saved = True
        except Exception as exc:
            _trace("trial_email_request_db", exc)
            db_saved = False
    fs_saved = False
    if not db_saved:
        fs_saved = _append_trial_email_request(payload)
    sent, send_error = _send_summary_email(email, payload)
    response = {
        "ok": sent,
        "sent": sent,
        "stored": {"db": db_saved, "fs": fs_saved},
    }
    if send_error:
        response["error"] = send_error
    status = 200 if sent else 502
    return jsonify(response), status


def _range_aware_send(path=None, mimetype=None, *, sid=None, filename=None):
    """Return a response that honours Range headers for large media files.

    Can be called directly with a resolved `path`, or with `sid`/`filename`
    so it remains compatible with older route registrations.
    """
    if sid is not None and filename is not None:
        base_path = Path(_session_path(sid)).resolve()
        target_path = (base_path / filename).resolve()
        if not str(target_path).startswith(str(base_path)) or not target_path.exists():
            abort(404)
        path = str(target_path)
        if mimetype is None:
            mimetype, _ = mimetypes.guess_type(path)

    if path is None:
        abort(404)

    if mimetype is None:
        mimetype, _ = mimetypes.guess_type(path)

    # Non-media types fall back to standard send_file handling
    if not mimetype or not (
        mimetype.startswith("video/") or mimetype.startswith("audio/")
    ):
        resp = send_file(path, mimetype=mimetype, conditional=True)
        resp.headers.setdefault("Accept-Ranges", "bytes")
        return resp

    size = os.path.getsize(path)
    range_header = request.headers.get("Range")
    partial = False
    if not range_header:
        byte1 = 0
        byte2 = min(size - 1, PARTIAL_CHUNK_BYTES - 1)
        partial = byte2 < size - 1
    else:
        range_match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if not range_match:
            resp = send_file(path, mimetype=mimetype, conditional=True)
            resp.headers.setdefault("Accept-Ranges", "bytes")
            return resp

        start_str, end_str = range_match.groups()
        try:
            byte1 = int(start_str) if start_str else 0
        except ValueError:
            byte1 = 0
        try:
            byte2 = int(end_str) if end_str else size - 1
        except ValueError:
            byte2 = size - 1
        partial = True

    byte1 = max(0, min(byte1, size - 1))
    byte2 = max(byte1, min(byte2, size - 1))
    length = byte2 - byte1 + 1
    if length <= 0:
        return Response(status=416)

    def generate():
        with open(path, "rb") as fh:
            fh.seek(byte1)
            remaining = length
            chunk_size = 8192
            while remaining > 0:
                chunk = fh.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    status_code = 206 if partial or range_header else 200
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {byte1}-{byte2}/{size}"

    if request.method == "HEAD":
        resp = Response(status=status_code, headers=headers, mimetype=mimetype)
        resp.direct_passthrough = True
        return resp

    resp = Response(
        generate(),
        status=status_code,
        headers=headers,
        mimetype=mimetype,
        direct_passthrough=True,
    )
    resp.headers.setdefault("Accept-Ranges", "bytes")
    return resp


@app.route("/sessions/<sid>/<path:filename>")
def serve_session_file(sid, filename):
    return _range_aware_send(sid=sid, filename=filename)


def _match_shot_clip_filename(name: str, idx_display: int):
    match = re.match(r"^shot[-_](\d+)(?:[-_](\d+))?\.(mp4|webm)$", name)
    if not match:
        return None
    try:
        idx = int(match.group(1))
    except Exception:
        return None
    if idx != idx_display:
        return None
    ts_raw = match.group(2)
    try:
        ts = int(ts_raw) if ts_raw else 0
    except Exception:
        ts = 0
    ext = match.group(3)
    return ext, ts


def _preferred_clip_rel(sid: str, idx_display: int) -> str:
    """Return the best clip URL for a shot, preferring MP4 when available."""
    clips_dir = Path(_session_path(sid)) / "clips"
    best = None
    best_score = None
    try:
        entries = list(clips_dir.iterdir()) if clips_dir.exists() else []
    except Exception:
        entries = []
    for clip_path in entries:
        if not clip_path.is_file():
            continue
        info = _match_shot_clip_filename(clip_path.name, idx_display)
        if not info:
            continue
        ext, ts = info
        try:
            mtime = clip_path.stat().st_mtime
        except Exception:
            mtime = 0
        score = (1 if ext == "mp4" else 0, ts, mtime)
        if best_score is None or score > best_score:
            best_score = score
            best = clip_path.name
    if best:
        return f"/sessions/{sid}/clips/{best}"
    return ""


def _ensure_shot_clip_urls(sid: str, shots: list[dict]) -> bool:
    updated = False
    if not isinstance(shots, list):
        return updated
    for i, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            continue
        idx_val = shot.get("idx")
        try:
            idx_display = int(idx_val) if idx_val is not None else i
            if idx_display <= 0:
                idx_display = i
        except Exception:
            idx_display = i
        current = shot.get("clip")
        if isinstance(current, dict):
            current_path = (
                current.get("path") or current.get("url") or current.get("href")
            )
        else:
            current_path = current
        is_remote = isinstance(current_path, str) and current_path.startswith(("http://", "https://"))
        local_exists = False
        if isinstance(current_path, str) and current_path.startswith(f"/sessions/{sid}/"):
            rel = current_path[len(f"/sessions/{sid}/") :]
            try:
                local_exists = (Path(_session_path(sid)) / rel).exists()
            except Exception:
                local_exists = False

        if is_remote or local_exists:
            continue

        preferred = _preferred_clip_rel(sid, idx_display)
        has_preferred = isinstance(preferred, str) and preferred.strip() != ""
        if has_preferred:
            if isinstance(current, dict):
                if current.get("path") != preferred:
                    current = dict(current)
                    current["path"] = preferred
                    shot["clip"] = current
                    updated = True
            else:
                shot["clip"] = {"path": preferred}
                updated = True
        else:
            if isinstance(current, dict):
                if current_path:
                    current = dict(current)
                    current["path"] = None
                    current["url"] = None
                    current["href"] = None
                    shot["clip"] = current
                    updated = True
            elif current_path:
                shot["clip"] = None
                updated = True
    return updated


def _sanitize_tags(tags):
    out = []
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        t = tag.strip()
        if t:
            out.append(t[:48])
    return out


@app.post("/api/community/publish")
def api_community_publish():
    data = request.get_json(force=True) or {}
    sid = (data.get("sessionId") or "").strip()
    if not sid:
        return jsonify({"error": "sessionId required"}), 400
    sess = _read_session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    now_ms = int(time.time() * 1000)
    summary = (data.get("summary") or "").strip()
    title = (data.get("title") or "").strip()
    if not title:
        project_name = sess.get("projectName") or sess.get("project")
        title = f"{project_name or 'Session'} recap"
    author = (data.get("author") or "").strip() or "Player"
    highlights = (
        data.get("highlights") if isinstance(data.get("highlights"), list) else []
    )
    tags = _sanitize_tags(data.get("tags") or sess.get("tags"))
    attempt_label = data.get("attemptLabel")
    if not isinstance(attempt_label, str):
        attempt_label = sess.get("attemptLabel")
    attempt_label = attempt_label.strip() if isinstance(attempt_label, str) else None
    attempts_label = data.get("attemptsLabel")
    if not isinstance(attempts_label, str):
        attempts_label = sess.get("attemptsLabel")
    attempts_label = attempts_label.strip() if isinstance(attempts_label, str) else None
    ready_prompt = data.get("readyPrompt")
    if not isinstance(ready_prompt, str):
        ready_prompt = sess.get("readyPrompt")
    ready_prompt = ready_prompt.strip() if isinstance(ready_prompt, str) else None

    # shots detail
    incoming_shots = data.get("shots") if isinstance(data.get("shots"), list) else []
    incoming_map = {}
    for shot in incoming_shots:
        try:
            idx_val = int(shot.get("idx"))
        except Exception:
            continue
        incoming_map[idx_val] = shot

    session_shots = sess.get("shots") or []
    detail_shots = []
    pose_scores = []
    for shot in session_shots:
        try:
            idx_server = int(shot.get("idx"))
        except Exception:
            continue
        idx_display = idx_server + 1
        entry = incoming_map.get(idx_display) or incoming_map.get(idx_server) or {}
        clip_entry = entry.get("clip") or shot.get("clip")
        clip_payload = None
        clip_path = None
        if isinstance(clip_entry, dict):
            clip_payload = dict(clip_entry)
            clip_path = clip_entry.get("path") or clip_entry.get("url") or clip_entry.get("href")
        elif isinstance(clip_entry, str):
            clip_path = clip_entry
        if not isinstance(clip_path, str) or not clip_path.strip():
            clip_path = _preferred_clip_rel(sid, idx_display)
        if isinstance(clip_payload, dict):
            if clip_path:
                clip_payload["path"] = clip_path
            else:
                clip_payload = None
        else:
            clip_payload = clip_path if isinstance(clip_path, str) and clip_path.strip() else None
        coach_note = entry.get("coachNote") or shot.get("coachNote")
        if isinstance(coach_note, str):
            coach_note = coach_note.strip()
        else:
            coach_note = None
        pose_score = entry.get("poseScore")
        if pose_score is None:
            pose_score = shot.get("poseScore")
        if isinstance(pose_score, (int, float)):
            pose_scores.append(pose_score)
        detail_shots.append(
            {
                "idx": idx_display,
                "clip": clip_payload,
                "poseScore": pose_score,
                "weightedScore": entry.get("weightedScore", shot.get("weightedScore")),
                "coachNote": coach_note,
            }
        )

    totals = sess.get("totals") or {}
    attempts = int(totals.get("attempts") or len(detail_shots) or 0)
    accuracy = totals.get("accuracy")
    pose_avg = None
    if pose_scores:
        pose_avg = round(sum(pose_scores) / max(1, len(pose_scores)))

    preview_path = _ensure_preview_image(sid)
    preview_rev = None
    if preview_path and os.path.exists(preview_path):
        try:
            preview_rev = int(os.path.getmtime(preview_path))
        except Exception:
            preview_rev = int(time.time())

    posts = _load_community_feed()
    post_map = {}
    for item in posts:
        key = _community_post_id(item)
        if key:
            post_map[key] = item
    existing = post_map.get(sid)
    existing_detail, _ = _load_community_detail_payload(sid)
    created_at = existing.get("createdAt") if isinstance(existing, dict) else None
    if not created_at:
        created_at = now_ms

    summary_entry = {
        "id": sid,
        "sessionId": sid,
        "title": title,
        "summary": summary,
        "author": author,
        "tags": tags,
        "createdAt": created_at,
        "project": sess.get("project"),
        "projectName": sess.get("projectName"),
        "dataset": sess.get("dataset"),
        "attemptLabel": attempt_label,
        "attemptsLabel": attempts_label,
        "readyPrompt": ready_prompt,
        "stats": {
            "attempts": attempts,
            "accuracy": accuracy,
            "poseAverage": pose_avg,
        },
        "highlights": highlights,
        "preview": f"/api/community/preview/{sid}.jpg" if preview_path else None,
        "previewRev": preview_rev,
        "likeCount": _safe_count(
            existing.get("likeCount")
            if isinstance(existing, dict) and existing.get("likeCount") is not None
            else (existing_detail or {}).get("likeCount"),
            0,
        ),
        "commentCount": _safe_count(
            existing.get("commentCount")
            if isinstance(existing, dict) and existing.get("commentCount") is not None
            else (existing_detail or {}).get("commentCount"),
            0,
        ),
        "shareCount": _safe_count(
            existing.get("shareCount")
            if isinstance(existing, dict) and existing.get("shareCount") is not None
            else (existing_detail or {}).get("shareCount"),
            0,
        ),
        "subscriberCount": _safe_count(
            existing.get("subscriberCount")
            if isinstance(existing, dict) and existing.get("subscriberCount") is not None
            else (existing_detail or {}).get("subscriberCount"),
            0,
        ),
    }

    if isinstance(existing, dict):
        for key in ("hidden", "hiddenAt", "hiddenBy", "hiddenReason"):
            if key in existing:
                summary_entry[key] = existing.get(key)
    if sess.get("communityHidden") is True and not summary_entry.get("hidden"):
        summary_entry["hidden"] = True
        summary_entry["hiddenAt"] = now_ms
        summary_entry["hiddenBy"] = "user"

    detail_payload = {
        "id": sid,
        "title": title,
        "summary": summary,
        "author": author,
        "tags": tags,
        "createdAt": created_at,
        "highlights": highlights,
        "project": sess.get("project"),
        "projectName": sess.get("projectName"),
        "dataset": sess.get("dataset"),
        "attemptLabel": attempt_label,
        "attemptsLabel": attempts_label,
        "readyPrompt": ready_prompt,
        "stats": summary_entry["stats"],
        "shots": detail_shots,
        "likeCount": summary_entry.get("likeCount", 0),
        "commentCount": summary_entry.get("commentCount", 0),
        "shareCount": summary_entry.get("shareCount", 0),
        "subscriberCount": summary_entry.get("subscriberCount", 0),
    }
    existing_comments = []
    if isinstance(existing_detail, dict) and isinstance(existing_detail.get("comments"), list):
        existing_comments = _sanitize_comment_list(existing_detail.get("comments"))
    detail_payload["comments"] = existing_comments
    if existing_comments:
        detail_payload["commentCount"] = max(detail_payload.get("commentCount", 0), len(existing_comments))
        summary_entry["commentCount"] = detail_payload["commentCount"]
    if summary_entry["preview"]:
        detail_payload["preview"] = summary_entry["preview"]
        detail_payload["previewRev"] = preview_rev
    if summary_entry.get("hidden"):
        detail_payload["hidden"] = True
        if summary_entry.get("hiddenAt") is not None:
            detail_payload["hiddenAt"] = summary_entry.get("hiddenAt")
        if summary_entry.get("hiddenBy"):
            detail_payload["hiddenBy"] = summary_entry.get("hiddenBy")
        if summary_entry.get("hiddenReason"):
            detail_payload["hiddenReason"] = summary_entry.get("hiddenReason")

    post_map[sid] = summary_entry
    updated_posts = sorted(
        post_map.values(), key=lambda x: x.get("createdAt") or 0, reverse=True
    )
    _write_community_feed(updated_posts)

    db = _db_get()
    if not db:
        with open(_community_detail_path(sid), "w", encoding="utf-8") as f:
            json.dump(detail_payload, f, ensure_ascii=False, indent=2)
    if db:
        try:
            _db_upsert_community_post(sid, summary_entry, detail_payload)
        except Exception:
            pass

    return jsonify({"ok": True, "post": summary_entry})


@app.get("/api/community/feed")
def api_community_feed():
    posts = _load_community_feed()
    changed = False
    for idx, post in enumerate(posts):
        sid = _community_post_id(post)
        if not sid:
            continue
        shots = post.get("shots")
        if _ensure_shot_clip_urls(sid, shots):
            changed = True
        updated_summary, _, action_changed = _ensure_community_action_fields(post, None)
        if action_changed:
            posts[idx] = updated_summary
            changed = True
    if changed:
        try:
            _write_community_feed(posts)
        except Exception as exc:
            _trace("community:feed update failed", exc)
    visible_posts = [post for post in posts if not post.get("hidden")]
    return jsonify({"posts": visible_posts})


@app.get("/api/community/session/<sid>")
def api_community_session_detail(sid):
    posts = _load_community_feed()
    _, post = _find_community_post(posts, sid)
    if not post or post.get("hidden"):
        return jsonify({"error": "session not published"}), 404
    detail_path = _community_detail_path(sid)
    detail = None
    detail_from_db = False
    db = _db_get()
    if db:
        detail = _db_get_community_detail(sid)
        detail_from_db = isinstance(detail, dict)
        if not detail_from_db and os.path.exists(detail_path):
            try:
                with open(detail_path, "r", encoding="utf-8") as f:
                    detail = json.load(f)
            except Exception:
                detail = None
            if isinstance(detail, dict):
                try:
                    _db_upsert_community_post(sid, post, detail)
                except Exception:
                    pass
    else:
        if os.path.exists(detail_path):
            with open(detail_path, "r", encoding="utf-8") as f:
                detail = json.load(f)
    if not isinstance(detail, dict):
        return jsonify({"error": "session not published"}), 404
    summary_updated, detail_updated, action_changed = _ensure_community_action_fields(post, detail)
    detail = detail_updated or detail
    if action_changed:
        try:
            idx, _ = _find_community_post(posts, sid)
            if idx is not None:
                posts[idx] = summary_updated
            _write_community_feed(posts)
        except Exception as exc:
            _trace("community:action update failed", exc)
        if not db:
            try:
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(detail, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                _trace("community:detail update failed", exc)
        if db:
            try:
                _db_upsert_community_post(sid, summary_updated, detail)
            except Exception:
                pass
    if _ensure_shot_clip_urls(sid, detail.get("shots") or []):
        if not db:
            try:
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(detail, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                _trace("community:detail update failed", exc)
        if db:
            try:
                _db_upsert_community_post(
                    sid, summary_updated if action_changed else post, detail
                )
            except Exception:
                pass
    elif (detail_from_db or action_changed) and db:
        try:
            _db_upsert_community_post(
                sid, summary_updated if action_changed else post, detail
            )
        except Exception:
            pass
    return jsonify(detail)


@app.get("/api/community/preview/<sid>.jpg")
def api_community_preview(sid):
    posts = _load_community_feed()
    _, post = _find_community_post(posts, sid)
    if not post or post.get("hidden"):
        abort(404)
    preview_path = _ensure_preview_image(sid)
    if not preview_path or not os.path.exists(preview_path):
        abort(404)
    return send_file(preview_path, mimetype="image/jpeg")


@app.get("/api/community/actions/<sid>")
def api_community_actions(sid):
    posts = _load_community_feed()
    idx, post = _find_community_post(posts, sid)
    if not post or post.get("hidden"):
        return jsonify({"error": "session not published"}), 404
    db = _db_get()
    detail, detail_path = _load_community_detail_payload(sid)
    if not isinstance(detail, dict):
        detail = _init_community_detail_from_summary(sid, post)
    summary_updated, detail_updated, changed = _ensure_community_action_fields(post, detail)
    detail = detail_updated or detail
    if changed:
        try:
            if idx is not None:
                posts[idx] = summary_updated
            _write_community_feed(posts)
        except Exception as exc:
            _trace("community:actions feed update failed", exc)
        if not db:
            try:
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(detail, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                _trace("community:actions detail update failed", exc)
        if db:
            try:
                _db_upsert_community_post(sid, summary_updated, detail)
            except Exception:
                pass
    payload = _community_actions_payload(summary_updated, detail)
    return jsonify({"ok": True, "actions": payload})


@app.post("/api/community/actions/<sid>")
def api_community_actions_update(sid):
    data = request.get_json(force=True) or {}
    action = str(data.get("action") or data.get("type") or "").strip().lower()
    if not action:
        return jsonify({"error": "action required"}), 400
    posts = _load_community_feed()
    idx, post = _find_community_post(posts, sid)
    if not post or post.get("hidden"):
        return jsonify({"error": "session not published"}), 404
    db = _db_get()
    detail, detail_path = _load_community_detail_payload(sid)
    if not isinstance(detail, dict):
        detail = _init_community_detail_from_summary(sid, post)

    summary_updated, detail_updated, _ = _ensure_community_action_fields(post, detail)
    detail = detail_updated or detail

    delta = data.get("delta")
    try:
        delta_val = int(delta) if delta is not None else 0
    except Exception:
        delta_val = 0

    if action in ("unlike", "decrement", "remove_like"):
        action = "like"
        delta_val = -1
    if action in ("unsubscribe", "alerts_off", "remove_subscribe"):
        action = "subscribe"
        delta_val = -1

    now_ms = int(time.time() * 1000)

    if action in ("like", "favorite"):
        if delta_val == 0:
            delta_val = 1
        summary_updated["likeCount"] = _safe_count(summary_updated.get("likeCount"), 0) + delta_val
        summary_updated["likeCount"] = max(0, summary_updated["likeCount"])
        detail["likeCount"] = summary_updated["likeCount"]
    elif action in ("share", "repost"):
        summary_updated["shareCount"] = _safe_count(summary_updated.get("shareCount"), 0) + 1
        detail["shareCount"] = summary_updated["shareCount"]
    elif action in ("subscribe", "alerts"):
        if delta_val == 0:
            delta_val = 1
        summary_updated["subscriberCount"] = _safe_count(summary_updated.get("subscriberCount"), 0) + delta_val
        summary_updated["subscriberCount"] = max(0, summary_updated["subscriberCount"])
        detail["subscriberCount"] = summary_updated["subscriberCount"]
    elif action in ("comment", "reply"):
        comment_payload = data.get("comment")
        if not isinstance(comment_payload, dict):
            comment_payload = {}
        if "text" not in comment_payload:
            comment_payload["text"] = data.get("text") or data.get("message") or ""
        if "author" not in comment_payload:
            comment_payload["author"] = data.get("author") or "Community member"
        if "ts" not in comment_payload:
            comment_payload["ts"] = data.get("ts") or now_ms
        comment_entry = _normalize_comment_entry(comment_payload)
        if not comment_entry:
            return jsonify({"error": "comment text required"}), 400
        comments = detail.get("comments") if isinstance(detail.get("comments"), list) else []
        comments = _sanitize_comment_list(comments + [comment_entry])
        detail["comments"] = comments
        summary_updated["commentCount"] = max(_safe_count(summary_updated.get("commentCount"), 0), len(comments))
        detail["commentCount"] = summary_updated["commentCount"]
    else:
        return jsonify({"error": "unsupported action"}), 400

    if idx is not None:
        posts[idx] = summary_updated
    try:
        _write_community_feed(posts)
    except Exception as exc:
        _trace("community:actions feed update failed", exc)
    if not db:
        try:
            with open(detail_path, "w", encoding="utf-8") as f:
                json.dump(detail, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            _trace("community:actions detail update failed", exc)
    if db:
        try:
            _db_upsert_community_post(sid, summary_updated, detail)
        except Exception:
            pass

    payload = _community_actions_payload(summary_updated, detail)
    return jsonify({"ok": True, "actions": payload})


# ---- Frontend release mark bridge ----------------------------------------
@app.post("/api/release_mark")
def api_release_mark():
    try:
        data = request.get_json(force=True) or {}
        sid = (data.get("sessionId") or data.get("sid") or "").strip() or None
        shot = data.get("shotId") if isinstance(data.get("shotId"), int) else None
        frame = int(data.get("frame") or data.get("fidx") or 0)
        t_ms = int(data.get("tMs") or data.get("t") or (time.time() * 1000))
        via = (data.get("via") or "frontend").strip()
        snap = data.get("poseSnapshot") or None
        hoop = data.get("hoop") or None
        gate = data.get("gate") or None
        rel_metrics = data.get("releaseMetrics") or None
        if not sid:
            return jsonify({"ok": False, "error": "sessionId required"}), 400
        if shot is None:
            return jsonify({"ok": False, "error": "shotId required"}), 400
        if not isinstance(snap, dict) or not snap:
            return jsonify({"ok": False, "error": "poseSnapshot required"}), 422
        entry = {
            "ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "sessionId": sid,
            "shotId": shot,
            "frame": frame,
            "tMs": t_ms,
            "via": via,
            "poseSnapshot": snap,
            "hoop": hoop,
            "gate": gate,
            "releaseMetrics": rel_metrics,
        }
        # Persist to session folder if present; else log in a global file
        if sid:
            d = _session_path(sid)
            p = os.path.join(d, "releases.jsonl")
        else:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            p = os.path.join(SESSIONS_DIR, "releases.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # Also persist to SQL if configured
        try:
            db = getattr(app, "db", None)
            if db and sid:
                with db["Session"]() as sdb:
                    # store a pose snapshot row (create table below)
                    PoseSnap = db.get("PoseSnapshotRow")
                    if PoseSnap is not None:
                        row = PoseSnap(
                            sid=sid,
                            shot_idx=(shot or 0),
                            frame=frame,
                            t_ms=t_ms,
                            via=via,
                            metrics=snap,
                            hoop=hoop,
                            gate=entry.get("gate"),
                        )
                        sdb.add(row)
                        sdb.commit()
        except Exception as e:
            print("db_add_pose_snapshot:", e)

        # Keep a simple in-memory mirror for quick debugging
        app.config["LAST_RELEASE"] = entry
        return jsonify({"ok": True, "saved": True})
    except Exception as e:
        print("release_mark error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------- Database (MySQL) -----------------------
Base = declarative_base() if SQLA_AVAILABLE else None
DBSessionLocal = None


def _try_init_db():
    global DBSessionLocal
    # Avoid redefining models if we’ve already connected
    existing = getattr(app, "db", None)
    if existing:
        return existing

    if not SQLA_AVAILABLE:
        if DB_REQUIRED:
            raise RuntimeError("SQLAlchemy unavailable but database is required.")
        return None
    try:
        load_dotenv()
        uri = (
            os.getenv("SQLALCHEMY_DATABASE_URI")
            or os.getenv("DATABASE_URI")
            or os.getenv("DATABASE_URL")
            or ""
        ).strip()
        if not uri:
            if DB_REQUIRED:
                raise RuntimeError("DATABASE_URL is required but not configured.")
            return None
        # On Windows, mysql-connector C extension can crash the interpreter.
        # Force pure-Python mode when using mysql+mysqlconnector to avoid access violations.
        if uri.startswith(("postgres://", "postgresql://")) and "sslmode=" not in uri:
            uri = f"{uri}&sslmode=require" if "?" in uri else f"{uri}?sslmode=require"
        if uri.startswith("mysql+mysqlconnector://"):
            engine = create_engine(
                uri, pool_pre_ping=True, future=True, connect_args={"use_pure": True}
            )
        else:
            engine = create_engine(uri, pool_pre_ping=True, future=True)

        class User(Base):
            __tablename__ = "users"
            user_id = Column(Integer, primary_key=True, autoincrement=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            status = Column(String(16), default="active")
            name = Column(String(100))
            password_hash = Column(String(255))
            email = Column(String(255))
            phone = Column(String(32))
            address = Column(String(255))
            birthday = Column(String(16))
            handle = Column(String(64))
            height_cm = Column(Integer)
            weight_kg = Column(Integer)
            sports = Column(MyJSON)
            goals = Column(MyJSON)

        class UserAdminMeta(Base):
            __tablename__ = "user_admin_meta"
            id = Column(Integer, primary_key=True, autoincrement=True)
            user_id = Column(
                Integer,
                ForeignKey("users.user_id", ondelete="CASCADE"),
                unique=True,
                nullable=False,
            )
            status = Column(String(16))
            notes = Column(Text)
            updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
            updated_by = Column(String(64))
            user = relationship("User", backref="admin_meta", uselist=False)

        class UserFaceLock(Base):
            __tablename__ = "user_face_lock"
            id = Column(Integer, primary_key=True, autoincrement=True)
            user_id = Column(
                Integer,
                ForeignKey("users.user_id", ondelete="CASCADE"),
                unique=True,
                nullable=False,
            )
            consent_face_lock = Column(Boolean, default=False, nullable=False)
            embedding = Column(MyJSON)
            embedding_dims = Column(Integer)
            strategy = Column(String(32))
            created_at = Column(DateTime, default=datetime.utcnow)
            updated_at = Column(
                DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
            )
            metadata_json = Column("metadata", MyJSON)
            user = relationship("User", backref="face_lock", uselist=False)

        class PasswordResetToken(Base):
            __tablename__ = "password_reset_tokens"
            id = Column(Integer, primary_key=True, autoincrement=True)
            user_id = Column(
                Integer,
                ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            )
            token_hash = Column(String(64), unique=True, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            expires_at = Column(DateTime, nullable=False)
            used_at = Column(DateTime)
            requested_ip = Column(String(64))
            requested_ua = Column(String(255))
            user = relationship("User", backref="password_reset_tokens")

        class SessionRow(Base):
            __tablename__ = "sessions"
            sid = Column(String(64), primary_key=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            ended_at = Column(DateTime)
            user_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)
            shots_count = Column(Integer, default=0)
            makes = Column(Integer, default=0)
            accuracy = Column(Integer, default=0)
            entry_angle_avg = Column(Float)
            release_angle_avg = Column(Float)
            arc_height_avg = Column(Float)
            summary = Column(Text)
            status = Column(String(16), default="private")
            video_url = Column(String(512))
            user = relationship("User", lazy="joined")

        class SessionPayloadRow(Base):
            __tablename__ = "session_payloads"
            sid = Column(String(64), primary_key=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
            data = Column(MyJSON)

        class CommunityPostRow(Base):
            __tablename__ = "community_posts"
            sid = Column(String(64), primary_key=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
            summary = Column(MyJSON)
            detail = Column(MyJSON)

        class ShotRow(Base):
            __tablename__ = "shots"
            id = Column(Integer, primary_key=True, autoincrement=True)
            sid = Column(String(64), ForeignKey("sessions.sid"), nullable=False)
            idx = Column(Integer, nullable=False)
            created_at = Column(DateTime, default=datetime.utcnow)
            release_frame = Column(Integer)
            end_frame = Column(Integer)
            release_ms = Column(BigInteger)
            end_ms = Column(BigInteger)
            made = Column(Boolean)
            entry_angle = Column(Float)
            release_angle = Column(Float)
            arc_height = Column(Float)
            miss_reason = Column(String(128))
            clip_url = Column(String(512))
            pose_score = Column(Float)
            data = Column(MyJSON)

        class PoseSnapshotRow(Base):
            __tablename__ = "pose_snapshots"
            id = Column(Integer, primary_key=True, autoincrement=True)
            sid = Column(String(64), ForeignKey("sessions.sid"), nullable=False)
            shot_idx = Column(Integer, nullable=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            frame = Column(BigInteger)
            t_ms = Column(BigInteger)
            via = Column(String(64))
            metrics = Column(MyJSON)  # poseSnapshot metrics JSON
            hoop = Column(MyJSON)  # hoop box JSON
            gate = Column(MyJSON)  # unified gate result/tests JSON

        class CoachFeedbackRow(Base):
            __tablename__ = "ai_feedback"
            id = Column(Integer, primary_key=True, autoincrement=True)
            sid = Column(String(64), ForeignKey("sessions.sid"), nullable=True)
            shot_idx = Column(Integer, nullable=True)
            created_at = Column(DateTime, default=datetime.utcnow)
            provider = Column(String(64))
            model = Column(String(64))
            latency_ms = Column(BigInteger)
            text = Column(Text)
            score = Column(Float)

        class Event(Base):
            __tablename__ = "events"
            id = Column(Integer, primary_key=True, autoincrement=True)
            slug = Column(String(80), unique=True, nullable=False)  # 'cav-camps-2025'
            name = Column(String(160), nullable=False)
            start_date = Column(Date, nullable=False)
            end_date = Column(Date, nullable=False)
            daily_limit = Column(
                Integer, nullable=False, default=1
            )  # 1 challenge session / day
            min_shots = Column(
                Integer, nullable=False, default=10
            )  # session must have >=10
            tz = Column(String(64), nullable=True)  # optional IANA tz
            created_at = Column(DateTime, default=datetime.utcnow)

        class EventRegistration(Base):
            __tablename__ = "event_registrations"
            id = Column(Integer, primary_key=True, autoincrement=True)
            event_id = Column(
                Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
            )
            user_id = Column(
                Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
            )
            registered_at = Column(DateTime, default=datetime.utcnow)
            dob = Column(Date)  # snapshot or from profile
            age_group = Column(
                String(16), nullable=False
            )  # '<11','11-14','14-16','17-19','>19'
            __table_args__ = (
                UniqueConstraint("event_id", "user_id", name="uq_event_user"),
            )

        class EventSession(Base):
            __tablename__ = "event_sessions"
            id = Column(Integer, primary_key=True, autoincrement=True)
            event_id = Column(
                Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
            )
            user_id = Column(
                Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
            )
            session_id = Column(
                String(64), ForeignKey("sessions.sid", ondelete="SET NULL")
            )
            session_date = Column(
                Date, nullable=False
            )  # event-local date (use UTC if no tz)
            eligible = Column(Boolean, nullable=False, default=True)
            analyzed = Column(Boolean, nullable=False, default=False)
            makes = Column(Integer)
            attempts = Column(Integer)
            created_at = Column(DateTime, default=datetime.utcnow)
            __table_args__ = (
                UniqueConstraint(
                    "event_id", "user_id", "session_date", name="uq_event_user_day"
                ),
                Index("ix_event_user", "event_id", "user_id"),
            )

        class EventUserStats(Base):
            __tablename__ = "event_user_stats"
            event_id = Column(
                Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
            )
            user_id = Column(
                Integer,
                ForeignKey("users.user_id", ondelete="CASCADE"),
                primary_key=True,
            )
            total_makes = Column(Integer, nullable=False, default=0)
            total_attempts = Column(Integer, nullable=False, default=0)
            best_session_mk = Column(Integer, nullable=False, default=0)
            best_session_att = Column(Integer, nullable=False, default=0)
            first4_avg_mk = Column(Float)
            last3_avg_mk = Column(Float)
            improvement = Column(Float)  # last3 - first4
            age_group = Column(String(16), nullable=False)  # mirror from registration
            updated_at = Column(DateTime, default=datetime.utcnow)

        class SupportTicket(Base):
            __tablename__ = "support_tickets"
            id = Column(Integer, primary_key=True, autoincrement=True)
            user_id = Column(
                Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
            )
            created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
            status = Column(String(24), nullable=False, default="open")
            priority = Column(String(16), nullable=False, default="normal")
            category = Column(String(32), nullable=False, default="technical")
            title = Column(String(255), nullable=False)
            description = Column(Text, nullable=False)
            last_update_at = Column(
                DateTime,
                nullable=False,
                default=datetime.utcnow,
                onupdate=datetime.utcnow,
            )
            assignee = Column(String(64))
            session_id = Column(
                String(64),
                ForeignKey("sessions.sid", ondelete="SET NULL"),
                nullable=True,
            )
            meta = Column(MyJSON)

            interactions = relationship(
                "SupportInteraction",
                back_populates="ticket",
                passive_deletes=True,
            )

            def to_dict(self):
                return {
                    "id": self.id,
                    "user_id": self.user_id,
                    "status": self.status,
                    "priority": self.priority,
                    "category": self.category,
                    "title": self.title,
                    "description": self.description,
                    "assignee": self.assignee,
                    "session_id": self.session_id,
                    "meta": self.meta,
                    "created_at": self.created_at.isoformat()
                    if getattr(self, "created_at", None)
                    else None,
                    "last_update_at": self.last_update_at.isoformat()
                    if getattr(self, "last_update_at", None)
                    else None,
                }

        class SupportInteraction(Base):
            __tablename__ = "support_interactions"
            id = Column(Integer, primary_key=True, autoincrement=True)
            user_id = Column(
                Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
            )
            session_id = Column(
                String(64),
                ForeignKey("sessions.sid", ondelete="SET NULL"),
                nullable=True,
            )
            shot_id = Column(String(64))
            created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
            role = Column(String(16), nullable=False)
            message = Column(Text, nullable=False)
            intent = Column(String(64))
            action_taken = Column(MyJSON)
            result_status = Column(String(32), nullable=False, default="pending_user")
            related_ticket_id = Column(
                Integer,
                ForeignKey("support_tickets.id", ondelete="SET NULL"),
                nullable=True,
            )
            meta = Column(MyJSON)

            ticket = relationship("SupportTicket", back_populates="interactions")

            def to_dict(self):
                return {
                    "id": self.id,
                    "user_id": self.user_id,
                    "session_id": self.session_id,
                    "shot_id": self.shot_id,
                    "role": self.role,
                    "message": self.message,
                    "intent": self.intent,
                    "action_taken": self.action_taken,
                    "result_status": self.result_status,
                    "related_ticket_id": self.related_ticket_id,
                    "meta": self.meta,
                    "created_at": self.created_at.isoformat()
                    if getattr(self, "created_at", None)
                    else None,
                }

        class TrialEmailRequestRow(Base):
            __tablename__ = "trial_email_requests"
            id = Column(Integer, primary_key=True, autoincrement=True)
            sid = Column(String(64))
            email = Column(String(255))
            created_at = Column(DateTime, default=datetime.utcnow)
            data = Column(MyJSON)

        Base.metadata.create_all(engine)
        DBSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

        # attach to app for reuse
        app.db = {
            "engine": engine,
            "Session": DBSessionLocal,
            "User": User,
            "UserAdminMeta": UserAdminMeta,
            "UserFaceLock": UserFaceLock,
            "PasswordResetToken": PasswordResetToken,
            "SessionRow": SessionRow,
            "SessionPayloadRow": SessionPayloadRow,
            "CommunityPostRow": CommunityPostRow,
            "ShotRow": ShotRow,
            "PoseSnapshotRow": PoseSnapshotRow,
            "CoachFeedbackRow": CoachFeedbackRow,
            "Event": Event,
            "EventRegistration": EventRegistration,
            "EventSession": EventSession,
            "EventUserStats": EventUserStats,
            "SupportTicket": SupportTicket,
            "SupportInteraction": SupportInteraction,
            "TrialEmailRequestRow": TrialEmailRequestRow,
        }
        print("✅ SQLAlchemy connected")
        return app.db
    except Exception as e:
        if DB_REQUIRED:
            raise
        print("⚠️ DB init skipped:", e)
        return None


_try_init_db()

_ensure_arcmm_worker_started()


def _db_add_session(sid, started_at):
    try:
        db = getattr(app, "db", None)
        if not db:
            return
        with db["Session"]() as s:
            row = db["SessionRow"](
                sid=sid, created_at=datetime.utcfromtimestamp(started_at / 1000.0)
            )
            uid = session.get("user_id")
            if uid:
                row.user_id = uid
            s.add(row)
            s.commit()
    except Exception as e:
        print("db_add_session:", e)


def _db_delete_session_row(sid):
    try:
        db = getattr(app, "db", None)
        if not db:
            return
        with db["Session"]() as s:
            row = s.get(db["SessionRow"], sid)
            if row:
                s.delete(row)
                s.commit()
    except Exception as e:
        print("db_delete_session:", e)


def _db_add_shot(sid, idx, payload):
    try:
        db = getattr(app, "db", None)
        if not db:
            return

        def _resolve_score(src):
            score_val = src.get("poseScore")
            if score_val is None:
                score_val = src.get("weightedScore")
            try:
                return float(score_val)
            except (TypeError, ValueError):
                return None

        with db["Session"]() as s:
            from sqlalchemy import select, func, case

            ShotRow = db["ShotRow"]

            def _ensure_session_entry(session_obj, session_id):
                try:
                    sess = session_obj.get(db["SessionRow"], session_id)
                    if not sess:
                        sess = db["SessionRow"](
                            sid=session_id, created_at=datetime.utcnow()
                        )
                        session_obj.add(sess)
                except Exception as e_sess:
                    _trace("db_add_shot: ensure session error:", e_sess)

            def _recalc_session_totals(session_obj, session_id):
                try:
                    sess = session_obj.get(db["SessionRow"], session_id)
                    if sess:
                        total, makes = session_obj.execute(
                            select(
                                func.count(ShotRow.id),
                                func.sum(case((ShotRow.made, 1), else_=0)),
                            ).where(ShotRow.sid == session_id)
                        ).one()
                        sess.shots_count = int(total or 0)
                        sess.makes = int(makes or 0)
                        sess.accuracy = int(
                            round((sess.makes / max(1, sess.shots_count)) * 100)
                        )
                except Exception as e_tot:
                    _trace("db_add_shot: totals error:", e_tot)

            def _sync_feedback_score(session_obj, session_id, shot_idx, score_val):
                try:
                    FB = db.get("CoachFeedbackRow")
                    if FB is None or score_val is None:
                        return
                    fb_row = (
                        session_obj.execute(
                            select(FB)
                            .where(FB.sid == session_id, FB.shot_idx == shot_idx)
                            .order_by(FB.id.desc())
                        )
                        .scalars()
                        .first()
                    )
                    if fb_row:
                        fb_row.score = score_val
                    else:
                        session_obj.add(
                            FB(
                                sid=session_id,
                                shot_idx=shot_idx,
                                provider="auto-metrics",
                                model="auto",
                                score=score_val,
                                text=None,
                            )
                        )
                except Exception as e_fb:
                    _trace("db_add_shot: feedback score error:", e_fb)

            score_val = _resolve_score(payload)
            try:
                idx_int = int(idx)
            except (TypeError, ValueError):
                idx_int = idx

            values = {
                "sid": sid,
                "idx": idx_int,
                "entry_angle": payload.get("entryAngle"),
                "release_angle": payload.get("releaseAngle"),
                "arc_height": payload.get("arcHeight"),
                "miss_reason": payload.get("missReason"),
                "data": payload,
            }

            made_val = payload.get("made")
            if made_val is not None:
                values["made"] = bool(made_val)

            if score_val is not None:
                for attr in ("pose_score", "weighted_score", "score"):
                    if hasattr(ShotRow, attr):
                        values[attr] = score_val

            existing = (
                s.execute(
                    select(ShotRow).where(ShotRow.sid == sid, ShotRow.idx == idx_int)
                )
                .scalars()
                .first()
            )
            if existing is None:
                existing = ShotRow(**values)
                s.add(existing)
            else:
                for key, val in values.items():
                    if key in ("sid", "idx"):
                        continue
                    try:
                        setattr(existing, key, val)
                    except Exception:
                        pass

            with s.no_autoflush:
                _ensure_session_entry(s, sid)

            try:
                s.flush()
            except Exception as e_flush:
                _trace("db_add_shot: flush error:", e_flush)

            _recalc_session_totals(s, sid)
            _sync_feedback_score(s, sid, idx_int, score_val)
            s.commit()

            _trace(
                "db:upsert:shot",
                {
                    "sid": sid,
                    "idx": idx_int,
                    "made": values.get("made", payload.get("made")),
                    "arcHeight": payload.get("arcHeight"),
                    "entryAngle": payload.get("entryAngle"),
                    "releaseAngle": payload.get("releaseAngle"),
                },
            )
            try:
                queue_idx = int(idx)
            except (TypeError, ValueError):
                queue_idx = None
            if queue_idx is not None:
                try:
                    _arcmm_queue_shot(sid, queue_idx)
                except Exception as q_exc:
                    _trace("arcmm queue error:", q_exc)
    except Exception as e:
        _trace("db_add_shot:", e)


def _db_backfill_session_shots(sid: str):
    """Ensure DB has a ShotRow for every shot in session.json. Idempotent."""
    try:
        db = getattr(app, "db", None)
        if not db:
            return
        sess_file = _read_session(sid) or {}
        shots = list(sess_file.get("shots") or [])
        if not shots:
            return
        with db["Session"]() as s:
            from sqlalchemy import select

            ShotRow = db["ShotRow"]
            have = set()
            for r in (
                s.execute(select(ShotRow.idx).where(ShotRow.sid == sid)).scalars().all()
            ):
                try:
                    have.add(int(r))
                except Exception:
                    continue
            # Create missing rows
            created = 0
            for sh in shots:
                try:
                    i = int(sh.get("idx"))
                except Exception:
                    continue
                if i in have:
                    continue
                m_in = sh.get("made")
                row = ShotRow(
                    sid=sid,
                    idx=i,
                    made=(None if m_in is None else bool(m_in)),
                    entry_angle=sh.get("entryAngle"),
                    release_angle=sh.get("releaseAngle"),
                    arc_height=sh.get("arcHeight"),
                    miss_reason=sh.get("missReason"),
                    data=sh,
                )
                s.add(row)
                created += 1
            if created:
                try:
                    s.commit()
                except Exception:
                    s.rollback()
    except Exception as e:
        print("db_backfill_session_shots:", e)


@app.post("/api/arcmm/requeue")
def api_arcmm_requeue():
    body = request.get_json(silent=True) or {}
    sid = (body.get("sid") or "").strip()
    if not sid:
        return jsonify({"ok": False, "error": "sid is required"}), 400
    try:
        idx = body.get("idx")
        queued: list[int] = []
        if idx is None:
            sess = _read_session(sid) or {}
            for shot in sess.get("shots") or []:
                try:
                    shot_idx = int(shot.get("idx"))
                except Exception:
                    continue
                _arcmm_queue_shot(sid, shot_idx)
                queued.append(shot_idx)
        else:
            shot_idx = int(idx)
            _arcmm_queue_shot(sid, shot_idx)
            queued.append(shot_idx)
        return jsonify({"ok": True, "queued": queued})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _db_finalize_session(sid):
    try:
        db = getattr(app, "db", None)
        if not db:
            return
        with db["Session"]() as s:
            sess = s.get(db["SessionRow"], sid)
            if sess:
                sess.ended_at = datetime.utcnow()
                s.commit()
        try:
            _db_event_finalize_from_session(sid)
        except Exception as evt_err:
            print("db_finalize_session:event", evt_err)
    except Exception as e:
        print("db_finalize_session:", e)


def _age_group_from_dob(dob: date, ref: date) -> str:
    if not dob or not ref:
        return ">19"
    years = (ref - dob).days // 365
    if years < 11:
        return "<11"
    if years <= 14:
        return "11-14"
    if years <= 16:
        return "14-16"
    if years <= 19:
        return "17-19"
    return ">19"


def _event_local_date(dt_utc: datetime, tz_name: str | None) -> date:
    # keep simple: use UTC if tz missing
    base = dt_utc
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    try:
        if tz_name:
            import zoneinfo

            return base.astimezone(zoneinfo.ZoneInfo(tz_name)).date()
    except Exception:
        pass
    # fall back to local server date when no timezone provided
    try:
        return datetime.now().date()
    except Exception:
        return base.astimezone(timezone.utc).date()


def _db_event_register(user_id: int, event_slug: str, dob_iso: str | None = None):
    db = getattr(app, "db", None)
    if not db:
        return {"ok": False, "err": "db unavailable"}
    with db["Session"]() as s:
        Event, EventRegistration = db["Event"], db["EventRegistration"]
        ev = s.query(Event).filter(Event.slug == event_slug).one_or_none()
        if not ev:
            return {"ok": False, "err": "event not found"}
        dob = None
        try:
            if dob_iso:
                dob = datetime.strptime(dob_iso, "%Y-%m-%d").date()
        except Exception:
            pass
        ag = _age_group_from_dob(dob, ev.start_date)
        # upsert-ish
        reg = (
            s.query(EventRegistration)
            .filter_by(event_id=ev.id, user_id=user_id)
            .one_or_none()
        )
        if not reg:
            reg = EventRegistration(
                event_id=ev.id, user_id=user_id, dob=dob, age_group=ag
            )
            s.add(reg)
        else:
            if dob:
                reg.dob = dob
                reg.age_group = ag
            elif not reg.age_group:
                reg.age_group = ag
        s.commit()
        return {"ok": True, "event_id": ev.id, "age_group": reg.age_group}


# ---------------------- Auth (register/login/logout) ---------------------


def _db_get():
    db = getattr(app, "db", None)
    if db is None and not getattr(app, "_db_retrying", False):
        try:
            app._db_retrying = True
            db = _try_init_db()
        except Exception:
            if DB_REQUIRED:
                raise
            db = None
        finally:
            app._db_retrying = False
    elif db is None:
        db = getattr(app, "db", None)
    return db


def _serialize_face_lock(row, include_embedding=False):
    if not row:
        payload = {
            "consent": False,
            "has_embedding": False,
            "embedding_dims": None,
            "updated_at": None,
            "strategy": None,
            "metadata": {},
        }
        if include_embedding:
            payload["embedding"] = []
        return payload
    ts = row.updated_at or row.created_at
    payload = {
        "consent": bool(getattr(row, "consent_face_lock", False)),
        "has_embedding": bool(row.embedding) and len(row.embedding or []) > 0,
        "embedding_dims": int(row.embedding_dims) if row.embedding_dims else None,
        "updated_at": ts.isoformat() if ts else None,
        "strategy": row.strategy,
        "metadata": row.metadata_json or {},
    }
    if include_embedding and row.embedding:
        payload["embedding"] = [float(x) for x in row.embedding]
    elif include_embedding:
        payload["embedding"] = []
    return payload


def _session_face_lock_set(payload):
    try:
        session["_face_lock"] = payload
    except Exception:
        pass


def _session_face_lock_get():
    try:
        data = session.get("_face_lock") or {}
        return dict(data)
    except Exception:
        return {}


def _serialize_session_face_lock(data, include_embedding=False):
    consent = bool(data.get("consent_face_lock"))
    embedding = data.get("embedding") or []
    dims = data.get("embedding_dims")
    updated_at = data.get("updated_at")
    payload = {
        "consent": consent,
        "has_embedding": bool(embedding),
        "embedding_dims": int(dims)
        if dims
        else (len(embedding) if embedding else None),
        "updated_at": updated_at,
        "strategy": data.get("strategy"),
        "metadata": data.get("metadata") or {},
    }
    if include_embedding:
        payload["embedding"] = [float(x) for x in embedding] if embedding else []
    return payload


def _get_stub_user():
    try:
        data = session.get("_stub_user")
        return dict(data) if data else None
    except Exception:
        return None


def _set_stub_user(user, password=None):
    try:
        session["_stub_user"] = user or {}
        if user is None:
            session.pop("_stub_pw", None)
        elif password is not None:
            session["_stub_pw"] = generate_password_hash(password)
    except Exception:
        pass


def _get_stub_profile():
    try:
        data = session.get("_stub_profile")
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _set_stub_profile(profile):
    try:
        session["_stub_profile"] = profile or {}
    except Exception:
        pass


def _normalize_profile_payload(data: dict | None) -> dict:
    payload = data or {}
    profile = {}
    profile["name"] = (payload.get("name") or "").strip()
    profile["handle"] = (payload.get("handle") or "").strip()
    profile["primary_sport"] = (payload.get("primary_sport") or "").strip()
    profile["dominant_hand"] = (payload.get("dominant_hand") or "").strip()
    profile["skill_level"] = (payload.get("skill_level") or "").strip()
    try:
        profile["height_cm"] = (
            int(payload.get("height_cm")) if payload.get("height_cm") else None
        )
    except Exception:
        profile["height_cm"] = None
    try:
        profile["weight_kg"] = (
            int(payload.get("weight_kg")) if payload.get("weight_kg") else None
        )
    except Exception:
        profile["weight_kg"] = None
    goals_val = payload.get("goals")
    goals: list[str] = []
    if isinstance(goals_val, str):
        goals = [
            line.strip()
            for line in goals_val.replace("\r", "\n").split("\n")
            if line.strip()
        ]
    elif isinstance(goals_val, list):
        goals = [str(item).strip() for item in goals_val if str(item).strip()]
    profile["goals"] = goals
    return profile


def _profile_is_complete(profile: dict | None) -> bool:
    if not profile:
        return False
    return bool(profile.get("name") and profile.get("primary_sport"))


def _serialize_profile_row(row) -> dict:
    if row is None:
        return {}
    sports_raw = getattr(row, "sports", None)
    primary_sport = ""
    dominant_hand = ""
    skill_level = ""
    if isinstance(sports_raw, dict):
        primary_sport = sports_raw.get("primary") or sports_raw.get("sport") or ""
        dominant_hand = sports_raw.get("hand") or ""
        skill_level = sports_raw.get("skill") or ""
    elif isinstance(sports_raw, list) and sports_raw:
        first = sports_raw[0]
        if isinstance(first, dict):
            primary_sport = first.get("primary") or first.get("sport") or ""
            dominant_hand = first.get("hand") or ""
            skill_level = first.get("skill") or ""
        elif isinstance(first, str):
            primary_sport = first
    goals_raw = getattr(row, "goals", None)
    if isinstance(goals_raw, list):
        goals = [str(item) for item in goals_raw if item is not None]
    elif isinstance(goals_raw, str):
        goals = [goals_raw]
    else:
        goals = []
    return {
        "user_id": getattr(row, "user_id", None),
        "email": getattr(row, "email", None),
        "name": getattr(row, "name", "") or "",
        "handle": getattr(row, "handle", "") or "",
        "primary_sport": primary_sport,
        "dominant_hand": dominant_hand,
        "skill_level": skill_level,
        "height_cm": getattr(row, "height_cm", None),
        "weight_kg": getattr(row, "weight_kg", None),
        "goals": goals,
    }

USER_STATUS_VALUES = {
    "active",
    "inactive",
    "blocked",
    "banned",
    "suspended",
    "review",
}
USER_BLOCKED_STATUSES = {"inactive", "blocked", "banned", "suspended", "review"}


def _normalize_user_status(value) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw or raw in {"none", "null", "clear"}:
        return None
    if raw in USER_STATUS_VALUES:
        return raw
    return None


def _resolve_user_status(base_status, admin_status) -> str:
    return _normalize_user_status(admin_status) or _normalize_user_status(base_status) or "active"


def _is_user_blocked(status: str | None) -> bool:
    return _normalize_user_status(status) in USER_BLOCKED_STATUSES


def _load_user_profile(user_id: int | None) -> dict:
    if not user_id:
        return {}
    db = _db_get()
    if db:
        with db["Session"]() as s:
            row = s.get(db["User"], user_id)
            if not row:
                return {}
            return _serialize_profile_row(row)
    profile = _get_stub_profile()
    user = _get_stub_user() or {}
    if not profile:
        profile = {"name": user.get("name", ""), "email": user.get("email", "")}
    else:
        profile.setdefault("email", user.get("email"))
    return profile


def _save_user_profile(user_id: int | None, profile: dict) -> dict:
    if not user_id:
        raise ValueError("unauthenticated")
    db = _db_get()
    if db:
        with db["Session"]() as s:
            row = s.get(db["User"], user_id)
            if not row:
                raise ValueError("user not found")
            if profile.get("name"):
                row.name = profile["name"]
            if profile.get("handle") or row.handle:
                row.handle = profile.get("handle") or None
            row.height_cm = profile.get("height_cm")
            row.weight_kg = profile.get("weight_kg")
            sports_blob = {
                k: v
                for k, v in {
                    "primary": profile.get("primary_sport") or None,
                    "hand": profile.get("dominant_hand") or None,
                    "skill": profile.get("skill_level") or None,
                }.items()
                if v
            }
            row.sports = sports_blob or None
            goals = profile.get("goals") or None
            row.goals = goals if goals else None
            s.add(row)
            s.commit()
            s.refresh(row)
            return _serialize_profile_row(row)
    _set_stub_profile(profile)
    user = _get_stub_user() or {}
    if profile.get("name"):
        user["name"] = profile["name"]
        _set_stub_user(user)
    profile.setdefault("email", user.get("email"))
    return profile


def _check_stub_credentials(email, password):
    user = _get_stub_user()
    if not user:
        return None
    stored_pw = session.get("_stub_pw")
    if user.get("email") != email:
        return None
    if stored_pw is None:
        return user
    try:
        if check_password_hash(stored_pw, password):
            return user
    except Exception:
        if stored_pw == password:
            return user
    return None


def _hash_password_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _write_face_lock(
    user_id,
    *,
    embedding=None,
    dims=None,
    strategy=None,
    metadata=None,
    consent=None,
    clear=False,
    include_embedding=False,
):
    db = _db_get()
    if not db:
        if not ALLOW_STUB_AUTH:
            return False, "db not configured"
        data = _session_face_lock_get()
        if clear:
            data = {}
        if embedding is not None:
            vec = [float(x) for x in embedding]
            data.update(
                {
                    "embedding": vec,
                    "embedding_dims": int(dims or len(vec)),
                    "strategy": strategy,
                    "metadata": metadata or {},
                    "consent_face_lock": True if consent is None else bool(consent),
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )
        if consent is not None:
            data["consent_face_lock"] = bool(consent)
            data["updated_at"] = datetime.utcnow().isoformat()
        _session_face_lock_set(data)
        return True, _serialize_session_face_lock(
            data, include_embedding=include_embedding
        )
    FaceLock = db.get("UserFaceLock")
    if not FaceLock:
        return False, "face lock storage unavailable"
    with db["Session"]() as s:
        row = s.query(FaceLock).filter(FaceLock.user_id == user_id).one_or_none()
        if not row:
            row = FaceLock(user_id=user_id)
            s.add(row)
            s.flush()
        if clear:
            row.embedding = None
            row.embedding_dims = None
            row.strategy = None
            row.metadata_json = None
            row.updated_at = datetime.utcnow()
        if embedding is not None:
            vec = [float(x) for x in embedding]
            row.embedding = vec
            row.embedding_dims = int(dims or len(vec))
            row.strategy = strategy
            row.metadata_json = metadata or {}
            row.updated_at = datetime.utcnow()
            if consent is None:
                row.consent_face_lock = True
        if consent is not None:
            row.consent_face_lock = bool(consent)
            row.updated_at = datetime.utcnow()
        s.commit()
        return True, _serialize_face_lock(row, include_embedding=include_embedding)


def _read_face_lock(user_id, include_embedding=False):
    db = _db_get()
    if not db:
        if not ALLOW_STUB_AUTH:
            return False, "db not configured", None
        data = _session_face_lock_get()
        return (
            True,
            None,
            _serialize_session_face_lock(data, include_embedding=include_embedding),
        )
    FaceLock = db.get("UserFaceLock")
    if not FaceLock:
        return False, "face lock storage unavailable", None
    with db["Session"]() as s:
        row = s.query(FaceLock).filter(FaceLock.user_id == user_id).one_or_none()
        return (
            True,
            None,
            _serialize_face_lock(row, include_embedding=include_embedding),
        )


def _decode_data_url(data_url):
    if not data_url:
        return None
    if isinstance(data_url, bytes):
        return data_url
    if "," in data_url:
        _, data = data_url.split(",", 1)
    else:
        data = data_url
    data = data.strip()
    try:
        return base64.b64decode(data)
    except Exception:
        return None


def _cheap_face_embedding(image: Image.Image, dims: int = 512):
    try:
        dims = int(dims or 512)
    except Exception:
        dims = 512
    dims = max(64, min(1024, dims))
    base_side = 32
    gray = image.convert("L").resize((base_side, base_side), Image.BILINEAR)
    arr = np.asarray(gray, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return np.zeros((dims,), dtype=np.float32)
    arr = arr / 255.0
    arr = arr - float(arr.mean())
    base = arr / (np.linalg.norm(arr) or 1.0)
    if dims == base.shape[0]:
        vec = base
    else:
        idx = np.linspace(0, base.shape[0] - 1, num=dims)
        vec = np.interp(idx, np.arange(base.shape[0]), base)
    vec = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec.astype(np.float32)


def _face_crop_quality(image: Image.Image):
    try:
        w, h = image.size
    except Exception:
        return {"width": None, "height": None}
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return {
            "width": w,
            "height": h,
            "brightness": None,
            "contrast": None,
            "sharpness": None,
        }
    brightness = float(gray.mean() / 255.0)
    contrast = float(gray.std() / 255.0)
    try:
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        sharpness = None
    return {
        "width": w,
        "height": h,
        "brightness": round(brightness, 4),
        "contrast": round(contrast, 4),
        "sharpness": round(sharpness, 4) if sharpness is not None else None,
    }


@app.post("/api/auth/register")
def api_register():
    b = request.get_json(force=True) or {}
    name = (b.get("name") or "").strip()
    email = (b.get("email") or "").strip().lower()
    pw = b.get("password") or ""
    if not email or not pw:
        return jsonify({"error": "email and password required"}), 400
    db = _db_get()
    if not db:
        if not ALLOW_STUB_AUTH:
            return jsonify({"error": "db not configured"}), 500
        user_id = session.get("user_id") or int(datetime.utcnow().timestamp() * 1000)
        user = {"user_id": user_id, "name": name or email.split("@")[0], "email": email}
        session["user_id"] = user_id
        _set_stub_user(user, password=pw)
        _session_face_lock_set({})
        profile = _get_stub_profile()
        if name:
            profile["name"] = name
        profile.setdefault("email", email)
        _set_stub_profile(profile)
        return jsonify(
            {
                "user_id": user_id,
                "name": user["name"],
                "email": user["email"],
                "face_lock": _serialize_session_face_lock(
                    _session_face_lock_get(), include_embedding=False
                ),
                "profile": profile,
                "profile_complete": _profile_is_complete(profile),
            }
        )
    with db["Session"]() as s:
        from sqlalchemy import select

        exists = s.execute(
            select(db["User"]).where(db["User"].email == email)
        ).scalar_one_or_none()
        if exists:
            return jsonify({"error": "email already registered"}), 409
        row = db["User"](
            name=name or email.split("@")[0],
            email=email,
            password_hash=generate_password_hash(pw),
        )
        s.add(row)
        s.commit()
        session["user_id"] = row.user_id
        profile = _serialize_profile_row(row)
        return jsonify(
            {
                "user_id": row.user_id,
                "name": row.name,
                "email": row.email,
                "face_lock": _serialize_face_lock(getattr(row, "face_lock", None)),
                "profile": profile,
                "profile_complete": _profile_is_complete(profile),
            }
        )


@app.post("/api/auth/login")
def api_login():
    b = request.get_json(force=True) or {}
    email = (b.get("email") or "").strip().lower()
    pw = b.get("password") or ""
    db = _db_get()
    if not db:
        if not ALLOW_STUB_AUTH:
            return jsonify({"error": "db not configured"}), 500
        user = _check_stub_credentials(email, pw)
        if not user:
            return jsonify({"error": "invalid credentials"}), 401
        session["user_id"] = user["user_id"]
        profile = _get_stub_profile()
        profile.setdefault("email", user.get("email"))
        profile.setdefault("name", user.get("name"))
        return jsonify(
            {
                "user_id": user["user_id"],
                "name": user.get("name"),
                "email": user.get("email"),
                "face_lock": _serialize_session_face_lock(
                    _session_face_lock_get(), include_embedding=False
                ),
                "profile": profile,
                "profile_complete": _profile_is_complete(profile),
            }
        )
    from sqlalchemy import select

    with db["Session"]() as s:
        row = s.execute(
            select(db["User"]).where(db["User"].email == email)
        ).scalar_one_or_none()
        if not row or not check_password_hash(row.password_hash or "", pw):
            return jsonify({"error": "invalid credentials"}), 401
        admin_status = None
        try:
            AdminMeta = db.get("UserAdminMeta")
            if AdminMeta is not None:
                meta = (
                    s.query(AdminMeta)
                    .filter(AdminMeta.user_id == row.user_id)
                    .one_or_none()
                )
                if meta and meta.status:
                    admin_status = meta.status
        except Exception:
            admin_status = None
        effective_status = _resolve_user_status(row.status, admin_status)
        if _is_user_blocked(effective_status):
            return (
                jsonify({"error": "account suspended", "status": effective_status}),
                403,
            )
        session["user_id"] = row.user_id
        profile = _serialize_profile_row(row)
        return jsonify(
            {
                "user_id": row.user_id,
                "name": row.name,
                "email": row.email,
                "face_lock": _serialize_face_lock(getattr(row, "face_lock", None)),
                "profile": profile,
                "profile_complete": _profile_is_complete(profile),
            }
        )


@app.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/auth/password_reset/request")
def api_password_reset_request():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "valid email required"}), 400
    db = _db_get()
    if not db:
        return jsonify({"ok": True})
    Token = db.get("PasswordResetToken")
    if Token is None:
        return jsonify({"error": "reset unavailable"}), 500
    from sqlalchemy import select

    user = None
    with db["Session"]() as s:
        user = s.execute(
            select(db["User"]).where(db["User"].email == email)
        ).scalar_one_or_none()
        if not user:
            return jsonify({"ok": True})
        now = datetime.utcnow()
        try:
            s.query(Token).filter(
                Token.user_id == user.user_id,
                Token.used_at.is_(None),
                Token.expires_at > now,
            ).update({Token.used_at: now}, synchronize_session=False)
        except Exception:
            pass
        token = secrets.token_urlsafe(32)
        token_hash = _hash_password_reset_token(token)
        reset_row = Token(
            user_id=user.user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES),
            requested_ip=request.headers.get("X-Forwarded-For") or request.remote_addr,
            requested_ua=(request.headers.get("User-Agent") or "")[:255],
        )
        s.add(reset_row)
        s.commit()
        user_email = user.email
        user_name = user.name

    reset_url = _build_password_reset_link(token, email=user_email)
    payload = {
        "reset_url": reset_url,
        "name": user_name,
        "ttl_minutes": PASSWORD_RESET_TOKEN_TTL_MINUTES,
    }
    sent, send_error = _send_password_reset_email(user_email, payload)
    if not sent:
        _trace("password_reset_send_failed", send_error or "unknown")
    return jsonify({"ok": True})


@app.post("/api/auth/password_reset/confirm")
def api_password_reset_confirm():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not token or not password:
        return jsonify({"error": "token and password required"}), 400
    db = _db_get()
    if not db:
        return jsonify({"error": "db not configured"}), 500
    Token = db.get("PasswordResetToken")
    if Token is None:
        return jsonify({"error": "reset unavailable"}), 500
    token_hash = _hash_password_reset_token(token)
    now = datetime.utcnow()
    with db["Session"]() as s:
        row = s.query(Token).filter(Token.token_hash == token_hash).one_or_none()
        if not row or row.used_at or (row.expires_at and row.expires_at < now):
            return jsonify({"error": "invalid or expired token"}), 400
        user = s.get(db["User"], row.user_id)
        if not user:
            return jsonify({"error": "invalid token"}), 400
        user.password_hash = generate_password_hash(password)
        row.used_at = now
        s.add(user)
        s.add(row)
        s.commit()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def api_me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"user": None})
    # If we previously used stub auth the session id might be a non-numeric token
    if isinstance(uid, str) and not uid.isdigit():
        if ALLOW_STUB_AUTH:
            user = _get_stub_user()
            if not user:
                return jsonify({"user": None})
            profile = _get_stub_profile()
            profile.setdefault("email", user.get("email"))
            profile.setdefault("name", user.get("name"))
            return jsonify(
                {
                    "user": {
                        "user_id": user.get("user_id"),
                        "name": user.get("name"),
                        "email": user.get("email"),
                        "face_lock": _serialize_session_face_lock(
                            _session_face_lock_get(), include_embedding=False
                        ),
                    },
                    "profile": profile,
                    "profile_complete": _profile_is_complete(profile),
                }
            )
        else:
            # Invalid cookie; clear it so the client can re-auth cleanly
            session.pop("user_id", None)
            return jsonify({"user": None})
    if isinstance(uid, str) and uid.isdigit():
        uid = int(uid)
    db = _db_get()
    if not db:
        if not ALLOW_STUB_AUTH:
            return jsonify({"user": None})
        user = _get_stub_user()
        if not user:
            return jsonify({"user": None})
        profile = _get_stub_profile()
        profile.setdefault("email", user.get("email"))
        profile.setdefault("name", user.get("name"))
        return jsonify(
            {
                "user": {
                    "user_id": user.get("user_id"),
                    "name": user.get("name"),
                    "email": user.get("email"),
                    "face_lock": _serialize_session_face_lock(
                        _session_face_lock_get(), include_embedding=False
                    ),
                },
                "profile": profile,
                "profile_complete": _profile_is_complete(profile),
            }
        )
    with db["Session"]() as s:
        row = s.get(db["User"], uid)
        if not row:
            return jsonify({"user": None})
        admin_status = None
        try:
            AdminMeta = db.get("UserAdminMeta")
            if AdminMeta is not None:
                meta = (
                    s.query(AdminMeta)
                    .filter(AdminMeta.user_id == row.user_id)
                    .one_or_none()
                )
                if meta and meta.status:
                    admin_status = meta.status
        except Exception:
            admin_status = None
        effective_status = _resolve_user_status(row.status, admin_status)
        if _is_user_blocked(effective_status):
            try:
                session.pop("user_id", None)
            except Exception:
                pass
            return (
                jsonify({"error": "account suspended", "status": effective_status}),
                403,
            )
        profile = _serialize_profile_row(row)
        return jsonify(
            {
                "user": {
                    "user_id": row.user_id,
                    "name": row.name,
                    "email": row.email,
                    "face_lock": _serialize_face_lock(getattr(row, "face_lock", None)),
                    "status": effective_status,
                },
                "profile": profile,
                "profile_complete": _profile_is_complete(profile),
            }
        )


@app.get("/api/face/status")
def api_face_status():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "not authenticated"}), 401
    include = request.args.get("include") or ""
    include_embedding = any(
        part.strip().lower() == "embedding"
        for part in include.split(",")
        if part.strip()
    )
    ok, err, payload = _read_face_lock(uid, include_embedding=include_embedding)
    if not ok:
        return jsonify({"error": err or "face lock unavailable"}), 500
    return jsonify({"status": payload})


@app.post("/api/face/consent")
def api_face_consent():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "not authenticated"}), 401
    body = request.get_json(force=True) or {}
    consent = bool(body.get("consent"))
    ok, result = _write_face_lock(uid, consent=consent)
    if not ok:
        return jsonify({"error": result or "face lock unavailable"}), 500
    return jsonify({"status": result})


@app.post("/api/face/enroll")
def api_face_enroll_client():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "not authenticated"}), 401
    body = request.get_json(force=True) or {}
    embedding = body.get("embedding")
    dims = body.get("dims")
    strategy = (body.get("strategy") or "client").strip() or "client"
    if not isinstance(embedding, list) or not embedding:
        return jsonify({"error": "embedding required"}), 400
    try:
        dims = int(dims or len(embedding))
    except Exception:
        dims = len(embedding)
    if len(embedding) != dims:
        if len(embedding) < dims:
            return jsonify({"error": "embedding length mismatch"}), 400
        embedding = embedding[:dims]
    if dims < 64 or dims > 1024:
        return jsonify({"error": "embedding dims out of bounds"}), 400
    metadata = body.get("metadata") or {}
    samples = int(body.get("samples") or metadata.get("samples") or 0)
    if samples:
        metadata["samples"] = samples
    include_embed = bool(body.get("return_embedding"))
    ok, result = _write_face_lock(
        uid,
        embedding=embedding,
        dims=dims,
        strategy=strategy,
        metadata=metadata,
        include_embedding=include_embed,
    )
    if not ok:
        return jsonify({"error": result or "face lock unavailable"}), 500
    resp = {"status": result}
    if include_embed:
        resp["embedding"] = result.get("embedding") or []
    return jsonify(resp)


@app.post("/api/face/enroll_server")
def api_face_enroll_server():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "not authenticated"}), 401
    body = request.get_json(force=True) or {}
    crops = body.get("crops") or []
    if not isinstance(crops, list) or not crops:
        return jsonify({"error": "crops required"}), 400
    try:
        dims = int(body.get("dims") or 512)
    except Exception:
        dims = 512
    dims = max(64, min(1024, dims))
    vectors = []
    qualities = []
    max_bytes = int(body.get("max_bytes") or 2_500_000)
    for raw in crops:
        blob = _decode_data_url(raw)
        if not blob:
            continue
        if max_bytes and len(blob) > max_bytes:
            continue
        try:
            img = Image.open(io.BytesIO(blob))
            img = img.convert("RGB")
        except Exception:
            continue
        qual = _face_crop_quality(img)
        qualities.append(qual)
        min_side = min(img.size)
        if min_side < max(96, int(body.get("min_side") or 96)):
            continue
        vec = _cheap_face_embedding(img, dims=dims)
        vectors.append(vec)
    if len(vectors) < 2:
        return jsonify({"error": "insufficient usable crops"}), 422
    stacked = np.stack(vectors, axis=0).astype(np.float32)
    avg = stacked.mean(axis=0)
    norm = float(np.linalg.norm(avg))
    if norm > 0:
        avg = avg / norm
    avg_list = avg.astype(np.float32).tolist()
    metadata = body.get("metadata") or {}
    metadata.update(
        {"samples": len(vectors), "qualities": qualities, "source": "server"}
    )
    ok, result = _write_face_lock(
        uid,
        embedding=avg_list,
        dims=len(avg_list),
        strategy=body.get("strategy") or "server",
        metadata=metadata,
    )
    if not ok:
        return jsonify({"error": result or "face lock unavailable"}), 500
    if body.get("return_embedding"):
        return jsonify(
            {"status": result, "embedding": avg_list, "qualities": qualities}
        )
    return jsonify({"status": result, "qualities": qualities})


@app.delete("/api/face/enroll")
def api_face_clear():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "not authenticated"}), 401
    ok, result = _write_face_lock(uid, clear=True)
    if not ok:
        return jsonify({"error": result or "face lock unavailable"}), 500
    return jsonify({"status": result})


@app.route("/my_visaion")
def my_visaion():
    return send_from_directory("static", "my_visaion.html")


@app.route("/user_setup")
def user_setup_page():
    return send_from_directory("static", "user_setup.html")


@app.route("/my_sessions")
def my_sessions_page():
    return send_from_directory("static", "my_sessions.html")


@app.route("/dashboard")
def dashboard():
    return send_from_directory("static", "dashboard.html")


# Serve root favicon for browsers that request /favicon.ico
@app.route("/favicon.ico")
def favicon_root():
    try:
        return send_from_directory("static", "favicon.ico")
    except Exception:
        return ("", 204)


@app.route("/d_admin")
def d_admin_page():
    return send_from_directory("static", "d_admin.html")


# Serve development tools (e.g., arc_contract.js) to the client
@app.route("/tools/<path:filename>")
def serve_tools(filename):
    return send_from_directory("tools", filename)


# -- videos
@app.get("/api/videos")
def list_videos_api():
    return list_videos()


# ------------------------ coach routes --------------------------

# Voice presets: use global DATA_DIR/PRESET_FILE defined once at top

# Language names used in prompts/translations
LANG_NAMES = {
    "en-US": "English (United States)",
    "en-GB": "English (United Kingdom)",
    "en-AU": "English (Australia)",
    "es-ES": "Spanish (Spain)",
    "es-MX": "Spanish (Mexico)",
    "fr-FR": "French",
    "de-DE": "German",
    "pt-BR": "Portuguese (Brazil)",
    "it-IT": "Italian",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Chinese (Simplified)",
}


def _read_presets():
    if PRESET_FILE.exists():
        try:
            return json.loads(PRESET_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _write_presets(items):
    PRESET_FILE.write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )


@app.get("/api/voice_presets")
def get_voice_presets():
    return jsonify({"presets": _read_presets()})


@app.post("/api/voice_presets")
def upsert_voice_preset():
    b = request.get_json() or {}
    p = b.get("preset") or {}
    if not p.get("name"):
        return jsonify({"error": "missing preset.name"}), 400
    items = _read_presets()
    i = next((k for k, it in enumerate(items) if it.get("name") == p["name"]), -1)
    if i >= 0:
        items[i] = p
    else:
        items.append(p)
    _write_presets(items)
    return jsonify({"ok": True, "preset": p})


@app.delete("/api/voice_presets/<name>")
def delete_voice_preset(name):
    items = [it for it in _read_presets() if it.get("name") != name]
    _write_presets(items)
    return jsonify({"ok": True})


def translate_if_needed(text: str, lang_code: str) -> str:
    """
    OpenAI TTS infers language from text. If user selected a non-US English
    locale or a non-English language, convert/translate first so speech sounds right.
    """
    if not lang_code or lang_code in ("en", "en-US"):
        return text

    client = get_openai_client()
    target = LANG_NAMES.get(lang_code, lang_code)

    if lang_code.startswith("en-"):
        system = (
            f"Convert the user's text to {target} with appropriate spelling/phrasing. "
            "ONLY return the converted text."
        )
        user = text
    else:
        system = (
            f"Translate the user's text to {target}. Keep names/numbers. Natural for speech. "
            "ONLY return the translation."
        )
        user = text

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    out = (resp.choices[0].message.content or "").strip()
    return out or text


# Voices your server will accept (front-end should match these)
ALLOWED_VOICES = {"alloy", "verse", "amber", "aria", "coral", "sage", "vivid", "bright"}


@app.post("/api/tts")
def api_tts():
    try:
        b = request.get_json(force=True) or {}
        text = (b.get("text") or "").strip()
        voice = (b.get("voice") or "alloy").strip().lower()
        lang = (b.get("lang") or "en-US").strip()

        if not text:
            return jsonify({"error": "text is required"}), 400
        if voice not in ALLOWED_VOICES:
            voice = "alloy"

        speak_text = translate_if_needed(text, lang)

        # Use OpenAI TTS (model name must be valid)
        r = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "model": "gpt-4o-mini-tts",
                "voice": voice,
                "input": speak_text,
                "response_format": "mp3",  # <-- key addition for iOS
            },
            stream=True,
            timeout=60,
        )

        if r.status_code != 200:
            # Bubble API error details back to the client UI
            try:
                return jsonify(r.json()), r.status_code
            except Exception:
                return jsonify({"error": r.text}), r.status_code

        return Response(r.iter_content(8192), mimetype="audio/mpeg")

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/coach")
def api_coach():
    b = request.get_json(force=True) or {}
    prompt = (b.get("prompt") or "").strip()
    model = b.get("model") or "gpt-4o-mini"
    lang = b.get("lang") or "en-US"
    shot = b.get("shot")
    profile = b.get("profile")
    sid = (b.get("sid") or b.get("sessionId") or "").strip() or None
    shot_idx = b.get("shotId") if isinstance(b.get("shotId"), int) else None

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    client = get_openai_client()
    lang_hint = (
        "" if lang in ("en", "en-US") else f" Respond in {LANG_NAMES.get(lang, lang)}."
    )

    system = (
        "You are visaion, a concise basketball shooting coach. "
        "Be supportive and specific; give 1–3 concrete cues (e.g., 'elbow under ball', "
        "'hold follow-through', 'higher arc' , 'feet placement', 'snap wrist', 'release point'). Keep it under ~6 sentences."
        + lang_hint
    )
    msgs = [{"role": "system", "content": system}]
    if profile:
        msgs.append({"role": "system", "content": f"Player profile: {profile}"})
    if shot:
        msgs.append({"role": "system", "content": f"Shot data: {shot}"})
    msgs.append({"role": "user", "content": prompt})

    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=msgs, temperature=0.6)
    dt_ms = int((time.time() - t0) * 1000)
    text = (resp.choices[0].message.content or "").strip()

    def _extract_score(obj):
        if not isinstance(obj, dict):
            return None
        for key in ("poseScore", "weightedScore", "score"):
            val = obj.get(key)
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
        return None

    score_val = _extract_score(shot)

    # Persist feedback when DB is available; also mirror brief coach text into the shot's JSON data
    try:
        db = getattr(app, "db", None)
        if db:
            with db["Session"]() as sdb:
                FB = db.get("CoachFeedbackRow")
                if FB is not None:
                    row = FB(
                        sid=sid,
                        shot_idx=(shot_idx or 0),
                        provider="openai",
                        model=model,
                        latency_ms=dt_ms,
                        text=text,
                        score=score_val,
                    )
                    sdb.add(row)
                # Mirror summary onto the shot row's JSON for easier joins later
                try:
                    if sid and isinstance(shot_idx, int):
                        Shot = db["ShotRow"]
                        from sqlalchemy import select

                        sr = sdb.execute(
                            select(Shot).where(Shot.sid == sid, Shot.idx == shot_idx)
                        ).scalar_one_or_none()
                        if sr is None:
                            # create a minimal row if missing
                            sr = Shot(sid=sid, idx=shot_idx, data={})
                            sdb.add(sr)
                        try:
                            d = dict(sr.data or {})
                            d["coach_summary"] = text
                            if score_val is not None:
                                d["poseScore"] = score_val
                            sr.data = d
                        except Exception:
                            base = {"coach_summary": text}
                            if score_val is not None:
                                base["poseScore"] = score_val
                            sr.data = base
                except Exception as e2:
                    print("coach mirror to shot failed:", e2)
                sdb.commit()
    except Exception as e:
        print("db_add_ai_feedback:", e)

    return jsonify({"text": text, "latency_ms": dt_ms})


def _handle_ai_feedback_payload(data: dict, sid_override: str | None = None):
    data = data or {}
    sid = (
        sid_override
        or (data.get("sid") or data.get("sessionId") or "").strip()
        or None
    )
    shot_idx_raw = data.get("shot_idx")
    if shot_idx_raw is None:
        shot_idx_raw = data.get("shotIdx")
    if shot_idx_raw is None:
        shot_idx_raw = data.get("shotId")
        if (
            shot_idx_raw is not None
            and data.get("shot_idx") is None
            and data.get("shotIdx") is None
        ):
            try:
                shot_idx_raw = int(shot_idx_raw) - 1
            except Exception:
                pass
    if shot_idx_raw is None:
        shot_idx_raw = data.get("idx")
    text = (data.get("text") or "").strip()
    provider = data.get("provider") or "client-final"
    model = data.get("model") or "coach-final"
    latency_ms = int(data.get("latency_ms") or 0)
    score_val = None
    for key in ("score", "poseScore", "weightedScore"):
        if key in data and data[key] is not None:
            try:
                score_val = float(data[key])
            except (TypeError, ValueError):
                score_val = None
            break
    if not sid or text == "" or shot_idx_raw is None:
        return {"error": "sid, shot_idx, and text are required"}, 400
    try:
        shot_idx = int(shot_idx_raw)
    except (TypeError, ValueError):
        return {"error": "shot_idx must be an integer"}, 400
    db_saved = False
    db_error = None
    try:
        db = _db_get()
        if not db:
            db_error = "database unavailable"
        else:
            from sqlalchemy import select

            with db["Session"]() as sdb:
                FB = db.get("CoachFeedbackRow")
                if FB is not None:
                    row = (
                        sdb.execute(
                            select(FB)
                            .where(FB.sid == sid, FB.shot_idx == shot_idx)
                            .order_by(FB.id.desc())
                        )
                        .scalars()
                        .first()
                    )
                    if row:
                        row.text = text
                        row.provider = provider
                        if score_val is not None:
                            row.score = score_val
                        if model:
                            row.model = model
                        if latency_ms:
                            row.latency_ms = latency_ms
                    else:
                        sdb.add(
                            FB(
                                sid=sid,
                                shot_idx=shot_idx,
                                provider=provider,
                                model=model,
                                latency_ms=latency_ms,
                                text=text,
                                score=score_val,
                            )
                        )
                Shot = db.get("ShotRow")
                if Shot is not None:
                    sr = sdb.execute(
                        select(Shot).where(Shot.sid == sid, Shot.idx == shot_idx)
                    ).scalar_one_or_none()
                    if sr is None:
                        sr = Shot(sid=sid, idx=shot_idx, data={})
                        sdb.add(sr)
                    try:
                        payload = dict(sr.data or {})
                        payload["coach_summary"] = text
                        if score_val is not None:
                            payload["poseScore"] = score_val
                        sr.data = payload
                    except Exception:
                        base = {"coach_summary": text}
                        if score_val is not None:
                            base["poseScore"] = score_val
                        sr.data = base
                sdb.commit()
            db_saved = True
    except Exception as e:
        db_error = str(e)

    fs_saved = _persist_coach_feedback_fs(sid, shot_idx, text, score_val)
    if not db_saved and not fs_saved:
        return {"ok": False, "error": db_error or "unable to save"}, 500
    payload = {"ok": True, "stored": {"db": db_saved, "fs": fs_saved}}
    if db_error:
        payload["warning"] = db_error
    return payload, 200


@app.post("/api/coach/finalize")
def api_coach_finalize():
    data = request.get_json(force=True, silent=True) or {}
    payload, status = _handle_ai_feedback_payload(data)
    return jsonify(payload), status


@app.post("/api/ai_feedback")
def api_ai_feedback():
    data = request.get_json(force=True, silent=True) or {}
    payload, status = _handle_ai_feedback_payload(data)
    return jsonify(payload), status


@app.post("/api/sessions/<sid>/ai_feedback")
def api_session_ai_feedback(sid):
    data = request.get_json(force=True, silent=True) or {}
    payload, status = _handle_ai_feedback_payload(data, sid_override=sid)
    return jsonify(payload), status


# -----------app routes --------------
@app.route("/frames/<video_name>/<frame_file>")
def serve_frame(video_name, frame_file):
    dataset = _resolve_dataset(request.args.get("dataset"))
    folder_abs = _dataset_abs_path(dataset, "frameCacheRoot", video_name)
    if not folder_abs or not os.path.exists(folder_abs):
        return abort(404)
    return send_from_directory(folder_abs, frame_file)


@app.route("/test_openai")
def test_openai():
    try:
        get_openai_client().models.list()
        return jsonify({"status": "✅ OpenAI client working"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/list_frames/<video_name>")
def list_frames(video_name):
    dataset = _resolve_dataset(request.args.get("dataset"))
    folder_path = _dataset_abs_path(dataset, "frameCacheRoot", video_name)
    if not folder_path or not os.path.exists(folder_path):
        _trace(
            "[list_frames]",
            "missing folder",
            "dataset=",
            dataset.get("slug"),
            "folder=",
            folder_path,
        )
        return jsonify({"error": "Folder not found"}), 404

    frames = [
        f
        for f in os.listdir(folder_path)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    frames.sort()
    _trace(
        "[list_frames]",
        "dataset=",
        dataset.get("slug"),
        "folder=",
        folder_path,
        "count=",
        len(frames),
    )
    return jsonify({"frames": frames, "dataset": dataset.get("slug")})


# ---------------------- Admin Debug Views ----------------------


@app.get("/admin/community/posts")
def admin_community_posts():
    posts = _load_community_feed()
    return jsonify({"posts": posts})


@app.get("/admin/community/session/<sid>")
def admin_community_session_detail(sid):
    detail_path = _community_detail_path(sid)
    detail = None
    detail_from_db = False
    db = _db_get()
    if db:
        detail = _db_get_community_detail(sid)
        detail_from_db = isinstance(detail, dict)
        if not detail_from_db and os.path.exists(detail_path):
            try:
                with open(detail_path, "r", encoding="utf-8") as f:
                    detail = json.load(f)
            except Exception:
                detail = None
            if isinstance(detail, dict):
                try:
                    posts = _load_community_feed()
                    _, post = _find_community_post(posts, sid)
                    if post:
                        _db_upsert_community_post(sid, post, detail)
                except Exception:
                    pass
    else:
        if os.path.exists(detail_path):
            with open(detail_path, "r", encoding="utf-8") as f:
                detail = json.load(f)
    if not isinstance(detail, dict):
        return jsonify({"error": "session not published"}), 404
    if _ensure_shot_clip_urls(sid, detail.get("shots") or []):
        if not db:
            try:
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(detail, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                _trace("community:detail update failed", exc)
        if db:
            try:
                posts = _load_community_feed()
                _, post = _find_community_post(posts, sid)
                if post:
                    _db_upsert_community_post(sid, post, detail)
            except Exception:
                pass
    elif detail_from_db and db:
        try:
            posts = _load_community_feed()
            _, post = _find_community_post(posts, sid)
            if post:
                _db_upsert_community_post(sid, post, detail)
        except Exception:
            pass
    return jsonify(detail)


@app.get("/admin/community/preview/<sid>.jpg")
def admin_community_preview(sid):
    preview_path = _ensure_preview_image(sid)
    if not preview_path or not os.path.exists(preview_path):
        abort(404)
    return send_file(preview_path, mimetype="image/jpeg")


@app.patch("/admin/community/posts/<sid>")
def admin_community_post_update(sid):
    payload = request.get_json(silent=True) or {}
    posts = _load_community_feed()
    idx, post = _find_community_post(posts, sid)
    if post is None:
        return jsonify({"error": "post not found"}), 404
    db = _db_get()

    if payload.get("delete") is True or (payload.get("action") or "").lower() == "delete":
        posts.pop(idx)
        _write_community_feed(posts)
        detail_path = _community_detail_path(sid)
        try:
            if os.path.exists(detail_path):
                os.remove(detail_path)
        except OSError:
            pass
        try:
            _db_delete_community_post(sid)
        except Exception:
            pass
        return jsonify({"ok": True, "deleted": True})

    if "hidden" in payload:
        hide_flag = bool(payload.get("hidden"))
        updated = dict(post)
        if hide_flag:
            updated["hidden"] = True
            updated["hiddenAt"] = int(time.time() * 1000)
            updated["hiddenBy"] = str(
                payload.get("by") or session.get("user_id") or "admin"
            )
            reason = (payload.get("reason") or "").strip()
            if reason:
                updated["hiddenReason"] = reason
        else:
            updated.pop("hidden", None)
            updated.pop("hiddenAt", None)
            updated.pop("hiddenBy", None)
            updated.pop("hiddenReason", None)
        posts[idx] = updated
        _write_community_feed(posts)

        detail_path = _community_detail_path(sid)
        detail = None
        if db:
            detail = _db_get_community_detail(sid)
            if not isinstance(detail, dict) and os.path.exists(detail_path):
                try:
                    with open(detail_path, "r", encoding="utf-8") as f:
                        detail = json.load(f)
                except Exception:
                    detail = None
        else:
            if os.path.exists(detail_path):
                try:
                    with open(detail_path, "r", encoding="utf-8") as f:
                        detail = json.load(f)
                except Exception:
                    detail = None
        if isinstance(detail, dict):
            if hide_flag:
                detail["hidden"] = True
                detail["hiddenAt"] = updated.get("hiddenAt")
                detail["hiddenBy"] = updated.get("hiddenBy")
                if updated.get("hiddenReason"):
                    detail["hiddenReason"] = updated.get("hiddenReason")
            else:
                detail.pop("hidden", None)
                detail.pop("hiddenAt", None)
                detail.pop("hiddenBy", None)
                detail.pop("hiddenReason", None)
            if not db:
                try:
                    with open(detail_path, "w", encoding="utf-8") as f:
                        json.dump(detail, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            if db:
                try:
                    _db_upsert_community_post(sid, updated, detail)
                except Exception:
                    pass
        else:
            if db:
                try:
                    _db_upsert_community_summary([updated])
                except Exception:
                    pass

        return jsonify({"ok": True, "post": updated})

    return jsonify({"error": "no updates"}), 400


@app.get("/admin/sessions")
def admin_sessions():
    """List sessions with basic stats and user info when available."""
    items = []
    try:
        db = _db_get()
        if db:
            _maybe_backfill_db_sessions_from_fs()
            from sqlalchemy import select

            with db["Session"]() as s:
                rows = s.execute(select(db["SessionRow"])).scalars().all()
                for r in rows:
                    last_shot_dt = s.execute(
                        select(db["ShotRow"].created_at)
                        .where(db["ShotRow"].sid == r.sid)
                        .order_by(db["ShotRow"].created_at.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    items.append(
                        {
                            "sid": r.sid,
                            "created_at": r.created_at.isoformat()
                            if r.created_at
                            else None,
                            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                            "updated_at": r.updated_at.isoformat()
                            if getattr(r, "updated_at", None)
                            else None,
                            "last_shot_at": last_shot_dt.isoformat()
                            if last_shot_dt
                            else None,
                            "shots": r.shots_count,
                            "makes": r.makes,
                            "accuracy": r.accuracy,
                            "user": (
                                {
                                    "user_id": r.user.user_id,
                                    "name": r.user.name,
                                    "email": r.user.email,
                                }
                                if r.user
                                else None
                            ),
                        }
                    )
        else:
            fs_summaries = _load_fs_session_summaries()
            for sid, fs in fs_summaries.items():
                user_id = fs.get("user_id")
                items.append(
                    {
                        "sid": sid,
                        "created_at": fs.get("startedAt"),
                        "ended_at": fs.get("endedAt"),
                        "updated_at": None,
                        "last_shot_at": fs.get("last_shot_at"),
                        "shots": fs.get("shots"),
                        "makes": fs.get("makes"),
                        "accuracy": fs.get("accuracy"),
                        "user": {"user_id": user_id} if user_id else None,
                    }
                )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"sessions": items})


@app.post("/api/sessions/<sid>/observer_frame")
def api_observer_frame(sid):
    """Accept low-FPS JPEG overlay snapshots from a client to aid real-time observing."""
    try:
        img_bytes = None
        raw_state = None
        if "image" in request.files:
            f = request.files["image"]
            img_bytes = f.read()
            raw_state = request.form.get("state")
        else:
            b = request.get_json(silent=True) or {}
            data_url = b.get("image") or ""
            if data_url.startswith("data:image") and ";base64," in data_url:
                img_bytes = base64.b64decode(data_url.split(",")[1])
            raw_state = b.get("state")
        if not img_bytes:
            return jsonify({"error": "no image"}), 400
        state_payload = None
        if raw_state:
            try:
                if isinstance(raw_state, (bytes, bytearray)):
                    raw_state = raw_state.decode("utf-8", "ignore")
                if isinstance(raw_state, str):
                    state_payload = json.loads(raw_state)
                elif isinstance(raw_state, dict):
                    state_payload = raw_state
            except Exception:
                state_payload = {"__parse_error": True}
        rec = {"ts": int(time.time() * 1000), "bytes": img_bytes}
        if state_payload is not None:
            rec["state"] = state_payload
        app.observer_frames[sid] = rec
        try:
            d = _session_path(sid)
            with open(os.path.join(d, "observer_latest.jpg"), "wb") as f:
                f.write(img_bytes)
            if state_payload is not None:
                with open(
                    os.path.join(d, "observer_latest.json"), "w", encoding="utf-8"
                ) as fjson:
                    json.dump(
                        {"ts": rec["ts"], "state": state_payload},
                        fjson,
                        ensure_ascii=False,
                    )
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/admin/observe/<sid>.jpg")
def admin_observe_jpg(sid):
    rec = app.observer_frames.get(sid)
    if rec and rec.get("bytes"):
        return Response(rec["bytes"], mimetype="image/jpeg")
    try:
        p = os.path.join(_session_path(sid), "observer_latest.jpg")
        if os.path.exists(p):
            return send_file(p, mimetype="image/jpeg")
    except Exception:
        pass
    return jsonify({"error": "no observer frame"}), 404


@app.get("/admin/observe/<sid>.json")
def admin_observe_json(sid):
    """Expose the latest observer snapshot so /d_admin Observe can mirror backend state."""
    rec = app.observer_frames.get(sid)
    if rec and rec.get("state") is not None:
        return jsonify({"ok": True, "ts": rec.get("ts"), "state": rec.get("state")})
    try:
        p = os.path.join(_session_path(sid), "observer_latest.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("ok", True)
                return jsonify(data)
    except Exception:
        pass
    return jsonify({"ok": False, "error": "no observer state"}), 404


@app.get("/admin/observe/<sid>")
def admin_observe_page(sid):
    stamp = int(time.time() * 1000)
    template = """<!doctype html>
<title>Observe __SID__</title>
<style>
  :root { color-scheme: dark; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }
  html, body { height:100%; }
  body { margin:0; background:#05080f; color:#e6edf3; font:14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; overflow:hidden; }
  .layout { display:flex; flex-wrap:nowrap; gap:16px; padding:16px; height:100%; box-sizing:border-box; }
  .pane, .panel { background:#0b101a; border:1px solid #1b2735; border-radius:14px; box-shadow:0 14px 32px rgba(0,0,0,0.35); overflow:hidden; box-sizing:border-box; min-height:0; }
  .pane { flex:2 1 0%; padding:12px; display:flex; flex-direction:column; }
  #pic { width:100%; height:100%; flex:1 1 auto; object-fit:contain; background:#000; border-radius:12px; }
  .panel { flex:1.2 1 0%; padding:12px; display:flex; flex-direction:column; gap:12px; overflow:hidden; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:8px; overflow-y:auto; max-height:200px; min-height:0; }
  .stat { background:#111b29; border:1px solid #1f2a3a; border-radius:10px; padding:8px; }
  .stat span { display:block; font-size:12px; opacity:.7; margin-bottom:2px; }
  #viz { width:100%; flex:1 1 auto; min-height:220px; background:#03070e; border-radius:10px; border:1px solid #182231; }
  .event-log { flex:0 0 170px; background:#080d16; border:1px solid #1f2a3a; border-radius:10px; overflow:auto; font-size:12px; padding:6px 8px; line-height:1.35; }
  .event-log .event { border-bottom:1px solid rgba(255,255,255,0.08); padding:4px 0; }
  .event-log .event:last-child { border-bottom:none; }
  pre { flex:1 1 0%; min-height:0; background:#080d16; border:1px solid #1f2a3a; border-radius:10px; padding:10px; overflow:auto; font-size:12px; }
  @media (max-width: 1100px) {
    body { overflow:auto; }
    .layout { flex-direction:column; height:auto; }
    .pane, .panel { height:auto; }
    .stats { max-height:none; }
    #viz { min-height:200px; }
    .event-log { flex:0 0 auto; max-height:220px; }
  }
</style>
<div class="layout">
  <div class="pane">
    <img id="pic" src="/admin/observe/__SID__.jpg?ts=__STAMP__" alt="observer feed"/>
  </div>
  <div class="panel">
    <div class="stats">
      <div class="stat"><span>frame</span><strong id="statFrame">&ndash;</strong></div>
      <div class="stat"><span>release</span><strong id="statRelease">&ndash;</strong></div>
      <div class="stat"><span>enter</span><strong id="statEnter">&ndash;</strong></div>
      <div class="stat"><span>exit</span><strong id="statExit">&ndash;</strong></div>
      <div class="stat"><span>trail pts</span><strong id="statTrail">0</strong></div>
      <div class="stat"><span>arc pts</span><strong id="statArc">0</strong></div>
      <div class="stat"><span>refined pts</span><strong id="statArcRef">0</strong></div>
      <div class="stat"><span>objects</span><strong id="statObjects">0</strong></div>
      <div class="stat"><span>bs state</span><strong id="statState">&ndash;</strong></div>
      <div class="stat"><span>shots</span><strong id="statShots">0</strong></div>
      <div class="stat"><span>last shot</span><strong id="statLast">&ndash;</strong></div>
      <div class="stat"><span>gate score</span><strong id="statGateScore">&ndash;</strong></div>
      <div class="stat"><span>gate reason</span><strong id="statGateReason">&ndash;</strong></div>
      <div class="stat"><span>gate side</span><strong id="statGateSide">&ndash;</strong></div>
      <div class="stat"><span>pose streak</span><strong id="statPoseStreak">0</strong></div>
      <div class="stat"><span>an frame</span><strong id="statAnalyzer">&ndash;</strong></div>
      <div class="stat"><span>seq</span><strong id="statSeq">0</strong></div>
      <div class="stat"><span>detect</span><strong id="statDetect">&ndash;</strong></div>
      <div class="stat"><span>overlay</span><strong id="statOverlay">&ndash;</strong></div>
    </div>
    <canvas id="viz" width="520" height="280"></canvas>
    <div id="eventLog" class="event-log">(no events yet)</div>
    <pre id="statePre">(no state yet)</pre>
  </div>
</div>
<script>
const img = document.getElementById('pic');
setInterval(() => { img.src = `/admin/observe/__SID__.jpg?ts=${Date.now()}`; }, 500);
const dash = '—';
const statFrame = document.getElementById('statFrame');
const statRelease = document.getElementById('statRelease');
const statEnter = document.getElementById('statEnter');
const statExit = document.getElementById('statExit');
const statTrail = document.getElementById('statTrail');
const statArc = document.getElementById('statArc');
const statArcRef = document.getElementById('statArcRef');
const statObjects = document.getElementById('statObjects');
const statState = document.getElementById('statState');
const statShots = document.getElementById('statShots');
const statLast = document.getElementById('statLast');
const statGateScore = document.getElementById('statGateScore');
const statGateReason = document.getElementById('statGateReason');
const statGateSide = document.getElementById('statGateSide');
const statPoseStreak = document.getElementById('statPoseStreak');
const statAnalyzer = document.getElementById('statAnalyzer');
const statSeq = document.getElementById('statSeq');
const statDetect = document.getElementById('statDetect');
const statOverlay = document.getElementById('statOverlay');
const pre = document.getElementById('statePre');
const eventLogEl = document.getElementById('eventLog');
const canvas = document.getElementById('viz');
const ctx = canvas.getContext('2d');
function esc(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function renderEvents(events) {
  if (!eventLogEl) return;
  if (!Array.isArray(events) || !events.length) {
    eventLogEl.innerHTML = '<div class="event" style="opacity:0.6">no events</div>';
    return;
  }
  const html = events.slice(-20).reverse().map((entry) => {
    const ts = entry?.ts ? new Date(entry.ts).toLocaleTimeString() : '';
    const frame = entry?.frame != null ? `f${entry.frame}` : '';
    let detail = '';
    if (entry?.detail != null) {
      try { detail = esc(JSON.stringify(entry.detail)); } catch { detail = esc(String(entry.detail)); }
    }
    return `<div class="event"><div><strong>${esc(entry?.type || 'event')}</strong> ${esc(ts)} ${esc(frame)}</div><div style="opacity:0.75">${detail}</div></div>`;
  }).join('');
  eventLogEl.innerHTML = html;
}
function drawState(state) {
  const vw = state?.view?.vw || state?.bg?.width || 1280;
  const vh = state?.view?.vh || state?.bg?.height || 720;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#050a12';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const scale = Math.min(canvas.width / vw, canvas.height / vh);
  const offX = (canvas.width - vw * scale) / 2;
  const offY = (canvas.height - vh * scale) / 2;
  ctx.save();
  ctx.translate(offX, offY);
  ctx.scale(scale, scale);
  const invScale = scale === 0 ? 0 : (1 / scale);
  const strokePX = (value) => Math.max(value * invScale, value <= 1 ? 1 : value);
  const baseStroke = 2.2;
  if (state?.proxRect) {
    const p = state.proxRect;
    if (Number.isFinite(p?.x) && Number.isFinite(p?.y) && Number.isFinite(p?.w) && Number.isFinite(p?.h)) {
      ctx.strokeStyle = '#21d4ff';
      ctx.lineWidth = strokePX(baseStroke);
      ctx.strokeRect(p.x, p.y, p.w, p.h);
    }
  }
  const hoop = state?.hoopCanon || state?.hoop;
  if (hoop) {
    const hx = hoop.x ?? (hoop.cx - (hoop.w || 0) / 2);
    const hy = hoop.y ?? (hoop.cy - (hoop.h || 0) / 2);
    const hw = hoop.w ?? ((hoop.x2 ?? 0) - (hoop.x1 ?? 0));
    const hh = hoop.h ?? ((hoop.y2 ?? 0) - (hoop.y1 ?? 0));
    if (Number.isFinite(hx) && Number.isFinite(hy) && Number.isFinite(hw) && Number.isFinite(hh)) {
      ctx.strokeStyle = '#ffb347';
      ctx.lineWidth = strokePX(baseStroke);
      ctx.strokeRect(hx, hy, hw, hh);
    }
  }
  let arcRef = Array.isArray(state?.ballArc?.refinedTrail) ? state.ballArc.refinedTrail : [];
  let arcRaw = Array.isArray(state?.ballArc?.trail) ? state.ballArc.trail : [];
  const fallbackTrail = Array.isArray(state?.ballState?.trail) ? state.ballState.trail : [];
  if (!arcRaw.length && Array.isArray(state?.shots)) {
    for (let i = state.shots.length - 1; i >= 0; i -= 1) {
      const shot = state.shots[i];
      if (Array.isArray(shot?.trail) && shot.trail.length) { arcRaw = shot.trail; break; }
    }
  }
  if (!arcRef.length) {
    if (Array.isArray(state?.lastSummary?.trail) && state.lastSummary.trail.length) {
      arcRef = state.lastSummary.trail;
    } else if (!arcRaw.length && Array.isArray(state?.ballState?.frozenShots)) {
      for (let i = state.ballState.frozenShots.length - 1; i >= 0; i -= 1) {
        const shot = state.ballState.frozenShots[i];
        if (Array.isArray(shot?.trail) && shot.trail.length) { arcRef = shot.trail; break; }
      }
    }
  }
  if (!arcRaw.length) arcRaw = fallbackTrail;
  if (!arcRef.length) arcRef = fallbackTrail;
  const drawPolyline = (points, color) => {
    if (!points.length) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = strokePX(baseStroke);
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i].x, points[i].y);
    ctx.stroke();
    const last = points[points.length - 1];
    if (Number.isFinite(last?.x) && Number.isFinite(last?.y)) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(last.x, last.y, Math.max(3 * invScale, 2), 0, Math.PI * 2);
      ctx.fill();
    }
  };
  drawPolyline(arcRaw, 'rgba(20,220,255,0.55)');
  drawPolyline(arcRef, 'rgba(255,220,80,0.85)');
  const trail = Array.isArray(state?.ballState?.trail) ? state.ballState.trail : [];
  if (trail.length) {
    ctx.strokeStyle = 'rgba(255,80,120,0.7)';
    ctx.lineWidth = strokePX(baseStroke);
    ctx.beginPath();
    ctx.moveTo(trail[0].x, trail[0].y);
    for (let i = 1; i < trail.length; i++) ctx.lineTo(trail[i].x, trail[i].y);
    ctx.stroke();
  }
  const objects = Array.isArray(state?.objects) ? state.objects : [];
  if (objects.length) {
    const fontPx = Math.max(11 / scale, 10);
    ctx.font = `${fontPx}px system-ui`;
    objects.forEach((obj) => {
      const box = Array.isArray(obj?.box) ? obj.box : null;
      if (!box || box.length < 4) return;
      let x = Number(box[0]) || 0;
      let y = Number(box[1]) || 0;
      let w = 0;
      let h = 0;
      if (box.length === 4) {
        const x2 = Number(box[2]);
        const y2 = Number(box[3]);
        if (Number.isFinite(x2) && Number.isFinite(y2)) {
          if (x2 > x && y2 > y) { w = x2 - x; h = y2 - y; }
          else { w = x2; h = y2; }
        }
      } else {
        const x2 = Number(box[2]);
        const y2 = Number(box[3]);
        const wCandidate = Number(box[4]);
        const hCandidate = Number(box[5]);
        if (Number.isFinite(wCandidate) && Number.isFinite(hCandidate) && wCandidate > 0 && hCandidate > 0) {
          w = wCandidate;
          h = hCandidate;
        } else if (Number.isFinite(x2) && Number.isFinite(y2)) {
          w = x2 - x;
          h = y2 - y;
        }
      }
      if (w <= 0 || h <= 0) return;
      const label = obj?.label || 'object';
      const colorMap = { ball: '#ff6b6b', hoop: '#ffd166', net: '#ffd166', player: '#4fd1c5' };
      ctx.strokeStyle = colorMap[label] || '#9f7aea';
      ctx.lineWidth = strokePX(baseStroke);
      ctx.strokeRect(x, y, w, h);
      const text = label.toUpperCase();
      const pad = 4 * invScale;
      const textWidth = ctx.measureText(text).width + pad * 2;
      const boxHeight = (fontPx + 6) * invScale;
      ctx.fillStyle = 'rgba(5,12,22,0.75)';
      ctx.fillRect(x, y - boxHeight, Math.max(textWidth, 44 * invScale), boxHeight);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(text, x + pad, y - (boxHeight / 3));
    });
  }
  ctx.restore();
}
async function poll() {
  try {
    const res = await fetch(`/admin/observe/__SID__.json?ts=${Date.now()}`);
    if (!res.ok) throw new Error('no state');
    const data = await res.json();
    const state = data.state || null;
    if (!state) return;
    statFrame.textContent = state.frame ?? dash;
    statRelease.textContent = state.ballState?.releaseFrame ?? dash;
    statEnter.textContent = state.ballState?.proxEnterFrame ?? dash;
    statExit.textContent = state.ballState?.proxExitFrame ?? dash;
    statTrail.textContent = state.ballStateTrailLen ?? (state.ballState?.trail?.length ?? 0);
    statArc.textContent = state.ballArcTrailLen ?? (state.ballArc?.trail?.length ?? 0);
    statArcRef.textContent = state.ballArcRefLen ?? (state.ballArc?.refinedTrail?.length ?? 0);
    statObjects.textContent = Array.isArray(state.objects) ? state.objects.length : 0;
    statState.textContent = state.ballState?.state || dash;
    statShots.textContent = state.shotCount ?? state.ballState?.shots ?? 0;
    const last = state.lastSummary || (Array.isArray(state.shots) && state.shots.length ? state.shots[state.shots.length - 1] : null);
    statLast.textContent = last ? (last.made === true ? 'make' : (last.made === false ? 'miss' : 'pending')) : dash;
    if (statGateScore) {
      const score = state.gate?.score;
      statGateScore.textContent = Number.isFinite(score) ? Number(score).toFixed(3) : dash;
    }
    if (statGateReason) statGateReason.textContent = state.gate?.reason || dash;
    if (statGateSide) statGateSide.textContent = state.gate?.side || dash;
    if (statPoseStreak) statPoseStreak.textContent = state.pose?.streak ?? 0;
    if (statAnalyzer) statAnalyzer.textContent = state.analyzer?.frame ?? dash;
    if (statSeq) statSeq.textContent = state.seq ?? dash;
    statDetect.textContent = state.detectSource ?? dash;
    statOverlay.textContent = state.overlayMode ?? dash;
    renderEvents(state.events);
    pre.textContent = JSON.stringify(state, null, 2);
    drawState(state);
  } catch (err) {
    console.warn('observe poll failed', err);
  }
}
setInterval(poll, 750);
poll();
</script>"""
    html = template.replace("__SID__", sid).replace("__STAMP__", str(stamp))
    return Response(html, mimetype="text/html")


@app.get("/admin/session/<sid>/debug")
def admin_session_debug(sid):
    """Return joined view: session.json + DB shots + pose_snapshots + ai_feedback + files"""
    out = {
        "sid": sid,
        "sessionFile": None,
        "user": None,
        "shotsDB": [],
        "snapshots": [],
        "feedback": [],
        "files": [],
    }
    # session.json
    try:
        sdat = _read_session(sid)
        out["sessionFile"] = sdat
    except Exception:
        pass
    # files in session dir
    try:
        d = _session_path(sid)
        files = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
        out["files"] = sorted(files)
    except Exception:
        pass
    # DB parts
    try:
        db = _db_get()
        if db:
            from sqlalchemy import select

            with db["Session"]() as s:
                # user from SessionRow
                sess = s.get(db["SessionRow"], sid)
                if sess and sess.user:
                    out["user"] = {
                        "user_id": sess.user.user_id,
                        "name": sess.user.name,
                        "email": sess.user.email,
                    }
                # shots
                ShotRow = db["ShotRow"]
                rows = (
                    s.execute(
                        select(ShotRow)
                        .where(ShotRow.sid == sid)
                        .order_by(ShotRow.idx.asc())
                    )
                    .scalars()
                    .all()
                )
                out["shotsDB"] = []
                for r in rows:
                    data = r.data if isinstance(r.data, dict) else {}
                    override = (
                        data.get("adminOverride") if isinstance(data, dict) else None
                    )
                    arcmm_payload = (
                        data.get("arcmm") if isinstance(data, dict) else None
                    )
                    out["shotsDB"].append(
                        {
                            "idx": r.idx,
                            "made": r.made,
                            "entryAngle": r.entry_angle,
                            "releaseAngle": r.release_angle,
                            "arcHeight": r.arc_height,
                            "missReason": r.miss_reason,
                            "created_at": r.created_at.isoformat()
                            if r.created_at
                            else None,
                            "adminOverride": override,
                            "poseScore": getattr(r, "pose_score", None),
                            "arcmm": arcmm_payload,
                        }
                    )
                # pose snapshots
                PS = db.get("PoseSnapshotRow")
                if PS:
                    snaps = (
                        s.execute(select(PS).where(PS.sid == sid).order_by(PS.id.asc()))
                        .scalars()
                        .all()
                    )
                    out["snapshots"] = [
                        {
                            "id": r.id,
                            "shot_idx": r.shot_idx,
                            "frame": r.frame,
                            "t_ms": r.t_ms,
                            "via": r.via,
                            "metrics": r.metrics,
                            "hoop": r.hoop,
                            "gate": r.gate,
                            "created_at": r.created_at.isoformat()
                            if r.created_at
                            else None,
                        }
                        for r in snaps
                    ]
                # ai feedback
                FB = db.get("CoachFeedbackRow")
                if FB:
                    fbs = (
                        s.execute(select(FB).where(FB.sid == sid).order_by(FB.id.asc()))
                        .scalars()
                        .all()
                    )
                    out["feedback"] = [
                        {
                            "id": r.id,
                            "shot_idx": r.shot_idx,
                            "provider": r.provider,
                            "model": r.model,
                            "latency_ms": r.latency_ms,
                            "text": r.text,
                            "score": r.score,
                            "created_at": r.created_at.isoformat()
                            if r.created_at
                            else None,
                        }
                        for r in fbs
                    ]
    except Exception as e:
        return jsonify({"error": str(e), "partial": out}), 500
    # Opportunistic backfill when DB rows are missing but session.json has shots
    try:
        sf = out.get("sessionFile") or {}
        if sf and isinstance(sf.get("shots"), list):
            if len(out.get("shotsDB") or []) < len(sf["shots"]):
                _db_backfill_session_shots(sid)
                # refresh minimal DB rows after backfill
                db = _db_get()
                if db:
                    from sqlalchemy import select

                    with db["Session"]() as s:
                        ShotRow = db["ShotRow"]
                        rows = (
                            s.execute(
                                select(ShotRow)
                                .where(ShotRow.sid == sid)
                                .order_by(ShotRow.idx.asc())
                            )
                            .scalars()
                            .all()
                        )
                        out["shotsDB"] = []
                        for r in rows:
                            data = r.data if isinstance(r.data, dict) else {}
                            override = (
                                data.get("adminOverride")
                                if isinstance(data, dict)
                                else None
                            )
                            arcmm_payload = (
                                data.get("arcmm") if isinstance(data, dict) else None
                            )
                            out["shotsDB"].append(
                                {
                                    "idx": r.idx,
                                    "made": r.made,
                                    "entryAngle": r.entry_angle,
                                    "releaseAngle": r.release_angle,
                                    "arcHeight": r.arc_height,
                                    "missReason": r.miss_reason,
                                    "created_at": r.created_at.isoformat()
                                    if r.created_at
                                    else None,
                                    "adminOverride": override,
                                    "poseScore": getattr(r, "pose_score", None),
                                    "arcmm": arcmm_payload,
                                }
                            )
    except Exception:
        pass
    # Fallback: read JSONL snapshots from filesystem if DB empty
    try:
        if not out["snapshots"]:
            snaps = []
            # session-scoped JSONL
            p1 = os.path.join(_session_path(sid), "releases.jsonl")
            # global JSONL
            p2 = os.path.join(SESSIONS_DIR, "releases.jsonl")
            for p in (p1, p2):
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        for ln in f:
                            ln = ln.strip()
                            if not ln:
                                continue
                            try:
                                j = json.loads(ln)
                                if j.get("sessionId") and j.get("sessionId") != sid:
                                    continue
                                snaps.append(
                                    {
                                        "frame": j.get("frame"),
                                        "t_ms": j.get("tMs") or j.get("t"),
                                        "via": j.get("via"),
                                        "metrics": j.get("poseSnapshot"),
                                        "hoop": j.get("hoop"),
                                        "gate": j.get("gate"),
                                    }
                                )
                            except Exception:
                                continue
            if snaps:
                out["snapshots"] = snaps
    except Exception as e:
        _trace("admin_session_debug: backfill error:", e)
    session_shots = 0
    try:
        session_file = out.get("sessionFile")
        if isinstance(session_file, dict):
            session_shots = len(session_file.get("shots") or [])
    except Exception:
        session_shots = 0
    _trace(
        "admin_session_debug",
        {
            "sid": sid,
            "sessionFile": session_shots,
            "shotsDB": len(out.get("shotsDB") or []),
            "feedback": len(out.get("feedback") or []),
        },
    )
    return jsonify(out)


@app.get("/admin/session/<sid>/clips")
def admin_session_clips(sid):
    """List saved microclips for a session."""
    clips_dir = Path(_session_path(sid)) / "clips"
    if not clips_dir.exists():
        return jsonify({"sid": sid, "count": 0, "clips": []})

    def _coerce_bool(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value >= 1
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "made", "make", "y", "yes"}:
                return True
            if v in {"0", "false", "miss", "no", "n"}:
                return False
        return None

    arcmm_meta: dict[int, dict] = {}
    try:
        db = _db_get()
        if db:
            from sqlalchemy import select

            with db["Session"]() as s:
                ShotRow = db["ShotRow"]
                rows = (
                    s.execute(select(ShotRow).where(ShotRow.sid == sid)).scalars().all()
                )
                for row in rows:
                    try:
                        idx = int(row.idx)
                    except Exception:
                        continue
                    data = row.data if isinstance(row.data, dict) else {}
                    arcmm = data.get("arcmm") if isinstance(data, dict) else None
                    meta = {}
                    if isinstance(arcmm, dict):
                        meta = {
                            "status": arcmm.get("status"),
                            "message": arcmm.get("message"),
                            "updated_at": arcmm.get("updated_at"),
                            "processed_clip": arcmm.get("processed_clip"),
                            "summary": arcmm.get("summary"),
                        }
                    else:
                        meta = {}
                    # fall back to DB columns when summary missing
                    if getattr(row, "made", None) is not None:
                        meta.setdefault("made", bool(row.made))
                        meta.setdefault("result", bool(row.made))
                    if getattr(row, "entry_angle", None) is not None:
                        meta.setdefault("entryAngle", row.entry_angle)
                    if getattr(row, "release_angle", None) is not None:
                        meta.setdefault("releaseAngle", row.release_angle)
                    if getattr(row, "arc_height", None) is not None:
                        meta.setdefault("arcHeight", row.arc_height)
                    if getattr(row, "pose_score", None) is not None:
                        meta.setdefault("poseScore", row.pose_score)
                    if isinstance(data, dict):
                        data_made = _coerce_bool(data.get("made"))
                        if data_made is not None:
                            meta["made"] = data_made
                            meta["result"] = data_made
                        override = data.get("adminOverride")
                        if isinstance(override, dict):
                            meta["adminOverride"] = override
                        score_val = data.get("poseScore")
                        try:
                            if score_val is not None:
                                meta["poseScore"] = float(score_val)
                        except Exception:
                            pass
                    arcmm_meta[idx] = meta
    except Exception as exc:
        _trace("admin_session_clips: arcmm meta error", {"sid": sid, "error": str(exc)})

    items = []
    try:
        for clip_path in clips_dir.glob("*"):
            if not clip_path.is_file():
                continue
            info = {
                "name": clip_path.name,
                "url": f"/sessions/{sid}/clips/{clip_path.name}",
            }
            try:
                stat = clip_path.stat()
                info["size"] = stat.st_size
                info["created"] = datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat()
                info["_mtime"] = stat.st_mtime
            except Exception:
                info["size"] = None
                info["created"] = None
            idx = None
            match = re.search(r"shot[-_]?(\d+)", clip_path.name, re.IGNORECASE)
            if match:
                try:
                    idx = int(match.group(1))
                    info["shotIdx"] = idx
                except Exception:
                    idx = None
            if idx is not None:
                arcmm = arcmm_meta.get(idx) or arcmm_meta.get(
                    idx if idx >= 0 else idx + 1
                )
                if arcmm:
                    info["arcmm"] = arcmm
                    if "poseScore" in arcmm:
                        info["poseScore"] = arcmm["poseScore"]
                    processed_clip = arcmm.get("processed_clip")
                    if processed_clip:
                        info["processedUrl"] = processed_clip
                    summary = arcmm.get("summary")
                    if isinstance(summary, dict):
                        info["summary"] = summary
                    if "result" not in info and "result" in arcmm:
                        info["result"] = arcmm["result"]
                    if "adminOverride" in arcmm:
                        info["adminOverride"] = arcmm["adminOverride"]
            items.append(info)
    except Exception as exc:
        _trace("admin_session_clips error", {"sid": sid, "error": str(exc)})
        return jsonify({"sid": sid, "count": 0, "clips": []})
    items.sort(key=lambda x: x.get("_mtime") or 0, reverse=True)
    for clip in items:
        clip.pop("_mtime", None)
    return jsonify({"sid": sid, "count": len(items), "clips": items})


@app.route("/admin/session/<path:sid>/shot/<int:idx>/result", methods=["POST"])
def admin_override_shot_result(sid, idx):
    """Allow an admin to override the make/miss call for a shot."""
    payload = request.get_json(force=True, silent=True) or {}
    if "made" not in payload:
        return jsonify({"error": "made flag required"}), 400

    def _coerce_bool(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value >= 1
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "made", "make", "y", "yes"}:
                return True
            if v in {"0", "false", "miss", "no", "n"}:
                return False
        return None

    made_val = _coerce_bool(payload.get("made"))
    if made_val is None:
        return jsonify({"error": "made must be truthy/falsey"}), 400
    reason = payload.get("reason")
    if reason is not None and isinstance(reason, str):
        reason = reason.strip()
        if not reason:
            reason = None
    if not made_val and reason is None:
        reason = "Admin override"
    by = (payload.get("by") or "").strip() or "admin"
    timestamp = datetime.now(timezone.utc).isoformat()

    override_info = {"made": bool(made_val), "updated_at": timestamp, "by": by}
    if reason:
        override_info["reason"] = reason

    # Update session JSON (filesystem copy)
    session_json = _read_session(sid) or {}
    shots_list = session_json.get("shots")
    if isinstance(shots_list, list):
        matched = False
        for idx0, shot in enumerate(shots_list, start=1):
            entry_idx = None
            if isinstance(shot, dict):
                for key in ("idx", "shotId", "shot_id", "shot", "id"):
                    if key in shot:
                        try:
                            entry_idx = int(shot[key])
                            if entry_idx <= 0:
                                entry_idx = idx0
                            break
                        except Exception:
                            continue
            if entry_idx is None:
                entry_idx = idx0
            if entry_idx == idx:
                matched = True
                shot["made"] = 1 if made_val else 0
                if made_val:
                    shot.pop("missReason", None)
                elif reason:
                    shot["missReason"] = reason
                shot["adminOverride"] = override_info
                arcmm = shot.get("arcmm")
                if isinstance(arcmm, dict):
                    summary = arcmm.get("summary")
                    if isinstance(summary, dict):
                        summary["made"] = bool(made_val)
                    else:
                        arcmm["summary"] = {"made": bool(made_val)}
                break
        # Update totals regardless
        attempts = len(shots_list)
        makes = sum(1 for shot in shots_list if _coerce_bool(shot.get("made")) is True)
        accuracy = int(round((makes / attempts) * 100)) if attempts else 0
        session_json["totals"] = {
            "attempts": attempts,
            "made": makes,
            "accuracy": accuracy,
        }
        _write_session(sid, session_json)
    else:
        attempts = makes = accuracy = 0

    # Update database rows
    totals = {"attempts": attempts, "made": makes, "accuracy": accuracy}
    db = _db_get()
    if db:
        from sqlalchemy import select, func, case

        with db["Session"]() as s:
            ShotRow = db["ShotRow"]
            row = s.execute(
                select(ShotRow).where(ShotRow.sid == sid, ShotRow.idx == idx)
            ).scalar_one_or_none()
            if row is None:
                row = ShotRow(sid=sid, idx=idx)
                s.add(row)
            row.made = bool(made_val)
            row.miss_reason = None if made_val else reason
            data_obj = row.data if isinstance(row.data, dict) else {}
            data_map = dict(data_obj) if isinstance(data_obj, dict) else {}
            data_map["made"] = bool(made_val)
            if reason is not None and not made_val:
                data_map["missReason"] = reason
            elif made_val:
                data_map.pop("missReason", None)
            data_map["adminOverride"] = override_info
            arcmm = data_map.get("arcmm")
            if isinstance(arcmm, dict):
                summary = arcmm.get("summary")
                if isinstance(summary, dict):
                    summary["made"] = bool(made_val)
                else:
                    arcmm["summary"] = {"made": bool(made_val)}
            row.data = data_map

            SessionRow = db["SessionRow"]
            sess_row = s.get(SessionRow, sid)
            if sess_row:
                total, made_count = s.execute(
                    select(
                        func.count(ShotRow.id),
                        func.sum(case((ShotRow.made == True, 1), else_=0)),
                    ).where(ShotRow.sid == sid)
                ).one()
                sess_row.shots_count = int(total or 0)
                sess_row.makes = int(made_count or 0)
                sess_row.accuracy = int(
                    round((sess_row.makes / max(1, sess_row.shots_count)) * 100)
                )
                totals = {
                    "attempts": sess_row.shots_count,
                    "made": sess_row.makes,
                    "accuracy": sess_row.accuracy,
                }

            # Record override in feedback log for traceability
            FB = db.get("CoachFeedbackRow")
            if FB is not None:
                score_val = None
                for key in ("poseScore", "weightedScore", "score"):
                    val = data_map.get(key)
                    if val is None:
                        continue
                    try:
                        score_val = float(val)
                        break
                    except (TypeError, ValueError):
                        continue
                s.add(
                    FB(
                        sid=sid,
                        shot_idx=idx,
                        provider="admin",
                        model="manual-override",
                        latency_ms=0,
                        text=f"[ADMIN] Result set to {'MAKE' if made_val else 'MISS'}",
                        score=score_val,
                    )
                )

            s.commit()

    return jsonify(
        {
            "ok": True,
            "sid": sid,
            "idx": idx,
            "made": bool(made_val),
            "override": override_info,
            "totals": totals,
        }
    )


@app.get("/admin/users")
def admin_users():
    try:
        db = _db_get()
        users = []
        if db:
            from sqlalchemy import select, func

            with db["Session"]() as s:
                U = db["User"]
                SR = db["SessionRow"]
                AdminMeta = db.get("UserAdminMeta")
                admin_map = {}
                if AdminMeta is not None:
                    try:
                        meta_rows = s.execute(select(AdminMeta)).scalars().all()
                        admin_map = {row.user_id: row for row in meta_rows}
                    except Exception:
                        admin_map = {}
                rows = s.execute(select(U)).scalars().all()
                for u in rows:
                    cnt = (
                        s.execute(
                            select(func.count(SR.sid)).where(SR.user_id == u.user_id)
                        ).scalar_one()
                        or 0
                    )
                    admin = admin_map.get(u.user_id)
                    effective_status = _resolve_user_status(u.status, getattr(admin, "status", None))
                    users.append(
                        {
                            "user_id": u.user_id,
                            "name": u.name,
                            "email": u.email,
                            "status": effective_status,
                            "created_at": u.created_at.isoformat() if u.created_at else None,
                            "notes": getattr(admin, "notes", None),
                            "sessions": int(cnt),
                        }
                    )
        return jsonify({"users": users, "active": list(app.active_users.values())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/admin/user/<int:uid>")
def admin_user_detail(uid):
    try:
        db = _db_get()
        if not db:
            return jsonify({"error": "db unavailable"}), 503
        with db["Session"]() as s:
            U = db["User"]
            AdminMeta = db.get("UserAdminMeta")
            user = s.get(U, uid)
            if not user:
                return jsonify({"error": "user not found"}), 404
            admin = None
            if AdminMeta is not None:
                admin = (
                    s.query(AdminMeta)
                    .filter(AdminMeta.user_id == uid)
                    .one_or_none()
                )
            effective_status = _resolve_user_status(user.status, getattr(admin, "status", None))
            profile = _serialize_profile_row(user)
            payload = {
                "user": {
                    "user_id": user.user_id,
                    "name": user.name,
                    "email": user.email,
                    "status": effective_status,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "handle": user.handle,
                    "phone": user.phone,
                },
                "profile": profile,
                "admin": {
                    "status": getattr(admin, "status", None),
                    "notes": getattr(admin, "notes", None),
                    "updated_at": admin.updated_at.isoformat() if getattr(admin, "updated_at", None) else None,
                    "updated_by": getattr(admin, "updated_by", None),
                },
            }
            return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.patch("/admin/user/<int:uid>")
def admin_user_update(uid):
    try:
        db = _db_get()
        if not db:
            return jsonify({"error": "db unavailable"}), 503
        payload = request.get_json(silent=True) or {}
        with db["Session"]() as s:
            U = db["User"]
            AdminMeta = db.get("UserAdminMeta")
            if AdminMeta is None:
                return jsonify({"error": "admin meta unavailable"}), 500
            user = s.get(U, uid)
            if not user:
                return jsonify({"error": "user not found"}), 404
            admin = (
                s.query(AdminMeta)
                .filter(AdminMeta.user_id == uid)
                .one_or_none()
            )
            if not admin:
                admin = AdminMeta(user_id=uid)
            has_status = "status" in payload
            status_val = _normalize_user_status(payload.get("status"))
            if has_status:
                admin.status = status_val
                try:
                    user.status = status_val
                except Exception:
                    pass
            if payload.get("clear_notes") is True:
                admin.notes = None
            if "notes" in payload:
                notes_raw = payload.get("notes")
                notes = str(notes_raw).strip() if notes_raw is not None else ""
                admin.notes = notes or None
            admin.updated_by = str(payload.get("by") or session.get("user_id") or "admin")
            s.add(admin)
            s.add(user)
            s.commit()
            s.refresh(admin)
            effective_status = _resolve_user_status(user.status, admin.status)
            return jsonify(
                {
                    "ok": True,
                    "user_id": uid,
                    "status": effective_status,
                    "notes": admin.notes,
                    "updated_at": admin.updated_at.isoformat()
                    if admin.updated_at
                    else None,
                    "updated_by": admin.updated_by,
                }
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/admin/user/<int:uid>/sessions")
def admin_user_sessions(uid):
    try:
        db = _db_get()
        items = []
        if db:
            from sqlalchemy import select

            with db["Session"]() as s:
                SR = db["SessionRow"]
                rows = s.execute(select(SR).where(SR.user_id == uid)).scalars().all()
                for r in rows:
                    items.append(
                        {
                            "sid": r.sid,
                            "created_at": r.created_at.isoformat()
                            if r.created_at
                            else None,
                            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                            "shots": r.shots_count,
                            "accuracy": r.accuracy,
                        }
                    )
        return jsonify({"sessions": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# new frame extraction routes


@app.route("/save_yolo_label", methods=["POST"])
def save_yolo_label():
    data = request.get_json()
    folder = data.get("folder")
    filename = data.get("filename")
    content = data.get("content", "")
    dataset_slug = data.get("dataset")

    dataset = _resolve_dataset(dataset_slug)
    folder_path = _dataset_abs_path(dataset, "framesRoot", folder)
    if not folder_path:
        return jsonify({"error": "Dataset frames directory unavailable"}), 400
    os.makedirs(folder_path, exist_ok=True)

    label_path = os.path.join(folder_path, filename)
    with open(label_path, "w", encoding="utf-8") as f:
        f.write(content.strip())
    return "", 200


# ---------- Extractor Rotation helper ----------
def _probe_video_rotation(video_path: str) -> int:
    """
    Returns 0/90/180/270 using ffprobe if available, else 0.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=side_data_list:stream_tags=rotate",
            "-of",
            "json",
            video_path,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            return 0
        data = json.loads(p.stdout)
        rot = 0
        streams = data.get("streams", [{}])
        s0 = streams[0] if streams else {}
        # tags.rotate (string degrees)
        tags = s0.get("tags", {})
        if "rotate" in tags:
            rot = int(tags["rotate"]) % 360
        # side_data_list.rotation (can be negative)
        sdl = s0.get("side_data_list", [])
        for ent in sdl:
            if "rotation" in ent:
                r = int(ent["rotation"])
                rot = (r % 360 + 360) % 360
        if rot not in (0, 90, 180, 270):
            rot = 0
        return rot
    except Exception:
        return 0


def _cv2_rotate(img, degrees: int):
    if degrees == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _rotate_yolo_label_line(line: str, degrees: int) -> str:
    """Rotate one YOLO xywh-normalized line (cid xc yc w h)."""
    parts = line.strip().split()
    if len(parts) != 5:
        return line
    cid, xc, yc, w, h = parts
    try:
        xc = float(xc)
        yc = float(yc)
        w = float(w)
        h = float(h)
    except Exception:
        return line
    if degrees == 90:
        x2, y2, w2, h2 = yc, 1.0 - xc, h, w
    elif degrees == 180:
        x2, y2, w2, h2 = 1.0 - xc, 1.0 - yc, w, h
    elif degrees == 270:
        x2, y2, w2, h2 = 1.0 - yc, xc, h, w
    else:
        x2, y2, w2, h2 = xc, yc, w, h
    x2 = _clamp01(x2)
    y2 = _clamp01(y2)
    w2 = _clamp01(w2)
    h2 = _clamp01(h2)
    return f"{cid} {x2:.6f} {y2:.6f} {w2:.6f} {h2:.6f}"


def _rotate_yolo_label_file_inplace(label_path: str, degrees: int):
    if not os.path.exists(label_path):
        return
    with open(label_path, "r") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    out = [_rotate_yolo_label_line(ln, degrees) for ln in lines]
    with open(label_path, "w") as f:
        f.write("\n".join(out) + ("\n" if out else ""))


# --------------------------------------


# Make sure compile_dataset copies images and labels to YOLO structure
@app.route("/compile_dataset/<folder>", methods=["POST"])
def compile_dataset(folder):
    import shutil

    data = request.get_json()
    yaml_text = data.get("yaml", "")

    base_path = os.path.join("datasets", "visaion_seg")
    img_dir = os.path.join(base_path, "images", "train")
    label_dir = os.path.join(base_path, "labels", "train")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(label_dir, exist_ok=True)

    supported_exts = (".jpg", ".jpeg", ".png", ".bmp")
    src_path = os.path.join("frames", folder)

    print(f"📂 Scanning: {src_path}")
    paired = 0

    for file in os.listdir(src_path):
        if not file.lower().endswith(supported_exts):
            continue
        name_no_ext = os.path.splitext(file)[0]
        label_file = name_no_ext + ".txt"

        img_src = os.path.join(src_path, file)
        lbl_src = os.path.join(src_path, label_file)

        if os.path.exists(lbl_src):
            shutil.copy2(img_src, os.path.join(img_dir, file))
            shutil.copy2(lbl_src, os.path.join(label_dir, label_file))
            paired += 1
        else:
            print(f"⚠️ Skipping {file} — no label found.")

    with open(os.path.join(base_path, "data.yaml"), "w") as f:
        f.write(yaml_text.strip())

    print(f"✅ Paired and copied {paired} image-label sets to {img_dir}")
    return "", 200


# replaces start_training - initiate training Yolo model
def _kickoff_training(folder=None):
    try:
        payload = {}
        if request.is_json:
            payload = request.get_json(silent=True) or {}
        dataset_slug = (payload.get("dataset") or "").strip() or (
            request.args.get("dataset") or ""
        ).strip()
        dataset = _resolve_dataset(dataset_slug or None)
        dataset_slug = dataset.get("slug", "visaion")

        val_ratio = payload.get("val_ratio", request.args.get("val_ratio", 0.1))
        try:
            val_ratio = float(val_ratio)
        except Exception:
            val_ratio = 0.1

        epochs = payload.get("epochs", request.args.get("epochs", 120))
        try:
            epochs = int(epochs)
        except Exception:
            epochs = 120

        imgsz = payload.get("imgsz", request.args.get("imgsz", 640))
        try:
            imgsz = int(imgsz)
        except Exception:
            imgsz = 640

        batch = payload.get("batch", request.args.get("batch", 16))
        try:
            batch = int(batch)
        except Exception:
            batch = 16

        workers = payload.get("workers", request.args.get("workers", 0))
        try:
            workers = int(workers)
        except Exception:
            workers = 0

        folder_hint = payload.get("folder") or folder

        split_info = _prepare_training_split(dataset, val_ratio=val_ratio)
        yaml_path = split_info["yaml_path"]
        run_name = f"{dataset_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        model = "yolov8s.pt"
        aug = (
            "hsv_h=0.015 hsv_s=0.7 hsv_v=0.4 degrees=5 translate=0.08 "
            "scale=0.20 shear=2 fliplr=0.5 perspective=0.0 close_mosaic=10"
        )
        optim = "optimizer=AdamW cos_lr=True"

        cmd = (
            f"yolo detect train model={model} data={yaml_path} project=runs/detect name={run_name} "
            f"epochs={epochs} imgsz={imgsz} batch={batch} workers={workers} "
            f"{aug} {optim} cache=True"
        )

        # used by your training monitor UI
        with open(TRAIN_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "run": run_name,
                    "epochs": epochs,
                    "started_at": datetime.now().isoformat(),
                    "dataset": dataset_slug,
                    "train_samples": split_info["train_count"],
                    "val_samples": split_info["val_count"],
                    "split_manifest": split_info["manifest_path"],
                    "source_folder": folder_hint,
                    "val_ratio": val_ratio,
                    "batch": batch,
                    "imgsz": imgsz,
                },
                f,
                indent=2,
            )

        print("[train] Running:", cmd)
        subprocess.Popen(cmd, shell=True)
        return jsonify(
            {
                "status": "Training started.",
                "run": run_name,
                "epochs": epochs,
                "train_samples": split_info["train_count"],
                "val_samples": split_info["val_count"],
                "dataset": dataset_slug,
                "val_ratio": val_ratio,
                "batch": batch,
                "imgsz": imgsz,
                "source_folder": folder_hint,
            }
        )
    except Exception as e:
        print("[train] Training failed:", e)
        return jsonify({"status": "Training failed.", "error": str(e)}), 500


# keep a route that accepts the old frontend shape with <folder>
@app.route("/start_training/<folder>")
def start_training(folder):
    return _kickoff_training(folder=folder)


# tolerant route without folder (frontend can call /start_training)
@app.route("/start_training", methods=["GET", "POST"])
def start_training_noparam():
    return _kickoff_training()


@app.route("/manual_review/<video_name>")
def list_manual_review_frames(video_name):
    folder = os.path.join("frame_cache", video_name, "manual_review")
    if not os.path.exists(folder):
        return jsonify({"frames": []})

    frames = [f for f in os.listdir(folder) if f.endswith(".jpg")]
    frames.sort()
    return jsonify({"frames": frames})


@app.route("/upload", methods=["POST"])
def upload():
    video = request.files.get("video")
    if video:
        filename = secure_filename(video.filename)
        path = os.path.join(UPLOAD_FOLDER, filename)
        video.save(path)
        frame_memory["ball_path"].clear()
        frame_memory["frame_id"] = 0
        global kalman
        kalman = init_kalman()
        return jsonify({"video": f"/uploads/{filename}"})
    return jsonify({"error": "No video uploaded"}), 400


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@app.post("/upload_image_set")
def upload_image_set():
    dataset = _resolve_dataset(request.form.get("dataset"))
    files = request.files.getlist("images")
    folder_name = request.form.get("folder") or ""
    if not files:
        return jsonify({"error": "No images provided"}), 400

    base_folder = secure_filename(folder_name) if folder_name else ""
    if not base_folder:
        base_folder = f"imageset_{int(time.time())}"
    target_dir = _dataset_abs_path(dataset, "frameCacheRoot", base_folder)
    if not target_dir:
        return jsonify({"error": "Dataset frame directory unavailable"}), 400
    os.makedirs(target_dir, exist_ok=True)

    saved = []
    for i, file in enumerate(files):
        filename = secure_filename(file.filename)
        if not filename:
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            continue
        dest = os.path.join(target_dir, filename)
        stem, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(target_dir, f"{stem}_{counter}{ext}")
            counter += 1
        try:
            file.save(dest)
            saved.append(os.path.basename(dest))
        except Exception as exc:
            print("[upload_image_set] failed to save", filename, exc)

    if not saved:
        return jsonify({"error": "No images saved (check file types)"}), 400

    return jsonify(
        {
            "folder": base_folder,
            "count": len(saved),
            "dataset": dataset.get("slug"),
            "images": saved,
        }
    )


@app.route("/uploads/<filename>")
def serve_video(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# 🧠 Kalman filter setup
kalman = None


def init_kalman():
    kf = cv2.KalmanFilter(4, 2)
    kf.transitionMatrix = np.array(
        [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32
    )
    kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
    kf.processNoiseCov = np.eye(4, dtype=np.float32) * 1e-2
    kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 1e-1
    kf.errorCovPost = np.eye(4, dtype=np.float32)
    return kf


def track_ball_with_kalman(ball_point):
    global kalman
    if ball_point:
        measured = np.array(
            [[np.float32(ball_point["x"])], [np.float32(ball_point["y"])]]
        )
        kalman.correct(measured)
    predicted = kalman.predict()
    return int(predicted[0]), int(predicted[1])


last_gray = None


def fallback_motion_ball(frame, min_area=30, max_area=5000):
    global last_gray
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 0)
    if last_gray is None:
        last_gray = gray
        return None
    frame_delta = cv2.absdiff(last_gray, gray)
    thresh = cv2.threshold(frame_delta, 20, 255, cv2.THRESH_BINARY)[1]
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = [
        cv2.boundingRect(c)
        for c in contours
        if min_area < cv2.contourArea(c) < max_area
    ]
    if candidates:
        x, y, w, h = max(candidates, key=lambda r: r[2] * r[3])
        return {
            "x": x + w // 2,
            "y": y + h // 2,
            "frame": frame_memory["frame_id"],
            "confidence": 0.5,
        }  # set low for fast motion & net inclusion
    last_gray = gray
    return None


# run extract for every Nth frame (or every N seconds if provided)
@app.route("/extract_frames", methods=["POST"])
def extract_frames():
    data = request.get_json()
    filename = data.get("filename")
    step = int(data.get("step", 5))  # every N frames
    every_seconds = data.get("every_seconds")  # optional float (e.g., 0.5)
    dataset = _resolve_dataset(data.get("dataset"))
    if every_seconds is not None:
        try:
            every_seconds = float(every_seconds)
        except Exception:
            every_seconds = None

    if not filename:
        return jsonify({"error": "Missing filename"}), 400

    video_path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(video_path):
        return jsonify({"error": f"File not found: {video_path}"}), 404

    base_name = os.path.splitext(os.path.basename(filename))[0]
    safe_folder = secure_filename(base_name) or "frameset"
    out_dir = _dataset_abs_path(dataset, "frameCacheRoot", safe_folder)
    if not out_dir:
        return jsonify({"error": "Dataset frame directory unavailable"}), 400
    os.makedirs(out_dir, exist_ok=True)
    _trace(
        "[extract_frames]",
        "dataset=",
        dataset.get("slug"),
        "out_dir=",
        out_dir,
        "step=",
        step,
        "every_seconds=",
        every_seconds,
    )

    try:
        saved_filenames = extract_video_frames(
            video_path, out_dir, step=step, every_seconds=every_seconds
        )
        return jsonify(
            {
                "frames": saved_filenames,
                "count": len(saved_filenames),
                "folder": safe_folder,
                "dataset": dataset.get("slug"),
            }
        )
    except Exception as e:
        print("❌ extract_frames failed:", e)
        return jsonify({"error": f"Frame extraction failed: {str(e)}"}), 500


def extract_video_frames(video_path, out_dir, step=5, every_seconds=None):
    """
    Extract frames from video_path into out_dir.
    Auto-corrects orientation using ffprobe rotate metadata.
    If every_seconds is set, it overrides 'step' to sample by time.
    """
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    # sampling stride
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = int(round(fps * every_seconds)) if every_seconds else int(step)
    frame_interval = max(1, frame_interval)

    # auto-rotate based on metadata
    rotation = _probe_video_rotation(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]

    i = 0
    frame_id = 0
    saved = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if i % frame_interval == 0:
            if rotation:
                frame = _cv2_rotate(frame, rotation)
            filename = f"{base_name}_frame_{frame_id:03d}.jpg"
            cv2.imwrite(os.path.join(out_dir, filename), frame)
            saved.append(filename)
            frame_id += 1
        i += 1

    cap.release()
    return saved


@app.route("/rotate_frame", methods=["POST"])
def rotate_frame():
    """
    JSON body:
      folder   : <video folder in frame_cache>
      filename : <frame image filename, e.g., IMG_1234_frame_002.jpg>
      degrees  : 90|180|270
      rotate_labels : bool (optional, default false) - rotate frames/<folder>/<name>.txt if present
    """
    b = request.get_json(force=True) or {}
    folder = b.get("folder")
    filename = b.get("filename")
    degrees = int(b.get("degrees", 0)) % 360
    rotate_labels = bool(b.get("rotate_labels", False))

    if not folder or not filename or degrees not in (90, 180, 270):
        return jsonify(
            {"error": "folder, filename, and degrees (90|180|270) are required"}
        ), 400

    img_path = os.path.join(FRAME_FOLDER, folder, filename)
    if not os.path.exists(img_path):
        return jsonify({"error": f"frame not found: {img_path}"}), 404

    img = cv2.imread(img_path)
    if img is None:
        return jsonify({"error": "failed to read image"}), 500

    rotated = _cv2_rotate(img, degrees)
    cv2.imwrite(img_path, rotated)

    # Rotate corresponding YOLO label (optional)
    label_name = os.path.splitext(filename)[0] + ".txt"
    label_path = os.path.join("frames", folder, label_name)
    labels_rotated = False
    if rotate_labels and os.path.exists(label_path):
        try:
            _rotate_yolo_label_file_inplace(label_path, degrees)
            labels_rotated = True
        except Exception as e:
            print("⚠️ label rotate failed:", e)

    # Invalidate dataset copies (if they exist) to prevent stale training
    ds_lbl = os.path.join("datasets", "visaion_seg", "labels", "train", label_name)
    ds_img = os.path.join("datasets", "visaion_seg", "images", "train", filename)
    for p in (ds_lbl, ds_img):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    return jsonify(
        {"status": "ok", "rotated": degrees, "labels_rotated": labels_rotated}
    )


# Load a YOLO label from frames/<folder>/<filename>, with dataset fallback
@app.route("/load_yolo_label/<folder>/<path:filename>")
def load_yolo_label(folder, filename):
    """
    Search order:
      1) framesRoot/<folder>/<filename>
      2) dataset label directory (labelTrainRoot)
    Returns text/plain if found; otherwise 204 (no content).
    """
    dataset = _resolve_dataset(request.args.get("dataset"))

    frames_root = _dataset_abs_path(dataset, "framesRoot", folder)
    if frames_root:
        cand = os.path.abspath(os.path.join(frames_root, filename))
        if cand.startswith(frames_root) and os.path.exists(cand):
            return send_file(cand, mimetype="text/plain")

    fallback = _send_dataset_label(dataset, filename)
    if fallback:
        return fallback

    # keep console clean when no label exists yet
    return ("", 204)


@app.route("/dataset_label/<dataset_slug>/<path:filename>")
def dataset_label(dataset_slug, filename):
    dataset = _resolve_dataset(dataset_slug)
    result = _send_dataset_label(dataset, filename)
    if result:
        return result
    return ("", 204)


# use openai to label objects in ea frame
@app.route("/label_frame", methods=["POST"])
def label_frame():
    data = request.get_json()
    path = data.get("path")

    if not path or not path.startswith("/frames/"):
        return jsonify({"error": "Invalid path"}), 400

    abs_path = os.path.join("frame_cache", *path.split("/")[2:])
    if not os.path.exists(abs_path):
        return jsonify({"error": f"Frame not found: {abs_path}"}), 404

    try:
        # 🔍 Load image and encode to base64
        with open(abs_path, "rb") as f:
            img_bytes = f.read()
            b64_img = base64.b64encode(img_bytes).decode("utf-8")

        # 🧠 GPT prompt
        vision_prompt = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Identify the basketball, hoop, player, net, and backboard in this frame. "
                        "For each object found, return a bounding box in normalized % coordinates "
                        "as: label: [x%, y%, width%, height%]. "
                        "Example:\n"
                        "basketball: [45%, 32%, 5%, 7%]\n"
                        "hoop: [50%, 20%, 15%, 10%]\n"
                        "player: [10%, 40%, 20%, 50%]\n"
                        "backboard: [45%, 32%, 5%, 7%]\n"
                        "net: [50%, 20%, 15%, 10%]"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                },
            ],
        }

        response = get_openai_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant trained to detect basketball scene objects and return bounding boxes."
                    ),
                },
                vision_prompt,
            ],
            max_tokens=500,
        )

        raw_text = response.choices[0].message.content.strip()
        boxes = parse_vision_boxes(raw_text)

        # ✅ Filter by confidence
        high_conf_boxes = [
            b for b in boxes if b.get("confidence", 1.0) >= CONFIDENCE_THRESHOLD
        ]
        low_conf_labels = {
            b["label"] for b in boxes if b.get("confidence", 1.0) < CONFIDENCE_THRESHOLD
        }

        if len(high_conf_boxes) < len(REQUIRED_LABELS):
            print(f"⚠️ Frame has low confidence boxes: {low_conf_labels}")
            return move_to_manual_review(
                abs_path, boxes, reason="low confidence", extra=sorted(low_conf_labels)
            )

        # ✅ Check for required labels
        found_labels = {b["label"] for b in high_conf_boxes}
        missing = REQUIRED_LABELS - found_labels

        if missing:
            print(f"⚠️ Frame missing required objects: {missing}")
            return move_to_manual_review(
                abs_path,
                high_conf_boxes,
                reason="missing labels",
                extra=sorted(missing),
            )

        # ✅ Save label and return
        yolo_path = save_yolo_labels(abs_path, high_conf_boxes)
        # 🟡 Also copy label + image to YOLO training dataset
        train_label_dir = "datasets/visaion_seg/labels/train"
        train_image_dir = "datasets/visaion_seg/images/train"
        os.makedirs(train_label_dir, exist_ok=True)
        os.makedirs(train_image_dir, exist_ok=True)

        # Copy label
        shutil.copy(
            yolo_path, os.path.join(train_label_dir, os.path.basename(yolo_path))
        )

        # Copy image
        shutil.copy(abs_path, os.path.join(train_image_dir, os.path.basename(abs_path)))

        return jsonify(
            {"summary": raw_text, "boxes": high_conf_boxes, "yolo_path": yolo_path}
        )

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Vision labeling failed: {str(e)}"}), 500


# use openai to detect objects in the frame for extractor
@app.route("/auto_detect_frame_openai", methods=["POST"])
def auto_detect_frame_openai():
    """
    Body:  { folder: str, filename: str, confidence?: float, dataset?: str }
    Return: { img_w:int, img_h:int, detections:[ {label,confidence,box:[x1,y1,x2,y2]}... ] }
            NOTE: box is in ORIGINAL image pixels (no 1280x720 mapping).
    """
    try:
        b = request.get_json(force=True) or {}
        folder = b.get("folder")
        filename = b.get("filename")
        conf = float(b.get("confidence", 0.20))
        dataset = _resolve_dataset(b.get("dataset"))

        search_dirs = []
        fc_path = _dataset_abs_path(dataset, "frameCacheRoot", folder)
        if fc_path:
            search_dirs.append(fc_path)
        fr_path = _dataset_abs_path(dataset, "framesRoot", folder)
        if fr_path:
            search_dirs.append(fr_path)
        search_dirs.extend(
            [
                os.path.join(app.root_path, "frame_cache", folder),
                os.path.join(app.root_path, "frames", folder),
                os.path.join("frame_cache", folder),
                os.path.join("frames", folder),
            ]
        )
        image_path = None
        for d in search_dirs:
            p = os.path.join(d, filename)
            if os.path.exists(p):
                image_path = p
                break
        if not image_path:
            return jsonify({"error": f"Frame not found: {filename}"}), 404

        img = cv2.imread(image_path)
        if img is None:
            return jsonify({"error": "Failed to read image"}), 500
        H, W = img.shape[:2]

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        default_labels = ["basketball", "hoop", "net", "backboard", "player"]
        allowed = dataset.get("labels") or default_labels
        allowed = [lbl for lbl in allowed if lbl]
        if not allowed:
            allowed = default_labels
        alias = {
            "rim": "hoop",
            "ring": "hoop",
            "goal": "hoop",
            "basket": "hoop",
            "board": "backboard",
            "back board": "backboard",
            "human": "player",
            "person": "player",
            "net": "net",
        }
        alias = {k: v for k, v in alias.items() if v in allowed}

        client = get_openai_client()
        prompt = (
            f"Detect ONLY these classes: {', '.join(allowed)}.\n"
            "Return STRICT JSON:\n"
            '{ "detections": [ {"label":"<class>", "confidence":0..1, "box":[x1,y1,x2,y2]} ] }\n'
            "Box MUST be pixel coords in the original image size "
            f"({W}x{H}), x1<x2, y1<y2. Use at most 3 boxes per class."
        )

        resp = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return ONLY the requested JSON."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                },
            ],
            temperature=0.0,
            max_tokens=800,
        )
        raw = resp.choices[0].message.content or "{}"
        try:
            js = json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}\s*$", raw, flags=re.S)
            js = json.loads(m.group(0)) if m else {"detections": []}

        dets_out = []
        for d in js.get("detections", []):
            lbl = (d.get("label") or "").strip().lower()
            lbl = alias.get(lbl, lbl)
            if lbl not in allowed:
                continue
            c = float(d.get("confidence", 0.0))
            if c < conf:
                continue

            box = d.get("box") or []
            if len(box) != 4:
                continue

            x1, y1, x2, y2 = [float(v) for v in box]
            if (
                0.0 <= x1 <= 1.0
                and 0.0 <= x2 <= 1.0
                and 0.0 <= y1 <= 1.0
                and 0.0 <= y2 <= 1.0
            ):
                x1, x2 = x1 * W, x2 * W
                y1, y2 = y1 * H, y2 * H
            elif (
                0.0 <= x1 <= 100.0
                and 0.0 <= x2 <= 100.0
                and 0.0 <= y1 <= 100.0
                and 0.0 <= y2 <= 100.0
            ):
                x1, x2 = (x1 / 100.0) * W, (x2 / 100.0) * W
                y1, y2 = (y1 / 100.0) * H, (y2 / 100.0) * H
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            if x2 - x1 <= 1 or y2 - y1 <= 1:
                continue

            dets_out.append(
                {
                    "label": lbl,
                    "confidence": float(c),
                    "box": [int(x1), int(y1), int(x2), int(y2)],
                }
            )

        return jsonify({"img_w": int(W), "img_h": int(H), "detections": dets_out})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/auto_detect_frame", methods=["POST"])
def auto_detect_frame():
    data = request.get_json()
    folder = data["folder"]
    filename = data["filename"]
    conf = float(data.get("confidence", 0.15))  # extractor auto-detect setting
    dataset = _resolve_dataset(data.get("dataset"))

    search_dirs = []
    fc_path = _dataset_abs_path(dataset, "frameCacheRoot", folder)
    if fc_path:
        search_dirs.append(fc_path)
    fr_path = _dataset_abs_path(dataset, "framesRoot", folder)
    if fr_path:
        search_dirs.append(fr_path)
    search_dirs.extend(
        [
            os.path.join(app.root_path, "frame_cache", folder),
            os.path.join(app.root_path, "frames", folder),
            os.path.join("frame_cache", folder),
            os.path.join("frames", folder),
        ]
    )

    image_path = None
    for d in search_dirs:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            image_path = p
            break
    if not image_path:
        return jsonify({"error": f"Frame not found: {filename}"}), 404

    try:
        img = cv2.imread(image_path)
        if img is None:
            return jsonify({"error": "Failed to read image"}), 500
        H, W = img.shape[:2]

        model, names = _get_dataset_detector(dataset)
        if model is None:
            return jsonify({"error": "Detector model unavailable"}), 500

        with predict_lock:
            res = model.predict(image_path, conf=conf, imgsz=640, verbose=False)[0]

        class_names = names or []
        dets = []
        for b in res.boxes:
            cid = int(b.cls[0])
            x1, y1, x2, y2 = map(float, b.xyxy[0].tolist())
            dets.append(
                {
                    "label": class_names[cid]
                    if 0 <= cid < len(class_names)
                    else f"class_{cid}",
                    "confidence": float(b.conf[0]),
                    "box": [int(x1), int(y1), int(x2), int(y2)],
                }
            )

        return jsonify({"img_w": int(W), "img_h": int(H), "detections": dets})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/fix_label_swap", methods=["POST"])
def fix_label_swap():
    """
    Body JSON: { "folder": "IMG_2830", "swap": [[2,4]] }
    Swaps class IDs in YOLO txt labels for frames/<folder>/*.txt
    and in matching dataset copies if present.
    """
    b = request.get_json(force=True) or {}
    folder = b.get("folder")
    swaps = b.get("swap") or [[2, 4]]
    if not folder:
        return jsonify({"error": "folder required"}), 400

    frames_dir = os.path.join("frames", folder)
    if not os.path.isdir(frames_dir):
        return jsonify({"error": f"frames/{folder} not found"}), 404

    def swap_line(line: str) -> str:
        parts = line.strip().split()
        if len(parts) != 5:
            return line
        cid = int(parts[0])
        for a, b in swaps:
            if cid == a:
                cid = b
            elif cid == b:
                cid = a
        parts[0] = str(cid)
        return " ".join(parts)

    changed = 0
    files = [f for f in os.listdir(frames_dir) if f.lower().endswith(".txt")]
    for fn in files:
        p = os.path.join(frames_dir, fn)
        try:
            with open(p, "r") as f:
                lines = [ln.rstrip("\n") for ln in f]
            new_lines = [swap_line(ln) for ln in lines]
            if new_lines != lines:
                with open(p, "w") as w:
                    w.write("\n".join(new_lines) + ("\n" if new_lines else ""))
                changed += 1
                # also update dataset copy if exists
                ds_path = os.path.join("datasets", "visaion_seg", "labels", "train", fn)
                if os.path.exists(ds_path):
                    with open(ds_path, "w") as w:
                        w.write("\n".join(new_lines) + ("\n" if new_lines else ""))
        except Exception as e:
            print("swap failed for", p, e)

    return jsonify({"status": "ok", "folder": folder, "files_changed": changed})


@app.route("/model_names")
def model_names():
    names = getattr(getattr(model_det, "model", None), "names", None)
    return jsonify({"names": names})


# Extractor UI panel helpers
# ---------- Training monitor ----------
TRAIN_STATE_PATH = os.path.join(STATIC_CONFIG_DIR, "training_state.json")


def _latest_detect_run():
    if not os.path.exists(RUNS_DETECT_DIR):
        return None
    runs = [
        d
        for d in os.listdir(RUNS_DETECT_DIR)
        if os.path.isdir(os.path.join(RUNS_DETECT_DIR, d))
    ]
    if not runs:
        return None
    runs.sort(
        key=lambda r: os.path.getmtime(os.path.join(RUNS_DETECT_DIR, r)), reverse=True
    )
    return runs[0]


@app.route("/train_status")
def train_status():
    run = _latest_detect_run()
    if not run:
        return jsonify({})
    run_dir = os.path.join(RUNS_DETECT_DIR, run)
    csv_path = os.path.join(run_dir, "results.csv")
    weights_best = os.path.join(run_dir, "weights", "best.pt")

    out = {"run": run, "epoch": 0, "epochs": None, "done": False}

    # try to read planned epochs from state file
    if os.path.exists(TRAIN_STATE_PATH):
        try:
            with open(TRAIN_STATE_PATH, "r") as f:
                st = json.load(f)
                if st.get("run") == run:
                    out["epochs"] = st.get("epochs")
        except Exception:
            pass

    # read last row of results.csv
    if os.path.exists(csv_path):
        try:
            with open(csv_path, "r", newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                last = rows[-1]
                out["epoch"] = int(float(last.get("epoch", 0)))
                # losses
                tl = sum(
                    float(last.get(k, 0.0))
                    for k in ("train/box_loss", "train/cls_loss", "train/dfl_loss")
                )
                vl = sum(
                    float(last.get(k, 0.0))
                    for k in ("val/box_loss", "val/cls_loss", "val/dfl_loss")
                )
                out["loss_train"] = round(tl, 4)
                out["loss_val"] = round(vl, 4)
                # metrics
                out["map50"] = float(last.get("metrics/mAP50(B)", 0.0))
                out["map50_95"] = float(last.get("metrics/mAP50-95(B)", 0.0))
                # eta is not in csv; leave blank
        except Exception as e:
            print("train_status csv parse:", e)

    out["done"] = os.path.exists(weights_best)
    return jsonify(out)


@app.route("/train_stream")
def train_stream():
    def gen():
        last_epoch = -1
        while True:
            try:
                js = json.loads(train_status().response[0].decode())
                if js.get("epoch") != last_epoch:
                    last_epoch = js.get("epoch")
                    yield f"data: {json.dumps(js)}\n\n"
                if js.get("done"):
                    break
            except Exception:
                break
            time.sleep(2)

    return Response(gen(), mimetype="text/event-stream")


# serve results.png under /runs/detect/ so the UI <img> can load it
@app.route("/runs/detect/<path:subpath>")
def serve_runs_detect(subpath):
    return send_from_directory(os.path.join("runs", "detect"), subpath)


# --------------------------------------


def _list_best_pt():
    """Return list of {run, pt_path, mtime} for runs/detect/*/weights/best.pt sorted by mtime desc."""
    pattern = os.path.join(RUNS_DETECT_DIR, "*", "weights", "best.pt")
    items = []
    for pt in glob.glob(pattern):
        try:
            st = os.stat(pt)
            items.append(
                {
                    "run": os.path.basename(
                        os.path.dirname(os.path.dirname(pt))
                    ),  # run folder name
                    "pt_path": pt,
                    "mtime": st.st_mtime,
                    "mtime_human": datetime.fromtimestamp(st.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )
        except Exception:
            continue
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


@app.route("/list_trained_models", methods=["GET"])
def list_trained_models():
    """Lists recent best.pt weights for convenience."""
    try:
        return jsonify({"models": _list_best_pt()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/export_onnx", methods=["POST"])
def export_onnx():
    """
    Body JSON:
      pt_path   : string (optional; if absent, picks newest)
      imgsz     : int (default 640)
      opset     : int (default 17)
      profile   : string (default 'basketball')  # used to name output file
      simplify  : bool (default True)
      dynamic   : bool (default False)
      activate  : bool (default True)  # if true, writes static/config/detector.json to point worker at new model
    """
    try:
        data = request.get_json(force=True) or {}
        pt_path = data.get("pt_path")
        imgsz = int(data.get("imgsz", 640))
        opset = int(data.get("opset", 17))
        profile = (data.get("profile") or "basketball").strip().lower()
        simplify = bool(data.get("simplify", True))
        dynamic = bool(data.get("dynamic", False))
        activate = bool(data.get("activate", True))

        if not pt_path:
            models = _list_best_pt()
            if not models:
                return jsonify(
                    {"error": "No best.pt found under runs/detect/*/weights/"}
                ), 404
            pt_path = models[0]["pt_path"]

        if not os.path.exists(pt_path):
            return jsonify({"error": f"pt not found: {pt_path}"}), 404

        # Export ONNX next to the PT (Ultralytics handles writing best.onnx)
        y = YOLO(pt_path)
        onnx_path = y.export(
            format="onnx", opset=opset, imgsz=imgsz, simplify=simplify, dynamic=dynamic
        )

        if not onnx_path or not os.path.exists(onnx_path):
            return jsonify({"error": "Export did not produce an ONNX file."}), 500

        # Copy into static/models with profile-based name
        dest_name = f"{profile}_best.onnx"
        dest_path = os.path.join(STATIC_MODELS_DIR, dest_name)
        shutil.copy2(onnx_path, dest_path)

        # Optionally activate by writing detector.json
        cfg = {
            "model_url": f"/static/models/{dest_name}",
            "imgsz": imgsz,
            "profile": profile,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if activate:
            with open(DETECTOR_CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)

        return jsonify(
            {
                "status": "ok",
                "pt_path": pt_path,
                "onnx_exported": onnx_path,
                "model_copied_to": dest_path,
                "activated": activate,
                "detector_cfg": cfg if activate else None,
            }
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"ONNX export failed: {e}"}), 500


@app.route("/set_detector_model", methods=["POST"])
def set_detector_model():
    """Switch the active ONNX model without re-exporting."""
    try:
        data = request.get_json(force=True) or {}
        model_url = data.get("model_url")  # e.g. /static/models/basketball_best.onnx
        imgsz = int(data.get("imgsz", 640))
        profile = (data.get("profile") or "basketball").strip().lower()

        if not model_url:
            return jsonify({"error": "model_url required"}), 400

        cfg = {
            "model_url": model_url,
            "imgsz": imgsz,
            "profile": profile,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(DETECTOR_CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return jsonify({"status": "ok", "detector_cfg": cfg})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# route to serve training labels
@app.route("/datasets/visaion_seg/labels/train/<filename>")
def serve_dataset_label(filename):
    dataset = _resolve_dataset("basketball_pose")
    result = _send_dataset_label(dataset, filename)
    if result:
        return result
    return ("", 204)


# list_frame_folders route to populate dropdown on extraction page
@app.route("/list_frame_folders")
def list_frame_folders():
    dataset = _resolve_dataset(request.args.get("dataset"))
    root = _dataset_abs_path(dataset, "frameCacheRoot")
    folders = []
    if root and os.path.exists(root):
        folders = [f for f in os.listdir(root) if os.path.isdir(os.path.join(root, f))]
    _trace(
        "[list_frame_folders]",
        "dataset=",
        dataset.get("slug"),
        "root=",
        root,
        "folders=",
        folders,
    )
    return jsonify({"folders": sorted(folders), "dataset": dataset.get("slug")})


# ✅ Utility: Move rejected to manual_review/ and log it
def move_to_manual_review(abs_path, boxes, reason, extra=None):
    video_name = abs_path.split(os.sep)[1]
    manual_dir = os.path.join("frame_cache", video_name, "manual_review")
    os.makedirs(manual_dir, exist_ok=True)
    dest_path = os.path.join(manual_dir, os.path.basename(abs_path))
    shutil.move(abs_path, dest_path)

    # Move label file if it exists
    label_name = os.path.splitext(os.path.basename(abs_path))[0] + ".txt"
    label_path = os.path.join("labels", label_name)
    if os.path.exists(label_path):
        os.makedirs("labels/manual_review", exist_ok=True)
        shutil.move(label_path, os.path.join("labels/manual_review", label_name))

    # Log to skipped_frames.json
    log_skipped_frame(os.path.basename(abs_path), extra or [], reason)

    return jsonify(
        {
            "summary": f"⚠️ Skipped: {reason.replace('_', ' ').title()}",
            "boxes": boxes,
            "skipped": True,
        }
    )


# ✅ Audit logger
def log_skipped_frame(frame_name, issues, reason):
    import json

    entry = {"frame": frame_name, "reason": reason, "details": issues}

    try:
        if os.path.exists(SKIPPED_LOG_PATH):
            with open(SKIPPED_LOG_PATH, "r") as f:
                data = json.load(f)
        else:
            data = []

        data.append(entry)

        with open(SKIPPED_LOG_PATH, "w") as f:
            json.dump(data, f, indent=2)

        print(f"📝 Logged skipped frame: {frame_name} → {reason}")
    except Exception as e:
        print(f"❌ Failed to log skipped frame: {e}")


# 📦 Utility: Extract bounding boxes from GPT output
def parse_vision_boxes(text):
    pattern = r"(\w+):\s*\[(\d+)%?,\s*(\d+)%?,\s*(\d+)%?,\s*(\d+)%?\]"
    boxes = []

    for match in re.findall(pattern, text):
        label, x, y, w, h = match
        boxes.append(
            {
                "label": label.lower(),
                "x_pct": int(x),
                "y_pct": int(y),
                "w_pct": int(w),
                "h_pct": int(h),
            }
        )

    return boxes


def save_yolo_labels(frame_path, boxes):
    label_dir = "labels"
    os.makedirs(label_dir, exist_ok=True)

    frame_name = os.path.splitext(os.path.basename(frame_path))[0]
    label_path = os.path.join(label_dir, f"{frame_name}.txt")

    # read actual dims (not strictly needed for % → 0..1, but lets us clamp)
    img = cv2.imread(frame_path)
    H, W = (img.shape[0], img.shape[1]) if img is not None else (1, 1)

    with open(label_path, "w") as f:
        for box in boxes:
            class_id = LABEL_TO_CLASS.get(box["label"], -1)
            if class_id == -1:
                continue

            # convert % top-left + size  → normalized center + size
            x_pct = float(box["x_pct"])
            y_pct = float(box["y_pct"])
            w_pct = float(box["w_pct"])
            h_pct = float(box["h_pct"])

            xc = (x_pct + w_pct / 2.0) / 100.0
            yc = (y_pct + h_pct / 2.0) / 100.0
            w = w_pct / 100.0
            h = h_pct / 100.0

            # clamp to [0,1] just in case
            xc = min(max(xc, 0.0), 1.0)
            yc = min(max(yc, 0.0), 1.0)
            w = min(max(w, 0.0), 1.0)
            h = min(max(h, 0.0), 1.0)

            f.write(f"{class_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
    return label_path


@app.route("/review/accept", methods=["POST"])
def accept_reviewed_frame():
    data = request.get_json()
    video = data.get("video")
    frame = data.get("frame")

    frame_path = os.path.join("frame_cache", video, "manual_review", frame)
    label_path = os.path.join(
        "labels", "manual_review", os.path.splitext(frame)[0] + ".txt"
    )

    if not os.path.exists(frame_path):
        return jsonify({"error": "Frame not found"}), 404

    dst_img = os.path.join("frame_cache", video, frame)
    dst_label = os.path.join("labels", os.path.basename(label_path))

    shutil.move(frame_path, dst_img)
    if os.path.exists(label_path):
        shutil.move(label_path, dst_label)

    return jsonify({"status": "✅ Accepted and moved to training"})


@app.route("/review/delete", methods=["POST"])
def delete_reviewed_frame():
    data = request.get_json()
    video = data.get("video")
    frame = data.get("frame")

    frame_path = os.path.join("frame_cache", video, "manual_review", frame)
    label_path = os.path.join(
        "labels", "manual_review", os.path.splitext(frame)[0] + ".txt"
    )

    if os.path.exists(frame_path):
        os.remove(frame_path)
    if os.path.exists(label_path):
        os.remove(label_path)

    return jsonify({"status": "🗑 Deleted from manual_review"})


# where the magic happens - what does the ai model see
@app.route("/detect_frame", methods=["POST"])
def detect_frame():
    if not TORCH_AVAILABLE or model_det is None:
        # Graceful fallback to let UI run with local ONNX worker
        return jsonify(
            {"objects": [], "frameIndex": 0, "error": "server-detector-unavailable"}
        ), 200
    data = request.get_json()
    if not data or "frame" not in data:
        return jsonify({"error": "Missing frame"}), 400

    try:
        # Decode base64 image
        b64 = data["frame"].split(",")[-1]
        img_data = base64.b64decode(b64)
        frame = cv2.imdecode(np.frombuffer(img_data, np.uint8), cv2.IMREAD_COLOR)

        # Optional: crisp it up a bit
        sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        frame = cv2.filter2D(frame, -1, sharpen_kernel)
        frame = cv2.convertScaleAbs(frame, alpha=1.3, beta=15)

        # YOLO predict (low-ish conf; filter below by object)
        results = model_det.predict(frame, conf=0.22, imgsz=1280)[0]

        # Class ID -> label (must match training/export)
        label_map = {0: "basketball", 1: "hoop", 2: "net", 3: "backboard", 4: "player"}

        # Per-class thresholds (tune as needed)
        class_conf_thresholds = {
            "basketball": 0.18,
            "hoop": 0.68,
            "backboard": 0.65,
            "player": 0.45,
            "net": 0.25,
        }

        detections = []
        for det in results.boxes:
            cls = int(det.cls[0])
            conf = float(det.conf[0])
            x1, y1, x2, y2 = map(int, det.xyxy[0])
            label = label_map.get(cls)
            if not label:
                continue
            if conf < class_conf_thresholds.get(label, 0.25):
                continue
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            detections.append(
                {
                    "label": label,
                    "confidence": round(conf, 3),
                    "x": cx,
                    "y": cy,
                    "box": [x1, y1, x2, y2],
                }
            )

        # ---------- POST-PROCESS CORRECTIONS (runs BEFORE return) ----------
        # helpers
        def _w_h_ar(box):
            x1, y1, x2, y2 = box
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            return w, h, w / float(h)

        def _iou(a, b):
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            x1 = max(ax1, bx1)
            y1 = max(ay1, by1)
            x2 = min(ax2, bx2)
            y2 = min(ay2, by2)
            iw = max(0, x2 - x1)
            ih = max(0, y2 - y1)
            inter = iw * ih
            ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
            return inter / float(ua) if ua > 0 else 0.0

        bb = next((d for d in detections if d["label"] == "backboard"), None)
        ho = next((d for d in detections if d["label"] == "hoop"), None)

        # pass 1: flip player→net when it's flat & overlaps bb/hoop area
        for d in detections:
            if d["label"] != "player":
                continue
            w, h, ar = _w_h_ar(d["box"])
            area = w * h
            near_bb = bb and _iou(d["box"], bb["box"]) > 0.15
            near_ho = ho and _iou(d["box"], ho["box"]) > 0.08
            if ar > 1.3 and (near_bb or near_ho):
                if not bb:
                    d["label"] = "net"
                else:
                    bbw = bb["box"][2] - bb["box"][0]
                    bbh = bb["box"][3] - bb["box"][1]
                    if area < 0.35 * (bbw * bbh):
                        d["label"] = "net"

        # pass 2: flip net→player when it's tall, bigger, and away from bb/hoop
        for d in detections:
            if d["label"] != "net":
                continue
            w, h, ar = _w_h_ar(d["box"])
            area = w * h
            far_bb = (bb is None) or (_iou(d["box"], bb["box"]) < 0.05)
            far_ho = (ho is None) or (_iou(d["box"], ho["box"]) < 0.03)
            if ar < 0.9 and area > 3200 and far_bb and far_ho:
                d["label"] = "player"

        # synthesize a hoop if missing (from net/backboard geometry)
        if not any(d["label"] == "hoop" for d in detections):
            src = next((d for d in detections if d["label"] == "net"), None) or next(
                (d for d in detections if d["label"] == "backboard"), None
            )
            if src:
                x1, y1, x2, y2 = src["box"]
                w = max(1, x2 - x1)
                cx = (x1 + x2) // 2
                rim_w = max(40, int(0.55 * w))
                xL = int(cx - rim_w / 2)
                xR = int(cx + rim_w / 2)
                yR = int(y1)  # rim ≈ top of net
                detections.append(
                    {
                        "label": "hoop",
                        "confidence": 0.51,
                        "x": cx,
                        "y": yR,
                        "box": [xL, yR - 4, xR, yR + 4],
                        "synthetic": True,
                    }
                )
        # -------------------------------------------------------------------

        # (optional) quick label histogram for debugging
        # from collections import Counter
        # print("Counts:", Counter([d['label'] for d in detections]))

        return jsonify(
            {
                "frameIndex": frame_memory["frame_id"],
                "objects": detections,
                "ball_path": frame_memory["ball_path"],
            }
        )

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"YOLO detection failed: {str(e)}"}), 500


# ------------------------------------------------------------------------------#
#                Start Training content for multiple sports
# ------------------------------------------------------------------------------#
CURRENT_SPORT = "basketball"
LABELS_PATH = os.path.join(app.root_path, "static", "models", "labels.json")


def _load_labels_manifest():
    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _sport_profile_names(sport: str):
    man = _load_labels_manifest()
    prof = man["profiles"].get(sport, man["profiles"]["basketball"])
    return prof  # list of label strings for that sport


def _sport_dataset_root(sport: str):
    return os.path.join("datasets", f"{sport}_seg")


@app.get("/get_labels")
def get_labels():
    sport = request.args.get("sport", CURRENT_SPORT).lower()
    names = _sport_profile_names(sport)
    return jsonify({"sport": sport, "names": names})


@app.post("/set_sport")
def set_sport():
    global CURRENT_SPORT
    b = request.get_json(force=True) or {}
    s = (b.get("sport") or "basketball").lower()
    CURRENT_SPORT = s
    names = _sport_profile_names(s)
    return jsonify({"sport": s, "names": names, "dataset_root": _sport_dataset_root(s)})


@app.route("/copy_label_to_dataset", methods=["POST"])
def copy_label_to_dataset():
    data = request.get_json()
    folder = data.get("folder")
    filename = data.get("filename")
    image = data.get("image")
    sport = (data.get("sport") or CURRENT_SPORT).lower()
    dataset_slug = data.get("dataset")
    if not dataset_slug and sport:
        dataset_slug = f"{sport}_pose"
    dataset = _resolve_dataset(dataset_slug)

    src_txt = _dataset_abs_path(dataset, "framesRoot", folder, filename)
    src_img = _dataset_abs_path(dataset, "frameCacheRoot", folder, image)
    if not src_txt or not os.path.exists(src_txt):
        return jsonify({"error": f"missing label file: {src_txt}"}), 404
    if not src_img or not os.path.exists(src_img):
        return jsonify({"error": f"missing image: {src_img}"}), 404

    label_dst = _dataset_abs_path(dataset, "labelTrainRoot", filename)
    image_dst = _dataset_abs_path(dataset, "imagesTrainRoot", image)
    if not label_dst or not image_dst:
        return jsonify({"error": "dataset paths unavailable"}), 400

    os.makedirs(os.path.dirname(label_dst), exist_ok=True)
    os.makedirs(os.path.dirname(image_dst), exist_ok=True)

    shutil.copy2(src_txt, label_dst)
    shutil.copy2(src_img, image_dst)
    return jsonify(
        {
            "status": f"�o. Copied {filename} and {image} to {dataset.get('slug')} training folders.",
            "dataset": dataset.get("slug"),
        }
    )


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


def _db_event_start_challenge_session(
    sid: str, user_id: int, event_slug: str, started_ms: int
):
    db = getattr(app, "db", None)
    if not db:
        return {"ok": False, "err": "db unavailable"}
    with db["Session"]() as s:
        Event = db["Event"]
        EventRegistration = db["EventRegistration"]
        EventSession = db["EventSession"]
        ev = s.query(Event).filter(Event.slug == event_slug).one_or_none()
        if not ev:
            return {"ok": False, "err": "event not found"}
        reg = (
            s.query(EventRegistration)
            .filter_by(event_id=ev.id, user_id=user_id)
            .one_or_none()
        )
        if not reg:
            return {"ok": False, "err": "not registered"}
        started = datetime.utcfromtimestamp(started_ms / 1000.0)
        session_date = _event_local_date(started, ev.tz)
        within_window = True
        window_state = "active"
        try:
            if ev.start_date and session_date < ev.start_date:
                within_window = False
                window_state = "upcoming"
            if ev.end_date and session_date > ev.end_date:
                within_window = False
                window_state = "closed"
        except Exception:
            within_window = True
            window_state = "active"
        if not within_window:
            return {"ok": False, "err": "event inactive", "window": window_state}
        existing_today = (
            s.query(EventSession)
            .filter_by(event_id=ev.id, user_id=user_id, session_date=session_date)
            .order_by(EventSession.id.asc())
            .all()
        )
        daily_limit = int(getattr(ev, "daily_limit", 1) or 1)
        if daily_limit and len(existing_today) >= daily_limit:
            return {
                "ok": False,
                "err": "daily limit reached",
                "limit": daily_limit,
                "session_date": session_date.isoformat(),
            }
        es = EventSession(
            event_id=ev.id,
            user_id=user_id,
            session_id=sid,
            session_date=session_date,
            eligible=True,
            analyzed=False,
        )
        s.add(es)
        s.commit()
        return {
            "ok": True,
            "event_session_id": es.id,
            "event_id": ev.id,
            "slug": ev.slug,
            "name": ev.name,
            "session_date": session_date.isoformat(),
            "eligible": es.eligible,
            "daily_used": len(existing_today) + 1,
            "daily_limit": daily_limit,
        }


def _db_event_finalize_from_session(sid: str):
    """If this sid is a challenge session, mark analyzed and recompute user stats."""
    db = getattr(app, "db", None)
    if not db:
        return
    with db["Session"]() as s:
        Event = db["Event"]
        EventRegistration = db["EventRegistration"]
        EventSession = db["EventSession"]
        EventUserStats = db["EventUserStats"]
        SessionRow = db["SessionRow"]
        sess = s.get(SessionRow, sid)
        if not sess:
            return
        es = s.query(EventSession).filter_by(session_id=sid).one_or_none()
        if not es:
            return
        ev = s.query(Event).filter_by(id=es.event_id).one_or_none()
        if not ev:
            return
        es.makes = int(sess.makes or 0)
        es.attempts = int(sess.shots_count or 0)
        min_shots = int(getattr(ev, "min_shots", 0) or 0)
        eligible = True
        if min_shots and (es.attempts or 0) < min_shots:
            eligible = False
        try:
            if es.session_date:
                if getattr(ev, "start_date", None) and es.session_date < ev.start_date:
                    eligible = False
                if getattr(ev, "end_date", None) and es.session_date > ev.end_date:
                    eligible = False
        except Exception:
            pass
        es.eligible = eligible
        es.analyzed = True
        s.flush()

        rows = (
            s.query(EventSession)
            .filter_by(event_id=ev.id, user_id=es.user_id, eligible=True, analyzed=True)
            .order_by(EventSession.session_date.asc(), EventSession.id.asc())
            .all()
        )
        total_makes = sum((r.makes or 0) for r in rows)
        total_attempts = sum((r.attempts or 0) for r in rows)
        best_row = None
        best_key = None
        for r in rows:
            makes = int(r.makes or 0)
            attempts = int(r.attempts or 0)
            accuracy = (makes / attempts) if attempts else 0.0
            date_ord = (
                r.session_date.toordinal()
                if getattr(r, "session_date", None)
                else -(10**9)
            )
            key = (makes, accuracy, -date_ord)
            if best_key is None or key > best_key:
                best_key = key
                best_row = r
        best_mk = int(best_row.makes or 0) if best_row else 0
        best_att = int(best_row.attempts or 0) if best_row else 0
        first4 = rows[:4]
        last3 = rows[-3:] if len(rows) >= 3 else rows[-len(rows) :]

        def _avg_makes(items):
            return (
                (sum((r.makes or 0) for r in items) / float(len(items)))
                if items
                else None
            )

        f4 = _avg_makes(first4)
        l3 = _avg_makes(last3)
        improvement = (l3 - f4) if (f4 is not None and l3 is not None) else None

        stats = s.get(EventUserStats, (ev.id, es.user_id))
        reg = (
            s.query(EventRegistration)
            .filter_by(event_id=ev.id, user_id=es.user_id)
            .one_or_none()
        )
        if not stats:
            stats = EventUserStats(
                event_id=ev.id,
                user_id=es.user_id,
                age_group=(reg.age_group if reg else ">19"),
            )
            s.add(stats)
        elif reg and reg.age_group and stats.age_group != reg.age_group:
            stats.age_group = reg.age_group
        stats.total_makes = total_makes
        stats.total_attempts = total_attempts
        stats.best_session_mk = best_mk
        stats.best_session_att = best_att
        stats.first4_avg_mk = f4
        stats.last3_avg_mk = l3
        stats.improvement = improvement
        stats.updated_at = datetime.utcnow()
        s.commit()


def _build_event_leaderboard_entries(s, ev, category: str, age_group: str):
    db = getattr(app, "db", None)
    if not db:
        return []
    EventUserStats = db["EventUserStats"]
    EventSession = db["EventSession"]
    User = db["User"]

    stats_rows = (
        s.query(EventUserStats)
        .filter(EventUserStats.event_id == ev.id, EventUserStats.age_group == age_group)
        .all()
    )
    if not stats_rows:
        return []

    user_ids = [st.user_id for st in stats_rows]
    users = {
        u.user_id: u for u in s.query(User).filter(User.user_id.in_(user_ids)).all()
    }
    sessions_by_user = defaultdict(list)
    if user_ids:
        session_rows = (
            s.query(EventSession)
            .filter(
                EventSession.event_id == ev.id,
                EventSession.user_id.in_(user_ids),
                EventSession.eligible,
                EventSession.analyzed,
            )
            .all()
        )
        for row in session_rows:
            sessions_by_user[row.user_id].append(row)
        for rows in sessions_by_user.values():
            rows.sort(key=lambda r: (r.session_date, r.id))

    entries = []
    category = (category or "overall").lower()
    if category not in ("overall", "best_session", "improvement"):
        category = "overall"

    for stats in stats_rows:
        rows = sessions_by_user.get(stats.user_id, [])
        user = users.get(stats.user_id)
        handle = (
            user.handle or user.email or f"user-{stats.user_id}"
            if user
            else f"user-{stats.user_id}"
        )

        entry = {
            "user_id": stats.user_id,
            "handle": handle,
        }

        if category == "overall":
            makes = int(stats.total_makes or 0)
            attempts = int(stats.total_attempts or 0)
            accuracy = (makes / attempts) if attempts else None
            last_row = rows[-1] if rows else None
            last_date = getattr(last_row, "session_date", None)
            entry.update(
                {
                    "score": float(makes),
                    "makes": makes,
                    "attempts": attempts,
                    "accuracy": float(accuracy) if accuracy is not None else None,
                    "last_session_date": last_date.isoformat() if last_date else None,
                }
            )
            sort_key = (
                -makes,
                -(accuracy if accuracy is not None else -1.0),
                last_date or date.max,
                stats.user_id,
            )
        elif category == "best_session":
            best_row = None
            best_key = None
            for row in rows:
                makes = int(row.makes or 0)
                attempts = int(row.attempts or 0)
                accuracy = (makes / attempts) if attempts else None
                date_ord = (
                    row.session_date.toordinal()
                    if getattr(row, "session_date", None)
                    else -(10**9)
                )
                key = (makes, (accuracy if accuracy is not None else -1.0), -date_ord)
                if best_key is None or key > best_key:
                    best_key = key
                    best_row = row
            makes = int(getattr(best_row, "makes", 0) or 0)
            attempts = int(getattr(best_row, "attempts", 0) or 0)
            accuracy = (makes / attempts) if attempts else None
            best_date = getattr(best_row, "session_date", None)
            entry.update(
                {
                    "score": float(makes),
                    "makes": makes,
                    "attempts": attempts,
                    "accuracy": float(accuracy) if accuracy is not None else None,
                    "session_date": best_date.isoformat() if best_date else None,
                }
            )
            sort_key = (
                -makes,
                -(accuracy if accuracy is not None else -1.0),
                best_date or date.max,
                stats.user_id,
            )
        else:
            first_part = rows[:4]
            last_part = rows[-3:] if len(rows) >= 3 else rows[-len(rows) :]

            def _avg(items):
                return (
                    (sum((r.makes or 0) for r in items) / len(items)) if items else None
                )

            first_avg = _avg(first_part)
            last_avg = _avg(last_part)
            score = None
            if first_avg is not None and last_avg is not None:
                score = last_avg - first_avg
            elif last_avg is not None and first_avg is None:
                score = last_avg
            elif last_avg is None and first_avg is not None:
                score = -first_avg
            last_row = rows[-1] if rows else None
            last_date = getattr(last_row, "session_date", None)
            entry.update(
                {
                    "score": float(score) if score is not None else None,
                    "first_avg": float(first_avg) if first_avg is not None else None,
                    "last_avg": float(last_avg) if last_avg is not None else None,
                    "first_count": len(first_part),
                    "last_count": len(last_part),
                    "sessions": len(rows),
                    "last_session_date": last_date.isoformat() if last_date else None,
                    "provisional_first": len(first_part) < 4,
                    "provisional_last": len(last_part) < 3,
                }
            )
            score_val = score if score is not None else float("-inf")
            last_avg_val = last_avg if last_avg is not None else float("-inf")
            sort_key = (
                -score_val,
                -last_avg_val,
                last_date or date.max,
                stats.user_id,
            )

        entry["_sort"] = sort_key
        entries.append(entry)

    entries.sort(key=lambda e: e["_sort"])
    for idx, entry in enumerate(entries, start=1):
        entry["rank"] = idx
        entry.pop("_sort", None)
    return entries


def _db_lb_top(event_slug: str, category: str, age_group: str, limit: int = 10):
    db = getattr(app, "db", None)
    if not db:
        return {"ok": False, "err": "db unavailable"}
    with db["Session"]() as s:
        Event = db["Event"]
        ev = s.query(Event).filter_by(slug=event_slug).one_or_none()
        if not ev:
            return {"ok": False, "err": "event not found"}
        category_norm = (category or "overall").lower()
        if category_norm not in ("overall", "best_session", "improvement"):
            category_norm = "overall"
        entries = _build_event_leaderboard_entries(s, ev, category_norm, age_group)
        if limit:
            entries = entries[:limit]
        return {
            "ok": True,
            "event_id": ev.id,
            "category": category_norm,
            "age_group": age_group,
            "top": entries,
        }


def _db_lb_my_rank(event_slug: str, category: str, user_id: int):
    db = getattr(app, "db", None)
    if not db:
        return {"ok": False, "err": "db unavailable"}
    with db["Session"]() as s:
        Event = db["Event"]
        ev = s.query(Event).filter_by(slug=event_slug).one_or_none()
        if not ev:
            return {"ok": False, "err": "event not found"}
        EventUserStats = db["EventUserStats"]
        me = (
            s.query(EventUserStats)
            .filter_by(event_id=ev.id, user_id=user_id)
            .one_or_none()
        )
        if not me:
            return {"ok": True, "rank": None, "score": None, "age_group": None}
        category_norm = (category or "overall").lower()
        if category_norm not in ("overall", "best_session", "improvement"):
            category_norm = "overall"
        entries = _build_event_leaderboard_entries(s, ev, category_norm, me.age_group)
        mine = next((row for row in entries if row.get("user_id") == user_id), None)
        if not mine:
            return {"ok": True, "rank": None, "score": None, "age_group": me.age_group}

        payload = {
            "ok": True,
            "rank": mine.get("rank"),
            "score": mine.get("score"),
            "age_group": me.age_group,
        }
        if category_norm == "overall":
            payload.update(
                {
                    "makes": mine.get("makes", 0),
                    "attempts": mine.get("attempts", 0),
                    "accuracy": mine.get("accuracy"),
                    "last_session_date": mine.get("last_session_date"),
                }
            )
        elif category_norm == "best_session":
            payload.update(
                {
                    "makes": mine.get("makes", 0),
                    "attempts": mine.get("attempts", 0),
                    "accuracy": mine.get("accuracy"),
                    "session_date": mine.get("session_date"),
                }
            )
        else:
            payload.update(
                {
                    "sessions": mine.get("sessions", 0),
                    "first_avg": mine.get("first_avg"),
                    "first_count": mine.get("first_count"),
                    "last_avg": mine.get("last_avg"),
                    "last_count": mine.get("last_count"),
                    "provisional_first": mine.get("provisional_first"),
                    "provisional_last": mine.get("provisional_last"),
                    "last_session_date": mine.get("last_session_date"),
                }
            )
        return payload


def _db_event_today_status(user_id: int, event_slug: str):
    db = getattr(app, "db", None)
    if not db:
        return {"ok": False, "err": "db unavailable"}
    with db["Session"]() as s:
        Event = db["Event"]
        EventRegistration = db["EventRegistration"]
        EventSession = db["EventSession"]
        ev = s.query(Event).filter_by(slug=event_slug).one_or_none()
        if not ev:
            return {"ok": False, "err": "event not found"}
        reg = (
            s.query(EventRegistration)
            .filter_by(event_id=ev.id, user_id=user_id)
            .one_or_none()
        )

        today_local = _event_local_date(datetime.utcnow(), ev.tz)
        window_state = "active"
        if getattr(ev, "start_date", None) and today_local < ev.start_date:
            window_state = "upcoming"
        elif getattr(ev, "end_date", None) and today_local > ev.end_date:
            window_state = "closed"
        within_window = window_state == "active"

        daily_limit = int(getattr(ev, "daily_limit", 1) or 1)
        if daily_limit <= 0:
            daily_limit = 0  # treat <=0 as unlimited
        min_shots = int(getattr(ev, "min_shots", 0) or 0)

        today_sessions = (
            s.query(EventSession)
            .filter_by(event_id=ev.id, user_id=user_id, session_date=today_local)
            .order_by(EventSession.id.asc())
            .all()
        )
        today_payload = []
        for row in today_sessions:
            today_payload.append(
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "session_date": row.session_date.isoformat()
                    if row.session_date
                    else None,
                    "eligible": bool(row.eligible),
                    "analyzed": bool(row.analyzed),
                    "makes": int(row.makes or 0) if row.makes is not None else None,
                    "attempts": int(row.attempts or 0)
                    if row.attempts is not None
                    else None,
                    "created_at": row.created_at.isoformat()
                    if getattr(row, "created_at", None)
                    else None,
                }
            )

        submitted_today = any(
            r.get("analyzed") and r.get("eligible") for r in today_payload
        )
        today_started = bool(today_payload)
        remaining = None
        if daily_limit:
            remaining = max(0, daily_limit - len(today_payload))

        can_start = bool(reg) and within_window
        if daily_limit:
            can_start = can_start and len(today_payload) < daily_limit

        last_session = (
            s.query(EventSession)
            .filter_by(event_id=ev.id, user_id=user_id)
            .order_by(EventSession.session_date.desc(), EventSession.id.desc())
            .first()
        )

        return {
            "ok": True,
            "event_id": ev.id,
            "slug": ev.slug,
            "name": ev.name,
            "tz": ev.tz,
            "event_start": ev.start_date.isoformat()
            if getattr(ev, "start_date", None)
            else None,
            "event_end": ev.end_date.isoformat()
            if getattr(ev, "end_date", None)
            else None,
            "today_date": today_local.isoformat(),
            "window_state": window_state,
            "within_window": within_window,
            "registered": bool(reg),
            "age_group": getattr(reg, "age_group", None) if reg else None,
            "dob": reg.dob.isoformat() if reg and getattr(reg, "dob", None) else None,
            "registered_at": reg.registered_at.isoformat()
            if reg and getattr(reg, "registered_at", None)
            else None,
            "daily_limit": daily_limit,
            "min_shots": min_shots,
            "today_started": today_started,
            "today_submitted": submitted_today,
            "today_remaining": remaining,
            "today_sessions": today_payload,
            "can_start": can_start,
            "last_session_date": last_session.session_date.isoformat()
            if last_session and getattr(last_session, "session_date", None)
            else None,
            "last_session_makes": int(last_session.makes or 0)
            if last_session and getattr(last_session, "makes", None) is not None
            else None,
            "last_session_attempts": int(last_session.attempts or 0)
            if last_session and getattr(last_session, "attempts", None) is not None
            else None,
        }


@app.route("/api/events", methods=["GET"])
def api_list_events():
    db = getattr(app, "db", None)
    if not db:
        if not ALLOW_STUB_AUTH:
            return jsonify({"ok": False, "err": "db unavailable"}), 503
        return jsonify({"ok": True, "events": []})
    with db["Session"]() as s:
        Event = db["Event"]
        events = s.query(Event).order_by(Event.start_date.asc()).all()
        items = []
        for ev in events:
            items.append(
                {
                    "slug": ev.slug,
                    "name": ev.name,
                    "start_date": ev.start_date.isoformat()
                    if getattr(ev, "start_date", None)
                    else None,
                    "end_date": ev.end_date.isoformat()
                    if getattr(ev, "end_date", None)
                    else None,
                    "daily_limit": ev.daily_limit,
                    "min_shots": ev.min_shots,
                    "tz": ev.tz,
                }
            )
    return jsonify({"ok": True, "events": items})


@app.post("/api/events/<slug>/register")
def api_event_register_route(slug):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "err": "auth"}), 401
    dob_iso = None
    try:
        payload = request.get_json(force=True, silent=True) or {}
        dob_iso = payload.get("dob")
    except Exception:
        dob_iso = None
    res = _db_event_register(user_id, slug, dob_iso)
    status = 200 if res.get("ok") else 400
    return jsonify(res), status


@app.get("/api/events/<slug>/status")
def api_event_status(slug):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "err": "auth"}), 401
    res = _db_event_today_status(user_id, slug)
    status = 200 if res.get("ok") else 400
    return jsonify(res), status


@app.get("/api/events/<slug>/leaderboard")
def api_event_leaderboard(slug):
    category = request.args.get("category", "overall")
    age = request.args.get("age", ">19")
    res = _db_lb_top(slug, category, age)
    status = 200 if res.get("ok") else 400
    return jsonify(res), status


@app.get("/api/events/<slug>/my_rank")
def api_event_my_rank_route(slug):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"ok": False, "err": "auth"}), 401
    category = request.args.get("category", "overall")
    res = _db_lb_my_rank(slug, category, user_id)
    status = 200 if res.get("ok") else 400
    return jsonify(res), status


def _event_to_dict(ev):
    return {
        "id": ev.id,
        "slug": ev.slug,
        "name": ev.name,
        "start_date": ev.start_date.isoformat()
        if getattr(ev, "start_date", None)
        else None,
        "end_date": ev.end_date.isoformat() if getattr(ev, "end_date", None) else None,
        "daily_limit": ev.daily_limit,
        "min_shots": ev.min_shots,
        "tz": ev.tz,
        "created_at": ev.created_at.isoformat()
        if getattr(ev, "created_at", None)
        else None,
    }


def _parse_date_field(value):
    if value in (None, "", "null"):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


@app.get("/admin/events")
def admin_list_events():
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503
    Event = db["Event"]
    with db["Session"]() as s:
        events = s.query(Event).order_by(Event.start_date.asc()).all()
        return jsonify({"ok": True, "events": [_event_to_dict(ev) for ev in events]})


@app.post("/admin/events")
def admin_create_event():
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503
    payload = request.get_json(silent=True) or {}
    slug = (payload.get("slug") or "").strip()
    name = (payload.get("name") or "").strip()
    if not slug or not name:
        return jsonify({"ok": False, "err": "slug and name required"}), 400
    start_date = _parse_date_field(payload.get("start_date"))
    end_date = _parse_date_field(payload.get("end_date"))
    daily_limit = payload.get("daily_limit")
    min_shots = payload.get("min_shots")
    tz = (payload.get("tz") or "").strip() or None
    Event = db["Event"]
    with db["Session"]() as s:
        existing = s.query(Event).filter_by(slug=slug).first()
        if existing:
            return jsonify({"ok": False, "err": "slug already exists"}), 400
        ev = Event(
            slug=slug,
            name=name,
            start_date=start_date or date.today(),
            end_date=end_date or date.today(),
            daily_limit=int(daily_limit or 1),
            min_shots=int(min_shots or 10),
            tz=tz,
        )
        s.add(ev)
        s.commit()
        s.refresh(ev)
        return jsonify({"ok": True, "event": _event_to_dict(ev)}), 201


@app.patch("/admin/events/<int:event_id>")
def admin_update_event(event_id):
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503
    payload = request.get_json(silent=True) or {}
    Event = db["Event"]
    with db["Session"]() as s:
        ev = s.query(Event).filter_by(id=event_id).first()
        if not ev:
            return jsonify({"ok": False, "err": "not found"}), 404
        slug = payload.get("slug")
        name = payload.get("name")
        if slug:
            slug = slug.strip()
            if slug and slug != ev.slug:
                exists = (
                    s.query(Event).filter(Event.slug == slug, Event.id != ev.id).first()
                )
                if exists:
                    return jsonify({"ok": False, "err": "slug already exists"}), 400
                ev.slug = slug
        if name:
            ev.name = name.strip()
        if "start_date" in payload:
            parsed = _parse_date_field(payload.get("start_date"))
            if parsed:
                ev.start_date = parsed
        if "end_date" in payload:
            parsed = _parse_date_field(payload.get("end_date"))
            if parsed:
                ev.end_date = parsed
        if "daily_limit" in payload and payload.get("daily_limit") is not None:
            try:
                ev.daily_limit = int(payload.get("daily_limit"))
            except Exception:
                pass
        if "min_shots" in payload and payload.get("min_shots") is not None:
            try:
                ev.min_shots = int(payload.get("min_shots"))
            except Exception:
                pass
        if "tz" in payload:
            tz = (payload.get("tz") or "").strip() or None
            ev.tz = tz
        s.add(ev)
        s.commit()
        s.refresh(ev)
        return jsonify({"ok": True, "event": _event_to_dict(ev)})


@app.delete("/admin/events/<int:event_id>")
def admin_delete_event(event_id):
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503
    Event = db["Event"]
    with db["Session"]() as s:
        ev = s.query(Event).filter_by(id=event_id).first()
        if not ev:
            return jsonify({"ok": False, "err": "not found"}), 404
        s.delete(ev)
        s.commit()
        return jsonify({"ok": True})


# ----------------------------------------------------------
#  Support API (skeleton for in-app help interactions)
# ----------------------------------------------------------

SUPPORT_ALLOWED_ROLES = {"user", "visaion", "admin"}
SUPPORT_RESULT_STATUSES = {
    "pending_user",
    "resolved",
    "needs_ticket",
    "failed",
    "in_progress",
}
SUPPORT_TICKET_STATUSES = {"open", "in_progress", "waiting_user", "resolved", "closed"}
SUPPORT_TICKET_PRIORITIES = {"low", "normal", "high", "urgent"}
SUPPORT_TICKET_CATEGORIES = {
    "technical",
    "billing",
    "account",
    "challenge",
    "coaching",
    "other",
}


def _coerce_json_field(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {"value": value}
    return value


def _ensure_db_or_503():
    db = getattr(app, "db", None)
    if db:
        return db
    db = _try_init_db()
    if not db:
        raise RuntimeError("db unavailable")
    return db


@app.post("/api/support/assistant")
def api_support_assistant():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "err": "message required"}), 400

    context = {
        "message": message,
        "last_release": app.config.get("LAST_RELEASE"),
        "app_config_last_release": app.config.get("LAST_RELEASE"),
        "active_sessions": list(app.active_users.values()),
    }
    result = generate_general_reply(message, context)
    if result and result.reply:
        meta = result.meta or {}
        return jsonify({"ok": True, "reply": result.reply, "meta": meta})
    return jsonify(
        {
            "ok": True,
            "reply": "I'm still thinking that through. Could you share a little more detail?",
        }
    )


@app.post("/api/support/ingest")
def api_support_ingest():
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "err": "message required"}), 400

    role = (payload.get("role") or "user").strip().lower()
    if role not in SUPPORT_ALLOWED_ROLES:
        return jsonify({"ok": False, "err": "invalid role"}), 400

    result_status = (payload.get("result_status") or "pending_user").strip()
    if result_status and result_status not in SUPPORT_RESULT_STATUSES:
        return jsonify({"ok": False, "err": "invalid result_status"}), 400

    user_id = payload.get("user_id")
    if user_id is None:
        user_id = session.get("user_id")
        if user_id is None and not ALLOW_STUB_AUTH:
            return jsonify({"ok": False, "err": "auth"}), 401

    related_ticket_id = payload.get("related_ticket_id")
    if related_ticket_id is not None:
        try:
            related_ticket_id = int(related_ticket_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "err": "invalid related_ticket_id"}), 400

    base_intent = (payload.get("intent") or "").strip() or None
    action_taken = _coerce_json_field(payload.get("action_taken"))
    meta = _coerce_json_field(payload.get("meta"))

    SupportInteraction = db["SupportInteraction"]
    new_row_id = None
    with db["Session"]() as s:
        row = SupportInteraction(
            user_id=user_id,
            session_id=payload.get("session_id"),
            shot_id=payload.get("shot_id"),
            role=role,
            message=message,
            intent=base_intent,
            action_taken=action_taken,
            result_status=result_status or "pending_user",
            related_ticket_id=related_ticket_id,
            meta=meta,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        new_row_id = row.id
        primary_payload = row.to_dict()

    detected_intent = base_intent or "general"
    handler_result = None
    visaion_reply_dict = None

    if role == "user":
        if not base_intent:
            detected_intent = detect_intent(message)
        context = {
            "message": message,
            "session_id": payload.get("session_id"),
            "shot_id": payload.get("shot_id"),
            "user_id": user_id,
            "role": role,
            "request_payload": payload,
            "last_release": app.config.get("LAST_RELEASE"),
            "app_config_last_release": app.config.get("LAST_RELEASE"),
            "torch_available": TORCH_AVAILABLE,
            "active_sessions": list(app.active_users.values()),
        }
        handler_result = run_intent_handler(db, detected_intent, context)

    if handler_result:
        with db["Session"]() as s:
            row = s.query(SupportInteraction).filter_by(id=new_row_id).first()
            if row:
                row.intent = detected_intent
                if handler_result.action_taken:
                    row.action_taken = handler_result.action_taken
                if handler_result.result_status:
                    row.result_status = handler_result.result_status
                if handler_result.related_ticket_id:
                    row.related_ticket_id = handler_result.related_ticket_id
                if handler_result.meta:
                    row.meta = handler_result.meta
                s.add(row)
                s.commit()
                s.refresh(row)
                primary_payload = row.to_dict()

        if handler_result.reply:
            with db["Session"]() as s:
                reply_row = SupportInteraction(
                    user_id=user_id,
                    session_id=payload.get("session_id"),
                    shot_id=payload.get("shot_id"),
                    role="visaion",
                    message=handler_result.reply,
                    intent=detected_intent,
                    action_taken=handler_result.action_taken,
                    result_status=handler_result.result_status or "resolved",
                    related_ticket_id=handler_result.related_ticket_id,
                    meta=handler_result.meta,
                )
                s.add(reply_row)
                s.commit()
                s.refresh(reply_row)
                visaion_reply_dict = reply_row.to_dict()

    response_body = {
        "ok": True,
        "interaction": primary_payload,
        "intent": detected_intent,
    }
    if handler_result:
        response_body["handler"] = handler_result.to_dict()
    if visaion_reply_dict:
        response_body["response"] = visaion_reply_dict
    return jsonify(response_body), 201


@app.get("/api/support/history")
def api_support_history():
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        if not ALLOW_STUB_AUTH:
            return jsonify({"ok": False, "err": "db unavailable"}), 503
        return jsonify({"ok": True, "interactions": []})

    scope_all = request.args.get("scope") == "all"
    if scope_all and not ALLOW_STUB_AUTH:
        scope_all = False

    SupportInteraction = db["SupportInteraction"]
    with db["Session"]() as s:
        query = s.query(SupportInteraction).order_by(
            SupportInteraction.created_at.asc()
        )

        session_id = request.args.get("session_id")
        if session_id:
            query = query.filter(SupportInteraction.session_id == session_id)

        user_id_param = request.args.get("user_id")
        if user_id_param:
            try:
                user_id_int = int(user_id_param)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "err": "invalid user_id"}), 400
            query = query.filter(SupportInteraction.user_id == user_id_int)
        elif not scope_all:
            current_uid = session.get("user_id")
            if current_uid is None and not ALLOW_STUB_AUTH:
                return jsonify({"ok": False, "err": "auth"}), 401
            if current_uid is not None:
                query = query.filter(SupportInteraction.user_id == current_uid)

        limit = request.args.get("limit")
        if limit:
            try:
                limit_int = max(1, min(int(limit), 200))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "err": "invalid limit"}), 400
            query = query.limit(limit_int)

        rows = [row.to_dict() for row in query.all()]
        return jsonify({"ok": True, "interactions": rows})


@app.get("/api/support/tickets")
def api_support_ticket_list():
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        if not ALLOW_STUB_AUTH:
            return jsonify({"ok": False, "err": "db unavailable"}), 503
        return jsonify({"ok": True, "tickets": []})

    scope_all = request.args.get("scope") == "all"
    if scope_all and not ALLOW_STUB_AUTH:
        scope_all = False

    SupportTicket = db["SupportTicket"]
    with db["Session"]() as s:
        query = s.query(SupportTicket).order_by(SupportTicket.created_at.desc())

        user_id_param = request.args.get("user_id")
        if user_id_param:
            try:
                user_id_int = int(user_id_param)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "err": "invalid user_id"}), 400
            query = query.filter(SupportTicket.user_id == user_id_int)
        elif not scope_all:
            current_uid = session.get("user_id")
            if current_uid is None and not ALLOW_STUB_AUTH:
                return jsonify({"ok": False, "err": "auth"}), 401
            if current_uid is not None:
                query = query.filter(SupportTicket.user_id == current_uid)

        status = request.args.get("status")
        if status:
            query = query.filter(SupportTicket.status == status)

        rows = [row.to_dict() for row in query.all()]
        return jsonify({"ok": True, "tickets": rows})


@app.post("/api/support/tickets")
def api_support_ticket_create():
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip()
    if not title or not description:
        return jsonify({"ok": False, "err": "title and description required"}), 400

    user_id = payload.get("user_id")
    if user_id is None:
        user_id = session.get("user_id")
        if user_id is None and not ALLOW_STUB_AUTH:
            return jsonify({"ok": False, "err": "auth"}), 401

    status = (payload.get("status") or "open").strip()
    if status not in SUPPORT_TICKET_STATUSES:
        return jsonify({"ok": False, "err": "invalid status"}), 400

    priority = (payload.get("priority") or "normal").strip()
    if priority not in SUPPORT_TICKET_PRIORITIES:
        return jsonify({"ok": False, "err": "invalid priority"}), 400

    category = (payload.get("category") or "technical").strip()
    if category not in SUPPORT_TICKET_CATEGORIES:
        return jsonify({"ok": False, "err": "invalid category"}), 400

    meta = _coerce_json_field(payload.get("meta"))

    SupportTicket = db["SupportTicket"]
    with db["Session"]() as s:
        ticket = SupportTicket(
            user_id=user_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            category=category,
            assignee=(payload.get("assignee") or "").strip() or None,
            session_id=payload.get("session_id"),
            meta=meta,
        )
        s.add(ticket)
        s.commit()
        s.refresh(ticket)
        return jsonify({"ok": True, "ticket": ticket.to_dict()}), 201


@app.patch("/api/support/tickets/<int:ticket_id>")
def api_support_ticket_update(ticket_id):
    try:
        db = _ensure_db_or_503()
    except RuntimeError:
        return jsonify({"ok": False, "err": "db unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    SupportTicket = db["SupportTicket"]
    with db["Session"]() as s:
        ticket = s.query(SupportTicket).filter_by(id=ticket_id).first()
        if not ticket:
            return jsonify({"ok": False, "err": "not found"}), 404

        if "status" in payload:
            status = (payload.get("status") or "").strip()
            if status and status in SUPPORT_TICKET_STATUSES:
                ticket.status = status
        if "priority" in payload:
            priority = (payload.get("priority") or "").strip()
            if priority and priority in SUPPORT_TICKET_PRIORITIES:
                ticket.priority = priority
        if "category" in payload:
            category = (payload.get("category") or "").strip()
            if category and category in SUPPORT_TICKET_CATEGORIES:
                ticket.category = category
        if "title" in payload:
            title = (payload.get("title") or "").strip()
            if title:
                ticket.title = title
        if "description" in payload:
            description = (payload.get("description") or "").strip()
            if description:
                ticket.description = description
        if "assignee" in payload:
            assignee = (payload.get("assignee") or "").strip()
            ticket.assignee = assignee or None
        if "session_id" in payload:
            ticket.session_id = payload.get("session_id")
        if "meta" in payload:
            ticket.meta = _coerce_json_field(payload.get("meta"))

        ticket.last_update_at = datetime.utcnow()
        s.add(ticket)
        s.commit()
        s.refresh(ticket)
        return jsonify({"ok": True, "ticket": ticket.to_dict()})


try:
    for _rule in list(app.url_map.iter_rules()):
        if getattr(_rule, "rule", "").startswith("/api/events"):
            print("[routes] available:", _rule)
except Exception:
    pass


if __name__ == "__main__":
    # Single process, single thread — avoids Windows resets
    host = os.getenv("HOST", "127.0.0.1")
    try:
        port = int(os.getenv("PORT", "5001"))
    except Exception:
        port = 5001
    print(f"Starting visaion server on http://{host}:{port}")
    try:
        app.run(host=host, port=port, debug=True, use_reloader=False, threaded=False)
    except OSError as e:
        alt = port + 1
        print(f"Port {port} unavailable ({e}); retrying on {alt}…")
        app.run(host=host, port=alt, debug=True, use_reloader=False, threaded=False)

# WSGI entrypoint for PythonAnywhere
application = app
