import torch
import torchvision.models as models
from torchvision.models import ViT_B_16_Weights
import os


def convert_model():
    """
    加载PyTorch Vision Transformer模型，移除分类头，并将其导出为ONNX格式。
    """
    print("正在加载预训练的 Vision Transformer (ViT-B/16) 模型...")

    # 1. 加载并修改模型，与我们之前的逻辑完全相同
    weights = ViT_B_16_Weights.DEFAULT
    model = models.vit_b_16(weights=weights)
    model.heads.head = torch.nn.Identity()  # 移除分类头以获取特征
    model.eval()  # 设置为评估模式

    print("模型加载完成。")

    # 2. 创建一个符合模型输入的虚拟张量 (dummy input)
    # 模型的输入尺寸是 (batch_size, channels, height, width)
    # 对于ViT-B/16，标准输入是 3x224x224
    dummy_input = torch.randn(1, 3, 224, 224, requires_grad=True)

    # 3. 定义输出文件名
    onnx_file_path = "vit_b_16_features.onnx"

    print(f"正在将模型导出到 {onnx_file_path}...")

    # 4. 导出模型
    torch.onnx.export(
        model,  # 要转换的模型
        dummy_input,  # 虚拟输入
        onnx_file_path,  # 输出文件路径
        export_params=True,  # 将训练好的权重也一并导出
        opset_version=14,  # ONNX 算子集版本，11是比较通用的选择
        do_constant_folding=True,  # 执行常量折叠优化
        input_names=['input'],  # 为输入张量命名
        output_names=['output'],  # 为输出张量命名
        dynamic_axes={'input': {0: 'batch_size'},  # 允许batch_size是动态的
                      'output': {0: 'batch_size'}}
    )

    print("-" * 50)
    print(f"模型已成功导出到: {os.path.abspath(onnx_file_path)}")
    print("现在你可以在 image_similarity.py 中使用这个文件了。")
    print("-" * 50)


if __name__ == '__main__':
    convert_model()