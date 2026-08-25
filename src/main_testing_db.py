# coding=utf-8
"""
stickerdb v1 测试脚本

用于验证 stickerdb/v1 模块是否能正常工作。
测试数据来自 "Testing Data" 目录。
"""

import os
import sys
import hashlib
import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.stickerdb.v1.sticker_db import StickerDBV1
from src.commons.dto import StickerImage, Tag


# 测试数据目录
TESTING_DATA_DIR = project_root / "Testing Data"
TEST_IMAGE_PATH = TESTING_DATA_DIR / "example.jpg"
TEST_TAGS_FILE = TESTING_DATA_DIR / "testing tags.txt"

# 数据库路径
DB_DIR = project_root / "StickerGenie Library" / "Library3" / "db" / "v1"
DB_PATH = DB_DIR / "test_stickerdb.db"


def calculate_file_hash(file_path: str) -> str:
    """
    计算文件的 SHA1 哈希值。
    使用 SHA1 而非 SHA256 的原因：计算速度更快，对于仅区分内容不同文件的需求足够。
    :param file_path: 文件路径
    :return: 十六进制哈希字符串
    """
    sha1_hash = hashlib.sha1()
    with open(file_path, "rb") as f:
        # 分块读取大文件
        for byte_block in iter(lambda: f.read(4096), b""):
            sha1_hash.update(byte_block)
    return sha1_hash.hexdigest()


def read_tags_from_file(tags_file_path: str) -> list[str]:
    """
    从标签文件读取标签列表。
    :param tags_file_path: 标签文件路径
    :return: 标签名称列表
    """
    tags = []
    with open(tags_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:  # 跳过空行
                tags.append(line)
    return tags


def test_stickerdb_v1():
    """
    主测试函数。
    测试 stickerdb v1 的初始化和数据插入功能。
    """
    print("=" * 60)
    print("StickerDB V1 测试脚本")
    print("=" * 60)
    
    # ========== 步骤 1: 准备测试数据 ==========
    print("\n[步骤 1] 准备测试数据...")
    
    # 检查测试图片是否存在
    if not TEST_IMAGE_PATH.exists():
        print(f"错误：测试图片不存在：{TEST_IMAGE_PATH}")
        return False
    print(f"  - 测试图片：{TEST_IMAGE_PATH}")
    
    # 计算图片哈希
    image_hash = calculate_file_hash(str(TEST_IMAGE_PATH))
    print(f"  - 图片哈希：{image_hash[:16]}...")
    
    # 获取图片文件大小
    file_size = TEST_IMAGE_PATH.stat().st_size
    print(f"  - 文件大小：{file_size} 字节")
    
    # 读取标签文件
    if not TEST_TAGS_FILE.exists():
        print(f"错误：标签文件不存在：{TEST_TAGS_FILE}")
        return False
    
    tag_names = read_tags_from_file(str(TEST_TAGS_FILE))
    print(f"  - 标签数量：{len(tag_names)}")
    for tag_name in tag_names:
        print(f"    * {tag_name}")
    
    # ========== 步骤 2: 初始化数据库 ==========
    print("\n[步骤 2] 初始化数据库...")
    
    # 创建数据库目录（如果不存在）
    DB_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  - 数据库目录：{DB_DIR}")
    
    # 删除旧的测试数据库（如果存在）
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"  - 已删除旧的测试数据库")
    
    # 初始化 StickerDB
    print(f"  - 数据库路径：{DB_PATH}")
    db = StickerDBV1(str(DB_PATH))
    print("  - 数据库初始化成功！")
    
    # ========== 步骤 3: 插入测试数据 ==========
    print("\n[步骤 3] 插入测试数据...")
    
    # 创建 StickerImage DTO
    now = datetime.datetime.now()
    sticker = StickerImage()
    sticker.original_file_name = TEST_IMAGE_PATH.name
    sticker.file_size = file_size
    sticker.hash = image_hash
    sticker.imported_at = now
    sticker.modification_date = now
    sticker.size_width = 0  # 图片解析模块尚未开发，暂时设为 0
    sticker.size_height = 0
    sticker.vectordb_id = None
    sticker.text_in_image = None
    
    # 创建 Tag DTO 并关联
    for tag_name in tag_names:
        tag = Tag()
        tag.name = tag_name
        tag.description = f"测试标签：{tag_name}"
        tag.enabled = True
        tag.color_rgb = "#FFFFFF"
        sticker.tags.append(tag)
    
    print(f"  - 创建表情包记录：{sticker.original_file_name}")
    print(f"  - 关联标签：{[t.name for t in sticker.tags]}")
    
    # 插入数据库
    db.add_stickers([sticker])
    print("  - 数据插入成功！")
    
    # ========== 步骤 4: 验证数据 ==========
    print("\n[步骤 4] 验证数据...")
    
    # 查询所有表情包
    stickers = db.list_stickers()
    print(f"  - 数据库中的表情包数量：{len(stickers)}")
    
    if len(stickers) == 0:
        print("  - 错误：未查询到任何数据！")
        return False
    
    # 验证插入的数据
    test_sticker = stickers[0]
    print(f"  - 验证记录：")
    print(f"    * ID: {test_sticker.id}")
    print(f"    * 文件名：{test_sticker.original_file_name}")
    print(f"    * 哈希：{test_sticker.hash[:16]}...")
    print(f"    * 标签数量：{len(test_sticker.tags)}")
    
    # 验证标签
    expected_tags = set(tag_names)
    actual_tags = {tag.name for tag in test_sticker.tags}
    
    if expected_tags == actual_tags:
        print(f"  - 标签验证通过：{actual_tags}")
    else:
        print(f"  - 标签验证失败！")
        print(f"    期望：{expected_tags}")
        print(f"    实际：{actual_tags}")
        return False
    
    # 测试按标签查询
    print("\n[步骤 5] 测试按标签查询...")
    for tag_name in tag_names:
        tag = Tag()
        tag.name = tag_name
        results = db.query_by_single_tag(tag)
        print(f"  - 标签 '{tag_name}' 关联的表情包数量：{len(results)}")
        if len(results) != 1:
            print(f"    错误：期望 1 条记录，实际 {len(results)} 条")
            return False
    
    # ========== 测试完成 ==========
    print("\n" + "=" * 60)
    print("测试完成！所有测试通过！")
    print("=" * 60)
    print(f"\n测试数据库位置：{DB_PATH}")
    print("可以使用数据库工具查看详细内容。")
    
    return True


if __name__ == "__main__":
    try:
        success = test_stickerdb_v1()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n测试失败：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)