#!/bin/bash
# so101 一键环境检测脚本
# 用法: bash check_env.sh

echo "========== 系统信息 =========="
uname -a
echo ""

echo "========== Python 环境 =========="
which python
python --version
echo ""

echo "========== Conda 环境 =========="
echo "当前环境: $CONDA_DEFAULT_ENV"
conda info --envs 2>/dev/null || echo "(conda 不可用)"
echo ""

echo "========== so101 包状态 =========="
pip show so101 2>/dev/null && echo "" || echo "so101 未安装"
echo ""

echo "========== 核心依赖 =========="
for pkg in pyyaml opencv-python colorama rich psutil; do
    ver=$(pip show $pkg 2>/dev/null | grep Version | awk '{print $2}')
    if [ -z "$ver" ]; then
        echo "  $pkg: 未安装"
    else
        echo "  $pkg: $ver"
    fi
done
echo ""

echo "========== LeRobot 状态 =========="
pip show lerobot 2>/dev/null | grep -E "^(Name|Version|Location)" || echo "lerobot 未安装"
echo ""

echo "========== PyTorch 状态 =========="
python -c "import torch; print(f'torch: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')" 2>/dev/null || echo "torch 未安装"
echo ""

echo "========== HuggingFace Hub =========="
pip show huggingface_hub 2>/dev/null | grep Version || echo "huggingface_hub 未安装"
echo ""

echo "========== CLI 入口 =========="
which so101 2>/dev/null && echo "so101 命令可用" || echo "so101 命令不可用"
echo ""

echo "========== 串口设备 =========="
ls /dev/ttyACM* 2>/dev/null || echo "无 ttyACM 设备"
ls /dev/ttyUSB* 2>/dev/null || echo "无 ttyUSB 设备"
echo ""

echo "========== 串口权限 =========="
echo "用户组: $(groups)"
groups | grep -q dialout && echo "dialout 组: 已加入" || echo "dialout 组: 未加入 (需 sudo usermod -aG dialout \$USER)"
echo ""

echo "========== 摄像头设备 =========="
ls /dev/video* 2>/dev/null | head -20 || echo "无 video 设备"
echo ""

echo "========== v4l2-ctl =========="
which v4l2-ctl 2>/dev/null && echo "v4l2-ctl 可用" || echo "v4l2-ctl 不可用 (需 sudo apt install v4l-utils)"
echo ""

echo "========== so101 配置文件 =========="
CONFIG_PATH="$HOME/so101/config/camera_config.yaml"
if [ ! -f "$CONFIG_PATH" ]; then
    CONFIG_PATH="$HOME/wjx/so101/config/camera_config.yaml"
fi
if [ -f "$CONFIG_PATH" ]; then
    echo "配置文件存在: $CONFIG_PATH"
    grep -c "cameras:" "$CONFIG_PATH" >/dev/null && echo "  - cameras 区: 有" || echo "  - cameras 区: 无"
    grep -c "arms:" "$CONFIG_PATH" >/dev/null && echo "  - arms 区: 有" || echo "  - arms 区: 无"
    grep -c "scenes:" "$CONFIG_PATH" >/dev/null && echo "  - scenes 区: 有" || echo "  - scenes 区: 无"
else
    echo "配置文件不存在: $CONFIG_PATH"
fi
echo ""

echo "========== so101 命令测试 =========="
if which so101 >/dev/null 2>&1; then
    so101 --help 2>&1 | head -5
    echo ""
    echo "so101 list:"
    so101 list 2>&1 | head -10
    echo ""
    echo "so101 scan (摄像头):"
    so101 scan --cameras 2>&1 | head -20
else
    echo "so101 命令不可用，请先安装："
    echo "  cd ~/wjx/so101 && pip install -e \".[full]\""
fi
echo ""

echo "========== 检测完成 =========="
