import os
import lancedb
from PIL import Image
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
    INDEX_DONE_MARKER, CONTENT_SKIP_FILENAMES, CONTENT_SKIP_DIRS,
    CONTENT_SKIP_SUFFIXES, CONTENT_SKIP_EXTENSIONS, INDEX_LOG_PATH
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


def _collect_files(base_dir):
    """Single walk of the directory tree. Returns (all_files, text_files, image_files).

    Each entry is (full_path, filename). text_files and image_files are subsets
    of all_files, classified by extension.
    """
    all_files = []
    text_files = []
    image_files = []

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for file in files:
            if file.startswith("."):
                continue
            _, ext = os.path.splitext(file)
            if ext.lower() in BLOCKED_EXTENSIONS:
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

    # Clear index log
    try:
        with open(INDEX_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(f"Indexing started at {time.ctime()}\n")
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

    if skip_filenames:
        logging.info("Phase 1/3 — Skipping filename indexing (--skip-filenames).")
    else:
        logging.info("Phase 1/3 — Indexing filenames...")
        phase_start = time.time()

        BATCH_SIZE = 512
        try:
            db.drop_table(TABLE_NAME)
        except Exception:
            pass
        table = None
        next_sample_at = 5000

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
            for full_path, filename in all_files:
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

    # ── Phase 2: Content indexing ─────────────────────────────────
    logging.info("Phase 2/3 — Indexing file content (text/PDF/DOCX/XLSX/PPTX/CSV)...")
    phase_start = time.time()

    CHUNKS_TABLE = "file_chunks"
    try:
        db.drop_table(CHUNKS_TABLE)
    except Exception:
        pass
    chunk_table = None

    CHUNK_BATCH_SIZE = 16
    current_batch_chunks = []
    current_batch_metadata = []
    total_chunks_indexed = 0
    content_files = 0
    total_text_count = len(text_files)
    next_sample_at = 50

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

    llm_candidates = [
        (full_path, filename) for full_path, filename in text_files
        if not _static_skip(full_path, filename)
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

    skipped_content = 0
    for full_path, filename in text_files:
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
                        f"({pct:.1f}%), {total_chunks_indexed:,} chunks  "
                        f"({_elapsed(phase_start)})  ETA {eta}"
                    )

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
        f"{total_chunks_indexed:,} chunks in {_elapsed(phase_start)} "
        f"({skipped_content:,} boilerplate files skipped)"
    )

    # ── Phase 3: Image indexing (CLIP) ────────────────────────────
    logging.info("Phase 3/3 — Indexing images with CLIP...")
    phase_start = time.time()

    IMAGES_TABLE = "images"
    try:
        db.drop_table(IMAGES_TABLE)
    except Exception:
        pass
    img_table = None

    vision_model = None
    IMAGE_BATCH_SIZE = 32
    img_batch = []
    total_images = 0
    total_image_count = len(image_files)

    def _flush_image_batch(batch, vmodel, tbl, count):
        """Encode and store a batch of images. Returns (model, table, new_count)."""
        if not batch:
            return vmodel, tbl, count

        if vmodel is None:
            from sentence_transformers import SentenceTransformer
            logging.info("  Loading CLIP vision model (first time)...")
            vmodel = SentenceTransformer("clip-ViT-B-32", device="cpu")

        vectors = []
        valid = []
        for item in batch:
            try:
                img = Image.open(item["path"])
                vectors.append(vmodel.encode(img).tolist())
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
            img_batch = []

    vision_model, img_table, total_images = _flush_image_batch(
        img_batch, vision_model, img_table, total_images
    )

    if vision_model is not None:
        del vision_model
        gc.collect()

    logging.info(f"Phase 3/3 complete — {total_images:,} images indexed in {_elapsed(phase_start)}")

    # ── Done ──────────────────────────────────────────────────────
    with open(INDEX_DONE_MARKER, "w") as f:
        f.write(f"files={total_files_indexed} chunks={total_chunks_indexed} images={total_images}\n")

    logging.info(
        f"All done!  files={total_files_indexed:,}  chunks={total_chunks_indexed:,}  images={total_images:,}"
    )


if __name__ == "__main__":
    main()
