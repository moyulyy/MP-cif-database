"""Download the Materials Project AWS Open Data snapshot used by the local CIF index.

The bucket ``materialsproject-build`` is public (no key / no auth / no rate limit).
We mirror the three collections that the index builder consumes:

    collections/<DATE>/materials/nelements=*/symmetry_number=*.jsonl.gz
    collections/<DATE>/thermo/thermo_type=GGA_GGA+U/nelements=*/symmetry_number=*.jsonl.gz
    collections/<DATE>/electronic-structure/nelements=*/symmetry_number=*.jsonl.gz

Only the classic ``mp-<number>`` id system is present in this dated snapshot, which is
what the rest of this project (search / CIF generation) is built around.

Usage::

    python fetch_mp_data.py                # download everything (resumable)
    python fetch_mp_data.py --jobs 24
    python fetch_mp_data.py --list-only    # just (re)build the manifests, no download
    python fetch_mp_data.py --check        # verify sizes of already downloaded files
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BUCKET = "https://materialsproject-build.s3.amazonaws.com"
DATE = "2025-09-25"
BASE_PREFIX = f"collections/{DATE}/"

# (S3 prefix to list, keep-filter for the resulting keys)
COLLECTIONS = [
    (f"{BASE_PREFIX}materials/", lambda k: "/nelements=" in k and k.endswith(".jsonl.gz")),
    (f"{BASE_PREFIX}thermo/thermo_type=GGA_GGA+U/", lambda k: "/nelements=" in k and k.endswith(".jsonl.gz")),
    (f"{BASE_PREFIX}electronic-structure/", lambda k: "/nelements=" in k and k.endswith(".jsonl.gz")),
]

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "mp_open_data"
URLS_FILE = DATA_DIR / "urls.txt"
SIZES_FILE = DATA_DIR / "urls_with_size.txt"

_print_lock = threading.Lock()


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


def s3_url(key: str) -> str:
    """Percent-encode an S3 key so that '+' inside thermo_type becomes %2B."""
    return f"{BUCKET}/{urllib.parse.quote(key, safe='/=-_.~')}"


def list_keys(prefix: str) -> list[tuple[str, int]]:
    """List every object under ``prefix`` via ListObjectsV2 (paginated)."""
    results: list[tuple[str, int]] = []
    token: str | None = None
    while True:
        query = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }
        if token:
            query["continuation-token"] = token
        url = f"{BUCKET}/?" + urllib.parse.urlencode(query)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    body = response.read().decode("utf-8", "replace")
                break
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 4:
                    raise
                log(f"  列出 {prefix} 失败（{error}），重试 {attempt + 1}/5 …")
                time.sleep(2 ** attempt)
        for key, size in re.findall(r"<Key>(.*?)</Key>.*?<Size>(\d+)</Size>", body, re.S):
            results.append((key, int(size)))
        match = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", body)
        token = match.group(1) if match else None
        if not token:
            break
    return results


def build_manifest() -> list[tuple[str, int]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, int]] = []
    seen: set[str] = set()
    for prefix, keep in COLLECTIONS:
        log(f"枚举 {prefix} …")
        for key, size in list_keys(prefix):
            if key.endswith("/") or not keep(key) or key in seen:
                continue
            seen.add(key)
            entries.append((key, size))
    entries.sort()
    with URLS_FILE.open("w", encoding="utf-8", newline="\n") as urls, \
         SIZES_FILE.open("w", encoding="utf-8", newline="\n") as sizes:
        for key, size in entries:
            url = s3_url(key)
            urls.write(url + "\n")
            sizes.write(f"{size}\t{url}\n")
    total = sum(size for _, size in entries)
    log(f"清单完成：{len(entries)} 个文件，共 {total / 1048576:.1f} MB")
    log(f"  {URLS_FILE}")
    log(f"  {SIZES_FILE}")
    return entries


def read_manifest() -> list[tuple[str, int]]:
    if not SIZES_FILE.is_file():
        return build_manifest()
    items: list[tuple[str, int]] = []
    for line in SIZES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        size_text, url = line.split("\t", 1)
        items.append((url, int(size_text)))
    return items


def local_path(url: str) -> Path:
    key = urllib.parse.unquote(url[len(BUCKET) + 1:])
    return DATA_DIR / key


def download_one(url: str, size: int, jobs_hint: int = 1) -> tuple[str, int, str | None]:
    """Return (url, downloaded_bytes, error)."""
    dst = local_path(url)
    if dst.is_file() and dst.stat().st_size == size:
        return url, 0, None  # already complete
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    start = part.stat().st_size if part.is_file() else 0
    if start > size:
        part.unlink(missing_ok=True)
        start = 0
    headers = {"User-Agent": "mp-cif-data/1.0"}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            mode = "ab" if start and response.status == 206 else "wb"
            if mode == "wb":
                start = 0
            with part.open(mode) as stream:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    stream.write(chunk)
    except Exception as error:  # noqa: BLE001 - report and retry at caller level
        return url, 0, str(error)
    if part.stat().st_size != size:
        return url, 0, f"大小不符：{part.stat().st_size} != {size}"
    os.replace(part, dst)
    return url, size, None


def download_all(jobs: int) -> int:
    items = read_manifest()
    if not items:
        log("清单为空。")
        return 1
    pending = [(u, s) for u, s in items if not (local_path(u).is_file() and local_path(u).stat().st_size == s)]
    done_bytes = sum(s for u, s in items) - sum(s for u, s in pending)
    total_bytes = sum(s for _, s in items)
    log(f"清单 {len(items)} 个文件，共 {total_bytes / 1048576:.1f} MB；"
        f"已就绪 {len(items) - len(pending)} 个，待下载 {len(pending)} 个。并发 {jobs}")
    if not pending:
        log("全部文件已完整，无需下载。")
        return 0

    errors: list[str] = []
    errors_lock = threading.Lock()
    counter = {"finished": 0, "bytes": done_bytes, "errors": 0}
    start_time = time.time()
    start_bytes = done_bytes

    def worker(url: str, size: int) -> None:
        last_error = None
        for attempt in range(5):
            _, moved, error = download_one(url, size)
            if error is None:
                with _print_lock:
                    counter["finished"] += 1
                    counter["bytes"] += size if moved else 0
                return
            last_error = error
            time.sleep(min(2 ** attempt, 20))
        with errors_lock:
            counter["errors"] += 1
            errors.append(f"{url}\t{last_error}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(worker, u, s) for u, s in pending]
        while True:
            if all(f.done() for f in futures):
                break
            time.sleep(10)
            elapsed = max(time.time() - start_time, 1e-6)
            rate = (counter["bytes"] - start_bytes) / elapsed / 1048576
            remaining = (total_bytes - counter["bytes"]) / max(rate, 1e-6) if rate else 0
            log(f"  进度 {counter['finished']}/{len(pending)} 文件 · "
                f"{counter['bytes'] / 1048576:.1f}/{total_bytes / 1048576:.1f} MB · "
                f"{rate:.1f} MB/s · 预计剩余 {remaining / 60:.1f} 分钟")
        for future in futures:
            future.result()

    if errors:
        (DATA_DIR / "fetch_errors.log").write_text("\n".join(errors) + "\n", encoding="utf-8")
        log(f"完成，但有 {len(errors)} 个文件失败，详见 {DATA_DIR / 'fetch_errors.log'}")
        return 2
    (DATA_DIR / "fetch_done.flag").write_text(
        time.strftime("%Y-%m-%d %H:%M:%S") + f"\n{len(items)} files\n", encoding="utf-8")
    log(f"完成：{len(items)}/{len(items)} 文件，{total_bytes / 1048576:.1f} MB，"
        f"用时 {(time.time() - start_time) / 60:.1f} 分钟")
    return 0


def check_all() -> int:
    items = read_manifest()
    bad = [u for u, s in items if not (local_path(u).is_file() and local_path(u).stat().st_size == s)]
    if bad:
        log(f"{len(bad)}/{len(items)} 个文件缺失或大小不符：")
        for url in bad[:20]:
            log(f"  {local_path(url).relative_to(DATA_DIR)}")
        if len(bad) > 20:
            log(f"  … 其余 {len(bad) - 20} 个")
        return 1
    log(f"全部 {len(items)} 个文件大小正确。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 Materials Project Open Data 快照")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("JOBS", "16")),
                        help="并发下载数（默认 16，可用环境变量 JOBS 覆盖）")
    parser.add_argument("--list-only", action="store_true", help="只生成清单，不下载")
    parser.add_argument("--check", action="store_true", help="校验已下载文件的字节数")
    args = parser.parse_args()

    if args.check:
        if not SIZES_FILE.is_file():
            build_manifest()
        return check_all()
    if args.list_only:
        build_manifest()
        return 0
    if not SIZES_FILE.is_file():
        build_manifest()
    return download_all(max(1, args.jobs))


if __name__ == "__main__":
    sys.exit(main())
