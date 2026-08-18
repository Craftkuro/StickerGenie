# 单实例与重复启动前台激活设计方案

状态：已实施（前台激活采用 Qt 原生行为）

日期：2026-08-17

## 1. 目标与范围

本程序目前没有任何防重复启动机制，用户从同一目录多次启动会打开多个独立实例，
各自占用一份数据库/向量库资源，且体验上不符合桌面程序的常见习惯。

本次目标：

- 同一个目录下的应用只允许运行一个实例；不同目录（便携复制、不同版本）各自独立运行；
- 当检测到已有实例时，不新建窗口、不重复执行启动任务，而是把已存在的实例
  主窗口还原并提到前台（类似其他桌面程序的行为）；
- 仅做前台激活，不转发本次命令行参数（已与用户确认）。

范围：仅涉及启动流程与窗口激活，不改动任何业务功能、文案或信号语义。

## 2. 方案选型

已与用户确认采用 **QLocalServer / QLocalSocket（Qt 原生 IPC）** 方案：

- 主实例启动一个 `QLocalServer` 监听；
- 次实例以 `QLocalSocket` 连接该服务，发送"激活"信号后立即退出；
- 主实例收到连接后把自身主窗口还原并置顶。

优点：纯 Qt 实现（`PyQt6.QtNetwork`），跨平台、无新增第三方依赖，
符合项目"在满足目标前提下优先简单设计"的原则。

不采用纯 Win32 命名互斥体 + `EnumWindows` 方案：需手写更多底层代码，
且按标题/类查找窗口较脆弱。

## 3. 实例标识（按目录隔离）

单实例作用域必须是"应用程序所在目录"，而非全局进程名。标识计算方式：

- 打包后（frozen）：`os.path.dirname(sys.executable)`
- 源码运行：`os.path.dirname(os.path.abspath(__file__))`（即 `src/` 目录）

将上述路径做归一化（取绝对路径、小写）后取 sha1，拼成服务名：

```
StickerGenie-{sha1}
```

归一化 + 哈希的目的：

- 避免路径中的空格、中文、反斜杠等特殊字符破坏管道名；
- 不同目录路径哈希不同，天然实现目录级隔离；
- 同一目录每次启动路径一致，哈希稳定，可正确识别为同一实例。

## 4. 新增模块 `src/services/single_instance.py`

提供两个对外能力：

### 4.1 `build_instance_key() -> str`
按第 3 节规则计算并返回服务名。

### 4.2 `ensure_single_instance(app) -> bool`
- 以 `QLocalSocket` 尝试连接 `build_instance_key()`，`waitForConnected` 成功：
  - 写入 1 字节并 `flush()`、`waitForBytesWritten`；
  - 关闭 socket，**返回 False（次实例）**，主流程随后直接退出；
- 连接失败（说明当前没有主实例）：
  - `QLocalServer.removeServer(key)` 清理上次崩溃可能残留的端点；
  - 创建 `QLocalServer` 并 `listen(key)`；
  - 把 server 挂到 `app` 上（属性引用）防止被 GC 回收；
  - 连接 `server.newConnection` 到内部处理函数：读取并丢弃数据，发射
    `activationRequested` 信号；
  - **返回 True（主实例）**。

模块内定义 `activationRequested = pyqtSignal()`，供主实例把激活事件转交给主窗口。

## 5. 接入点改动

### 5.1 `src/main.py`
- 在 `QApplication` 创建之后、调用 `services.startup.run_startup_tasks()` 之前，
  调用 `ensure_single_instance(application)`；
- 若返回 `False`（次实例）：跳过启动任务与建窗，直接 `return 0`；
- 主实例：建完 `MainWindow` 后，将 `single_instance.activationRequested`
  连接到 `main_window.raise_and_activate`。

### 5.2 `src/ui/main_window.py`
新增方法 `raise_and_activate()`，仅使用 Qt 原生窗口操作：

```python
def raise_and_activate(self) -> None:
    # 若最小化/隐藏，先还原
    if self.windowState() & Qt.WindowState.WindowMinimized:
        self.setWindowState(Qt.WindowState.WindowNoState)
    self.show()
    self.raise_()
    self.activateWindow()
```

## 6. 已知边界

- 极短时间内（毫秒级）同时启动两个实例，存在极小竞态：两者都判定为主实例，
  可能短暂各起一份。对单机桌面应用影响极小，按简单优先原则不引入
  `QLockFile` 等额外串行化机制；若后续需要可再补充。
- 前台激活仅使用 Qt 原生 `raise_()/activateWindow()`；Windows 前台锁定规则可能
  导致窗口无法真正置顶，而只产生任务栏闪烁提醒。该局限性已接受。

## 7. 验证方式

- 先启动一次程序（主实例），再于同一目录双击/命令行二次启动：
   - 应只存在一个进程、一个窗口；
   - 原窗口尝试被还原并激活；Windows 上也可能仅出现任务栏闪烁提醒；
   - 次实例进程迅速退出（任务管理器/进程列表确认无残留）。
- 将程序复制到另一目录启动：两个目录的实例应可同时运行、互不干扰。
- 主实例异常崩溃后，再次启动应能正常成为主实例（验证 `removeServer` 清理残留）。
