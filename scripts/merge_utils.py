import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from visual_reference_sanitizer import sanitize_question


SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"
DONE_DIR = EXTRACTOR_DIR / "done"
DATA_DIR = EXTRACTOR_DIR / "data"
WOLFMED_DB = DATA_DIR / "wolfmed.db"
UUID_REGISTRY_JSON = DATA_DIR / "uuid_registry.json"
OUTPUTS_DIR = ECOSYSTEM_DIR / "extractor" / "outputs"

MERGE_SCHEMA_VERSION = 1
COMPLETE_CHUNK_STATUSES = {"validated", "done"}
COURSE_BY_CATEGORY = {"opiekun-medyczny": "opiekun-medyczny"}


class CategoryMergeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergePaths:
    data_dir: Path = DATA_DIR
    outputs_dir: Path = OUTPUTS_DIR
    done_dir: Path = DONE_DIR
    registry_db: Path = WOLFMED_DB
    legacy_registry_json: Path = UUID_REGISTRY_JSON


@dataclass(frozen=True)
class MergeResult:
    output_path: Path
    question_count: int
    included_manifests: tuple[str, ...]
    skipped_manifests: tuple[str, ...]
    output_sha256: str
    changed: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        raise CategoryMergeError(f"Invalid JSON artifact {path}: {exc}") from exc


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary_path, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json(path: Path, data) -> None:
    content = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_write_bytes(path, content)


@contextmanager
def _category_lock(category: str, done_dir: Path, timeout_seconds: float = 30.0):
    done_dir.mkdir(parents=True, exist_ok=True)
    lock_path = done_dir / f".{category}.merge.lock"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "created_at": _utc_now()}))
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 300:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise CategoryMergeError(f"Timed out waiting for category merge lock: {category}")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def get_db_connection(db_path: Path = WOLFMED_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db(
    db_path: Path = WOLFMED_DB,
    legacy_registry_json: Path | None = UUID_REGISTRY_JSON,
) -> None:
    connection = get_db_connection(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uuid_registry (
                uuid TEXT PRIMARY KEY,
                category TEXT,
                question_hash TEXT,
                registered_at TEXT
            )
            """
        )
        count = connection.execute("SELECT COUNT(*) FROM uuid_registry").fetchone()[0]
        if count == 0 and legacy_registry_json and legacy_registry_json.exists():
            legacy_uuids = _load_json(legacy_registry_json)
            now = _utc_now()
            connection.executemany(
                "INSERT OR IGNORE INTO uuid_registry "
                "(uuid, category, question_hash, registered_at) VALUES (?, ?, ?, ?)",
                [(str(value), "legacy", "", now) for value in legacy_uuids],
            )
        connection.commit()
    finally:
        connection.close()


def validate_entry(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    data = entry.get("data", {})
    question = data.get("question")
    answers = data.get("answers", [])
    if not isinstance(question, str) or len(question.strip()) < 5:
        return False
    if not isinstance(answers, list) or len(answers) != 4:
        return False
    if any(not isinstance(answer, dict) for answer in answers):
        return False
    return sum(answer.get("isCorrect") is True for answer in answers) == 1


def _is_uuid4(value: object) -> bool:
    try:
        parsed = UUID(str(value))
        return parsed.version == 4 and str(parsed) == str(value).lower()
    except (ValueError, TypeError, AttributeError):
        return False


def _stable_uuid4(category: str, question_key: str, salt: int = 0) -> str:
    seed = hashlib.sha256(f"{category}\0{question_key}\0{salt}".encode()).digest()
    return str(UUID(bytes=seed[:16], version=4))


def _course_for_category(category: str) -> str:
    return COURSE_BY_CATEGORY.get(category, "pielegniarstwo")


def _load_registry(connection: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {
        row["uuid"]: (row["category"] or "", row["question_hash"] or "")
        for row in connection.execute("SELECT uuid, category, question_hash FROM uuid_registry")
    }


def _resolve_uuid(
    entry: dict,
    category: str,
    question_key: str,
    question_hash: str,
    seen_uuids: set[str],
    registry: dict[str, tuple[str, str]],
) -> str:
    candidate = str(entry.get("id") or "").lower()
    registered = registry.get(candidate)
    registered_matches = not registered or registered == (category, question_hash)
    if _is_uuid4(candidate) and candidate not in seen_uuids and registered_matches:
        return candidate
    salt = 0
    while True:
        candidate = _stable_uuid4(category, question_key, salt)
        registered = registry.get(candidate)
        if candidate not in seen_uuids and (
            not registered or registered == (category, question_hash)
        ):
            return candidate
        salt += 1


def _collect_category_inputs(
    category: str, paths: MergePaths
) -> tuple[list[tuple[Path, dict, list[Path]]], list[str]]:
    complete: list[tuple[Path, dict, list[Path]]] = []
    skipped: list[str] = []
    manifest_paths = sorted(
        paths.data_dir.glob("*_manifest.json"), key=lambda path: _natural_key(path.name)
    )
    for manifest_path in manifest_paths:
        manifest = _load_json(manifest_path)
        if manifest.get("category") != category:
            continue
        chunks = manifest.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise CategoryMergeError(f"Manifest has no chunks: {manifest_path.name}")
        validated_paths: list[Path] = []
        incomplete = False
        for chunk in chunks:
            chunk_id = chunk.get("id")
            if not chunk_id:
                raise CategoryMergeError(f"Chunk without ID in {manifest_path.name}")
            validated_path = paths.outputs_dir / f"{chunk_id}_validated.json"
            if chunk.get("status") not in COMPLETE_CHUNK_STATUSES:
                incomplete = True
                continue
            if not validated_path.exists():
                raise CategoryMergeError(
                    f"Completed chunk missing validated artifact: {validated_path.name}"
                )
            content = _load_json(validated_path)
            if not isinstance(content, list):
                raise CategoryMergeError(f"Validated artifact is not an array: {validated_path.name}")
            validated_paths.append(validated_path)
        if incomplete:
            skipped.append(manifest_path.name)
            continue
        complete.append((manifest_path, manifest, validated_paths))
    if not complete:
        raise CategoryMergeError(f"No complete manifests found for category: {category}")
    return complete, skipped


def _snapshot_previous(output_path: Path, old_content: bytes) -> None:
    old_hash = _sha256_bytes(old_content)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    history_dir = output_path.parent / "history" / output_path.stem
    snapshot_path = history_dir / f"{timestamp}_{old_hash[:12]}.json"
    _atomic_write_bytes(snapshot_path, old_content)


def release_is_current(category: str, paths: MergePaths | None = None) -> bool:
    paths = paths or MergePaths()
    output_path = paths.done_dir / f"{category}_all_merged.json"
    release_path = paths.done_dir / f"{category}_all_merged.release.json"
    if not output_path.exists() or not release_path.exists():
        return False
    try:
        release = _load_json(release_path)
        return release.get("output_sha256") == _sha256_file(output_path)
    except CategoryMergeError:
        return False


def rebuild_category(category: str, paths: MergePaths | None = None) -> MergeResult:
    paths = paths or MergePaths()
    with _category_lock(category, paths.done_dir):
        complete, skipped = _collect_category_inputs(category, paths)
        initialize_db(paths.registry_db, paths.legacy_registry_json)
        connection = get_db_connection(paths.registry_db)
        try:
            registry = _load_registry(connection)
            entries: list[dict] = []
            seen_questions: set[str] = set()
            seen_uuids: set[str] = set()
            input_artifacts: list[dict[str, str]] = []
            for manifest_path, manifest, validated_paths in complete:
                for validated_path in validated_paths:
                    input_artifacts.append(
                        {"file": validated_path.name, "sha256": _sha256_file(validated_path)}
                    )
                    for entry in _load_json(validated_path):
                        if not validate_entry(entry):
                            raise CategoryMergeError(
                                f"Invalid question in validated artifact: {validated_path.name}"
                            )
                        sanitization = sanitize_question(entry["data"]["question"])
                        if sanitization.residual_visual_reference:
                            continue
                        entry["data"]["question"] = sanitization.sanitized
                        if not validate_entry(entry):
                            continue
                        question_key = entry["data"]["question"].strip().lower()
                        if question_key in seen_questions:
                            continue
                        meta = entry.setdefault("meta", {})
                        meta["course"] = _course_for_category(category)
                        meta["category"] = category
                        question_hash = hashlib.sha256(question_key.encode()).hexdigest()
                        entry_uuid = _resolve_uuid(
                            entry,
                            category,
                            question_key,
                            question_hash,
                            seen_uuids,
                            registry,
                        )
                        entry["id"] = entry_uuid
                        entries.append(entry)
                        seen_questions.add(question_key)
                        seen_uuids.add(entry_uuid)
                        registry[entry_uuid] = (category, question_hash)

            output_bytes = (json.dumps(entries, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            output_hash = _sha256_bytes(output_bytes)
            output_path = paths.done_dir / f"{category}_all_merged.json"
            old_content = output_path.read_bytes() if output_path.exists() else None
            changed = old_content != output_bytes
            if changed:
                if old_content is not None:
                    _snapshot_previous(output_path, old_content)
                _atomic_write_bytes(output_path, output_bytes)

            now = _utc_now()
            connection.executemany(
                "INSERT OR REPLACE INTO uuid_registry "
                "(uuid, category, question_hash, registered_at) VALUES (?, ?, ?, ?)",
                [
                    (
                        str(entry["id"]),
                        category,
                        hashlib.sha256(
                            entry["data"]["question"].strip().lower().encode()
                        ).hexdigest(),
                        now,
                    )
                    for entry in entries
                ],
            )
            connection.commit()

            release = {
                "schema_version": MERGE_SCHEMA_VERSION,
                "category": category,
                "course": _course_for_category(category),
                "created_at": now,
                "output_file": output_path.name,
                "output_sha256": output_hash,
                "question_count": len(entries),
                "included_manifests": [item[0].name for item in complete],
                "skipped_incomplete_manifests": skipped,
                "input_artifacts": input_artifacts,
            }
            _atomic_write_json(
                paths.done_dir / f"{category}_all_merged.release.json", release
            )
            for manifest_path, manifest, _ in complete:
                manifest["validated_count"] = len(manifest["chunks"])
                manifest["merge_status"] = "done"
                manifest["merged_output"] = str(output_path)
                manifest["merged_sha256"] = output_hash
                manifest["updated_at"] = now
                _atomic_write_json(manifest_path, manifest)

            return MergeResult(
                output_path=output_path,
                question_count=len(entries),
                included_manifests=tuple(item[0].name for item in complete),
                skipped_manifests=tuple(skipped),
                output_sha256=output_hash,
                changed=changed,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def merge_book(manifest: dict, output_path: Path | None = None, overwrite: bool = False) -> Path:
    """Compatibility wrapper. Every merge now rebuilds the full category."""
    category = manifest.get("category")
    if not category:
        raise CategoryMergeError("Manifest has no category")
    if output_path and output_path != DONE_DIR / f"{category}_all_merged.json":
        raise CategoryMergeError("Custom output paths are unsupported for category rebuilds")
    return rebuild_category(category).output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Deterministically rebuild a category bank from validated manifests."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path, help="Any manifest in the category")
    group.add_argument("--category", help="Category to rebuild")
    parser.add_argument("--overwrite", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.manifest:
        manifest_data = _load_json(arguments.manifest)
        selected_category = manifest_data.get("category")
        if not selected_category:
            raise SystemExit(f"Manifest has no category: {arguments.manifest}")
    else:
        selected_category = arguments.category
    result = rebuild_category(selected_category)
    action = "Rebuilt" if result.changed else "Verified unchanged"
    print(f"{action}: {result.output_path} ({result.question_count} questions)")
    if result.skipped_manifests:
        print("Skipped incomplete manifests:")
        for name in result.skipped_manifests:
            print(f"- {name}")
