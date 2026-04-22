#!/usr/bin/env python
"""
SO-101 部署脚本：ACT 模型推理
================================

在真实 SO-101 机械臂上运行预训练 ACT 策略。

用法:
    conda run -n lerobot python ~/so101/deploy_act.py

模型: Ready321/act_pick_redcube
数据集: Ready321/pickup_redcube_20260421_095438
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path

import torch
import numpy as np

# LeRobot imports
from lerobot.configs import PreTrainedConfig
from lerobot.robots.so_follower import SO100Follower, SO100FollowerConfig
from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig
from lerobot.datasets import LeRobotDataset
from lerobot.policies.factory import make_policy
from lerobot.commonrobots.utils import RobotObservation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# 配置（默认参数，可通过命令行覆盖）
# ============================================================================

# 机器人
FOLLOWER_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B41532613-if00"  # → /dev/ttyACM0
ROBOT_ID = "so101_cong_left"

# 相机 by-id 路径
CAMERA_TOP = "/dev/v4l/by-id/usb-Orbbec_R__Orbbec_Gemini_335_CP15641000AW-video-index0"
CAMERA_SIDE = "/dev/v4l/by-id/usb-Orbbec_R__Orbbec_Gemini_335_CP1L44P0007K-video-index0"
CAMERA_WRIST = "/dev/v4l/by-id/usb-icSpring_icspring_camera_20240307110322-video-index0"

# 推理参数
FPS = 30
EPISODE_TIME_SEC = 60
NUM_EPISODES = 5
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# SO-101 电机名称（FeetechMotorsBus 总线格式）
MOTOR_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]

# 动作安全限幅
MAX_JOINT_VELOCITY = 10.0  # deg/step
GRIPPER_MIN, GRIPPER_MAX = 0.0, 45.0
JOINT_MIN, JOINT_MAX = -180.0, 180.0


# ============================================================================
# 观测格式转换
# ============================================================================

def robot_observation_to_policy_input(obs: RobotObservation, camera_keys: list[str]) -> dict:
    """
    将 SO100Follower.get_observation() 返回的 RobotObservation（dict）
    转换为 ACT policy.select_action() 所需的格式。

    SO100Follower.get_observation() 返回的原始格式:
        {
            "shoulder_pan.pos": float,
            "shoulder_lift.pos": float,
            "elbow_flex.pos": float,
            "wrist_flex.pos": float,
            "wrist_roll.pos": float,
            "gripper.pos": float,
            "top":    HWC uint8 numpy array (H, W, 3),
            "side":   HWC uint8 numpy array (H, W, 3),
            "wrist":  HWC uint8 numpy array (H, W, 3),
        }

    ACT select_action() 期望的格式:
        {
            "observation.images.top":    (1, 3, H, W) torch.float32,
            "observation.images.side":   (1, 3, H, W) torch.float32,
            "observation.images.wrist": (1, 3, H, W) torch.float32,
            "observation.state":         (1, 6)        torch.float32,
        }
    """
    batch = {}

    # 图像: HWC → CHW, uint8 [0,255] → float [0,1]
    for cam in camera_keys:
        img = obs[cam]
        if img is None:
            raise ValueError(f"相机 {cam} 返回空帧，请检查相机连接")
        # BGR → RGB（OpenCV 读出来是 BGR）
        if img.ndim == 3 and img.shape[2] == 3:
            img = img[:, :, ::-1]
        img_tensor = torch.from_numpy(img).float().permute(2, 0, 1) / 255.0
        batch[f"observation.images.{cam}"] = img_tensor[None].to(DEVICE)

    # 状态: (6,) → (1, 6)
    state = np.array([obs[f"{m}.pos"] for m in MOTOR_NAMES], dtype=np.float32)
    batch["observation.state"] = torch.from_numpy(state)[None].to(DEVICE)

    return batch


# ============================================================================
# 动作后处理
# ============================================================================

def clamp_action(action_dict: dict, present_pos: dict) -> dict:
    """对动作进行速度限制和关节限幅。"""
    result = {}
    for motor in MOTOR_NAMES:
        key = f"{motor}.pos"
        if key not in action_dict:
            continue
        target = action_dict[key]
        present = present_pos.get(motor, 0.0)

        # 速度限制
        delta = target - present
        if abs(delta) > MAX_JOINT_VELOCITY:
            target = present + np.sign(delta) * MAX_JOINT_VELOCITY

        # 关节限幅
        if motor == "gripper":
            target = np.clip(target, GRIPPER_MIN, GRIPPER_MAX)
        else:
            target = np.clip(target, JOINT_MIN, JOINT_MAX)

        result[key] = target
    return result


# ============================================================================
# 主推理流程
# ============================================================================

def run_episode(robot, policy, camera_keys: list[str]):
    """运行一次推理 episode。"""
    dt = 1.0 / FPS
    max_steps = int(EPISODE_TIME_SEC * FPS)

    policy.reset()  # 清空时序 ensemble hidden state

    step = 0
    start_time = time.time()

    while step < max_steps:
        t_loop_start = time.time()

        # 获取观测
        obs = robot.get_observation()

        # 当前关节位置（用于动作限幅）
        present_pos = {motor: obs[f"{motor}.pos"] for motor in MOTOR_NAMES}

        # 转换为 policy 输入格式
        batch = robot_observation_to_policy_input(obs, camera_keys)

        # 推理（无梯度），返回已反归一化的动作
        with torch.no_grad():
            action_tensor = policy.select_action(batch)

        # tensor → numpy dict (UnnormalizerProcessor 已内置于 make_policy)
        # action_tensor shape: (1, 6) 或 (6,)
        if action_tensor.ndim == 2:
            action_np = action_tensor.cpu().numpy()[0]  # (6,)
        else:
            action_np = action_tensor.cpu().numpy()

        # 转换为 dict 格式 {f"{motor}.pos": value}
        action_dict = {f"{motor}.pos": float(v) for motor, v in zip(MOTOR_NAMES, action_np)}

        # 速度+限幅
        action_clamped = clamp_action(action_dict, present_pos)

        # 发送到机器人
        robot.send_action(action_clamped)

        step += 1

        # 打印进度
        if step % 30 == 0:
            angles = ", ".join(
                f"{m}={action_clamped.get(f'{m}.pos', 0):.1f}°"
                for m in MOTOR_NAMES
            )
            elapsed = time.time() - start_time
            logger.info(f"  step {step:4d} | {elapsed:.1f}s | {angles}")

        # 循环节奏控制
        elapsed = time.time() - t_loop_start
        sleep_time = dt - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    return step


# ============================================================================
# 入口
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="SO-101 ACT 推理")
    parser.add_argument("--policy", default="Ready321/act_pick_redcube",
                        help="策略模型 repo_id 或本地路径")
    parser.add_argument("--dataset", default="Ready321/pickup_redcube_20260421_095438",
                        help="训练数据集 repo_id（用于获取归一化统计量）")
    parser.add_argument("--policy_type", default="act",
                        choices=["act", "diffusion", "smolvla"],
                        help="策略类型（默认: act）")
    parser.add_argument("--device", default=DEVICE,
                        help=f"推理设备（默认: {DEVICE}）")
    parser.add_argument("--port", default=FOLLOWER_PORT,
                        help=f"从臂串口（默认: {FOLLOWER_PORT}）")
    parser.add_argument("--robot_id", default=ROBOT_ID,
                        help=f"机器人 ID（默认: {ROBOT_ID}）")
    parser.add_argument("--episodes", type=int, default=NUM_EPISODES,
                        help=f"Episode 数（默认: {NUM_EPISODES}）")
    parser.add_argument("--fps", type=int, default=FPS,
                        help=f"控制频率（默认: {FPS}）")
    parser.add_argument("--episode_time", type=float, default=EPISODE_TIME_SEC,
                        help=f"每 episode 时长（默认: {EPISODE_TIME_SEC}）")
    return parser.parse_args()


def main():
    args = parse_args()

    # 同步全局参数
    global FPS, EPISODE_TIME_SEC
    FPS = args.fps
    EPISODE_TIME_SEC = args.episode_time

    logger.info("=" * 60)
    logger.info("  SO-101 ACT 推理")
    logger.info("=" * 60)
    logger.info(f"  策略: {args.policy}")
    logger.info(f"  数据集: {args.dataset}")
    logger.info(f"  设备: {args.device}")
    logger.info(f"  机器人: {args.robot_id} @ {args.port}")
    logger.info(f"  频率: {FPS} Hz | 时长: {EPISODE_TIME_SEC}s | Episodes: {args.episodes}")
    logger.info("=" * 60)

    # -------------------------------------------------------------------------
    # 1. 加载数据集 metadata（用于归一化统计）
    # -------------------------------------------------------------------------
    logger.info(f"加载数据集: {args.dataset}")
    try:
        dataset = LeRobotDataset(args.dataset)
        dataset_stats = dataset.meta.stats
        logger.info(f"  加载成功 | features: {list(dataset.meta.features.keys())}")
    except Exception as e:
        logger.error(f"  无法加载数据集 metadata: {e}")
        logger.error("  推理可能输出错误尺度的动作，请确认 --dataset 参数正确")
        dataset_stats = None

    # -------------------------------------------------------------------------
    # 2. 创建 Policy（使用 make_policy 会自动包含反归一化处理器）
    # -------------------------------------------------------------------------
    logger.info(f"创建策略: {args.policy_type} @ {args.device}")
    policy_cfg = PreTrainedConfig(
        type=args.policy_type,
        pretrained_path=args.policy,
        device=args.device,
    )
    policy = make_policy(cfg=policy_cfg, ds_meta=dataset_stats)
    policy.eval()
    logger.info(f"  策略加载完成 | 参数量: {sum(p.numel() for p in policy.parameters()):,}")

    # -------------------------------------------------------------------------
    # 3. 连接机器人
    # -------------------------------------------------------------------------
    camera_keys = ["top", "side", "wrist"]
    cameras = {
        "top": OpenCVCameraConfig(index_or_path=CAMERA_TOP, width=640, height=480, fps=FPS),
        "side": OpenCVCameraConfig(index_or_path=CAMERA_SIDE, width=640, height=480, fps=FPS),
        "wrist": OpenCVCameraConfig(index_or_path=CAMERA_WRIST, width=640, height=480, fps=FPS),
    }
    robot_cfg = SO100FollowerConfig(
        port=args.port,
        id=args.robot_id,
        cameras=cameras,
        use_degrees=True,
    )
    robot = SO100Follower(robot_cfg)

    logger.info(f"连接机器人 @ {args.port}...")
    try:
        robot.connect(calibrate=False)
    except Exception as e:
        logger.error(f"连接失败: {e}")
        return

    if not robot.is_connected:
        logger.error("机器人连接失败")
        return

    # 确认观测格式
    obs_sample = robot.get_observation()
    motor_keys = [k for k in obs_sample.keys() if k.endswith(".pos")]
    cam_keys = [k for k in obs_sample.keys() if k not in motor_keys]
    logger.info(f"  连接成功 | 电机 keys: {motor_keys}")
    logger.info(f"  相机 keys: {cam_keys}")

    # -------------------------------------------------------------------------
    # 4. 推理循环
    # -------------------------------------------------------------------------
    logger.info(f"\n开始推理: {args.episodes} episodes, {EPISODE_TIME_SEC}s each")
    logger.info("按 Ctrl+C 中断\n")

    try:
        for ep in range(args.episodes):
            logger.info(f"=== Episode {ep + 1}/{args.episodes} ===")
            ep_start = time.time()
            steps = run_episode(robot, policy, camera_keys)
            elapsed = time.time() - ep_start
            logger.info(f"  完成 | {steps} steps | {elapsed:.1f}s | avg {steps/elapsed:.1f} Hz")

            if ep < args.episodes - 1:
                time.sleep(2.0)  # episode 间停顿

    except KeyboardInterrupt:
        logger.info("\n用户中断")
    finally:
        robot.disconnect()
        logger.info("机器人已断开")


if __name__ == "__main__":
    main()
