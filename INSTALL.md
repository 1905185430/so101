# SO-101 安装指南

## 1. 克隆仓库

```bash
cd ~
git clone https://github.com/1905185430/so101.git
```

## 2. 创建 Conda 环境

```bash
conda create -n lerobot python=3.12 -y
conda activate lerobot
```

## 3. 安装 so101 包

```bash
# 基础安装（摄像头扫描、配置管理）
# 注意：先进入 so101 仓库目录
cd ~/so101        # 或你的实际路径，如 ~/wjx/so101
pip install -e .

# 完整安装（含录制、部署、数据集管理）
pip install -e ".[full]"

# 如果不在仓库目录内，用绝对路径：
# pip install -e /path/to/so101/.[full]
```

## 4. 安装 LeRobot

```bash
# 方式一：从源码安装（推荐，可修改）
cd ~
git clone https://github.com/huggingface/lerobot.git
cd lerobot
pip install -e ".[feetech]"

# 方式二：从 PyPI 安装
pip install "lerobot[feetech]>=0.5.1"
```

## 5. 串口权限设置

```bash
# 永久方案（需重新登录生效）
sudo usermod -aG dialout $USER

# 临时方案（每次插拔后需重新执行）
sudo chmod 666 /dev/ttyACM*
```

## 6. 验证安装

```bash
# 检查 CLI 是否可用
so101 --help

# 扫描摄像头和串口设备
so101 scan

# 查看可用场景
so101 list
```

## 可选依赖

### HuggingFace Hub（数据集上传）

```bash
pip install huggingface_hub>=0.16
huggingface-cli login
```

### Flask（数据集网页查看器）

```bash
pip install flask>=2.3
so101 dataset view  # 启动后访问 http://localhost:5555
```

### PyTorch（模型部署推理）

```bash
# CUDA 12.1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# CPU only
pip install torch torchvision
```

### Transformers（pi0 模型支持）

```bash
pip install transformers>=4.30
```

## 常见问题

### conda pip 冲突

如果 `so101` 命令找不到或使用了错误的 Python：

```bash
# 用 conda run 运行
conda run -n lerobot so101 --help

# 或检查 PATH 顺序
which so101
which python
```

### 串口设备无权限

```bash
# 检查设备是否存在
ls /dev/ttyACM*

# 检查用户组
groups $USER  # 应包含 dialout

# 重新登录后生效（或重启）
```

### Orbbec 摄像头无法打开

确保使用字符串路径（非整数）：

```bash
# 正确：V4L2 后端
python -c "import cv2; cap = cv2.VideoCapture('/dev/video10', cv2.CAP_V4L2); print(cap.isOpened())"

# 错误：FFMPEG 后端会失败
python -c "import cv2; cap = cv2.VideoCapture(10); print(cap.isOpened())"
```

更新摄像头设备路径：

```bash
cd ~/so101
python scripts/cam_resolve.py --update
```

### KDE Plasma 系统负载

KDE 后台服务影响串口通信，遥操作时建议停用：

```bash
balooctl disable
systemctl --user stop kdeconnect
```

## 目录结构速览

```
~/so101/
├── src/so101/              # Python 包源码
├── config/
│   └── camera_config.yaml  # 核心配置（摄像头 + 机械臂 + 场景）
├── scripts/
│   ├── cam_resolve.py      # 摄像头检测工具
│   └── gen_eval.py         # 生成评估脚本
└── doc/
    └── cheatsheet.md       # 命令速查表
```

## 下一步

安装完成后，参考 `doc/cheatsheet.md` 查看完整命令列表：

```bash
cat ~/so101/doc/cheatsheet.md
```
