# -*- encoding: utf-8 -*-
"""调试入口：python -m ppocr_lite <image>，打印 (text, score) 列表。"""

import sys

from .engine import OcrEngine


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("用法：python -m ppocr_lite <image_path>")
        return 2

    engine = OcrEngine()
    results = engine.recognize(args[0])
    for text, score in results:
        print(f"{score:.5f}\t{text}")
    print(f"共 {len(results)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
