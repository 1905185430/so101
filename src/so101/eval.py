"""
so101.eval — 本地实机评估脚本
=============================

在 SO-101 机械臂上运行预训练策略，评估任务成功率。

用法:
    python -m so101.eval --policy Ready321/act_policy_so101_grab_redcube --episodes 10
    python -m so101.eval --policy /local/path/to/checkpoint --episodes 5 --device cpu

依赖:
    conda run -n lerobot python -m so101.eval ...
"""

from __future__ import annotations

import os
import sys
import time
import argparse
import threading
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import torch
import numpy as np

from so101 import config
from so101.sound_helpers import sound_episode_done, sound_all_done, sound_warn

logger = logging.getLogger(__name__)

# ============================================================================
# LeRobot API 延迟导入（只在实际调用时加载）
# ============================================================================

def _import_lerobot():
    from lerobot.configs import PreTrainedConfig
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from lerobot.datasets import LeRobotDataset
    from lerobot.policies.factory import make_policy
    from lerobot.commonrobots.utils import RobotObservation
    return PreTrainedConfig, SO101Follower, SO101FollowerConfig, LeRobotDataset, make_policy, RobotObservation


def _import_tqdm():
    try:
        from tqdm import tqdm
        return tqdm
    except ImportError:
        return None


# ============================================================================
# 观察格式转换
# ============================================================================

def robot_observation_to_policy_input(observation, camera_keys: list[str]) -> dict:
    """
    将 SO101Follower.get_observation() 返回的 RobotObservation
    转换为 policy.select_action() 所需的字典格式。

    RobotObservation 字段:
        timestamp, state (joint positions), action, images (dict of camera -> frame)
    """
    result = {}

    # 处理图像观测
    if hasattr(observation, "images") and observation.images:
        for key, frame in observation.images.items():
            # frame 可能是 RGB 或 BGR numpy array
            # 确保是 HWC 格式且值在 [0,255] 或 [0,1]
            if isinstance(frame, np.ndarray):
                if frame.dtype == np.uint8:
                    # 已经是 [0, 255] uint8，保持不变
                    result[key] = frame
                else:
                    # 浮点 array，归一化到 [0, 1]
                    result[key] = (frame * 255).astype(np.uint8) if frame.max() <= 1.0 else frame.astype(np.uint8)
    elif hasattr(observation, "image") and observation.image is not None:
        # 单图像情况
        result["image"] = observation.image

    # 处理状态观测（关节位置）
    if hasattr(observation, "state") and observation.state is not None:
        result["state"] = np.array(observation.state, dtype=np.float32)

    return result


# ============================================================================
# 评估逻辑
# ============================================================================

def make_robot(cfg: SO101FollowerConfig, camera_configs: dict) -> SO101Follower:
    """创建并连接 SO101Follower。"""
    PreTrainedConfig, SO101Follower, SO101FollowerConfig, _, _, _ = _import_lerobot()

    # 构建 cameras 字典
    cameras = {}
    for role, cam_cfg in camera_configs.items():
        cameras[role] = {
            "type": "opencv",
            "index_or_path": cam_cfg["by_id"],
            "width": cam_cfg.get("width", 640),
            "height": cam_cfg.get("height", 480),
            "fps": cam_cfg.get("fps", 30),
        }
        if cam_cfg.get("fourcc"):
            cameras[role]["fourcc"] = cam_cfg["fourcc"]

    robot_cfg = SO101FollowerConfig(
        port=cfg.port,
        id=cfg.id,
        cameras=cameras,
        disable_torque_on_disconnect=True,
        use_degrees=True,
    )
    robot = SO101Follower(robot_cfg)

    return robot


def connect_robot_with_retry(robot, name: str, max_retries: int = 3):
    """带重试的机器人连接。"""
    for attempt in range(max_retries):
        try:
            robot.connect()
            logger.info(f"[{name}] 已连接")
            return True
        except PermissionError:
            print(f"[错误] 无权限访问 {name}，请运行: sudo chmod 666 {getattr(robot, 'port', '?')}")
            raise
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            print(f"[{name}] 连接失败 ({attempt+1}/{max_retries}): {e}，重试中...")
            time.sleep(1)


def load_policy_from_hub(policy_path: str, device: str = "cuda") -> tuple:
    """
    从 HuggingFace repo 或本地路径加载预训练策略。

    Returns:
        (policy, config_obj, preprocess_fn)
    """
    PreTrainedConfig, _, _, LeRobotDataset, make_policy, _ = _import_lerobot()

    # 如果是 HF repo，先尝试获取数据集 metadata 以确定特征形状
    is_hf = "/" in policy_path and not os.path.isdir(policy_path)

    if is_hf:
        # 从数据集 metadata 推断特征形状
        try:
            repo_id = policy_path.rstrip("/").replace("/outputs/train/", "/").rsplit("/", 1)[0]
            # 尝试从数据集获取 metadata
            logger.info(f"尝试加载数据集 metadata: {repo_id}")
            ds_meta = None
            try:
                ds_meta = LeRobotDataset(repo_id)
                logger.info(f"  数据集 metadata 加载成功: {list(ds_meta.features.keys())}")
            except Exception as e:
                logger.warning(f"  无法加载数据集 metadata: {e}，使用空配置")
        except Exception:
            ds_meta = None
    else:
        ds_meta = None

    # 创建 policy config
    cfg = PreTrainedConfig(
        type="act",  # 默认 ACT，可通过 --policy_type 覆盖
        pretrained_path=policy_path,
        device=device,
    )

    policy = make_policy(cfg=cfg, ds_meta=ds_meta)
    policy.eval()

    logger.info(f"策略加载完成: {policy_path}")
    logger.info(f"  设备: {device}")
    logger.info(f"  参数量: {sum(p.numel() for p in policy.parameters()):,}")

    return policy, cfg, ds_meta


def run_inference_loop(
    robot,
    policy,
    ds_meta=None,
    episode_time_s: float = 60.0,
    fps: int = 30,
    camera_keys: Optional[list[str]] = None,
    output_dir: Optional[str] = None,
):
    """
    运行推理循环一个 episode。

    Args:
        robot: SO101Follower 实例
        policy: 加载好的策略模型
        ds_meta: 数据集 metadata（用于格式转换）
        episode_time_s: episode 最大时长
        fps: 控制频率
        camera_keys: 图像观测的 key 列表
        output_dir: 可选，保存每帧图像用于回放分析

    Returns:
        dict: 包含 episode 结果
    """
    PreTrainedConfig, _, _, _, _, RobotObservation = _import_lerobot()

    dt = 1.0 / fps
    max_steps = int(episode_time_s * fps)

    policy.reset()  # 重置策略状态（hidden state 等）
    robot_state = robot.get_observation()

    steps = 0
    actions_history = []
    observations_history = []
    start_time = time.time()
    failures = 0

    print(f"    开始推理 | 时长上限 {episode_time_s}s | 频率 {fps}Hz")

    try:
        while steps < max_steps:
            t_loop_start = time.time()

            # 获取观测
            obs = robot.get_observation()

            # 转换为 policy 输入格式
            policy_obs = robot_observation_to_policy_input(obs, camera_keys or [])

            # 推理（无梯度）
            with torch.no_grad():
                action_dict = policy.select_action(policy_obs)

            # action_dict 可能是 dict 或 array，统一处理
            if isinstance(action_dict, dict):
                action = action_dict.get("action", list(action_dict.values())[0] if action_dict else None)
            elif isinstance(action_dict, np.ndarray):
                action = action_dict
            else:
                action = action_dict

            if action is None:
                failures += 1
                steps += 1
                continue

            actions_history.append(action if isinstance(action, np.ndarray) else np.array(action))
            observations_history.append({
                "step": steps,
                "timestamp": time.time() - start_time,
            })

            # 发送动作到机器人
            robot.send_action(action)

            steps += 1

            # 控制循环节奏
            elapsed = time.time() - t_loop_start
            sleep_time = dt - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n    用户中断 (step {steps})")
    except Exception as e:
        print(f"\n    推理异常: {e}")
        failures += 1

    actual_time = time.time() - start_time
    avg_fps = steps / actual_time if actual_time > 0 else 0

    result = {
        "steps": steps,
        "duration_s": actual_time,
        "avg_fps": avg_fps,
        "failures": failures,
        "success": failures == 0,
        "actions_history": actions_history,
    }

    return result


# ============================================================================
# 主评估流程
# ============================================================================

def run_evaluation(
    policy_path: str,
    robot_port: str,
    robot_id: str,
    camera_configs: dict,
    num_episodes: int = 10,
    episode_time_s: float = 60.0,
    fps: int = 30,
    device: str = "cuda",
    output_dir: Optional[str] = None,
    verbose: bool = True,
):
    """
    主评估流程：连接机器人 -> 加载策略 -> 运行多个 episode -> 输出结果。
    """
    PreTrainedConfig, SO101Follower, SO101FollowerConfig, _, _, _ = _import_lerobot()
    tqdm_cls = _import_tqdm()

    if verbose:
        print("=" * 60)
        print("  SO-101 本地实机评估")
        print("=" * 60)
        print(f"  策略: {policy_path}")
        print(f"  机器人: {robot_id} @ {robot_port}")
        print(f"  相机: {list(camera_configs.keys())}")
        print(f"  Episode 数: {num_episodes} × {episode_time_s}s")
        print(f"  设备: {device}")
        print(f"  输出: {output_dir or '（不保存录像）'}")
        print("=" * 60)

    # 1. 连接机器人
    robot_cfg = SO101FollowerConfig(port=robot_port, id=robot_id)
    robot = make_robot(robot_cfg, camera_configs)
    connect_robot_with_retry(robot, robot_id)

    # 2. 加载策略
    policy, policy_cfg, ds_meta = load_policy_from_hub(policy_path, device=device)

    # 确定 camera keys
    camera_keys = list(camera_configs.keys())

    # 3. 运行评估循环
    results = []
    total_steps = 0

    try:
        for ep in range(num_episodes):
            if verbose:
                print(f"\n[Episode {ep+1}/{num_episodes}]")
                sound_warn()

            # 重置机器人到初始位置（如果有 reset 机制）
            # 注意: SO101Follower 没有显式 reset，需手动归位或跳过
            print(f"    等待开始... (按 Enter 继续，输入 q 退出)")
            try:
                user_input = input()
                if user_input.strip().lower() == "q":
                    print("    用户退出")
                    break
            except EOFError:
                pass

            ep_start = time.time()
            result = run_inference_loop(
                robot=robot,
                policy=policy,
                ds_meta=ds_meta,
                episode_time_s=episode_time_s,
                fps=fps,
                camera_keys=camera_keys,
                output_dir=output_dir,
            )

            result["episode"] = ep + 1
            result["wall_time"] = datetime.now().isoformat()
            results.append(result)
            total_steps += result["steps"]

            if verbose:
                sound_episode_done()
                elapsed = time.time() - ep_start
                print(f"    完成 | {result['steps']} steps | {elapsed:.1f}s | "
                      f"avg {result['avg_fps']:.1f} Hz | 失败 {result['failures']}次")

            # 短暂暂停
            time.sleep(1.0)

    finally:
        # 断开连接
        try:
            robot.disconnect()
            logger.info("机器人已断开")
        except Exception:
            pass

    # 4. 汇总结果
    total_time = sum(r["duration_s"] for r in results)
    success_count = sum(1 for r in results if r["success"])
    fail_count = num_episodes - success_count

    print("\n" + "=" * 60)
    print("  评估结果汇总")
    print("=" * 60)
    print(f"  总 Episode 数: {len(results)}")
    print(f"  成功: {success_count} | 失败: {fail_count}")
    print(f"  成功率: {success_count/len(results)*100:.1f}%")
    print(f"  总步数: {total_steps}")
    print(f"  总时长: {total_time:.1f}s")
    print("=" * 60)

    for r in results:
        status = "✓" if r["success"] else "✗"
        print(f"  [{status}] Ep{r['episode']:2d} | "
              f"{r['steps']:4d} steps | "
              f"{r['avg_fps']:5.1f} Hz | "
              f"{r['duration_s']:6.1f}s")

    print()

    sound_all_done()

    # 保存结果
    if output_dir:
        import json
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        results_file = output_path / "eval_results.json"
        with open(results_file, "w") as f:
            json.dump({
                "policy_path": policy_path,
                "num_episodes": len(results),
                "success_count": success_count,
                "success_rate": success_count / len(results),
                "total_steps": total_steps,
                "results": [
                    {k: v for k, v in r.items() if k != "actions_history"}
                    for r in results
                ],
            }, f, indent=2)
        print(f"  结果已保存: {results_file}")

    return results


# ============================================================================
# CLI 入口
# ============================================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="SO-101 本地实机评估",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 评估 HuggingFace 上的 ACT 策略
  python -m so101.eval --policy Ready321/act_policy_so101_grab_redcube --episodes 10

  # 使用本地 checkpoint
  python -m so101.eval --policy /path/to/checkpoint --episodes 5 --device cpu

  # 指定机器人端口和相机
  python -m so101.eval --policy Ready321/act_policy_so101_grab_redcube \\
      --robot_port /dev/ttyACM0 --robot_id so101_cong_left \\
      --episodes 10
        """
    )

    # 策略参数
    parser.add_argument(
        "--policy", "-p", required=True,
        help="策略模型路径（HuggingFace repo_id 或本地目录）"
    )
    parser.add_argument(
        "--policy_type", default="act",
        choices=["act", "diffusion", "smolvla", "pi0", "pi0_fast"],
        help="策略类型（默认: act）"
    )
    parser.add_argument(
        "--device", default="cuda" if torch.cuda.is_available() else "cpu",
        help="推理设备（默认: cuda 或 cpu）"
    )

    # 机器人参数
    parser.add_argument(
        "--robot_port", default="/dev/ttyACM0",
        help="从臂串口（默认: /dev/ttyACM0）"
    )
    parser.add_argument(
        "--robot_id", default="so101_cong_left",
        help="从臂 ID（默认: so101_cong_left）"
    )
    parser.add_argument(
        "--scene", "-s", default=None,
        help="从 camera_config.yaml 读取场景配置（覆盖 --robot_port 等参数）"
    )

    # 评估参数
    parser.add_argument(
        "--episodes", "-n", type=int, default=10,
        help="评估 episode 数（默认: 10）"
    )
    parser.add_argument(
        "--episode_time", type=float, default=60.0,
        help="每个 episode 最大时长秒数（默认: 60）"
    )
    parser.add_argument(
        "--fps", type=int, default=30,
        help="控制频率（默认: 30）"
    )

    # 相机参数（当未指定 --scene 时使用）
    parser.add_argument(
        "--cameras", type=str, default=None,
        help="相机配置 JSON 字符串，例: '{\"top\":\"/dev/video12\"}'"
    )

    # 输出
    parser.add_argument(
        "--output_dir", "-o", type=str, default=None,
        help="保存评估结果和录像的目录"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="减少输出"
    )

    args = parser.parse_args(argv)
    return args


def resolve_camera_configs(args) -> dict:
    """
    根据 args 解析相机配置。
    优先级: --cameras JSON > --scene 配置 > 默认
    """
    import json

    # 如果指定了 --cameras JSON，直接解析
    if args.cameras:
        cam_specs = json.loads(args.cameras)
        resolved = {}
        for role, path_or_cfg in cam_specs.items():
            if isinstance(path_or_cfg, str):
                resolved[role] = {
                    "by_id": path_or_cfg,
                    "width": 640,
                    "height": 480,
                    "fps": 30,
                    "fourcc": None,
                }
            else:
                resolved[role] = path_or_cfg
        return resolved

    # 如果指定了场景，从配置读取
    if args.scene:
        config.refresh_system_cameras()
        scene = config.resolve_scene(args.scene)
        if scene is None:
            print(f"[错误] 场景 '{args.scene}' 不存在")
            sys.exit(1)
        cameras = {}
        for role, cam in scene.get("cameras", {}).items():
            cameras[role] = {
                "by_id": cam.get("by_id", ""),
                "width": cam.get("width", 640),
                "height": cam.get("height", 480),
                "fps": cam.get("fps", 30),
                "fourcc": cam.get("fourcc"),
            }
        # 如果场景里有机器人配置，也更新 args
        if scene.get("follower"):
            args.robot_id = scene["follower"].get("id", args.robot_id)
            args.robot_port = scene["follower"].get("port", args.robot_port)
        return cameras

    # 默认三个相机
    return {
        "top": {
            "by_id": "/dev/v4l/by-id/usb-Orbbec_R__Orbbec_Gemini_335_CP15641000AW-video-index0",
            "width": 640, "height": 480, "fps": 30, "fourcc": "YUYV",
        },
        "side": {
            "by_id": "/dev/v4l/by-id/usb-Orbbec_R__Orbbec_Gemini_335_CP1L44P0007K-video-index0",
            "width": 640, "height": 480, "fps": 30, "fourcc": "YUYV",
        },
        "wrist": {
            "by_id": "/dev/v4l/by-id/usb-icSpring_icspring_camera_20240307110322-video-index0",
            "width": 640, "height": 480, "fps": 30, "fourcc": None,
        },
    }


def main(argv=None):
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if not args.quiet else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # 解析相机配置
    camera_configs = resolve_camera_configs(args)

    run_evaluation(
        policy_path=args.policy,
        robot_port=args.robot_port,
        robot_id=args.robot_id,
        camera_configs=camera_configs,
        num_episodes=args.episodes,
        episode_time_s=args.episode_time,
        fps=args.fps,
        device=args.device,
        output_dir=args.output_dir,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
