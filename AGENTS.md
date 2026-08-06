# 项目约定

## 文本文件编码（Windows / PowerShell 5.1）

本仓库的源码文件统一使用 UTF-8（无 BOM）。在 Windows PowerShell 5.1 中读取文件时，必须显式指定 UTF-8，否则中文会被按 GBK 解码成乱码，导致 apply_patch 无法匹配文件内容。

- 读取文件时使用 `Get-Content -Encoding UTF8`（或 `-Raw -Encoding UTF8`），不要使用不带 `-Encoding` 参数的 `Get-Content`。
- 也可以使用 `[System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)`。
- apply_patch 的上下文行或新增行中如果包含中文，必须从 UTF-8 读取结果中原样复制；不要使用乱码文本。也可以用纯 ASCII 代码行作为锚点来避免匹配中文行。
- 不要向仓库写入 GBK/GB2312 编码或带 BOM 的文本文件；新增或编辑的文本文件保持 UTF-8 无 BOM。
