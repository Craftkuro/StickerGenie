# coding=utf-8
import hashlib
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4)
def calculate_model_hash(
    model_path: str,
    file_size: int,
    modification_time_ns: int,
) -> str:
    del file_size, modification_time_ns
    digest = hashlib.sha256()
    with open(model_path, "rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"vit_b_16_{digest.hexdigest()[:16]}"


def get_model_hash(model_path: Path) -> str:
    stat = model_path.stat()
    return calculate_model_hash(
        str(model_path.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
    )
