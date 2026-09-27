"""
Voice Design Marketplace — v1.0.x voice profile sharing.

Export, import, and share custom voice profiles as portable `.omnivoice`
bundles. A bundle is a ZIP file containing:
  • metadata.json  — profile name, settings, engine, tags, creator info
  • ref_audio.wav  — reference audio clip
  • locked_audio.wav — locked/optimized audio (if locked)
  • thumbnail.jpg  — optional preview image

This enables:
  • Backup/restore of voice profiles across machines
  • Sharing voices via file transfer, Discord, forums
  • Future: P2P marketplace discovery (local network / IPFS)

Endpoints:
    POST /marketplace/export/{profile_id}  → download .omnivoice bundle
    POST /marketplace/import               → upload .omnivoice bundle → new profile
    GET  /marketplace/browse               → list importable bundles in local store
    POST /marketplace/publish/{profile_id} → save to local marketplace directory
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from core.config import OUTPUTS_DIR, VOICES_DIR
from core.db import db_conn
from core import event_bus
from core.version import APP_VERSION
from core.http_headers import content_disposition
from core.logging_utils import log_safe
from core.path_security import UnsafePath, resolve_within, safe_filename

logger = logging.getLogger("omnivoice.marketplace")

router = APIRouter(prefix="/marketplace", tags=["Voice Marketplace"])

# Local marketplace directory for published voices
MARKETPLACE_DIR = Path(OUTPUTS_DIR) / "marketplace"
MARKETPLACE_DIR.mkdir(parents=True, exist_ok=True)

# Bundle format version — increment if the schema changes
BUNDLE_VERSION = 1

# Maximum bundle upload size (100 MB) to prevent memory exhaustion
MAX_BUNDLE_BYTES = 100 * 1024 * 1024


#: Reference clips are served back through the /voice_audio static mount, so
#: only real audio extensions may land in VOICES_DIR. A bundle naming its clip
#: ``ref_audio.html`` must not become stored HTML on the backend's origin.
_BUNDLE_AUDIO_EXTS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus", ".webm", ".aac"})
#: Uncompressed cap per extracted entry (zip-bomb guard). Compressed size is
#: capped separately by MAX_BUNDLE_BYTES.
MAX_BUNDLE_ENTRY_BYTES = 200 * 1024 * 1024


def _bundle_audio_ext(name: str) -> str:
    ext = (os.path.splitext(name)[1] or ".wav").lower()
    if ext not in _BUNDLE_AUDIO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported audio type in bundle: {ext}")
    return ext


def _copy_capped(src, dst_path: str) -> None:
    written = 0
    with open(dst_path, "wb") as dst:
        while chunk := src.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_BUNDLE_ENTRY_BYTES:
                raise HTTPException(status_code=413, detail="Bundle audio entry too large.")
            dst.write(chunk)


def _extract_bundle_audio(zf: zipfile.ZipFile, profile_id: str, written: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Extract at most one ref and one locked clip; every path written is
    appended to ``written`` so the caller can clean up on any failure."""
    ref_audio_filename = None
    locked_audio_filename = None
    for info in zf.infolist():
        name = info.filename
        if name.startswith("ref_audio") and ref_audio_filename is None:
            ref_audio_filename = f"{profile_id}{_bundle_audio_ext(name)}"
            target = ref_audio_filename
        elif name.startswith("locked_audio") and locked_audio_filename is None:
            locked_audio_filename = f"{profile_id}_locked{_bundle_audio_ext(name)}"
            target = locked_audio_filename
        else:
            continue
        if info.file_size > MAX_BUNDLE_ENTRY_BYTES:
            raise HTTPException(status_code=413, detail="Bundle audio entry too large.")
        path = os.path.join(VOICES_DIR, target)
        written.append(path)
        with zf.open(info) as src:
            _copy_capped(src, path)
    return ref_audio_filename, locked_audio_filename


def _remove_written(paths: list[str]) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


async def _read_upload_capped(file: UploadFile) -> bytes:
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf.extend(chunk)
        if len(buf) > MAX_BUNDLE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Bundle too large. Max is {MAX_BUNDLE_BYTES} bytes.",
            )
    return bytes(buf)


def _contained_path(root, value, *, detail="Invalid file path") -> Path:
    try:
        return resolve_within(root, value)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail=detail) from exc


def _voice_asset(value) -> Path | None:
    """Resolve a DB-stored voice asset without trusting the database value."""
    if not value:
        return None
    try:
        resolved = resolve_within(VOICES_DIR, value)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Voice profile contains an invalid asset path") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Voice profile reference audio is missing")
    return resolved


# ── Export ──────────────────────────────────────────────────────────────────


def _bundle_metadata(profile: dict, **extra) -> dict:
    """Common .omnivoice metadata for export + publish.

    Captures ``kind`` and ``vd_states`` so a *designed* persona survives the
    bundle round-trip as a design (not silently demoted to a clone) — required
    for the synthetic-only gating of the persona gallery (§R3). Old bundles
    without these keys import as ``kind='clone'`` (backward-compatible).
    """
    meta = {
        "bundle_version": BUNDLE_VERSION,
        "profile_name": profile.get("name", "Unnamed"),
        "ref_text": profile.get("ref_text", ""),
        "instruct": profile.get("instruct", ""),
        "language": profile.get("language", "Auto"),
        "personality": profile.get("personality", ""),
        "seed": profile.get("seed"),
        "kind": profile.get("kind") or "clone",
        "vd_states": profile.get("vd_states"),
        "is_locked": bool(profile.get("is_locked")),
        "omnivoice_version": APP_VERSION,
    }
    meta.update(extra)
    return meta


@router.post("/export/{profile_id}")
def export_profile(profile_id: str):
    """Export a voice profile as a downloadable .omnivoice bundle (ZIP)."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Voice profile not found")

    profile = dict(row)

    # Build the ZIP bundle in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Metadata
        metadata = _bundle_metadata(
            profile, created_at=profile.get("created_at"), exported_at=time.time(),
        )
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        # Reference audio
        ref_path = profile.get("ref_audio_path")
        if ref_path:
            full_ref = _voice_asset(ref_path)
            if full_ref and full_ref.is_file():
                ext = os.path.splitext(ref_path)[1] or ".wav"
                zf.write(str(full_ref), f"ref_audio{ext}")

        # Locked audio (if profile is locked)
        locked_path = profile.get("locked_audio_path")
        if locked_path:
            full_locked = _voice_asset(locked_path)
            if full_locked and full_locked.is_file():
                ext = os.path.splitext(locked_path)[1] or ".wav"
                zf.write(str(full_locked), f"locked_audio{ext}")

    buf.seek(0)
    safe_name = "".join(
        c if c.isalnum() or c in "-_ " else "" for c in profile.get("name", "voice")
    ).strip().replace(" ", "_")[:40]
    filename = f"{safe_name}.omnivoice"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(buf.getbuffer().nbytes),
        },
    )


# ── Import ──────────────────────────────────────────────────────────────────


def _insert_imported_profile(profile_id, metadata, ref_audio_filename, locked_audio_filename) -> None:
    is_locked = bool(metadata.get("is_locked") and locked_audio_filename)
    with db_conn() as conn:
        conn.execute(
            """INSERT INTO voice_profiles
               (id, name, ref_audio_path, ref_text, instruct, language,
                seed, personality, is_locked, locked_audio_path, created_at,
                kind, vd_states)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                metadata.get("profile_name", "Imported Voice"),
                ref_audio_filename,
                metadata.get("ref_text", ""),
                metadata.get("instruct", ""),
                metadata.get("language", "Auto"),
                metadata.get("seed"),
                metadata.get("personality", ""),
                1 if is_locked else 0,
                locked_audio_filename or "",
                time.time(),
                # Preserve the design/clone distinction across the round-trip;
                # old bundles without these keys import as a clone.
                metadata.get("kind") or "clone",
                metadata.get("vd_states"),
            ),
        )


@router.post("/import")
async def import_profile(
    file: UploadFile = File(..., description="A .omnivoice bundle file"),
):
    """Import a voice profile from a .omnivoice bundle."""
    if not file.filename or not file.filename.endswith(".omnivoice"):
        raise HTTPException(
            status_code=400,
            detail="File must be a .omnivoice bundle (ZIP format).",
        )

    # Enforce the size limit while reading, not after buffering everything.
    content = await _read_upload_capped(file)

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid .omnivoice bundle (not a valid ZIP).") from exc

    # Read metadata
    if "metadata.json" not in zf.namelist():
        raise HTTPException(
            status_code=400,
            detail="Invalid .omnivoice bundle: missing metadata.json",
        )

    with zf.open("metadata.json") as mf:
        metadata = json.load(mf)
    profile_id = str(uuid.uuid4())[:8]

    # Extract audio files — stream from zip to disk; remove them on failure.
    written: list[str] = []
    try:
        ref_audio_filename, locked_audio_filename = _extract_bundle_audio(zf, profile_id, written)
        if not ref_audio_filename:
            raise HTTPException(
                status_code=400,
                detail="Invalid .omnivoice bundle: no reference audio found.",
            )
        _insert_imported_profile(profile_id, metadata, ref_audio_filename, locked_audio_filename)
    except BaseException:
        _remove_written(written)
        raise

    event_bus.emit("profiles", {"action": "created", "id": profile_id})
    logger.info(
        "Imported voice profile %r as %s from .omnivoice bundle",
        metadata.get("profile_name"), profile_id,
    )

    return {
        "success": True,
        "profile_id": profile_id,
        "name": metadata.get("profile_name", "Imported Voice"),
        "is_locked": bool(metadata.get("is_locked") and locked_audio_filename),
        "source_bundle": file.filename,
    }


# ── Local Marketplace (Publish & Browse) ───────────────────────────────────


@router.post("/publish/{profile_id}")
def publish_to_marketplace(
    profile_id: str,
    tags: str = Query("", description="Comma-separated tags"),
):
    """Publish a voice profile to the local marketplace directory.

    This saves a .omnivoice bundle to the marketplace folder so other
    VoiceStudio instances on the same machine (or shared network drive)
    can discover and import it.
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Voice profile not found")

    profile = dict(row)
    safe_name = "".join(
        c if c.isalnum() or c in "-_ " else "" for c in profile.get("name", "voice")
    ).strip().replace(" ", "_")[:40]
    bundle_path = _contained_path(
        MARKETPLACE_DIR,
        f"{safe_name}_{profile_id}.omnivoice",
        detail="Invalid profile id",
    )

    # Build the bundle
    with zipfile.ZipFile(str(bundle_path), "w", zipfile.ZIP_DEFLATED) as zf:
        metadata = _bundle_metadata(
            profile,
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            published_at=time.time(),
        )
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

        ref_path = profile.get("ref_audio_path")
        if ref_path:
            full_ref = _voice_asset(ref_path)
            if full_ref and full_ref.is_file():
                ext = os.path.splitext(ref_path)[1] or ".wav"
                zf.write(str(full_ref), f"ref_audio{ext}")

        locked_path = profile.get("locked_audio_path")
        if locked_path:
            full_locked = _voice_asset(locked_path)
            if full_locked and full_locked.is_file():
                ext = os.path.splitext(locked_path)[1] or ".wav"
                zf.write(str(full_locked), f"locked_audio{ext}")

    logger.info("Voice published to marketplace")
    return {
        "success": True,
        "profile_id": profile_id,
        "bundle_path": str(bundle_path),
        "bundle_size": os.path.getsize(bundle_path),
    }


@router.get("/browse")
def browse_marketplace(
    search: Optional[str] = Query(None, description="Search by name or tags"),
):
    """List available .omnivoice bundles in the local marketplace directory."""
    bundles = []

    for path in sorted(MARKETPLACE_DIR.glob("*.omnivoice"), key=os.path.getmtime, reverse=True):
        try:
            with zipfile.ZipFile(str(path)) as zf:
                if "metadata.json" not in zf.namelist():
                    continue
                metadata = json.loads(zf.read("metadata.json"))

                # Apply search filter
                if search:
                    searchable = " ".join([
                        metadata.get("profile_name", ""),
                        " ".join(metadata.get("tags", [])),
                        metadata.get("personality", ""),
                    ]).lower()
                    if search.lower() not in searchable:
                        continue

                bundles.append({
                    "filename": path.name,
                    "name": metadata.get("profile_name", path.stem),
                    "language": metadata.get("language", "unknown"),
                    "tags": metadata.get("tags", []),
                    "is_locked": metadata.get("is_locked", False),
                    "personality": metadata.get("personality", ""),
                    "published_at": metadata.get("published_at"),
                    "size_bytes": os.path.getsize(path),
                    "has_ref_audio": any(
                        n.startswith("ref_audio") for n in zf.namelist()
                    ),
                    "has_locked_audio": any(
                        n.startswith("locked_audio") for n in zf.namelist()
                    ),
                })
        except Exception as e:
            logger.warning("Skipping invalid bundle %s: %s", log_safe(path.name), log_safe(e))

    return {"bundles": bundles, "total": len(bundles), "directory": str(MARKETPLACE_DIR)}


@router.post("/install/{filename}")
async def install_from_marketplace(filename: str):
    """Import a voice profile from a bundle in the local marketplace directory."""
    try:
        filename = safe_filename(filename)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid bundle filename") from exc
    if not filename.endswith(".omnivoice"):
        raise HTTPException(status_code=400, detail="Invalid bundle filename")
    bundle_path = _contained_path(MARKETPLACE_DIR, filename, detail="Invalid bundle filename")
    if not bundle_path.is_file():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {filename}")

    # Read and delegate to the import logic
    with open(bundle_path, "rb") as f:
        content = f.read()

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid .omnivoice bundle.") from exc

    if "metadata.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="Invalid bundle: missing metadata.json")

    with zf.open("metadata.json") as mf:
        metadata = json.load(mf)
    profile_id = str(uuid.uuid4())[:8]

    written: list[str] = []
    try:
        ref_audio_filename, locked_audio_filename = _extract_bundle_audio(zf, profile_id, written)
        if not ref_audio_filename:
            raise HTTPException(status_code=400, detail="No reference audio in bundle.")
        is_locked = bool(metadata.get("is_locked") and locked_audio_filename)
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO voice_profiles
                   (id, name, ref_audio_path, ref_text, instruct, language,
                    seed, personality, is_locked, locked_audio_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_id,
                    metadata.get("profile_name", "Marketplace Voice"),
                    ref_audio_filename,
                    metadata.get("ref_text", ""),
                    metadata.get("instruct", ""),
                    metadata.get("language", "Auto"),
                    metadata.get("seed"),
                    metadata.get("personality", ""),
                    1 if is_locked else 0,
                    locked_audio_filename or "",
                    time.time(),
                ),
            )
    except BaseException:
        _remove_written(written)
        raise

    event_bus.emit("profiles", {"action": "created", "id": profile_id})

    return {
        "success": True,
        "profile_id": profile_id,
        "name": metadata.get("profile_name", "Marketplace Voice"),
        "source": filename,
    }


@router.delete("/{filename}")
def remove_from_marketplace(filename: str):
    """Remove a bundle from the local marketplace directory."""
    try:
        filename = safe_filename(filename)
    except UnsafePath as exc:
        raise HTTPException(status_code=400, detail="Invalid bundle filename") from exc
    if not filename.endswith(".omnivoice"):
        raise HTTPException(status_code=400, detail="Invalid bundle filename")
    bundle_path = _contained_path(MARKETPLACE_DIR, filename, detail="Invalid bundle filename")
    if not bundle_path.is_file():
        raise HTTPException(status_code=404, detail=f"Bundle not found: {filename}")
    try:
        os.unlink(bundle_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")
    return {"success": True, "deleted": filename}
