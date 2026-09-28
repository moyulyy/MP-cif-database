"""Recover any missing / incomplete MP shards using parallel HTTP range requests.

The default single-stream downloader can stall on a handful of objects when the
network path to S3 is throttled.  This tool splits each remaining object into small
byte ranges, downloads them concurrently and concatenates the result, which works
around per-connection bandwidth limits.

Usage::

    D:/miniconda3/envs/chem_env/python.exe tools/recover_missing.py --workers 48
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch_mp_data import DATA_DIR, SIZES_FILE, local_path, log  # noqa: E402

DEFAULT_CHUNK = 512 * 1024      # 512 KiB per range request
DEFAULT_WORKERS = 48


def _fetch_range(url: str, start: int, end: int, dst: Path) -> None:
    if dst.is_file() and dst.stat().st_size == end - start + 1:
        return
    request = urllib.request.Request(
        url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "mp-cif-recover/1.0"})
    last_error = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
            if len(data) != end - start + 1:
                raise IOError(f"range {start}-{end} returned {len(data)} bytes")
            dst.write_bytes(data)
            return
        except (urllib.error.URLError, socket.timeout, TimeoutError, IOError, OSError) as error:
            last_error = error
            time.sleep(min(1.5 ** attempt, 15))
    raise RuntimeError(f"{url} range {start}-{end}: {last_error}")


def download_parallel(url: str, size: int, workers: int, chunk: int) -> None:
    final = local_path(url)
    if final.is_file() and final.stat().st_size == size:
        return
    final.parent.mkdir(parents=True, exist_ok=True)
    ranges = []
    position = 0
    while position < size:
        end = min(position + chunk, size) - 1
        ranges.append((position, end))
        position = end + 1
    with tempfile.TemporaryDirectory(dir=str(final.parent)) as tmp:
        tmpdir = Path(tmp)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(ranges))) as pool:
            futures = [pool.submit(_fetch_range, url, start, end, tmpdir / str(index))
                       for index, (start, end) in enumerate(ranges)]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        part = final.with_name(final.name + ".part")
        with part.open("wb") as stream:
            for index in range(len(ranges)):
                stream.write((tmpdir / str(index)).read_bytes())
        if part.stat().st_size != size:
            part.unlink(missing_ok=True)
            raise IOError(f"assembled size {part.stat().st_size} != {size}")
        os.replace(part, final)


def main() -> int:
    parser = argparse.ArgumentParser(description="并行分块补全缺失的 MP 分片")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    parser.add_argument("--list", action="store_true", help="只列出缺失文件")
    args = parser.parse_args()

    missing = []
    for line in SIZES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        size_text, url = line.split("\t", 1)
        size = int(size_text)
        final = local_path(url)
        if not (final.is_file() and final.stat().st_size == size):
            missing.append((url, size, final.stat().st_size if final.is_file() else 0))

    if not missing:
        log("没有缺失文件，全部完整。")
        return 0
    log(f"缺失 / 不完整 {len(missing)} 个文件，共 "
        f"{sum(s for _, s, _ in missing) / 1048576:.1f} MB")
    if args.list:
        for url, size, have in missing:
            log(f"  {size / 1048576:7.1f} MB (have {have / 1048576:5.1f}) {local_path(url).relative_to(DATA_DIR)}")
        return 0

    failures = 0
    for index, (url, size, _) in enumerate(missing, 1):
        log(f"[{index}/{len(missing)}] {local_path(url).relative_to(DATA_DIR)} ({size / 1048576:.1f} MB)")
        start = time.time()
        try:
            download_parallel(url, size, args.workers, args.chunk)
            log(f"    完成，用时 {time.time() - start:.1f}s")
        except Exception as error:  # noqa: BLE001
            failures += 1
            log(f"    失败：{error}")
    if failures:
        log(f"仍有 {failures} 个文件失败。")
        return 1
    log("全部缺失文件已补全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
