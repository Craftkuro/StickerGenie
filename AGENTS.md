# 项目约定

## PowerShell 执行规范

在执行任何脚本、命令或处理环境交互时，所有 CLI 命令和脚本执行必须严格遵守以下 PowerShell 规则：

1. 统一 Shell 环境：所有命令必须使用 PowerShell (推荐 pwsh 即 PowerShell 7 以上)。禁止使用 Windows 旧版 cmd.exe 或默认 powershell.exe。
2. 转义字符约束：Bash 与 PowerShell 的转义规则不同，在编写跨平台脚本时，禁止混用 Shell 语法。
3. 严格的错误处理：所有脚本首部必须包含 $ErrorActionPreference = 'Stop'，确保遇到错误时立即中断并报错，避免静默失败。
4. 编码规范：脚本文件生成与读写必须强制指定 -Encoding UTF8，防止中文字符或特殊符号乱码。
5. 管道与对象优先：在处理数据解析时，优先使用 PowerShell 的对象管道特性（如 Select-Object, Where-Object），避免过度依赖传统的文本截取。


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
