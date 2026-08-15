# 项目约定

## 文本文件编码（Windows / PowerShell 7）

本仓库的源码文件统一使用 UTF-8（无 BOM）。开发任务优先使用 PowerShell 7（`pwsh`），其默认编码即为 UTF-8，读写文件无需额外参数。

- 不要向仓库写入 GBK/GB2312 编码或带 BOM 的文本文件；新增或编辑的文本文件保持 UTF-8 无 BOM。

## apply_patch 用法（PowerShell 7）

应用补丁统一使用项目根目录的 `apply_patch.ps1`，并在 PowerShell 7（`pwsh`）中调用；不要用 `apply_patch.bat`（经 cmd.exe 传参，多行补丁会在首个换行处被截断）。

- 直接传多行补丁：`& .\apply_patch.ps1 $patch`，例如：

```powershell
$patch = @'
*** Begin Patch
*** Add File: test.txt
+hello
*** End Patch
'@
& .\apply_patch.ps1 $patch
```

- 从 UTF-8 文件读补丁：`& .\apply_patch.ps1 -PatchFile .\patch.txt`
- 管道输入：`Get-Content .\patch.txt -Raw | & .\apply_patch.ps1`
- 退出码 0 表示成功；codex.exe 的输出会原样显示。
- codex.exe 路径解析顺序：`-ExePath` 参数 > `CODEX_APPLY_PATCH_EXE` 环境变量 > 脚本内默认 npm 路径（换机器或重装后需更新）。
- 补丁中的相对路径以 `pwsh` 启动时的目录为准；`Set-Location` 不会改变子进程的工作目录，跨目录时请使用绝对路径或直接从目标目录启动 `pwsh`。

## 项目数据完整性需求

本仓库对数据完整性要求不高，并非所有操作都必须原子化。
数据完整性目标是，对于导入和维护操作，只需确保SQLite部分的数据完整，向量数据库的完整性只要能够通过维护功能修复即可。
当进行设计复杂性和数据完整性的权衡时，在满足完整性目标的前提下，尽可能选择简单的设计。

导入操作和数据库维护操作在UI层面互斥，不会同时运行，但是，它们运行时，应用程序的其他部分仍然会共存。

## 项目运行环境

本仓库的依赖全部位于.venv的虚拟环境，不要使用系统解释器
