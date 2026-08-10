# coding=utf-8
"""RapidOCR 实验脚本：批量识别图片中的文本（默认 PP-OCRv6 small，覆盖中英日）。

用法示例：
    python ocr_experiment.py 图片.png
    python ocr_experiment.py ./图片目录
    python ocr_experiment.py --min-score 0.5 a.png b.jpg
    python ocr_experiment.py --json a.png > result.json

默认输出每个图片的识别文本行及置信度；--json 时向标准输出打印 JSON 摘要。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
    ".gif",
}


def _collect_images(raw_paths: list[str]) -> list[Path]:
    images: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_paths:
        path = Path(raw)
        if not path.exists():
            print(f"警告：路径不存在，跳过：{path}", file=sys.stderr)
            continue
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() in IMAGE_EXTENSIONS
                ):
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        images.append(candidate)
        elif path.is_file():
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                print(
                    f"警告：不是支持的图片格式，跳过：{path}",
                    file=sys.stderr,
                )
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                images.append(path)
        else:
            print(f"警告：不是文件也不是目录，跳过：{path}", file=sys.stderr)
    return images


def _recognize(engine, image_path: Path, min_score: float) -> dict:
    try:
        result = engine(str(image_path))
    except Exception as exc:
        return {
            "path": str(image_path),
            "error": f"{type(exc).__name__}: {exc}",
        }

    lines: list[dict] = []
    elapse = None
    if result is not None:
        txts = tuple(getattr(result, "txts", ()) or ())
        scores = tuple(getattr(result, "scores", ()) or ())
        elapse = getattr(result, "elapse", None)
        for text, score in zip(txts, scores):
            text = str(text).strip()
            if not text:
                continue
            if float(score) < min_score:
                continue
            lines.append({"text": text, "score": round(float(score), 4)})
    return {
        "path": str(image_path),
        "elapse": round(float(elapse), 4) if elapse is not None else None,
        "lines": lines,
    }


def main() -> int:
    # Windows 控制台可能默认使用 GBK，统一用 UTF-8 输出，避免中文乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(
        description="使用 RapidOCR（默认 PP-OCRv6 small）识别图片中的文本。"
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="图片文件或目录，目录会递归扫描",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="只输出置信度不低于该值的识别结果（默认 0.0）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="向标准输出打印 JSON 摘要（便于对比实验）",
    )
    args = parser.parse_args()

    images = _collect_images(args.paths)
    if not images:
        print("没有找到可识别的图片。", file=sys.stderr)
        return 1

    try:
        from rapidocr import RapidOCR

        engine = RapidOCR(params={"Global.log_level": "WARNING"})
    except Exception as exc:
        print(
            f"初始化 RapidOCR 失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    results = []
    total = len(images)
    for index, image_path in enumerate(images, start=1):
        print(f"[{index}/{total}] {image_path}")
        info = _recognize(engine, image_path, args.min_score)
        results.append(info)
        if "error" in info:
            print(f"  识别失败：{info['error']}")
            continue
        if not info["lines"]:
            print("  未检测到文本")
        else:
            for line in info["lines"]:
                print(f"  {line['text']}  ({line['score']:.4f})")
        if info["elapse"] is not None:
            print(f"  耗时：{info['elapse']:.4f} 秒")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
