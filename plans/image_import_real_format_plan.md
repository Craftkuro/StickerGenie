# 图片导入真实格式校正计划

## 文档状态

- 状态：实施已完成（2026-08-19）
- 日期：2026-08-19
- 范围：图片导入和图库备份导入时，按文件内容识别真实图片格式，并使 SQLite 中的扩展名与 Blob 文件扩展名保持一致

## 背景与问题

当前图片导入链路只按源文件路径的 `Path.suffix` 记录扩展名：

1. `get_image_metadata()` 将 `file_path.suffix.lower()` 写入 `StickerImageMetadata.extension`。
2. `_metadata_to_sticker_image()` 将该值复制到 `StickerImage.extension`。
3. `StickerDBV1` 将 `StickerImage.extension` 写入 `sticker_images.extension`。
4. `BlobStorage.store_file()` 再次从源路径提取扩展名，并用它生成 Blob 路径 `<hash><extension>`。

`PIL.Image.open()` 当前已经按文件内容打开图片并读取尺寸，但实际的 `img.format` 没有被保存。因此，PNG 数据使用 `.jpg` 文件名时，当前结果会是：

- SQLite 扩展名为 `.jpg`；
- Blob 文件名为 `<hash>.jpg`；
- 原始数据仍然是 PNG；
- 依赖扩展名的 GIF 判断、文件信息显示和导出文件名可能不正确。

当前存在两处独立的扩展名推导：元数据提取和 Blob 复制。两者通常都读取同一个源路径，但都可能记录错误的源文件扩展名。

## 目标

- 用图片内容识别出的真实格式生成规范扩展名。
- 让 `StickerImage.extension`、SQLite `extension` 和 Blob 文件名使用同一个真实扩展名。
- 保留原始输入路径作为复制源。
- 复制完成后，OCR、向量生成和后续图库操作继续使用 Blob 管理路径。
- 保持 `BlobStorage.store_file()` 现有调用方的默认行为，避免无关模块产生兼容性变化。
- 不修改数据库表结构。

## 已确认的设计

### 1. 在元数据提取阶段识别真实格式

修改 `src/utils/image_metadata.py` 的 `get_image_metadata()`：

- 在现有的 `Image.open(file_path)` 调用中读取 `img.format`。
- 在同一次打开操作中继续读取图片尺寸，避免额外打开文件。
- 将 PIL 格式名转换为带点号、小写的规范扩展名，例如：
  - `JPEG` -> `.jpg`
  - `PNG` -> `.png`
  - `GIF` -> `.gif`
  - `BMP` -> `.bmp`
  - `WEBP` -> `.webp`
  - `TIFF` -> `.tif`
  - `AVIF` -> `.avif`
  - `HEIF` -> `.heif`
- 不直接使用 `f".{img.format.lower()}"`，因为 PIL 格式名不一定就是合法或期望的文件扩展名。
- 对没有映射的格式或无法取得格式名的图片抛出 `ValueError`，不回退到错误的源文件扩展名。

`original_file_name` 和 `relative_path` 继续保留源文件信息，不在本计划中重命名原始文件。

### 2. 为 Blob 复制增加扩展名覆盖选项

修改 `src/blob_storage/blob_storage.py` 的 `BlobStorage.store_file()`，增加关键字参数：

```python
def store_file(
    self,
    source_file_path: str,
    file_hash: str | None = None,
    *,
    extension_override: str | None = None,
) -> BlobFileEntity:
```

行为：

- `extension_override is None` 时，保持当前逻辑，继续使用源路径扩展名。
- 提供覆盖值时，使用覆盖值生成目标路径和 `BlobFileEntity`。
- 覆盖值统一规范化为小写，并校验必须是安全的单一扩展名，不能包含路径分隔符。
- 文件内容 hash 的计算和校验逻辑不改变。
- Blob 目标路径仍由 `BlobStorage` 根据 hash 和最终扩展名生成，不由调用方手工拼接。

### 3. 普通图片导入传递真实扩展名

修改 `src/services/import_images.py` 的 `_commit_candidates()`：

- `candidate.file_path` 继续作为原始输入文件的复制源。
- `candidate.file_hash` 继续作为内容 hash。
- 使用 `candidate.sticker.extension` 作为 `extension_override`。
- `store_file()` 返回的 `BlobFileEntity` 继续交给 `read_file()`，取得实际的 Blob 管理路径。
- 不使用 `candidate.file_path` 替代 Blob 路径进行 OCR 或向量生成；后续处理必须继续读取已复制的 Blob 文件。

不修改 `ImportCandidate`，因为其中已有源路径、hash 和包含真实扩展名的 `StickerImage`。

### 4. 图库备份导入也传递真实扩展名

修改 `src/services/import_library.py` 的备份图片导入调用：

- `get_image_metadata(source)` 得到的真实扩展名写入 `StickerImage`。
- 调用 `blob_storage.store_file()` 时传入 `file_metadata.extension` 作为 `extension_override`。

这一步是必需的。备份中的图片文件名可能已经带有错误扩展名；如果只修改普通图片导入，备份导入会再次出现 SQLite 扩展名与 Blob 文件名不一致。

## 变更范围

### 需要修改

- `src/utils/image_metadata.py`
  - 读取 PIL 实际格式。
  - 增加格式到规范扩展名的转换逻辑。
- `src/blob_storage/blob_storage.py`
  - 增加可选的 `extension_override` 参数。
  - 校验并使用最终扩展名生成 Blob 路径。
- `src/services/import_images.py`
  - 普通导入调用 `store_file()` 时传递 `candidate.sticker.extension`。
- `src/services/import_library.py`
  - 备份导入调用 `store_file()` 时传递 `file_metadata.extension`。
- 相关测试文件
  - 增加错扩展名图片的元数据、Blob 和端到端导入测试。
  - 调整使用 `store_file` mock 的取消测试，使其接受新的关键字参数。

### 不需要修改

- `StickerImageMetadata`、`StickerImage` 和 SQLite 表结构。
- 图片导入对话框和 `ImportImagesRequest`，因为它们只传递路径。
- 缩略图、OCR、向量生成的读取协议。
- 图库浏览、删除和数据库维护逻辑；它们会自动使用修正后的数据库扩展名。

## 兼容性与边界

### 现有 BlobStorage 调用方

`extension_override` 是可选关键字参数，未传入时保持原行为。除两个导入入口外的现有调用方无需修改。

### 原始文件名与导出

本计划只校正程序内部管理的扩展名，不修改 `original_file_name`。因此，源文件为 `foo.jpg` 但内容为 PNG 时：

- Blob 文件会正确保存为 `<hash>.png`；
- SQLite 扩展名会是 `.png`；
- `original_file_name` 仍是 `foo.jpg`；
- 当前图库导出逻辑仍可能使用 `foo.jpg` 作为导出文件名。

是否在导出阶段也规范化原始文件名属于独立需求，不纳入本次改动，避免改变“保留原始文件名”的现有语义。

### 已有图库数据

本计划只保证新导入数据。已有错误记录不会因为修改导入逻辑自动修复，原因是普通导入会按 hash 去重，重新导入相同内容通常不会进入 Blob 修复流程。

已有数据的格式校正需要单独设计维护或迁移操作，至少要同时处理：

- 按文件内容重新识别格式；
- 更新 SQLite `extension`；
- 重命名 Blob 文件；
- 处理缩略图缓存和中断恢复。

## 测试计划

### 元数据识别

- 创建 PNG 内容并保存为 `.jpg` 文件名。
- 断言 `get_image_metadata()` 返回 `.png`。
- 覆盖 JPEG/GIF 等至少一个扩展名不匹配的案例。
- 对无法识别或不支持的实际格式，断言抛出 `ValueError`。

### BlobStorage

- 源文件使用错误扩展名时传入 `extension_override`。
- 断言返回的 `BlobFileEntity.extension` 为真实扩展名。
- 断言正确扩展名的 Blob 文件存在，错误扩展名文件不会被新建。
- 断言不传 `extension_override` 的旧调用行为不变。

### 普通图片导入

- 导入错扩展名图片。
- 断言 SQLite 中的 `extension` 为真实扩展名。
- 断言 Blob 可通过 `(hash, 真实扩展名)` 读取。
- 开启 OCR 或向量生成时，断言提取器收到的是 Blob 路径而不是原始输入路径。
- 保留取消发生在 Blob 复制后、SQLite 提交前的现有语义。

### 图库备份导入

- 构造文件名扩展名错误但内容有效的备份图片。
- 断言备份导入后的 SQLite 和 Blob 使用真实扩展名。
- 覆盖 hash 去重、损坏文件和取消场景。

## 实施顺序

1. 增加并测试真实格式到规范扩展名的转换逻辑。
2. 为 `BlobStorage.store_file()` 增加并测试 `extension_override`。
3. 修改普通图片导入和备份导入的两个调用点。
4. 更新受影响的 mock 和新增端到端测试。
5. 使用项目虚拟环境运行标准 unittest 测试集：

```text
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
```

## 验收标准

- PNG 数据命名为 `.jpg` 导入后，SQLite 扩展名和 Blob 文件扩展名均为 `.png`。
- 通过数据库中的 hash 和 extension 能够稳定读取 Blob。
- OCR、向量生成、缩略图和删除流程仍能正常访问导入后的图片。
- 普通导入和备份导入行为一致。
- 未传入 `extension_override` 的 BlobStorage 调用方行为不变。
- 不引入数据库迁移，不修改导入 UI，不改变原始文件名字段的语义。

## 实施记录

- `get_image_metadata()` 已改为读取 `PIL.Image.format`，并将常用实际格式转换为规范扩展名。
- `BlobStorage.store_file()` 已增加可选的关键字参数 `extension_override`，并对覆盖扩展名进行规范化与安全校验。
- 普通图片导入和图库备份导入均已将真实扩展名传递给 Blob 存储。
- 新增错扩展名的元数据、Blob、普通导入和备份导入回归测试。
- 更新 Blob 复制取消测试中的 mock，使其兼容新的关键字参数。
- 相关测试：40 项通过，2 项按环境条件跳过。
- 完整测试集：541 项通过，5 项按环境条件跳过。
