#!/bin/bash
# ============================================================
# so101 一键安装脚本
# ============================================================
#
# 在新电脑上运行此脚本，完成以下事情：
#   1. 检查 Ubuntu 22.04
#   2. 创建 / 加入 conda 环境（lerobot）
#   3. 安装依赖（opencv-python, pyyaml）
#   4. 修复 feetech 串口波特率（Feetech 默认 115200 -> 500000）
#   5. 配置用户组（dialout）权限
#   6. 安装 so101 包（可编辑模式）
#
# 用法：
#   bash scripts/setup.sh
#
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONDA_ENV="${CONDA_ENV:-lerobot}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

echo "========================================"
echo "  so101 快速部署脚本"
echo "========================================"
echo ""

# ----------------------------------------------------------
# 1. 检查系统
# ----------------------------------------------------------
echo "[1/6] 检查系统环境..."
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    if [[ "$ID" != "ubuntu" ]]; then
        echo "  警告: 推荐使用 Ubuntu 22.04，其他发行版可能有问题"
    fi
    echo "  系统: $PRETTY_NAME"
fi

# ----------------------------------------------------------
# 2. Conda 环境
# ----------------------------------------------------------
echo ""
echo "[2/6] 配置 Conda 环境 ($CONDA_ENV)..."

CONDA_BIN="${HOME}/anaconda3/bin/conda"
if [[ ! -f "$CONDA_BIN" ]]; then
    CONDA_BIN="${HOME}/miniconda3/bin/conda"
fi
if [[ ! -f "$CONDA_BIN" ]]; then
    echo "  错误: 未找到 conda！请先安装 Anaconda/Miniconda"
    exit 1
fi

source "$CONDA_BIN" 2>/dev/null || eval "$($CONDA_BIN shell.bash hook)"

if conda env list | grep -q "^${CONDA_ENV} "; then
    echo "  环境 $CONDA_ENV 已存在，跳过创建"
else
    echo "  创建环境 $CONDA_ENV (Python $PYTHON_VERSION)..."
    conda create -y -n "$CONDA_ENV" python="$PYTHON_VERSION"
fi

# ----------------------------------------------------------
# 3. 安装依赖
# ----------------------------------------------------------
echo ""
echo "[3/6] 安装依赖..."

conda run -n "$CONDA_ENV" pip install opencv-python pyyaml

# ----------------------------------------------------------
# 4. 修复 feetech 波特率（如果 lerobot 已克隆）
# ----------------------------------------------------------
echo ""
echo "[4/6] 检查 feetech 波特率修复..."

FEETECH_DIR="${HOME}/lerobot/libs/feetech"
if [[ -d "$FEETECH_DIR" ]]; then
    if grep -q "Baudrate.*115200" "$FEETECH_DIR"/*.py 2>/dev/null; then
        echo "  修复 feetech 波特率: 115200 -> 500000"
        find "$FEETECH_DIR" -name "*.py" -exec sed -i 's/115200/500000/g' {} +
    else
        echo "  feetech 波特率已是 500000 或无需修复"
    fi
else
    echo "  lerobot 未克隆，跳过 feetech 修复（稍后运行 so101 teleop 时会处理）"
fi

# ----------------------------------------------------------
# 5. 权限配置
# ----------------------------------------------------------
echo ""
echo "[5/6] 配置串口权限..."

if ! groups | grep -q dialout; then
    echo "  将当前用户加入 dialout 组..."
    sudo usermod -aG dialout "$USER"
    echo "  已添加。请重新登录使 dialout 组生效（下次生效）"
else
    echo "  用户已在 dialout 组中"
fi

# 创建 udev 规则（如果不存在）
UDev_FILE="/etc/udev/rules.d/99-so101.rules"
if [[ ! -f "$UDev_FILE" ]]; then
    echo "  创建 udev 规则: $UDev_FILE"
    cat << 'EOF' | sudo tee "$UDev_FILE" > /dev/null
# SO-101 机械臂（CH9101F USB Serial）
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", MODE="0666", GROUP="dialout"
# Orbbec 深度相机
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="2e5c", MODE="0666"
EOF
    sudo udevadm control --reload-rules
    sudo udevadm trigger
    echo "  udev 规则已创建"
else
    echo "  udev 规则已存在"
fi

# ----------------------------------------------------------
# 6. 安装 so101 包
# ----------------------------------------------------------
echo ""
echo "[6/6] 安装 so101 包..."

cd "$PROJECT_ROOT"
conda run -n "$CONDA_ENV" pip install -e .

echo ""
echo "========================================"
echo "  安装完成！"
echo "========================================"
echo ""
echo "验证安装："
echo "  conda activate $CONDA_ENV"
echo "  so101 --help"
echo ""
echo "快速开始："
echo "  1. 连接 SO-101 机械臂和摄像头"
echo "  2. so101 list                    # 查看当前设备"
echo "  3. so101 scan --all              # 注册设备到配置"
echo "  4. so101 check                   # 检查场景是否可用"
echo "  5. so101 calibrate --arm follower_left   # 首次需要校准"
echo "  6. so101 teleop                  # 开始遥操作"
echo ""
