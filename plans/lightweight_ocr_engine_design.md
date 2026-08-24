# 轻量 OCR 引擎重实现方案（ppocr_lite）

## 文档状态

- 状态：方案制定完成，待实施
- 日期：2026-08-24
- 前置文档：`plans/rapidocr_codepath_analysis.md`（下称"分析文档"）
- 关系说明：本方案以分析文档为技术底稿，但**冲突处以本方案为准**，本方案反映最新决策

## 一、目标与硬性约束（按用户决策）

| 约束 | 决策 |
|---|---|
| 依赖 | **禁止 cv2、scipy**（安装/打包体积不可接受）；numpy、Pillow 可用（其他组件必需）；其余依赖能省则省 |
| 输出 | 只要文本块文本 + 置信度 `(text, score)`；boxes、word_results、elapse、可视化一概不做 |
| API 形态 | **同步 API**。同一套代码既嵌入 batch_job_runner 子进程 worker，也支持脚本直接调用调试 |
| 精度 | 允许相对 rapidocr 略有下降（插值差异级别）；不允许明显质量退化 |
| 推理设备 | CPU 为主；GPU 只依赖 onnxruntime 自身的 provider 机制自动获得，**不单独引入任何 GPU 组件**，其余代码不得妨碍 onnxruntime 正常运行 |
| 配置 | 不引入配置系统，默认参数对齐 rapidocr 生效值（分析文档第二节） |

## 二、总体设计

### 2.1 包结构

新建独立包 `src/ppocr_lite/`（不改动 image_text_extractor 的流水线骨架）：

```
src/ppocr_lite/
├── __init__.py        # 导出 OcrEngine 与模块级便捷函数
├── __main__.py        # python -m ppocr_lite <image> 调试入口，打印 (text, score)
├── params.py          # 全部默认参数的冻结 dataclass（替代 config.yaml）
├── sessions.py        # ONNX 会话创建、provider 选择、rec 字符表读取
├── image_io.py        # 路径→BGR ndarray（PIL 读图、EXIF 转正、灰度/透明通道合成）
├── preprocess.py      # 整图限界缩放、垂直补边、det/cls/rec 三种输入张量构造
├── det_postprocess.py # DBNet 后处理：二值化→膨胀→连通域→打分→unclip→排序
├── geometry.py        # 凸包、最小面积旋转矩形、四点顺时针排序、单应逆映射采样
├── recognition.py     # 方向分类（cls）+ 批量识别（rec）+ CTC 解码
└── engine.py          # 同步门面 OcrEngine：串起上述全部环节
```

预计总量 900–1200 行（不含测试）。其中真正有难度的只有 `geometry.py`
和 `det_postprocess.py` 的三个算法（见第五节），其余是直白的过程式代码。

### 2.2 数据流（与分析文档第四节一一对应，标注变化点）

```
OcrEngine.recognize(path)
├─ image_io.load_bgr(path)            # A. PIL 读图 + EXIF + 通道合成（保持 RGBA 混合逻辑）
├─ preprocess.limit_image(img)        # B. min_side 30 / max_side 2000，32 倍数取整
├─ preprocess.vertical_padding(img)   # C1. h<=30 或 w/h>8 时上下补黑边
├─ detect(img)                        # C2. det 预处理(736/min) → session.run → DB 后处理(无 cv2)
│                                        └─ 变化点：连通域替代 findContours，自实现 minAreaRect
├─ crop_regions(img, boxes)           # C3. 单应逆映射透视裁剪（自实现，含 rot90）
├─ recognition.classify(crops)        # D. [3,48,192] 批 6，argmax，>0.9 且 180° 则翻转
├─ recognition.recognize(crops)       # E. 批内动态宽 [3,48,W] 批 6，CTC 解码
└─ assemble                           # F. strip 空行过滤 + text_score<0.5 过滤
    └─ 返回 list[(text, score)]，阅读顺序；无文本时返回 []
```

### 2.3 公开 API

```python
# engine.py
class OcrEngine:
    def __init__(
        self,
        *,
        models_dir: str | os.PathLike[str] | None = None,  # None → 自动定位（见第三节）
        log_level: int = logging.WARNING,
    ): ...
    def recognize(self, image: str | os.PathLike[str]) -> list[tuple[str, float]]:
        """同步识别一张图，返回阅读顺序的 (text, score) 列表；无文本返回 []。"""

# __init__.py 另提供一次性便捷函数（内部缓存引擎实例）
def recognize(image_path) -> list[tuple[str, float]]: ...
```

契约细节：

1. **输入只承诺本地路径字符串**（worker 用法）；PIL Image / ndarray 入参作为
   调试便利可选支持，不写入正式契约。
2. **输出即最终结果**：已完成 0.5 阈值过滤与空行过滤，调用方无需再处理 None
   语义（rapidocr 的"空则 None"简化为"空则 []"）。
3. 结果元素为普通 `(str, float)` 元组，天然可 pickle，跨进程 IPC 无需适配。
4. 引擎实例**非线程安全**，沿用现有约定由流水线 pool_size=1 保证串行。
5. 异常正常抛出（FileNotFoundError、ORT 错误等），由 scheduler 统一转失败条目；
   不在引擎内吞异常。

### 2.4 与现有代码的集成点

`src/image_text_extractor/stages.py` 只改一处：

```python
def load_ocr_engine():
    global _engine
    if _engine is None:
        from ppocr_lite import OcrEngine
        _engine = OcrEngine()
    return {"engine_name": "ppocr_lite"}
```

`ocr_image()` 与 `compose_ocr_text()` **零改动**：现有
`_normalize_ocr_items` 本就兼容 `[(text, score), ...]` 形态的纯列表，
新引擎直接输出该形态。

## 三、模型分发与会话管理

### 3.1 模型文件落位

沿用 siglip 特征模型的既有模式：

- 三个 onnx 从 `site-packages/rapidocr/models/` **复制**进仓库 `src/` 根目录：
  - `PP-OCRv6_det_small.onnx`
  - `ch_ppocr_mobile_v2.0_cls_mobile.onnx`
  - `PP-OCRv6_rec_small.onnx`
- 运行期定位：`apppath.app_path / <filename>`（开发态=src/，打包后=_MEIPASS
  资源根），与 `services/import_images.py:143` 定位特征模型完全同构；
  显式传入 `models_dir` 时优先使用（便于实验/测试指向其他目录）。
- **不做哈希校验、不写下载器**：文件损坏时 onnxruntime 加载会直接报错，
  符合本项目"数据完整性要求不高"的约定（AGENTS.md）。
- 许可说明：PP-OCR 模型 Apache-2.0，随仓库再分发的义务与经 rapidocr 分发时相同，无新增合规负担。

### 3.2 会话创建（sessions.py）

- 三个模型各一个 `ort.InferenceSession`，初始化时一次创建、整个进程生命周期复用。
- SessionOptions 对齐 rapidocr：`graph_optimization_level=ORT_ENABLE_ALL`、
  `enable_cpu_mem_arena=false`、`log_severity_level=4`、线程数交给 ORT 自动决策
  （`intra_op_num_threads=0`，与 image_features_extractor 同款注释理由：
  流水线 stage 已是单线程，避免超订）。
- **provider 策略**：`providers=None` 直接交给 `ort.InferenceSession` 默认行为
  （即按 `ort.get_available_providers()` 的顺序全部启用）。当前 CPU 版 onnxruntime
  只有 CPU EP；将来换装 onnxruntime-gpu 时自动走 CUDA EP，本包代码零改动——
  这就是"GPU 只靠 onnxruntime 自动提供"的全部实现。动态输入 shape 由各 EP 自行处理。
- rec 字符表：从 rec 模型 `custom_metadata_map["character"]` 按 `\n` split，
  尾部补 `" "`、头部补 blank（索引 0），仅初始化时执行一次
  （复刻 `OrtInferSession.get_character_list` + `CTCLabelDecode` 的合并逻辑）。

## 四、逐阶段实现要点

以下未标注"变化点"的行为均照抄分析文档对应小节的语义；实现时以 rapidocr
源码为参照核对常量与边界条件。

### 4.1 读图（image_io.py）

- `PIL.Image.open` → `ImageOps.exif_transpose`（失败忽略）→ `np.array` 得 RGB。
- 通道归一到 **BGR**（模型按 BGR 训练，必须保持）：
  - L/RGB：`np.stack`/切片反序转 BGR；
  - LA（灰+alpha）：先按 alpha 合成灰度再升 3 通道；
  - **RGBA：保留 rapidocr `cvt_four_to_three` 的完整公式**——按非透明区平均亮度
    选黑/白底做 alpha 混合（贴纸透明 PNG 的关键路径，不许简化成丢弃 alpha）。
- 实现期对照 `utils/load_image.py` 逐分支抄写公式。

### 4.2 整图预处理（preprocess.py）

- 限界缩放：最长边 >2000 等比缩小、最短边 <30 等比放大，尺寸取 32 倍数；
  尺寸已合规时不重采样（零拷贝直通）。
- 垂直补边：`h<=30 或 w/h>8` 时 `np.pad` 上下对称补至 `max(w/8,30)*2`。
- 由于本项目不消费 boxes，坐标回映链（ratio/padding 记账）**整体省略**，
  这是相对 rapidocr 的第一处纯减法。

### 4.3 检测与 DB 后处理（det_postprocess.py，去 cv2 核心）

预处理：min(h,w)<736 时等比放大到 736、resize 到 32 倍数、`(x/255-0.5)/0.5`、HWC→NCHW float32。

推理输出概率图 `[1,1,H,W]` 后：

1. **二值化** `prob > 0.3`（numpy 比较）。
2. **膨胀**：原为 cv2.dilate(2x2 全 1 核)。用 numpy 实现：
   `bin | shift(bin,(0,1)) | shift(bin,(0,-1)) | shift(bin,(1,0)) | shift(bin,(-1,1))`
   四方向或运算（边缘复制填充），向量化、微秒级。
3. **连通域提取**（替代 findContours RETR_LIST）：
   - 行游程(run-length)编码 + 两遍 union-find 合并，纯 numpy/python 实现；
   - **已知偏差（接受）**：RETR_LIST 会把带洞区域拆内外两条轮廓分别出框，
     连通域方案合并为一个框。带洞字形属罕见边角情况，分析文档已判定可接受。
4. **最小面积旋转矩形**（替代 minAreaRect/boxPoints）：
   - 取组件像素的每行最左/最右点构成候选点集（≤2×H 个点，凸包与全像素集一致）；
   - Andrew 单调链凸包 → 旋转卡壳：最小面积矩形必有一条边与凸包某边共线，
     枚举每条边投影即可，O(n·h) 且 n 很小；
   - 输出四角点，短边 <3 丢弃。
5. **box_score_fast 替代**：框内概率均值改用 `PIL.ImageDraw.polygon` 在框的
   bbox 局部区域栅格化掩膜 + numpy 均值；< box_thresh(0.5) 丢弃。
6. **unclip**：shapely 面积/周长改为鞋带公式 + `np.linalg.norm` 自算；
   外扩仍用 **pyclipper**（`PyclipperOffset(JT_ROUND)`，distance =
   area*unclip_ratio(1.6)/perimeter）。pyclipper 仅 230KB 且无传递负担，
   自实现多边形偏置复杂度高收益低，故**保留该依赖**（见第八节依赖清单）。
   外扩后再走一次第 4 步取矩形，短边 <5 丢弃。
7. 收尾：坐标 clip 到图内、宽或高 ≤3 丢弃、四点顺时针排序
   （order_points_clockwise 复刻）、按 y 行分组（相邻 y 差 ≥10 记新行）+
   行内 x 排序得阅读顺序。

### 4.4 透视裁剪（geometry.py）

替代 `getPerspectiveTransform + warpPerspective(CUBIC, BORDER_REPLICATE)`：

- 直接解 **dst→src 的单应矩阵**（8×8 线性方程组，`np.linalg.solve`），
  对目标网格（`np.meshgrid` 生成的齐次坐标）做逆映射；
- 双线性采样，越界坐标 **clamp 到边缘**（等价 BORDER_REPLICATE 语义）；
- 全程向量化 numpy，裁剪区域都是小图（高 ≤48px 量级），毫秒级以内；
- 高宽比 ≥1.5 时 `np.rot90`，与原逻辑一致。

**变化点（接受）**：INTER_CUBIC→双线性的插值差，影响仅在分数第 2~3 位小数。

### 4.5 方向分类 + 识别（recognition.py）

- cls：按宽高比 argsort 后批 6；`[3,48,192]` 等比缩放右零 pad；
  argmax 得 {0,180}，label=180 且置信度 >0.9 时 `img[::-1, ::-1]` 原地翻转。
- rec：同样排序分批；批内 max_wh_ratio 动态定宽 `[3,48,W]`，等比缩到高 48、
  超宽截断、右零 pad；输出 `[N,T,C]` argmax 后 CTC 解码：
  相邻去重 → 去 blank(0) → 查表 join → score = 选中位置概率均值。
- **变化点（性能优化，无精度影响）**：argmax 与相邻去重用 numpy 向量化
  （`np.argmax(axis=-1)` + `arr[1:] != arr[:-1]` 掩膜），替代逐步 python 循环。
- 最终 txts 顺序必须回填到 det 框阅读顺序（推理批次序 ≠ 输出序），
  该"看不见但有效果"的逻辑严格复刻。

### 4.6 输出组装

strip 后为空的行过滤 + `score < 0.5` 过滤（库内阈值保留，项目侧 0.80 阈值
仍在 compose_ocr_text 中二次施加，两层互不影响）。

## 五、性能设计

定位：**不加复杂度的前提下捡便宜**，不做激进重构。

| 优化点 | 说明 | 预期收益 |
|---|---|---|
| 坐标回映链删除 | 不消费 boxes，op_record/padding/ratio 记账全删 | 减少每图固定开销与代码量 |
| CTC 向量化解码 | 见 4.5 | 每图几 ms |
| 连通域行游程 + 行端点点集 | 点数从 O(像素) 降到 O(H)，凸包极快 | det 后处理几十 ms 内 |
| 膨胀四方向或运算 | 向量化 | 微秒级 |
| 尺寸合规图零重采样 | 直通 | 省 1 次 resize |
| JPEG draft 解码（可选） | 大图 `Image.draft()` 先降采样再解码，仅在最长边>2000 时启用 | 大 JPEG 读图提速明显 |
| 会话常驻 + 初始化一次建 3 session | 与现状一致 | — |

明确**不做**的：多线程/异步流水线改造、模型量化、batch 调大（保持 6 以对齐
行为）、cls 跳过启发式（有精度风险）。

性能预算（对照基线 PP-OCRv6 small CPU ~0.8–1s/张）：

- det 推理占大头不变；预期新增开销：连通域+矩形 ~20–50ms、透视裁剪 ~10–30ms；
- 验收线：单张耗时 ≤ 旧引擎 1.5×（预期实际持平或略优）。

## 六、GPU 支持策略（重申）

- 本包不出现任何 provider 名单硬编码、CUDA 相关分支或 GPU 专用依赖；
- `providers=None` 交由 onnxruntime 默认选择，天然获得其安装包提供的全部 EP；
- 动态 shape 输入是 PP-OCR 模型固有形态，CPU/CUDA EP 均原生支持；
- 打包配置中现有的 onnxruntime DLL upx 排除规则等保持不动。

## 七、依赖与打包变更

requirements.txt：

```diff
-rapidocr==3.9.2
+pyclipper==<当前传递版本，实施时固化>
```

净效果：移除 cv2(113MB)、omegaconf、PyYAML、shapely、colorlog、requests、
tqdm、six 及其传递闭包；新增一个显式 pin 的 pyclipper(0.23MB)。

StickerGenie.spec：

- 删除 `collect_all("rapidocr")` 及其 datas/binaries/hiddenimports 三处引用；
- datas 增加三个 onnx（模式与 siglip 条目相同，落到资源根 "."）。

风险控制：过渡期内 rapidocr 在 venv 中保留用于对比测试，全部验收通过后才
从 requirements.txt 移除并重新打包验证。

## 八、测试计划（unittest）

新增 `tests/test_ppocr_lite_geometry.py`、`tests/test_ppocr_lite_pipeline.py` 等：

1. **单元层**
   - geometry：已知四边形/旋转矩形的凸包、minAreaRect、单应变换往返
     （正变换→逆映射应还原原图）、clamp 边缘语义；
   - det_postprocess：合成概率图（两个分离方块）→ 应出 2 框且坐标正确；
     贴边框 clip、微小噪声被 box_thresh 过滤、阅读顺序排序；
   - recognition：手工构造 logits → CTC 解码文本/score/blank 去重；
   - image_io：合成 RGBA/LA/L 图 → 通道与混合结果断言；
   - preprocess：触发 min_side/max_side/垂直补边的三组合成图断言尺寸。
2. **集成层（模糊比对，标准沿用分析文档第九节）**
   - 环境中仍有 rapidocr 时跑对比：txts 完全一致、scores 差 <0.01、行数与
     顺序一致；rapidocr 缺席则跳过（`skipUnless`）；
   - 重点样例：透明 PNG 贴纸、纯色小图、细长横幅、倒置文本、无文字图
     （断言返回 `[]`）；
   - 样例图用 PIL ImageDraw 现场绘制，避免测试资产依赖。
3. **回归护栏**
   - grep 式断言测试：ppocr_lite 源码不得 import cv2/scipy/shapely/omegaconf；
   - stages.py 集成：mock 引擎注入，确认 ocr_image/compose_ocr_text 行为不变。

## 九、实施步骤

1. 复制模型文件入 `src/`，建包骨架与 params/sessions/image_io（含单测）；
2. geometry.py + det_postprocess.py（含单测，纯函数无 IO 可先行开发）；
3. preprocess + recognition + engine 串联，`python -m ppocr_lite` 手工冒烟；
4. 集成对比测试通过（第九节标准）；
5. 切换 `stages.load_ocr_engine`，跑真实导入流程回归；
6. 移除 rapidocr 依赖、更新 spec，重新打包并在打包产物上验证 OCR 全流程。

步骤 1–4 期间应用仍走 rapidocr，随时可弃坑不产生半成品状态。

## 十、明确不做（Non-goals）

- boxes/词框/可视化/多语种/RTL/CLI 参数体系/模型下载器/多推理引擎——
  与分析文档第六节清单一致，全部舍弃；
- 不追求与 cv2 实现逐位一致（插值与洞语义偏差已在第四、五节声明）；
- 不引入任何新的重型科学计算库；scipy 全程不出现。
