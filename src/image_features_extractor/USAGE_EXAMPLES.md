# Image Features Extractor 使用示例集

本文档提供了各种实际使用场景的完整示例代码，帮助你快速集成和使用特征提取器。

## 目录

- [基础示例](#基础示例)
- [批量处理](#批量处理)
- [错误处理和重试](#错误处理和重试)
- [进度跟踪](#进度跟踪)
- [与现有应用集成](#与现有应用集成)
- [性能监控](#性能监控)
- [高级用法](#高级用法)

---

## 基础示例

### 示例 1: 最简单的使用方式

```python
from image_features_extractor import ImageFeaturesExtractor

# 使用上下文管理器自动管理生命周期
with ImageFeaturesExtractor(num_workers=2) as extractor:
    # 提取单张图像特征
    features = extractor.extract_features_sync("image.jpg")
    print(f"特征维度: {features.shape}")
    print(f"特征范围: [{features.min():.3f}, {features.max():.3f}]")
```

### 示例 2: 手动管理生命周期

```python
from image_features_extractor import ImageFeaturesExtractor

# 创建提取器
extractor = ImageFeaturesExtractor(
    model_path="vit_b_16_features.onnx",
    num_workers=2,
    max_queue_size=100
)

# 启动
extractor.start()

try:
    # 使用提取器
    features = extractor.extract_features_sync("image.jpg")
    print(f"提取成功: {features.shape}")
finally:
    # 确保停止
    extractor.stop()
```

### 示例 3: 异步提取单张图像

```python
from image_features_extractor import ImageFeaturesExtractor
import time

def on_complete(result):
    if result.is_success:
        print(f"✓ 提取成功: {result.image_path}")
        print(f"  特征维度: {result.features.shape}")
        print(f"  处理耗时: {result.processing_time:.2f}秒")
    else:
        print(f"✗ 提取失败: {result.image_path}")
        print(f"  错误信息: {result.error_message}")

with ImageFeaturesExtractor(num_workers=2) as extractor:
    # 提交异步任务
    task_id = extractor.extract_features_async(
        "image.jpg",
        callback=on_complete
    )
    
    print(f"任务已提交: {task_id}")
    
    # 等待任务完成
    time.sleep(2)
```

---

## 批量处理

### 示例 4: 批量处理文件夹中的图像

```python
from pathlib import Path
from image_features_extractor import ImageFeaturesExtractor
import numpy as np

def batch_extract_features(image_dir, output_file, num_workers=2):
    """
    批量提取文件夹中所有图像的特征
    
    参数:
        image_dir: 图像文件夹路径
        output_file: 输出特征文件路径(.npz)
        num_workers: Worker 数量
    """
    # 扫描图像文件
    image_dir = Path(image_dir)
    extensions = ['*.jpg', '*.jpeg', '*.png', '*.bmp']
    image_files = []
    for ext in extensions:
        image_files.extend(image_dir.glob(ext))
    
    print(f"找到 {len(image_files)} 张图像")
    
    # 创建提取器
    with ImageFeaturesExtractor(num_workers=num_workers) as extractor:
        features_dict = {}
        failed_images = []
        
        # 批量提取
        for i, img_path in enumerate(image_files, 1):
            try:
                features = extractor.extract_features_sync(str(img_path), timeout=60.0)
                features_dict[img_path.name] = features
                print(f"[{i}/{len(image_files)}] ✓ {img_path.name}")
            except Exception as e:
                failed_images.append((img_path.name, str(e)))
                print(f"[{i}/{len(image_files)}] ✗ {img_path.name}: {e}")
        
        # 保存特征
        if features_dict:
            np.savez_compressed(output_file, **features_dict)
            print(f"\n特征已保存到: {output_file}")
            print(f"成功: {len(features_dict)}, 失败: {len(failed_images)}")
        
        # 保存失败列表
        if failed_images:
            failed_file = Path(output_file).with_suffix('.failed.txt')
            with open(failed_file, 'w', encoding='utf-8') as f:
                for name, error in failed_images:
                    f.write(f"{name}: {error}\n")
            print(f"失败列表已保存到: {failed_file}")

# 使用示例
batch_extract_features("images/", "features.npz", num_workers=4)
```

### 示例 5: 异步批量处理

```python
from pathlib import Path
from image_features_extractor import ImageFeaturesExtractor
import time
import numpy as np

def async_batch_extract(image_dir, output_file, num_workers=2):
    """异步批量提取特征"""
    
    # 扫描图像
    image_files = list(Path(image_dir).glob("*.jpg"))
    total = len(image_files)
    print(f"找到 {total} 张图像")
    
    # 结果收集
    results = {}
    completed = [0]  # 使用列表以便在闭包中修改
    
    def on_complete(result):
        completed[0] += 1
        if result.is_success:
            results[Path(result.image_path).name] = result.features
            print(f"[{completed[0]}/{total}] ✓ {Path(result.image_path).name}")
        else:
            print(f"[{completed[0]}/{total}] ✗ {Path(result.image_path).name}: {result.error_message}")
    
    with ImageFeaturesExtractor(num_workers=num_workers) as extractor:
        # 提交所有任务
        for img_path in image_files:
            extractor.extract_features_async(
                str(img_path),
                callback=on_complete
            )
        
        # 等待所有任务完成
        print("等待任务完成...")
        while completed[0] < total:
            time.sleep(0.1)
        
        # 保存结果
        if results:
            np.savez_compressed(output_file, **results)
            print(f"\n特征已保存: {output_file}")

# 使用示例
async_batch_extract("images/", "features_async.npz", num_workers=4)
```

### 示例 6: 带速率限制的批量处理

```python
from image_features_extractor import ImageFeaturesExtractor
from pathlib import Path
import time

def rate_limited_batch_extract(image_dir, max_concurrent=10):
    """
    限制并发任务数量的批量处理
    防止内存占用过高
    """
    image_files = list(Path(image_dir).glob("*.jpg"))
    
    with ImageFeaturesExtractor(num_workers=2, max_queue_size=50) as extractor:
        for img_path in image_files:
            # 等待队列有空间
            while extractor.pending_tasks_count >= max_concurrent:
                time.sleep(0.1)
            
            # 提交任务
            extractor.extract_features_async(str(img_path))
            print(f"已提交: {img_path.name} (待处理: {extractor.pending_tasks_count})")
        
        # 等待所有任务完成
        while extractor.pending_tasks_count > 0:
            print(f"等待完成... (剩余: {extractor.pending_tasks_count})")
            time.sleep(1)

# 使用示例
rate_limited_batch_extract("images/", max_concurrent=10)
```

---

## 错误处理和重试

### 示例 7: 自动重试机制

```python
from image_features_extractor import ImageFeaturesExtractor
import time

def extract_with_retry(extractor, image_path, max_retries=3, retry_delay=1.0):
    """
    带重试机制的特征提取
    
    参数:
        extractor: ImageFeaturesExtractor 实例
        image_path: 图像路径
        max_retries: 最大重试次数
        retry_delay: 重试延迟(秒)
    
    返回:
        features: 特征向量
    
    抛出:
        Exception: 所有重试都失败后抛出
    """
    for attempt in range(max_retries):
        try:
            features = extractor.extract_features_sync(image_path, timeout=30.0)
            return features
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"尝试 {attempt + 1} 失败: {e}, {retry_delay}秒后重试...")
                time.sleep(retry_delay)
            else:
                print(f"所有重试都失败")
                raise

# 使用示例
with ImageFeaturesExtractor(num_workers=2) as extractor:
    try:
        features = extract_with_retry(extractor, "problematic_image.jpg")
        print(f"提取成功: {features.shape}")
    except Exception as e:
        print(f"最终失败: {e}")
```

### 示例 8: 分类错误处理

```python
from image_features_extractor import (
    ImageFeaturesExtractor,
    ModelNotFoundError,
    WorkerInitError,
    TaskSubmissionError,
    InvalidImageError,
)

def robust_feature_extraction(image_path):
    """健壮的特征提取，详细的错误处理"""
    
    try:
        extractor = ImageFeaturesExtractor(
            model_path="vit_b_16_features.onnx",
            num_workers=2
        )
        
        try:
            extractor.start()
        except ModelNotFoundError as e:
            print(f"错误: 模型文件不存在 - {e.model_path}")
            print("解决方案: 请下载模型文件并放置在正确位置")
            return None
        except WorkerInitError as e:
            print(f"错误: Worker 初始化失败 - Worker {e.worker_id}: {e.reason}")
            print("解决方案: 检查依赖库是否完整安装")
            return None
        
        try:
            features = extractor.extract_features_sync(image_path)
            return features
            
        except TimeoutError:
            print(f"错误: 提取超时 - {image_path}")
            print("解决方案: 增加超时时间或检查图像大小")
            return None
            
        except TaskSubmissionError as e:
            print(f"错误: 任务提交失败 - {e.reason}")
            print("解决方案: 等待队列空闲或增加队列大小")
            return None
            
        except Exception as e:
            print(f"错误: 未知错误 - {e}")
            return None
            
        finally:
            extractor.stop()
            
    except Exception as e:
        print(f"致命错误: {e}")
        return None

# 使用示例
features = robust_feature_extraction("image.jpg")
if features is not None:
    print(f"成功: {features.shape}")
```

---

## 进度跟踪

### 示例 9: 简单的进度条

```python
from image_features_extractor import ImageFeaturesExtractor
from pathlib import Path
import sys

def extract_with_progress(image_dir):
    """带进度显示的批量提取"""
    
    image_files = list(Path(image_dir).glob("*.jpg"))
    total = len(image_files)
    
    with ImageFeaturesExtractor(num_workers=2) as extractor:
        for i, img_path in enumerate(image_files, 1):
            try:
                features = extractor.extract_features_sync(str(img_path))
                
                # 显示进度条
                progress = i / total
                bar_length = 40
                filled = int(bar_length * progress)
                bar = '█' * filled + '░' * (bar_length - filled)
                
                sys.stdout.write(f'\r进度: [{bar}] {i}/{total} ({progress*100:.1f}%)')
                sys.stdout.flush()
                
            except Exception as e:
                print(f"\n错误: {img_path.name}: {e}")
        
        print("\n完成!")

# 使用示例
extract_with_progress("images/")
```

### 示例 10: 使用 tqdm 进度条

```python
from image_features_extractor import ImageFeaturesExtractor
from pathlib import Path
from tqdm import tqdm

def extract_with_tqdm(image_dir, output_file):
    """使用 tqdm 显示详细进度"""
    
    image_files = list(Path(image_dir).glob("*.jpg"))
    
    with ImageFeaturesExtractor(num_workers=4) as extractor:
        results = {}
        
        # 使用 tqdm 包装迭代器
        for img_path in tqdm(image_files, desc="提取特征", unit="张"):
            try:
                features = extractor.extract_features_sync(str(img_path))
                results[img_path.name] = features
            except Exception as e:
                tqdm.write(f"失败: {img_path.name} - {e}")
        
        # 保存
        import numpy as np
        np.savez_compressed(output_file, **results)
        print(f"\n保存完成: {output_file}")

# 使用示例（需要先安装 tqdm: pip install tqdm）
# extract_with_tqdm("images/", "features.npz")
```

### 示例 11: PyQt 进度对话框

```python
from PyQt6.QtWidgets import QApplication, QProgressDialog
from PyQt6.QtCore import QThread, pyqtSignal
from image_features_extractor import ImageFeaturesExtractor
from pathlib import Path

class ExtractionThread(QThread):
    """特征提取线程"""
    progress = pyqtSignal(int, int, str)  # 当前, 总数, 文件名
    finished = pyqtSignal(dict)  # 结果
    
    def __init__(self, image_files, num_workers=2):
        super().__init__()
        self.image_files = image_files
        self.num_workers = num_workers
    
    def run(self):
        results = {}
        total = len(self.image_files)
        
        with ImageFeaturesExtractor(num_workers=self.num_workers) as extractor:
            for i, img_path in enumerate(self.image_files, 1):
                try:
                    features = extractor.extract_features_sync(str(img_path))
                    results[img_path.name] = features
                    self.progress.emit(i, total, img_path.name)
                except Exception as e:
                    self.progress.emit(i, total, f"失败: {img_path.name}")
        
        self.finished.emit(results)

def show_extraction_progress(image_dir):
    """显示提取进度对话框"""
    
    app = QApplication.instance() or QApplication([])
    
    # 扫描图像
    image_files = list(Path(image_dir).glob("*.jpg"))
    
    # 创建进度对话框
    progress_dialog = QProgressDialog(
        "正在提取特征...", "取消", 0, len(image_files)
    )
    progress_dialog.setWindowTitle("特征提取")
    progress_dialog.setMinimumWidth(400)
    
    # 创建提取线程
    thread = ExtractionThread(image_files)
    
    # 连接信号
    def on_progress(current, total, filename):
        progress_dialog.setValue(current)
        progress_dialog.setLabelText(f"正在处理: {filename}\n({current}/{total})")
    
    def on_finished(results):
        progress_dialog.close()
        print(f"完成! 成功提取 {len(results)} 张图像")
    
    thread.progress.connect(on_progress)
    thread.finished.connect(on_finished)
    
    # 启动
    thread.start()
    progress_dialog.exec()

# 使用示例
# show_extraction_progress("images/")
```

---

## 与现有应用集成

### 示例 12: 集成到 ImageSimilarityFinder

```python
from image_features_extractor import ImageFeaturesExtractor
import numpy as np
from pathlib import Path

class AsyncImageSimilarityFinder:
    """
    异步版本的图像相似度查找器
    使用 ImageFeaturesExtractor 进行特征提取
    """
    
    def __init__(self, num_workers=2):
        self.extractor = ImageFeaturesExtractor(num_workers=num_workers)
        self.extractor.start()
        self.index = {}  # 图像路径 -> 特征向量
        self.is_building = False
    
    def build_index_sync(self, image_dir):
        """同步构建索引"""
        print("开始构建索引...")
        image_files = list(Path(image_dir).glob("*.jpg"))
        
        for i, img_path in enumerate(image_files, 1):
            try:
                features = self.extractor.extract_features_sync(str(img_path))
                self.index[str(img_path)] = features
                print(f"[{i}/{len(image_files)}] 已添加: {img_path.name}")
            except Exception as e:
                print(f"[{i}/{len(image_files)}] 失败: {img_path.name} - {e}")
        
        print(f"索引构建完成: {len(self.index)} 张图像")
    
    def find_similar(self, query_image, top_k=5):
        """查找相似图像"""
        if not self.index:
            print("索引为空，请先构建索引")
            return []
        
        # 提取查询图像特征
        query_features = self.extractor.extract_features_sync(query_image)
        
        # 计算相似度
        similarities = []
        for img_path, features in self.index.items():
            similarity = self._cosine_similarity(query_features, features)
            similarities.append((img_path, similarity))
        
        # 排序并返回 top_k
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
    
    @staticmethod
    def _cosine_similarity(a, b):
        """计算余弦相似度"""
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    
    def __del__(self):
        self.extractor.stop()

# 使用示例
finder = AsyncImageSimilarityFinder(num_workers=4)
finder.build_index_sync("image_library/")

similar_images = finder.find_similar("query.jpg", top_k=5)
for img_path, similarity in similar_images:
    print(f"{Path(img_path).name}: {similarity:.4f}")
```

### 示例 13: 集成到 Flask Web 服务

```python
from flask import Flask, request, jsonify
from image_features_extractor import ImageFeaturesExtractor
import numpy as np
import base64
from io import BytesIO
from PIL import Image

app = Flask(__name__)

# 全局提取器实例
extractor = None

@app.before_first_request
def initialize():
    """首次请求前初始化"""
    global extractor
    extractor = ImageFeaturesExtractor(num_workers=2)
    extractor.start()
    print("特征提取器已启动")

@app.route('/extract', methods=['POST'])
def extract_features():
    """提取图像特征的 API 端点"""
    
    if 'image' not in request.files:
        return jsonify({'error': '未提供图像'}), 400
    
    # 保存临时文件
    image_file = request.files['image']
    temp_path = f"/tmp/{image_file.filename}"
    image_file.save(temp_path)
    
    try:
        # 提取特征
        features = extractor.extract_features_sync(temp_path, timeout=30.0)
        
        # 转换为列表以便 JSON 序列化
        features_list = features.tolist()
        
        return jsonify({
            'success': True,
            'features': features_list,
            'shape': list(features.shape)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    
    finally:
        # 清理临时文件
        import os
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'extractor_running': extractor.is_running() if extractor else False,
        'queue_size': extractor.queue_size if extractor else 0
    })

@app.teardown_appcontext
def shutdown(exception=None):
    """应用关闭时清理"""
    global extractor
    if extractor:
        extractor.stop()
        print("特征提取器已停止")

# 使用示例:
# if __name__ == '__main__':
#     app.run(host='0.0.0.0', port=5000)
```

---

## 性能监控

### 示例 14: 性能统计

```python
from image_features_extractor import ImageFeaturesExtractor
from pathlib import Path
import time
import statistics

def benchmark_extraction(image_dir, num_workers_list=[1, 2, 4, 8]):
    """
    对比不同 Worker 数量的性能
    """
    image_files = list(Path(image_dir).glob("*.jpg"))[:20]  # 测试 20 张图像
    
    print(f"测试图像数量: {len(image_files)}")
    print("-" * 60)
    
    results = {}
    
    for num_workers in num_workers_list:
        print(f"\n测试 {num_workers} 个 Worker...")
        
        with ImageFeaturesExtractor(num_workers=num_workers) as extractor:
            times = []
            
            for img_path in image_files:
                start_time = time.time()
                try:
                    features = extractor.extract_features_sync(str(img_path))
                    elapsed = time.time() - start_time
                    times.append(elapsed)
                except Exception as e:
                    print(f"  失败: {img_path.name}")
            
            if times:
                avg_time = statistics.mean(times)
                total_time = sum(times)
                throughput = len(times) / total_time
                
                results[num_workers] = {
                    'avg_time': avg_time,
                    'total_time': total_time,
                    'throughput': throughput
                }
                
                print(f"  平均耗时: {avg_time:.3f}秒")
                print(f"  总耗时: {total_time:.2f}秒")
                print(f"  吞吐量: {throughput:.2f}张/秒")
    
    # 输出对比
    print("\n" + "=" * 60)
    print("性能对比:")
    print("-" * 60)
    baseline = results[1]['total_time'] if 1 in results else None
    
    for num_workers, stats in results.items():
        speedup = baseline / stats['total_time'] if baseline else 1.0
        print(f"{num_workers} Workers: "
              f"{stats['total_time']:.2f}秒, "
              f"{stats['throughput']:.2f}张/秒, "
              f"加速比: {speedup:.2f}x")

# 使用示例
# benchmark_extraction("test_images/", num_workers_list=[1, 2, 4])
```

### 示例 15: 实时性能监控

```python
from image_features_extractor import ImageFeaturesExtractor
import time
import threading

class PerformanceMonitor:
    """实时性能监控器"""
    
    def __init__(self, extractor):
        self.extractor = extractor
        self.monitoring = False
        self.monitor_thread = None
        self.stats = {
            'tasks_completed': 0,
            'tasks_failed': 0,
            'total_time': 0.0,
        }
    
    def start_monitoring(self, interval=5.0):
        """开始监控"""
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """停止监控"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join()
    
    def _monitor_loop(self, interval):
        """监控循环"""
        while self.monitoring:
            print("\n" + "=" * 60)
            print("性能监控报告")
            print("-" * 60)
            print(f"队列大小: {self.extractor.queue_size}")
            print(f"待处理任务: {self.extractor.pending_tasks_count}")
            print(f"已完成: {self.stats['tasks_completed']}")
            print(f"已失败: {self.stats['tasks_failed']}")
            
            if self.stats['tasks_completed'] > 0:
                avg_time = self.stats['total_time'] / self.stats['tasks_completed']
                print(f"平均耗时: {avg_time:.3f}秒")
            
            time.sleep(interval)
    
    def record_completion(self, processing_time, success=True):
        """记录任务完成"""
        if success:
            self.stats['tasks_completed'] += 1
            self.stats['total_time'] += processing_time
        else:
            self.stats['tasks_failed'] += 1

# 使用示例
with ImageFeaturesExtractor(num_workers=2) as extractor:
    monitor = PerformanceMonitor(extractor)
    monitor.start_monitoring(interval=3.0)
    
    # 提交任务
    def callback(result):
        monitor.record_completion(
            result.processing_time,
            success=result.is_success
        )
    
    for i in range(10):
        extractor.extract_features_async(f"image_{i}.jpg", callback=callback)
    
    # 等待完成
    time.sleep(20)
    monitor.stop_monitoring()
```

---

## 高级用法

### 示例 16: 自定义结果处理管道

```python
from image_features_extractor import ImageFeaturesExtractor
from pathlib import Path
import numpy as np
from sklearn.decomposition import PCA

class FeaturePipeline:
    """特征提取和处理管道"""
    
    def __init__(self, num_workers=2, apply_pca=True, pca_dims=128):
        self.extractor = ImageFeaturesExtractor(num_workers=num_workers)
        self.extractor.start()
        self.apply_pca = apply_pca
        self.pca = PCA(n_components=pca_dims) if apply_pca else None
        self.features_cache = []
    
    def process_image(self, image_path):
        """处理单张图像"""
        # 提取原始特征
        features = self.extractor.extract_features_sync(image_path)
        
        # 缓存特征
        self.features_cache.append(features)
        
        return features
    
    def process_batch(self, image_paths):
        """批量处理并应用 PCA"""
        # 提取所有特征
        features_list = []
        for img_path in image_paths:
            features = self.process_image(img_path)
            features_list.append(features)
        
        features_matrix = np.array(features_list)
        
        # 应用 PCA 降维
        if self.apply_pca and len(features_list) > self.pca.n_components:
            print(f"应用 PCA 降维: {features_matrix.shape} -> ", end="")
            self.pca.fit(features_matrix)
            features_reduced = self.pca.transform(features_matrix)
            print(f"{features_reduced.shape}")
            return features_reduced
        
        return features_matrix
    
    def __del__(self):
        self.extractor.stop()

# 使用示例
pipeline = FeaturePipeline(num_workers=4, apply_pca=True, pca_dims=128)

image_files = list(Path("images/").glob("*.jpg"))[:100]
features_reduced = pipeline.process_batch(image_files)

print(f"降维后特征: {features_reduced.shape}")
```

### 示例 17: 增量索引构建

```python
from image_features_extractor import ImageFeaturesExtractor
import numpy as np
from pathlib import Path
import pickle

class IncrementalIndexBuilder:
    """增量式图像索引构建器"""
    
    def __init__(self, index_file="image_index.pkl", num_workers=2):
        self.index_file = index_file
        self.extractor = ImageFeaturesExtractor(num_workers=num_workers)
        self.extractor.start()
        self.index = self._load_index()
    
    def _load_index(self):
        """加载现有索引"""
        if Path(self.index_file).exists():
            with open(self.index_file, 'rb') as f:
                index = pickle.load(f)
            print(f"已加载索引: {len(index)} 张图像")
            return index
        return {}
    
    def _save_index(self):
        """保存索引"""
        with open(self.index_file, 'wb') as f:
            pickle.dump(self.index, f)
        print(f"索引已保存: {len(self.index)} 张图像")
    
    def add_image(self, image_path):
        """添加单张图像到索引"""
        path_str = str(image_path)
        
        if path_str in self.index:
            print(f"已存在: {Path(image_path).name}")
            return False
        
        try:
            features = self.extractor.extract_features_sync(path_str)
            self.index[path_str] = features
            print(f"已添加: {Path(image_path).name}")
            return True
        except Exception as e:
            print(f"失败: {Path(image_path).name} - {e}")
            return False
    
    def add_directory(self, directory, save_interval=10):
        """添加整个目录到索引"""
        image_files = list(Path(directory).glob("*.jpg"))
        added = 0
        
        for i, img_path in enumerate(image_files, 1):
            if self.add_image(img_path):
                added += 1
            
            # 定期保存
            if i % save_interval == 0:
                self._save_index()
        
        # 最后保存
        self._save_index()
        print(f"完成: 新增 {added} 张图像")
    
    def remove_missing_images(self):
        """移除不存在的图像"""
        missing = [path for path in self.index if not Path(path).exists()]
        for path in missing:
            del self.index[path]
            print(f"已移除: {path}")
        
        if missing:
            self._save_index()
        print(f"清理完成: 移除 {len(missing)} 张图像")
    
    def __del__(self):
        self.extractor.stop()

# 使用示例
builder = IncrementalIndexBuilder("my_index.pkl", num_workers=4)

# 添加新目录
builder.add_directory("new_images/", save_interval=20)

# 清理不存在的图像
builder.remove_missing_images()
```

---

## 总结

本文档提供了 `image_features_extractor` 模块的各种实际使用示例，涵盖了：

- ✅ 基础使用方法
- ✅ 批量处理策略
- ✅ 错误处理和重试机制
- ✅ 进度跟踪和反馈
- ✅ 与现有应用的集成
- ✅ 性能监控和优化
- ✅ 高级用法和扩展

更多信息请参考：
- [README.md](README.md) - 完整使用文档
- [DESIGN.md](DESIGN.md) - 设计文档

---

**版本**: 1.0.0  
**最后更新**: 2025-01-14