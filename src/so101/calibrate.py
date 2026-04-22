"""
so101.calibrate — 机械臂校准
=============================

直接从 config/camera_config.yaml 读取臂配置，
调用 LeRobot API 完成校准，不依赖原始项目文件。

Usage:
    so101 calibrate --arm follower_left
"""

import os
import sys
import time
import traceback

from so101 import config


def calibrate_arm(arm_name: str):
    """
    校准指定机械臂。

    arm_name: "follower_left", "follower_right", "leader_left", "leader_right"
    """
    arm_cfg = config.arm(arm_name)
    if not arm_cfg:
        print(f"[错误] 未找到臂 '{arm_name}' 的配置")
        print(f"  请先运行 so101 scan 注册设备")
        sys.exit(1)

    port = arm_cfg.get("port", "")
    arm_role = arm_cfg.get("role", "")
    arm_id = arm_name  # 用配置名作为 id

    if not port:
        print(f"[错误] 臂 '{arm_name}' 的 port 未配置")
        sys.exit(1)

    print(f"========================================")
    print(f"  校准: {arm_name}")
    print(f"========================================")
    print(f"  端口: {port}")
    print(f"  角色: {arm_role}")
    print(f"  ID:   {arm_id}")
    print()

    # 动态导入 LeRobot API（避免在模块加载时触发 ImportError）
    try:
        if arm_role == "follower":
            from lerobot.robots.so_follower import SO101FollowerConfig, SO101Follower
            cfg = SO101FollowerConfig(port=port, id=arm_id)
            robot = SO101Follower(cfg)
        else:
            from lerobot.teleoperators.so_leader import SO101LeaderConfig, SO101Leader
            cfg = SO101LeaderConfig(port=port, id=arm_id)
            robot = SO101Leader(cfg)
    except ImportError as e:
        print(f"[错误] 无法导入 LeRobot 模块: {e}")
        print(f"  请确认 lerobot 环境已激活: conda activate lerobot")
        print(f"  或运行: bash scripts/setup.sh")
        sys.exit(1)

    # 校准数据保存目录
    cal_dir = os.path.join(os.path.dirname(config.CONFIG_FILE), "calibration")
    os.makedirs(cal_dir, exist_ok=True)

    print(f"连接机械臂 ({port})...")
    print("(如果连接失败，请检查USB线是否插紧，或运行: sudo chmod 666 {port})")
    print()

    try:
        robot.connect(calibrate=False)
        print("已连接，开始校准...")
        print("请保持手臂静止，不要触碰！")
        print()
        robot.calibrate()
        print()
        print(f"[成功] 校准完成！")
        print(f"  校准数据默认保存在: ~/.lerobot/calibration/")
        print(f"  或项目目录: {cal_dir}/")
    except Exception as e:
        print(f"[错误] 校准失败: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            robot.disconnect()
            print("已断开连接。")
        except Exception:
            pass
