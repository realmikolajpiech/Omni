import os
import lancedb
import logging
import sys
import gc
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.core.config import (
    DB_PATH, HOME, BRAIN_HOST, BRAIN_PORT, IGNORE_DIRS, BLOCKED_EXTENSIONS,
    INDEX_DONE_MARKER, INDEX_PROGRESS_PATH, CONTENT_SKIP_FILENAMES, CONTENT_SKIP_DIRS,
    CONTENT_SKIP_SUFFIXES, CONTENT_SKIP_EXTENSIONS, INDEX_LOG_PATH,
    BLOCKED_FILENAMES
)
from src.services.search.utils import process_file_content, is_text_file, is_image_file

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TABLE_NAME = "files"
EMBED_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/embed"
CLASSIFY_URL = f"http://{BRAIN_HOST}:{BRAIN_PORT}/classify_files"
LLM_FILTER_BATCH_SIZE = 32


def _log_indexed_file(phase: str, path: str, extra: str = ""):
    """Append a line to the indexing log file."""
    try:
        with open(INDEX_LOG_PATH, "a", encoding="utf-8") as f:
            line = f"[{phase}] {path}"
            if extra:
                line += f" ({extra})"
            f.write(line + "\n")
    except Exception:
        pass


def _wait_for_brain(timeout: int = 60) -> bool:
    """Poll the brain's /health endpoint until it responds or timeout expires."""
    health_url = f"http://{BRAIN_HOST}:{BRAIN_PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _remote_encode(texts: list) -> list:
    """Call the brain's /embed endpoint and return a list of float vectors."""
    payload = json.dumps({"texts": texts}).encode("utf-8")
    req = urllib.request.Request(
        EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["vectors"]


def _count_tokens(text: str) -> int:
    """Rough token estimate for logging: ~4 characters per token."""
    return max(1, len(text) // 4)


def _elapsed(start: float) -> str:
    s = int(time.time() - start)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _eta(start: float, done: int, total: int) -> str:
    if done == 0 or total == 0:
        return "?"
    elapsed = time.time() - start
    remaining = elapsed * (total - done) / done
    s = int(remaining)
    if s < 60:
        return f"~{s}s"
    return f"~{s // 60}m {s % 60}s"


def _llm_filter_content(candidates: list) -> set:
    """Batch-classify candidate files via the brain's /classify_files endpoint.

    Routing through the brain reuses the already-authenticated Groq fast-model
    client.  Only filename + 2-level path context is sent — no file content,
    so the calls are cheap and fast.

    Returns a set of full_paths that should be skipped.
    Fails open: a failed batch leaves those files included.
    """
    skip_paths: set = set()
    total_batches = (len(candidates) + LLM_FILTER_BATCH_SIZE - 1) // LLM_FILTER_BATCH_SIZE

    for batch_idx in range(total_batches):
        batch = candidates[batch_idx * LLM_FILTER_BATCH_SIZE:(batch_idx + 1) * LLM_FILTER_BATCH_SIZE]

        files_payload = [
            {"filename": filename, "path": full_path}
            for full_path, filename in batch
        ]
        payload = json.dumps({"files": files_payload}).encode("utf-8")

        try:
            req = urllib.request.Request(
                CLASSIFY_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            decisions = result.get("decisions", [])
            skipped = 0
            for i, (full_path, _) in enumerate(batch):
                if i < len(decisions) and int(decisions[i]) == 0:
                    skip_paths.add(full_path)
                    skipped += 1

            logging.info(
                f"  [llm-filter] batch {batch_idx + 1}/{total_batches}: "
                f"{skipped}/{len(batch)} files filtered out"
            )

        except Exception as e:
            logging.warning(
                f"  [llm-filter] batch {batch_idx + 1}/{total_batches} failed ({e}) "
                f"— including all {len(batch)} files as fallback"
            )

    return skip_paths


def _load_progress() -> dict:
    """Load the indexing progress file, or return a fresh state."""
    try:
        if os.path.exists(INDEX_PROGRESS_PATH):
            with open(INDEX_PROGRESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"phase1_complete": False, "phase2_complete": False, "phase3_complete": False}


def _save_progress(progress: dict):
    """Persist the current progress state to disk."""
    try:
        os.makedirs(os.path.dirname(INDEX_PROGRESS_PATH), exist_ok=True)
        with open(INDEX_PROGRESS_PATH, "w", encoding="utf-8") as f:
            json.dump(progress, f)
    except Exception as e:
        logging.warning(f"Could not save progress: {e}")


def _write_phase_progress(progress: dict, phase: int, pct: float, eta: str, label: str):
    """Update the progress file with live within-phase progress for the UI."""
    progress["current_phase"] = phase
    progress["phase_pct"]     = round(pct, 1)
    progress["eta"]           = eta
    progress["phase_label"]   = label
    _save_progress(progress)


def _clear_progress():
    """Remove the progress file once indexing completes fully."""
    try:
        if os.path.exists(INDEX_PROGRESS_PATH):
            os.remove(INDEX_PROGRESS_PATH)
    except Exception:
        pass


def _get_existing_paths(db, table_name: str, path_col: str = "path") -> set:
    """Return the set of paths already stored in a LanceDB table. Empty set on any error."""
    try:
        if table_name not in db.table_names():
            return set()
        tbl = db.open_table(table_name)
        df = tbl.to_pandas()
        if path_col in df.columns:
            return set(df[path_col].tolist())
    except Exception as e:
        logging.warning(f"Could not read existing paths from '{table_name}': {e}")
    return set()


def _collect_files(base_dir):
    """Single walk of the directory tree. Returns (all_files, text_files, image_files).

    Each entry is (full_path, filename). text_files and image_files are subsets
    of all_files, classified by extension.
    """
    all_files = []
    text_files = []
    image_files = []

    for root, dirs, files in os.walk(base_dir):
        # Filter out ignored directories
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS
            and not d.startswith(".")
            and not any(d.lower().endswith(ext) for ext in BLOCKED_EXTENSIONS)
        ]
        for file in files:
            if file.startswith("."):
                continue
            if file in BLOCKED_FILENAMES:
                continue
            
            # Use endswith for multi-part extensions like .min.js or _bin.js
            if any(file.lower().endswith(ext) for ext in BLOCKED_EXTENSIONS):
                continue
                
            full_path = os.path.join(root, file)
            entry = (full_path, file)
            all_files.append(entry)
            if is_text_file(full_path):
                text_files.append(entry)
            if is_image_file(full_path):
                image_files.append(entry)

    return all_files, text_files, image_files


def _top_level_breakdown(file_list, base_dir):
    """Return a dict mapping the first directory component under base_dir to file count."""
    counts = defaultdict(int)
    base_len = len(base_dir.rstrip(os.sep)) + 1
    for full_path, _ in file_list:
        relative = full_path[base_len:]
        top_dir = relative.split(os.sep, 1)[0] if os.sep in relative else "(root files)"
        counts[top_dir] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def dry_run():
    """Preview what would be indexed without actually indexing."""
    logging.info(f"Scanning {HOME} (dry run)...")
    scan_start = time.time()
    all_files, text_files, image_files = _collect_files(HOME)
    logging.info(f"Scan complete in {_elapsed(scan_start)}")

    print(f"\n{'='*60}")
    print(f"  DRY RUN — File Indexing Preview")
    print(f"{'='*60}")
    print(f"\n  Base directory: {HOME}")
    print(f"  Total files:    {len(all_files):,}")
    print(f"  Text files:     {len(text_files):,}")
    print(f"  Image files:    {len(image_files):,}")

    # Directory breakdown
    print(f"\n{'─'*60}")
    print(f"  Top-level directory breakdown (all files):")
    print(f"{'─'*60}")
    breakdown = _top_level_breakdown(all_files, HOME)
    for dirname, count in breakdown.items():
        pct = 100.0 * count / len(all_files) if all_files else 0
        print(f"    {dirname:<40s} {count:>8,} ({pct:5.1f}%)")

    # Sample files from each category
    for label, flist in [("ALL FILES", all_files), ("TEXT FILES", text_files), ("IMAGE FILES", image_files)]:
        print(f"\n{'─'*60}")
        print(f"  Sample {label} (first 20):")
        print(f"{'─'*60}")
        for path, name in flist[:20]:
            print(f"    {path}")
        if len(flist) > 20:
            print(f"    ... and {len(flist) - 20:,} more")

    print(f"\n{'='*60}\n")


def main():
    # ── Handle --dry-run ──────────────────────────────────────────
    if "--dry-run" in sys.argv:
        dry_run()
        return

    skip_filenames = "--skip-filenames" in sys.argv

    logging.info(f"Waiting for brain service at {EMBED_URL}...")
    if not _wait_for_brain(timeout=60):
        logging.error("Brain service did not become available in time. Exiting.")
        sys.exit(1)
    logging.info("Brain service is up. Starting indexing.")

    # Load resume progress
    progress = _load_progress()
    is_resume = any(progress.get(k) for k in ("phase1_complete", "phase2_complete", "phase3_complete"))
    if is_resume:
        logging.info(
            f"Resuming previous run — "
            f"phase1={'done' if progress.get('phase1_complete') else 'incomplete'}, "
            f"phase2={'done' if progress.get('phase2_complete') else 'incomplete'}, "
            f"phase3={'done' if progress.get('phase3_complete') else 'incomplete'}"
        )

    # Init / append index log
    try:
        mode = "a" if is_resume else "w"
        with open(INDEX_LOG_PATH, mode, encoding="utf-8") as f:
            action = "Resumed" if is_resume else "Started"
            f.write(f"\nIndexing {action} at {time.ctime()}\n")
    except Exception as e:
        logging.warning(f"Could not init log file: {e}")

    # ── Single-pass file collection ───────────────────────────────
    logging.info(f"Scanning {HOME} for files...")
    scan_start = time.time()
    all_files, text_files, image_files = _collect_files(HOME)
    logging.info(
        f"Scan complete in {_elapsed(scan_start)}: "
        f"{len(all_files):,} files, {len(text_files):,} text, {len(image_files):,} images"
    )

    logging.info(f"Connecting to LanceDB at {DB_PATH}...")
    db = lancedb.connect(DB_PATH)

    EMBED_WORKERS = 3

    # ── Phase 1: Filename indexing ────────────────────────────────
    total_files_indexed = 0
    total_files_count = len(all_files)

    if skip_filenames or progress.get("phase1_complete"):
        if progress.get("phase1_complete"):
            logging.info("Phase 1/3 — Already complete, skipping (resuming).")
            # Count what's already indexed for accurate totals
            total_files_indexed = len(_get_existing_paths(db, TABLE_NAME))
        else:
            logging.info("Phase 1/3 — Skipping filename indexing (--skip-filenames).")
    else:
        logging.info("Phase 1/3 — Indexing filenames...")
        phase_start = time.time()
        _write_phase_progress(progress, 1, 0.0, "?", "Indexing file names")

        BATCH_SIZE = 512

        # Resume: open existing table and find already-indexed paths
        existing_filename_paths = _get_existing_paths(db, TABLE_NAME)
        if existing_filename_paths:
            logging.info(f"  [resume] {len(existing_filename_paths):,} filenames already indexed — skipping those")
            table = db.open_table(TABLE_NAME)
        else:
            try:
                db.drop_table(TABLE_NAME)
            except Exception:
                pass
            table = None

        files_to_index = [(p, f) for p, f in all_files if p not in existing_filename_paths]
        total_files_indexed = len(existing_filename_paths)
        next_sample_at = max(5000, total_files_indexed + 5000)

        def _encode_filename_batch(batch):
            """Encode a batch of filenames. Returns list of dicts ready for LanceDB."""
            names = [x["filename"] for x in batch]
            vectors = _remote_encode(names)
            return [
                {"vector": vectors[i], "filename": item["filename"], "path": item["path"]}
                for i, item in enumerate(batch)
            ]

        pending_futures = []
        current_batch = []

        with ThreadPoolExecutor(max_workers=EMBED_WORKERS) as pool:
            for full_path, filename in files_to_index:
                _log_indexed_file("filename", full_path)
                current_batch.append({"filename": filename, "path": full_path})

                if len(current_batch) >= BATCH_SIZE:
                    pending_futures.append(pool.submit(_encode_filename_batch, current_batch))
                    current_batch = []

                    while len(pending_futures) >= EMBED_WORKERS:
                        fut = pending_futures.pop(0)
                        try:
                            data = fut.result()
                        except Exception as e:
                            logging.error(f"Remote encode failed: {e}")
                            continue

                        if table is None:
                            table = db.create_table(TABLE_NAME, data=data)
                        else:
                            table.add(data)

                        total_files_indexed += len(data)
                        pct = 100.0 * total_files_indexed / total_files_count
                        eta = _eta(phase_start, total_files_indexed, total_files_count)
                        logging.info(
                            f"  [filenames] {total_files_indexed:,} / {total_files_count:,} "
                            f"({pct:.1f}%)  ({_elapsed(phase_start)})  ETA {eta}"
                        )
                        _write_phase_progress(progress, 1, pct, eta, "Indexing file names")

                        if total_files_indexed >= next_sample_at:
                            samples = [d["path"] for d in data[:3]]
                            logging.info(f"    sample: {samples}")
                            next_sample_at += 5000

                        del data
                        gc.collect()

            if current_batch:
                pending_futures.append(pool.submit(_encode_filename_batch, current_batch))

            for fut in pending_futures:
                try:
                    data = fut.result()
                except Exception as e:
                    logging.error(f"Remote encode failed: {e}")
                    continue

                if table is None:
                    table = db.create_table(TABLE_NAME, data=data)
                else:
                    table.add(data)

                total_files_indexed += len(data)
                del data
                gc.collect()

        logging.info(f"Phase 1/3 complete — {total_files_indexed:,} filenames indexed in {_elapsed(phase_start)}")
        progress["phase1_complete"] = True
        _save_progress(progress)

    # ── Phase 2: Content indexing ─────────────────────────────────
    CHUNKS_TABLE = "file_chunks"

    if progress.get("phase2_complete"):
        logging.info("Phase 2/3 — Already complete, skipping (resuming).")
        total_chunks_indexed = 0
        total_tokens = 0
        content_files = 0
    else:
        logging.info("Phase 2/3 — Indexing file content (text/PDF/DOCX/XLSX/PPTX/CSV)...")
        _write_phase_progress(progress, 2, 0.0, "?", "Reading file content")
    phase_start = time.time()

    if not progress.get("phase2_complete"):
        # Resume: find paths already in the chunks table
        existing_chunk_paths = _get_existing_paths(db, CHUNKS_TABLE)
        if existing_chunk_paths:
            logging.info(f"  [resume] {len(existing_chunk_paths):,} files already content-indexed — skipping those")
            chunk_table = db.open_table(CHUNKS_TABLE)
        else:
            try:
                db.drop_table(CHUNKS_TABLE)
            except Exception:
                pass
            chunk_table = None

    if not progress.get("phase2_complete"):
        CHUNK_BATCH_SIZE = 16
        current_batch_chunks = []
        current_batch_metadata = []
        total_chunks_indexed = 0
        content_files = 0
        total_text_count = len(text_files)
        next_sample_at = 50

    if not progress.get("phase2_complete"):
        # LLM-based smart filter: batch-classify files that pass static rules.
        # Only filenames + 2-level path context are sent — no file contents, minimal cost.
        llm_skip_paths: set = set()
        def _static_skip(full_path, filename):
            _, ext = os.path.splitext(filename)
            return (
                filename in CONTENT_SKIP_FILENAMES
                or set(full_path.split(os.sep)) & CONTENT_SKIP_DIRS
                or any(filename.endswith(s) for s in CONTENT_SKIP_SUFFIXES)
                or ext.lower() in CONTENT_SKIP_EXTENSIONS
            )

        # Only classify files not already in the chunks table
        llm_candidates = [
            (full_path, filename) for full_path, filename in text_files
            if not _static_skip(full_path, filename) and full_path not in existing_chunk_paths
        ]
        if llm_candidates:
            logging.info(
                f"  [llm-filter] Classifying {len(llm_candidates)} candidate files "
                f"with fast model ({(len(llm_candidates) + LLM_FILTER_BATCH_SIZE - 1) // LLM_FILTER_BATCH_SIZE} batches)..."
            )
            filter_start = time.time()
            llm_skip_paths = _llm_filter_content(llm_candidates)
            logging.info(
                f"  [llm-filter] Done in {_elapsed(filter_start)}: "
                f"{len(llm_skip_paths)}/{len(llm_candidates)} additional files filtered out"
            )

        total_tokens = 0
        skipped_content = 0
        for full_path, filename in text_files:
            # Skip already-indexed files (resume)
            if full_path in existing_chunk_paths:
                continue
            if _static_skip(full_path, filename) or full_path in llm_skip_paths:
                skipped_content += 1
                _log_indexed_file("content-skipped", full_path)
                continue

            try:
                chunks = process_file_content(full_path, chunk_size=512)
                if not chunks:
                    _log_indexed_file("content-empty", full_path)
                    continue

                content_files += 1
                logging.info(f"  [content] indexing: {full_path}")
                _log_indexed_file("content", full_path)

                for i, chunk in enumerate(chunks):
                    if len(chunk) > 512:
                        chunk = chunk[:512]

                    total_tokens += _count_tokens(chunk)
                    current_batch_chunks.append(chunk)
                    current_batch_metadata.append({
                        "filename": filename,
                        "path": full_path,
                        "chunk_id": i,
                        "content": chunk,
                    })

                    if len(current_batch_chunks) >= CHUNK_BATCH_SIZE:
                        try:
                            vectors = _remote_encode(current_batch_chunks)
                        except Exception as e:
                            logging.error(f"Remote encode failed: {e}")
                            current_batch_chunks = []
                            current_batch_metadata = []
                            continue

                        batch_data = [
                            {
                                "vector": vectors[idx],
                                "filename": meta["filename"],
                                "path": meta["path"],
                                "chunk_id": meta["chunk_id"],
                                "content": meta["content"],
                            }
                            for idx, meta in enumerate(current_batch_metadata)
                        ]

                        if chunk_table is None:
                            chunk_table = db.create_table(CHUNKS_TABLE, data=batch_data)
                        else:
                            chunk_table.add(batch_data)

                        total_chunks_indexed += len(batch_data)
                        pct = 100.0 * content_files / total_text_count if total_text_count else 0
                        eta = _eta(phase_start, content_files, total_text_count)
                        logging.info(
                            f"  [content] {content_files:,} / {total_text_count:,} files "
                            f"({pct:.1f}%), {total_chunks_indexed:,} chunks, "
                            f"~{total_tokens:,} tokens  "
                            f"({_elapsed(phase_start)})  ETA {eta}"
                        )
                        _write_phase_progress(progress, 2, pct, eta, "Reading file content")

                        if content_files >= next_sample_at:
                            logging.info(f"    sample: {full_path}")
                            next_sample_at += 500

                        current_batch_chunks = []
                        current_batch_metadata = []
                        del vectors, batch_data
                        gc.collect()

            except Exception:
                pass

        if current_batch_chunks:
            try:
                vectors = _remote_encode(current_batch_chunks)
                batch_data = [
                    {
                        "vector": vectors[idx],
                        "filename": meta["filename"],
                        "path": meta["path"],
                        "chunk_id": meta["chunk_id"],
                        "content": meta["content"],
                    }
                    for idx, meta in enumerate(current_batch_metadata)
                ]
                if chunk_table is None:
                    chunk_table = db.create_table(CHUNKS_TABLE, data=batch_data)
                else:
                    chunk_table.add(batch_data)
                total_chunks_indexed += len(batch_data)
                del vectors, batch_data
                gc.collect()
            except Exception as e:
                logging.error(f"Remote encode failed for final chunk batch: {e}")

        logging.info(
            f"Phase 2/3 complete — {content_files:,} files, "
            f"{total_chunks_indexed:,} chunks, ~{total_tokens:,} tokens "
            f"in {_elapsed(phase_start)} "
            f"({skipped_content:,} boilerplate files skipped)"
        )
        progress["phase2_complete"] = True
        _save_progress(progress)

    # ── Phase 3: Image indexing (CLIP) ────────────────────────────
    IMAGES_TABLE = "images"

    if progress.get("phase3_complete"):
        logging.info("Phase 3/3 — Already complete, skipping (resuming).")
        total_images = len(_get_existing_paths(db, IMAGES_TABLE))
    else:
        logging.info("Phase 3/3 — Indexing images with CLIP...")
        phase_start = time.time()
        _write_phase_progress(progress, 3, 0.0, "?", "Indexing images")

        # Resume: find already-indexed image paths
        existing_image_paths = _get_existing_paths(db, IMAGES_TABLE)
        if existing_image_paths:
            logging.info(f"  [resume] {len(existing_image_paths):,} images already indexed — skipping those")
            img_table = db.open_table(IMAGES_TABLE)
        else:
            try:
                db.drop_table(IMAGES_TABLE)
            except Exception:
                pass
            img_table = None

        vision_model = None
        IMAGE_BATCH_SIZE = 32
        img_batch = []
        total_images = len(existing_image_paths)
        total_image_count = len(image_files)

        def _flush_image_batch(batch, vmodel, tbl, count):
            """Encode and store a batch of images. Returns (model, table, new_count)."""
            if not batch:
                return vmodel, tbl, count

            if vmodel is None:
                from embed_anything import EmbeddingModel, ImageEmbedConfig
                import embed_anything as _ea
                logging.info("  Loading CLIP vision model (embed-anything, first time)...")

                _raw_clip = EmbeddingModel.from_pretrained_hf(model_id="openai/clip-vit-base-patch32")
                _img_cfg = ImageEmbedConfig(batch_size=IMAGE_BATCH_SIZE)

                class _CLIPWrapper:
                    def __init__(self, m, cfg):
                        self.model = m
                        self.cfg = cfg
                    def encode(self, image_path):
                        import numpy as np
                        results = _ea.embed_file(image_path, embedder=self.model, config=self.cfg)
                        if not results:
                            return np.zeros(512, dtype=np.float32)
                        vec = np.array(results[0].embedding, dtype=np.float32)
                        norm = np.linalg.norm(vec)
                        return vec / norm if norm > 0 else vec

                vmodel = _CLIPWrapper(_raw_clip, _img_cfg)

            vectors = []
            valid = []
            for item in batch:
                try:
                    vectors.append(vmodel.encode(item["path"]).tolist())
                    valid.append(item)
                except Exception as e:
                    logging.warning(f"  Skipping image {item['path']}: {e}")

            if valid:
                data = [
                    {"vector": vectors[i], "filename": v["filename"], "path": v["path"]}
                    for i, v in enumerate(valid)
                ]
                if tbl is None:
                    tbl = db.create_table(IMAGES_TABLE, data=data)
                else:
                    tbl.add(data)
                count += len(data)
                del vectors, data

            gc.collect()
            return vmodel, tbl, count

        for full_path, filename in image_files:
            if full_path in existing_image_paths:
                continue
            _log_indexed_file("image", full_path)
            img_batch.append({"filename": filename, "path": full_path})

            if len(img_batch) >= IMAGE_BATCH_SIZE:
                vision_model, img_table, total_images = _flush_image_batch(
                    img_batch, vision_model, img_table, total_images
                )
                pct = 100.0 * total_images / total_image_count if total_image_count else 0
                eta = _eta(phase_start, total_images, total_image_count)
                logging.info(
                    f"  [images] {total_images:,} / {total_image_count:,} "
                    f"({pct:.1f}%)  ({_elapsed(phase_start)})  ETA {eta}"
                )
                _write_phase_progress(progress, 3, pct, eta, "Indexing images")
                img_batch = []

        vision_model, img_table, total_images = _flush_image_batch(
            img_batch, vision_model, img_table, total_images
        )

        if vision_model is not None:
            del vision_model
            gc.collect()

        logging.info(f"Phase 3/3 complete — {total_images:,} images indexed in {_elapsed(phase_start)}")
        progress["phase3_complete"] = True
        _save_progress(progress)

    # ── Done ──────────────────────────────────────────────────────
    with open(INDEX_DONE_MARKER, "w") as f:
        f.write(f"files={total_files_indexed} chunks={total_chunks_indexed} tokens={total_tokens} images={total_images}\n")

    _clear_progress()  # clean up — full run succeeded

    logging.info(
        f"All done!  files={total_files_indexed:,}  chunks={total_chunks_indexed:,}  "
        f"~tokens={total_tokens:,}  images={total_images:,}"
    )


if __name__ == "__main__":
    main()
