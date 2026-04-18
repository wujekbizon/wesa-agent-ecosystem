"""
Lumio Storage Service
Handles file uploads per Linux user.
Materials stored at: /home/{username}/materials/
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import List

import aiofiles
from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

STORAGE_BASE = Path(os.environ.get("STORAGE_BASE_PATH", "/home"))
MATERIALS_SUBDIR = "materials"

app = FastAPI(title="Lumio Storage Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def user_materials_path(username: str) -> Path:
    """Returns /home/{username}/materials/ — created if missing."""
    path = STORAGE_BASE / username / MATERIALS_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_username(username: str) -> str:
    """Basic sanity check — no path traversal."""
    if not username or ".." in username or "/" in username:
        raise HTTPException(status_code=400, detail="Invalid username")
    return username


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "base_path": str(STORAGE_BASE)}


# ---------------------------------------------------------------------------
# List user's materials
# ---------------------------------------------------------------------------
@app.get("/files/{username}")
def list_files(username: str):
    username = validate_username(username)
    folder = user_materials_path(username)
    files = []
    for f in sorted(folder.iterdir()):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size_bytes": stat.st_size,
                "uploaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "path": str(f),
            })
    return {"username": username, "files": files, "count": len(files)}


# ---------------------------------------------------------------------------
# Upload file
# ---------------------------------------------------------------------------
@app.post("/files/{username}/upload")
async def upload_file(username: str, file: UploadFile = File(...)):
    username = validate_username(username)
    folder = user_materials_path(username)
    dest = folder / file.filename

    async with aiofiles.open(dest, "wb") as out:
        content = await file.read()
        await out.write(content)

    return {
        "status": "uploaded",
        "username": username,
        "filename": file.filename,
        "size_bytes": len(content),
        "path": str(dest),
        "trigger_ingestion": True,   # rag-service should be notified after upload
    }


# ---------------------------------------------------------------------------
# Delete file
# ---------------------------------------------------------------------------
@app.delete("/files/{username}/{filename}")
def delete_file(username: str, filename: str):
    username = validate_username(username)
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    folder = user_materials_path(username)
    target = folder / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    target.unlink()
    return {"status": "deleted", "username": username, "filename": filename}


# ---------------------------------------------------------------------------
# Download file
# ---------------------------------------------------------------------------
@app.get("/files/{username}/{filename}")
def download_file(username: str, filename: str):
    username = validate_username(username)
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    folder = user_materials_path(username)
    target = folder / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(target), filename=filename)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8767)))
