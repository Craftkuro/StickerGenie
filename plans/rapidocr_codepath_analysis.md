# RapidOCR 3.9.2 使用代码路径分析（为轻量重实现做准备）

## 文档状态

- 状态：分析完成
- 日期：2026-08-24
- 分析对象：pip 安装的 `rapidocr==3.9.2`（位于 `.venv/Lib/site-packages/rapidocr/`），运行环境 Python 3.x + onnxruntime 1.28.0
- 目的：项目只用到该库很小一部分功能，计划以更少依赖重新实现；本文档划定必须复刻的代码路径与可以舍弃的部分
- 注意：根目录 `simple_ppocr6/` 与本文档无关（其他会话使用）

## 一、项目侧的调用面（全部调用点）

全项目只有一处使用 rapidocr：`src/image_text_extractor/stages.py`。

```python
# 初始化（worker 进程内一次）
from rapidocr import RapidOCR
_engine = RapidOCR(params={"Global.log_level": "WARNING"})

# 调用（每张图片一次，参数是本地文件路径字符串）
result = _engine(image_path)   # image_path: str（绝对路径）

# 结果消费（_normalize_ocr_items）
if hasattr(items, "txts") and hasattr(items, "scores"):
    return list(zip(items.txts, items.scores))
```

由此确定的使用契约：

| 维度 | 项目实际用法 |
|---|---|
| 构造参数 | 仅覆盖 `Global.log_level="WARNING"`，其余全走默认 config.yaml |
| 输入类型 | `str` 本地图片路径（不传 ndarray/bytes/URL） |
| 调用参数 | 不传任何 kwargs（use_det/use_cls/use_rec/text_score 等全默认） |
| 输出消费 | 只读 `result.txts: Tuple[str]` 和 `result.scores: Tuple[float]`；不读 boxes、word_results、elapse、img |
| 后处理 | 项目自己过滤 score<=0.80 并拼接文本（stages.py 的 compose_ocr_text），不依赖库内 text_score 过滤 |

即：**需要重实现的只是"默认配置下 det+cls+rec 全流水线、输入路径字符串、输出 txts/scores"这一条窄路径。**

## 二、默认配置生效值（来自 rapidocr/config.yaml）

```
Global: text_score=0.5, use_det/cls/rec=true, use_preprocess_img=true,
        min_side_len=30, max_side_len=2000, use_vertical_padding=true,
        min_height=30, width_height_ratio=8, return_word_box=false,
        return_single_char_box=false, font_path=null, log_level=info(被覆盖为warning)

Det: engine=onnxruntime, PP-OCRv6 small, ch; limit_side_len=736, limit_type=min,
     mean/std=[0.5]*3, thresh=0.3, box_thresh=0.5, max_candidates=1000,
     unclip_ratio=1.6, use_dilation=true, score_mode=fast

Cls: engine=onnxruntime, PP-OCRv4 mobile; cls_image_shape=[3,48,192],
     cls_batch_num=6, cls_thresh=0.9, label_list=["0","180"]

Rec: engine=onnxruntime, PP-OCRv6 small, ch; rec_img_shape=[3,48,320], rec_batch_num=6,
     rec_keys_path=null（字典内嵌在 onnx 元数据里）
```

三个模型文件已随 wheel 打包在 `site-packages/rapidocr/models/`，首次初始化时由 `DownloadFile` 校验 SHA256 后落盘（本项目机器上已存在，无需联网）：

| 文件 | 用途 | 输入 | 输出 | 备注 |
|---|---|---|---|---|
| `PP-OCRv6_det_small.onnx` | 文本检测 DBNet | `[N,3,H,W]` 动态 | `[N,1,H,W]` 概率图 | 无自定义元数据 |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 方向分类 | `[N,3,48,W]` | `[N,2]` logits | 无自定义元数据 |
| `PP-OCRv6_rec_small.onnx` | 文本识别 CRNN/CTC | `[N,3,48,W]` | `[N,T,18710]` | **元数据含 `character` 键：18708 行字符字典** |

rec 字典直接从 onnx 的 `custom_metadata_map["character"]` 按 `\n` split 得到（`OrtInferSession.have_key()/get_character_list()`），随后 `CTCLabelDecode.get_character()` 在尾部插入 `" "`、头部插入 `"blank"`（索引 0 为 CTC blank）。因此**不需要下载 ppocr_keys_v1.txt**。

模型 URL 解析链：`InferSession.get_model_url()` → `default_models.yaml` → key `onnxruntime.PP-OCRv6.det.multi_PP-OCRv6_det_small` / `PP-OCRv4.cls.ch_ppocr_mobile_v2.0_cls_mobile` / `PP-OCRv6.rec.multi_PP-OCRv6_rec_small`（modelscope v3.9.2 tag）。

## 三、初始化代码路径

```
RapidOCR.__init__(params={"Global.log_level":"WARNING"})
├─ _load_config()
│   ├─ ParseParams.load("config.yaml")            # omegaconf.OmegaConf.load
│   │   └─ _convert_value_to_enum(Det/Cls/Rec)    # engine_type/model_type/ocr_version/task_type 转 Enum
│   ├─ ParseParams.update_batch(cfg, params)      # omegaconf merge 单个键
│   └─ model_root_dir = 包目录/models
├─ logger.setLevel("WARNING")                      # utils/log.py（colorlog 封装的标准 logging）
└─ _initialize(cfg)
    ├─ TextDetector(cfg.Det)
    │   ├─ DBPostProcess(thresh=0.3, box_thresh=0.5, max_candidates=1000,
    │   │                unclip_ratio=1.6, use_dilation=True, score_mode="fast")
    │   └─ OrtInferSession(cfg.Det)                # 见下
    ├─ TextClassifier(cfg.Cls)
    │   ├─ CLS_SHAPE_BY_OCR_VERSION[PPOCRV4] = [3,48,192]
    │   ├─ ClsPostProcess(["0","180"])
    │   └─ OrtInferSession(cfg.Cls)
    ├─ TextRecognizer(cfg.Rec)
    │   ├─ OrtInferSession(cfg.Rec)
    │   ├─ get_character_dict() → session.have_key()=True → 从 onnx 元数据取字符表
    │   └─ CTCLabelDecode(character=...)
    ├─ LoadImage()
    └─ CalRecBoxes()                               # 仅 return_word_box=True 时才被调用（本项目不会走到）
```

`OrtInferSession.__init__`（inference_engine/onnxruntime/main.py）：
1. `cfg.model_path=None` 且 `model_root_dir` 存在 → `get_model_url(FileInfo)` 查 default_models.yaml → `DownloadFile.run()` 校验/下载模型到 models 目录。
2. `_init_sess_opts(engine_cfg)`：`log_severity_level=4`、`ORT_ENABLE_ALL` 图优化、`enable_cpu_mem_arena=false`、线程数 -1 表示不显式设置。
3. `ProviderConfig.get_ep_list()`：默认仅 CPU EP（use_cuda/use_dml/use_cann/use_coreml 全 false）；CPU EP 参数 `arena_extend_strategy=kSameAsRequested`。
4. `onnxruntime.InferenceSession(model_path, sess_options, providers=[("CPUExecutionProvider", {...})])`。

## 四、单图推理代码路径（`RapidOCR.__call__(path)`）

### 总流程

```
__call__(img_content=path)
├─ update_params(**{})                       # 无 kwargs，空操作
├─ load_img(path)                            # A. 读图
├─ preprocess_img(img)                       # B. 整图缩放限界
├─ run_ocr_steps(img, op_record)
│   ├─ detect_and_crop(img)                  # C. 检测 + 裁剪
│   │   ├─ apply_vertical_padding()          # C1. 细长图上下补边
│   │   ├─ self.text_det(img)                # C2. det 预处理+推理+DB后处理
│   │   └─ crop_text_regions()               # C3. get_rotate_crop_image 透视裁剪
│   ├─ cls_and_rotate(crops)                 # D. 方向分类 + 翻转180°
│   └─ recognize_txt(cls_imgs)               # E. 批量识别 + CTC 解码
└─ build_final_output(...)
    ├─ map_boxes_to_original() / map_img_to_original()   # 坐标映射回原图（项目不消费 boxes，但代码会执行）
    ├─ filter_by_indices(...)                # 过滤空文本行
    ├─ RapidOCROutput(img, boxes, txts, scores, word_results, elapse_list,
    │                 viser=VisRes(...))      # VisRes 构造是惰性的，字体只在 vis() 时才下载
    ├─ filter_by_text_score()                 # score < 0.5 过滤
    └─ 返回 RapidOCROutput                    # len>0 否则返回空的 RapidOCROutput()
```

注意：无任何文本时返回 `RapidOCROutput()`（txts/scores/boxes 均 None）——项目侧 `_normalize_ocr_items` 对此返回空列表，重实现时"找不到文字 → 空"语义要保持。

### A. 读图 LoadImage（utils/load_image.py）

对 `str` 路径分支实际执行的逻辑：
1. `Path.exists()` 校验 → `PIL.Image.open` → `ImageOps.exif_transpose`（按 EXIF 转正，失败则忽略）→ `np.array(img)`（RGB）。
2. `convert_img`：ndim==2 → GRAY2BGR；3 通道且来源为 str/bytes/PIL → RGB2BGR；2 通道（灰+alpha）合成 BGR；4 通道 RGBA → **按 alpha 与非透明区平均亮度自动选黑/白底混合后转 BGR**（贴纸透明背景的关键处理，重实现必须保留）。

### B. 整图预处理（utils/process_img.py）

`use_preprocess_img=True`：`resize_image_within_bounds(img, min_side_len=30, max_side_len=2000)`
- 最长边 >2000：等比缩小到 2000 以内，并 round 到 32 的倍数；
- 最短边 <30：等比放大到 30 以上，同样取整到 32 倍数；
- 记录 `ratio_h/ratio_w` 进 op_record（后续坐标映射用）。

### C1. 垂直补边（apply_vertical_padding）

当 `h <= min_height(30)` 或 `w/h > width_height_ratio(8)`：上下对称补黑边至 `max(w/8, 30)*2` 高度，记录 padding 到 op_record。贴纸类细长横条图会命中此分支。

### C2. 文本检测 TextDetector（ch_ppocr_det/）

预处理 `DetPreProcess(limit_side_len=736, limit_type="min")`：
- `limit_type=min`：min(h,w)<736 时等比放大；否则 ratio=1（注：main.py 的 `get_preprocess` 在 max(h,w)<960/<1500 时会把 limit_side_len 提到 960/1500，>=1500 时 2000——但 limit_type=="min" 时直接用 736，所以本配置恒为 736）；
- resize 到 32 的倍数 → `(img*1/255 - 0.5)/0.5` 归一化 → HWC→CHW → 加 batch 维 float32。

推理：`session.run()` 得概率图 `[1,1,H,W]`。

DB 后处理 `DBPostProcess`（依赖 cv2 + pyclipper + shapely）：
1. `pred[:,0,:,:] > thresh(0.3)` 二值化；`use_dilation=True` → `cv2.dilate`（2x2 核）；
2. `cv2.findContours(RETR_LIST, CHAIN_APPROX_SIMPLE)`，最多 max_candidates=1000 个轮廓；
3. 每个轮廓：`cv2.minAreaRect`+`boxPoints` 排序得最小四点框（get_mini_boxes）；短边 <3 丢弃；
4. `score_mode="fast"`：`box_score_fast` —— fillPoly 掩膜内概率均值；< box_thresh(0.5) 丢弃;
5. `unclip`：shapely Polygon 面积/周长 × unclip_ratio(1.6) = 外扩距离，pyclipper `PyclipperOffset(JT_ROUND)` 外扩，再 get_mini_boxes；短边 <5 丢弃；
6. 坐标缩放回 resize 后的输入图尺寸，clip 到边界；
7. `filter_det_res`：order_points_clockwise 顺时针排序四点、clip、宽或高 ≤3 丢弃;
8. `TextDetector.sorted_boxes`：按 y 坐标稳定排序分行（相邻 y 差 ≥10 记新行）、行内按 x 排序（阅读顺序）。

输出 `TextDetOutput(img, boxes[N,4,2](int32), scores[N])`。

### C3. 区域裁剪（get_rotate_crop_image）

每个 box：按边长构造标准矩形 → `cv2.getPerspectiveTransform` + `warpPerspective(INTER_CUBIC, BORDER_REPLICATE)`；若裁剪高宽比 ≥1.5 再 `np.rot90`。

### D. 方向分类 TextClassifier（ch_ppocr_cls/）

- 按宽高比 argsort 排序加速；batch_num=6 分批；
- `resize_norm_img`：目标 [3,48,192]，等比缩放到高 48（超宽截到 192），`(x/255-0.5)/0.5`，右侧 zero-pad；
- session 输出 `[N,2]` → argmax 取 label ∈ {"0","180"} 及其概率；
- label 含 "180" 且置信度 > cls_thresh(0.9)：`cv2.rotate(img, ROTATE_180)` 原地翻转该行图。

### E. 文本识别 TextRecognizer（ch_ppocr_rec/）

- 同样按宽高比排序；batch_num=6 分批；
- 批内先求 `max_wh_ratio`（初始 320/48）：`resize_norm_img` 把每行图等比缩放到高 48、宽 `ceil(48*ratio)`（超过批内最大宽度则截断），归一化 `(x/255-0.5)/0.5`，右零 pad 到统一宽度 `int(48*max_wh_ratio)`；
- session 输出 `[N,T,C=18710]`；
- `CTCLabelDecode`：argmax 得 token 序列与逐位置概率；去重相邻重复 + 去掉 blank(0)；文本 = 字典查表 join；score = 选中位置概率的 mean(round 5)；空序列 conf=[0]；
- `return_word_box=False`：跳过 get_word_info 词级分析；lang=ch 非 RTL：跳过 bidi 重排。

### F. 输出组装 build_final_output

- det 有框时把 boxes/crop 映射回原图坐标（op_record 里 padding→ratio 逆序回放）；
- 过滤 rec 文本 strip 后为空的行（boxes/scores/txts 同步过滤）；
- 组装 `RapidOCROutput(txts=tuple[str], scores=tuple[float], ...)`；
- `filter_by_text_score`：score < 0.5 的行丢弃；
- 最终返回；`len(result)>0` 否则空对象。

**项目最终拿到的就是 `txts`/`scores` 两个 tuple，且已经过库内 0.5 阈值过滤；项目再自行施加 0.80 阈值。**

## 五、运行时实际依赖清单

上述路径真正 import 的第三方包：

| 包 | 用在哪 | 能否避免 |
|---|---|---|
| numpy | 全部数值处理 | 必需 |
| opencv-python (cv2) | 读图转换、resize/warp、dilate/findContours/minAreaRect/fillPoly | **可避免，见第八节**（安装体积 113MB，是最大单依赖） |
| onnxruntime | 三个模型推理 | 必需（44MB） |
| Pillow | LoadImage（str/bytes 输入走 PIL；EXIF transpose）；去 cv2 方案中兼任缩放/栅格化 | 保留（16MB，PNG/JPEG 解码需要它） |
| omegaconf + PyYAML | config.yaml 加载与 merge | 可完全避免（硬编码默认值即可，项目只改 log_level） |
| pyclipper | DBPostProcess.unclip 多边形外扩 | 必需（230KB，代价可忽略；自实现多边形偏置不划算） |
| Shapely | unclip 中算 polygon area/length | 可避免：面积用鞋带公式、周长用 np.linalg.norm 自算 |
| six | pyclipper 的传递依赖 | 随 pyclipper 走 |
| colorlog | logger 格式化 | 可避免（普通 logging） |
| requests | URL 图片加载 / 模型下载 | 可避免（本项目只用本地路径，模型已随包分发） |
| tqdm | 模型/字体下载进度条 | 可避免（同上） |

rapidocr 声明的其余依赖（openvino/paddle/torch/tensorrt/mnn 等）都是可选引擎，未安装就不会被 import（`get_engine` 先 `import_package` 探测）。

体积现状实测（本项目 .venv）：cv2 113MB > onnxruntime 44MB > numpy 33MB > Pillow 16MB > pyclipper 0.23MB。移除 cv2 后运行栈约 93MB，onnxruntime 成为最大项。

## 六、未被本项目触达的代码（重实现可整体舍弃）

- **其余五种推理引擎**：inference_engine/{openvino,paddle,pytorch,tensorrt,mnn}（pytorch 下还有整套 networks/backbones/heads/necks/transforms）；
- **cal_rec_boxes/**：词级/单字框计算，仅 return_word_box=True 触发；
- **VisRes 的绘图逻辑与字体下载**（vis_res.py 主体、FZYTK.TTF 下载）：构造虽发生在每次 __call__，但字体获取是惰性 property，项目从不调 vis()；
- **CLI**：cli.py、main.py 的 parse_args/main/check_install/generate_cfg；
- **to_json/to_markdown**：output 对象的可选方法；
- **download_models/download_file 的网络逻辑**：模型已打包，仅需"存在即校验/缺失才下载"或干脆要求随包分发（重实现建议：模型放自己包里，删掉下载器）；
- **RTL 语言 bidi 重排、多语种字典下载**（ch 固定）；
- **word_results 相关全部路径**（CTCLabelDecode.get_word_info、WordInfo/WordType）；
- **update_params 的动态参数机制**（项目从不改运行时参数）；
- **omegaconf Enum 配置体系**（ParseParams、typings.py 的 6 个 Enum 大多只为 config 服务）。

## 七、重实现的功能清单（最小集）

按调用顺序需要复刻的函数级清单：

1. **读图**：路径→BGR ndarray（含 EXIF 转正、灰度/RGBA 合成，参考 LoadImage.convert_img 的通道处理）。
2. **整图限界缩放**：min_side 30 / max_side 2000，32 倍数取整，记录 ratio。
3. **垂直补边**：条件 h<=30 或 w/h>8，补到 max(w/8,30)*2，记录 padding。
4. **Det 预处理**：min 边放大到 736、32 取整、mean/std 0.5 归一化、NCHW。
5. **Det 推理**：单个 onnx session（CPU EP、ORT_ENABLE_ALL、arena off）。
6. **DB 后处理**：二值化(0.3)→dilate→findContours→minAreaRect 四点→box_score_fast(0.5)→pyclipper unclip(1.6)→再 minAreaRect→映射坐标→clockwise 排序→尺寸过滤→y/x 阅读序排序。
7. **透视裁剪**：get_rotate_crop_image（含 h/w≥1.5 rot90）。
8. **Cls**：批 6，[3,48,192] resize+pad，argmax，>0.9 且 180 则 cv2.rotate。
9. **Rec**：批 6，批内 max_wh_ratio 动态宽 [3,48,W]，resize+pad，CTC 解码（内嵌字典 +blank/space，mean conf）。
10. **组装**：空文本行过滤 + text_score 0.5 过滤 → `(txts, scores)`。

精度保持提示：
- det 的 `sorted_boxes` 行分组阈值 10px、cls/rec 的批内排序与动态宽 pad 都影响输出顺序与精度，属"看不见但有效果"的逻辑，需一并复刻；
- rec 输出顺序由"按宽高比 argsort + 回填原位"决定，最终 txts 顺序 = det 框的阅读顺序（不是推理批次顺序）；
- `RapidOCROutput` 在完全无文本时 txts/scores 为 None——现有 `_normalize_ocr_items` 兼容了 None；若重实现改为返回空 tuple/list 也兼容，但建议维持"空则 None 或空集合均可"的测试覆盖。

## 八、去 cv2 轻量化方案

动机：cv2 安装体积 113MB，是全项目最大单依赖；`src/` 中没有任何模块直接使用 cv2，只有 rapidocr 内部使用，移除不影响项目其他功能。

### 8.1 cv2 调用替代映射

| cv2 调用 | 所在环节 | 替代方案 | 难度 |
|---|---|---|---|
| `cvtColor`（GRAY2BGR/RGB2BGR/2ch/4ch 合成） | 读图通道转换 | 纯 numpy（RGBA 混合逻辑照抄 LoadImage.cvt_four_to_three 的公式） | 平凡 |
| `resize`（LINEAR/CUBIC） | 整图缩放、det/cls/rec 预处理、裁剪后映射 | Pillow `Image.resize(BILINEAR/BICUBIC)` | 平凡 |
| `copyMakeBorder` | 垂直补边 letterbox | `np.pad` | 平凡 |
| `rotate`(ROTATE_180) | cls 翻转 | `np.rot90(img, 2)` 或切片 `[::-1, ::-1]` | 平凡 |
| `dilate`（2x2 全 1 核） | DB 后处理 | `np.maximum` 四方向位移或运算 | 平凡 |
| **`findContours`**(RETR_LIST) | DB 后处理连通域提取 | 行游程(run-length) + union-find 两遍标注（纯 numpy 可向量化，速度足够）；轮廓点只喂 minAreaRect，用连通域全部像素点集等价（凸包相同） | 中 |
| **`minAreaRect` / `boxPoints`** | 最小外接旋转矩形 | Andrew 单调链凸包 + 旋转卡壳，约 60–80 行纯 python/numpy | 中 |
| `fillPoly` + `mean`（box_score_fast） | 框内概率均值掩膜 | `PIL.ImageDraw.polygon` 栅格化成 mask + numpy 加权均值 | 低 |
| **`getPerspectiveTransform` + `warpPerspective`** | 文本行透视裁剪（INTER_CUBIC + BORDER_REPLICATE） | numpy 解 8×8 线性方程组求单应矩阵 + 逆映射双三次采样（边缘 clamp 即 replicate）；文本行裁剪区域小，性能无忧。也可用 `PIL Image.transform(PERSPECTIVE)`（仅双线性、无 replicate） | 中偏高 |

实现成本集中在三个算法：连通域标注、最小面积旋转矩形、单应变换采样，合计约 250–350 行代码 + 对应 unittest。

### 8.2 已知精度偏差（可接受）

1. **插值像素差异**：Pillow 与 cv2 的 resize 插值实现不同 → ONNX 输入有微小差异 → 分数在小数点后 1~3 位漂移、检测框偶发偏移 1px；
2. **透视裁剪边缘语义**：`warpPerspective` 的 `BORDER_REPLICATE`（边缘复制）在 PIL transform 中不直接支持，自实现时用坐标 clamp 可逼近，边缘 1px 级差异；
3. **findContours 洞语义**：RETR_LIST 把带洞区域拆成外/内两条轮廓分别出框；连通域方案合并为一个框。文本概率掩膜中出现带洞字形属罕见边角情况，接受该偏差。

因此新旧实现的验证不能要求逐位一致，需采用第九节的模糊比对标准。

## 九、验证方式建议

- 用 `plans/image_text_extractor_design.md` 中的实验结论作基准（PP-OCRv6 small CPU ~0.8–1s/张）；
- 对同一批评测图做**模糊比对**（去 cv2 方案下插值差异不可避免）：
  - 文本：`txts` 完全一致（识别结果对像素微扰应当稳定）；
  - 分数：`scores` 逐项差 < 0.01；
  - 若比对 boxes：IoU > 0.98；
  - 行数一致、顺序一致；
- 重点回归样例：透明 PNG 贴纸（4 通道）、纯色小图（触发 min_side 放大）、细长横幅（触发垂直补边与 width_height_ratio）、倒置文本（触发 cls 180°翻转）、无文字图（空结果路径）；
- 性能回归：单张耗时不应超过旧引擎的 1.5 倍（纯 numpy 后处理比 cv2 慢的部分主要在连通域与透视采样，量级应为几十毫秒）。
