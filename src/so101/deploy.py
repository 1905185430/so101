"""
so101.deploy — 模型推理部署 v2
=============================

在 SO-101 机械臂上运行预训练策略（ACT / Diffusion / SmolVLA / PI0 等）。

用法:
    so101 deploy --policy <repo_id> --dataset <repo_id> --scene <name>

v2 改进:
    - 端到端延迟监控（采集/推理/发送分段计时）
    - 异常恢复机制（摄像头/串口自动重连）
    - 动作跳变检测 + delta 累积保护
    - VLA 模型 task prompt 支持
    - 动作序列完整保存（可回放）
    - 实时统计面板
"""

from __future__ import annotations

import os
import sys
import time
import json
import signal
import argparse
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import torch

from so101 import config
from so101.sound_helpers import sound_start, sound_episode_done, sound_all_done, sound_warn

logger = logging.getLogger(__name__)

# ============================================================================
# 延迟导入 LeRobot
# ============================================================================


def _import_lerobot():
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig, Cv2Backends
    from lerobot.datasets import LeRobotDataset
    from lerobot.policies.factory import make_policy
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

    return (
        PreTrainedConfig,
        SO101Follower,
        SO101FollowerConfig,
        OpenCVCameraConfig,
        Cv2Backends,
        LeRobotDataset,
        make_policy,
        SO101Leader,
        SO101LeaderConfig,
    )


# ============================================================================
# 配置结构
# ============================================================================


@dataclass
class DeployConfig:
    policy_path: str
    dataset_repo: str
    policy_type: str
    device: str
    scene_name: str
    num_episodes: int
    episode_time_s: float
    fps: int
    output_dir: Optional[str]
    visualize: bool
    teleop: bool
    quiet: bool
    # 安全参数
    max_joint_velocity: float = 10.0   # deg/step
    action_smooth_alpha: float = 0.7   # 动作平滑系数
    max_delta_threshold: float = 30.0  # 单步最大跳变阈值（度），超此值视为异常
    # 推理参数
    use_bf16: bool = False
    task_prompt: str = ""              # VLA 模型的 task prompt
    rename_map: Optional[dict] = None  # 摄像头名重映射
    action_stats: Optional[dict] = None  # action 反归一化统计量
    # 恢复参数
    max_obs_retries: int = 3           # get_observation 最大重试
    max_action_retries: int = 3        # send_action 最大重试
    reconnect_on_failure: bool = True  # 失败后尝试重连


# ============================================================================
# 设备构建
# ============================================================================


def build_robot_from_scene(scene_name: str, fps: int):
    """根据场景配置构建 SO101Follower。"""
    _, SO101Follower, SO101FollowerConfig, OpenCVCameraConfig, Cv2Backends, *_ = _import_lerobot()

    config.refresh_system_cameras()
    resolved = config.resolve_scene(scene_name)
    if resolved is None:
        raise ValueError(f"场景 '{scene_name}' 不存在")

    follower_cfg = resolved.get("follower")
    if not follower_cfg or not follower_cfg.get("port"):
        raise ValueError(f"场景 '{scene_name}' 缺少 follower 配置")

    cameras = {}
    for role, cam in resolved["cameras"].items():
        dev_path = cam.get("dev", "") or cam.get("by_id", "")
        if not dev_path:
            logger.warning(f"摄像头 {role} 无可用路径，跳过")
            continue
        cameras[role] = OpenCVCameraConfig(
            index_or_path=str(dev_path),
            width=cam.get("width", 640),
            height=cam.get("height", 480),
            fps=cam.get("fps", fps),
            fourcc=cam.get("fourcc", "MJPG"),
            backend=Cv2Backends.V4L2,
        )

    robot_cfg = SO101FollowerConfig(
        port=follower_cfg["port"],
        id=follower_cfg.get("id", "so101_follower"),
        cameras=cameras,
        disable_torque_on_disconnect=True,
        use_degrees=True,
    )
    robot = SO101Follower(robot_cfg)
    return robot, resolved, list(cameras.keys())


def build_teleop_from_scene(scene_name: str):
    """根据场景配置构建 SO101Leader（可选）。"""
    _, _, _, _, _, _, SO101Leader, SO101LeaderConfig, _ = _import_lerobot()

    resolved = config.resolve_scene(scene_name)
    if resolved is None:
        return None
    leader_cfg = resolved.get("leader")
    if not leader_cfg or not leader_cfg.get("port"):
        return None

    teleop_cfg = SO101LeaderConfig(
        port=leader_cfg["port"],
        id=leader_cfg.get("id", "so101_leader"),
    )
    return SO101Leader(teleop_cfg)


# ============================================================================
# 策略加载
# ============================================================================

def load_policy(
    policy_path: str,
    dataset_repo: str,
    policy_type: str,
    device: str,
    rename_map: Optional[dict] = None,
):
    """
    加载策略模型。

    Returns:
        (policy, dataset, actual_camera_keys, action_stats)
        action_stats: 用于反归一化的 {mean, std} 或 None
    """
    PreTrainedConfig, _, _, _, _, LeRobotDataset, make_policy, *_ = _import_lerobot()

    logger.info(f"加载数据集 metadata: {dataset_repo}")
    action_stats = None
    try:
        dataset = LeRobotDataset(dataset_repo)
        ds_meta = dataset.meta
        logger.info(f"  features: {list(ds_meta.features.keys())}")
        # 提取 action 反归一化统计量
        if hasattr(ds_meta, 'stats') and 'action' in ds_meta.stats:
            act_stats = ds_meta.stats['action']
            if 'mean' in act_stats and 'std' in act_stats:
                action_stats = {
                    'mean': torch.tensor(act_stats['mean'], dtype=torch.float32),
                    'std': torch.tensor(act_stats['std'], dtype=torch.float32),
                }
                logger.info(f"  action 反归一化: mean={action_stats['mean'].numpy().round(2)}, std={action_stats['std'].numpy().round(2)}")
    except Exception as e:
        logger.warning(f"  无法加载数据集: {e}")
        logger.warning("  将尝试不依赖 dataset meta 加载策略（反归一化可能异常）")
        dataset = None
        ds_meta = None

    logger.info(f"创建策略: {policy_type} @ {device}")
    policy_cfg = PreTrainedConfig.from_pretrained(policy_path, device=device)

    policy = make_policy(cfg=policy_cfg, ds_meta=ds_meta, rename_map=rename_map)
    policy.eval()

    n_params = sum(p.numel() for p in policy.parameters())
    logger.info(f"  策略加载完成 | 参数量: {n_params:,}")

    # 推断策略期望的摄像头 keys
    cam_keys = []
    if ds_meta is not None:
        for k in ds_meta.features:
            if k.startswith("observation.images."):
                cam_keys.append(k.replace("observation.images.", ""))
    logger.info(f"  策略期望摄像头: {cam_keys}")

    return policy, dataset, cam_keys, action_stats


# ============================================================================
# 观测格式转换
# ============================================================================

MOTOR_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
GRIPPER_RANGE = (0.0, 45.0)
JOINT_RANGE = (-180.0, 180.0)


def robot_obs_to_policy_batch(
    obs: dict,
    camera_keys: list[str],
    device: str,
    motor_names: list[str],
    task_prompt: str = "",
) -> dict:
    """
    将 SO101Follower.get_observation() 返回的 dict 转换为 policy batch。

    SO101Follower 返回格式:
        {
            "shoulder_pan.pos": float, ...
            "top":    HWC uint8 numpy (来自 OpenCV V4L2 后端，RGB),
            "wrist":  HWC uint8 numpy,
        }

    Policy 期望格式:
        {
            "observation.images.top":   (1, 3, H, W) float32 [0,1],
            "observation.state":        (1, n_dof)   float32,
        }

    注意: LeRobot OpenCVCamera + V4L2 后端返回 RGB，无需 BGR→RGB 转换。
    但如果是旧版本或 FFMPEG 后端返回 BGR，则需要反转。
    这里统一做 BGR→RGB 转换，因为当前环境的摄像头实际返回 RGB，
    做一次反转后会变成 BGR（错误）。所以这里 **不做转换**，直接用。
    """
    batch = {}

    # 图像: HWC uint8 → CHW float [0,1]
    # LeRobot V4L2 后端返回 RGB，直接使用
    for cam in camera_keys:
        img = obs.get(cam)
        if img is None:
            raise ValueError(f"相机 '{cam}' 返回空帧，请检查设备连接")
        if isinstance(img, np.ndarray):
            if img.dtype == np.uint8:
                img_tensor = torch.from_numpy(img).float().permute(2, 0, 1) / 255.0
            else:
                img_tensor = torch.from_numpy(img).float().permute(2, 0, 1)
        else:
            img_tensor = img.float().permute(2, 0, 1) / 255.0
        batch[f"observation.images.{cam}"] = img_tensor.unsqueeze(0).to(device)

    # 状态: 根据 motor_names 从 dict 中提取
    state_vals = []
    for m in motor_names:
        key = f"{m}.pos"
        if key in obs:
            state_vals.append(float(obs[key]))
        else:
            state_vals.append(0.0)
    state = np.array(state_vals, dtype=np.float32)
    batch["observation.state"] = torch.from_numpy(state).unsqueeze(0).to(device)

    # VLA task prompt
    if task_prompt:
        batch["task"] = task_prompt

    return batch


# ============================================================================
# 动作后处理与安全保护
# ============================================================================


def action_tensor_to_dict(action_tensor: torch.Tensor, action_stats: Optional[dict] = None) -> dict:
    """
    将策略输出 tensor 转换为 {motor}.pos dict。
    如果提供 action_stats（mean/std），则反归一化到度数。
    """
    if action_tensor.ndim == 2:
        action_np = action_tensor.cpu().numpy()[0]
    else:
        action_np = action_tensor.cpu().numpy()

    # 反归一化：normalized * std + mean
    if action_stats is not None:
        mean = action_stats['mean'].numpy()
        std = action_stats['std'].numpy()
        action_np = action_np * std + mean

    return {f"{m}.pos": float(v) for m, v in zip(MOTOR_NAMES, action_np.flatten())}


def check_action_jump(
    action_dict: dict,
    last_action: Optional[dict],
    present_pos: dict,
    threshold: float = 30.0,
) -> tuple[bool, str]:
    """
    检查动作是否出现异常跳变。

    Returns:
        (is_safe, reason)
    """
    if last_action is None:
        return True, ""

    for motor in MOTOR_NAMES:
        key = f"{motor}.pos"
        if key not in action_dict:
            continue
        current = action_dict[key]
        prev = last_action.get(key, present_pos.get(motor, current))
        delta = abs(current - prev)
        if delta > threshold:
            return False, f"{motor} delta={delta:.1f}° (阈值={threshold}°)"

    return True, ""


def clamp_action(
    action_dict: dict,
    present_pos: dict,
    max_velocity: float = 10.0,
) -> dict:
    """速度限制 + 关节限幅。"""
    result = {}
    for motor in MOTOR_NAMES:
        key = f"{motor}.pos"
        if key not in action_dict:
            continue
        target = action_dict[key]
        present = present_pos.get(motor, target)

        # 速度限制
        delta = target - present
        if abs(delta) > max_velocity:
            target = present + np.sign(delta) * max_velocity

        # 关节限幅
        if motor == "gripper":
            target = np.clip(target, GRIPPER_RANGE[0], GRIPPER_RANGE[1])
        else:
            target = np.clip(target, JOINT_RANGE[0], JOINT_RANGE[1])

        result[key] = target
    return result


def smooth_action(
    action_dict: dict,
    last_action: Optional[dict],
    alpha: float = 0.7,
) -> dict:
    """指数平滑动作。"""
    if last_action is None:
        return action_dict
    result = {}
    for k, v in action_dict.items():
        prev = last_action.get(k, v)
        result[k] = alpha * v + (1 - alpha) * prev
    return result


# ============================================================================
# 延迟统计
# ============================================================================


@dataclass
class StepTiming:
    """单步各阶段耗时。"""
    obs_time: float = 0.0       # 获取观测
    preprocess_time: float = 0.0  # 观测格式转换
    inference_time: float = 0.0  # 模型推理
    postprocess_time: float = 0.0  # 动作后处理
    send_time: float = 0.0      # 发送动作
    total_time: float = 0.0     # 端到端


@dataclass
class EpisodeStats:
    """单 episode 运行统计。"""
    episode_idx: int = 0
    steps: int = 0
    duration_s: float = 0.0
    avg_fps: float = 0.0
    failures: int = 0
    jump_rejects: int = 0        # 跳变检测拒绝次数
    obs_retries: int = 0         # 观测重试次数
    action_retries: int = 0      # 动作重试次数
    reconnects: int = 0          # 重连次数
    success: bool = True
    # 动作历史（用于回放分析）
    action_history: list = field(default_factory=list)
    state_history: list = field(default_factory=list)
    # 延迟统计
    timings: list = field(default_factory=list)

    @property
    def avg_inference_ms(self) -> float:
        if not self.timings:
            return 0.0
        return np.mean([t.inference_time for t in self.timings]) * 1000

    @property
    def avg_total_ms(self) -> float:
        if not self.timings:
            return 0.0
        return np.mean([t.total_time for t in self.timings]) * 1000

    def to_dict(self) -> dict:
        return {
            "episode": self.episode_idx,
            "steps": self.steps,
            "duration_s": round(self.duration_s, 2),
            "avg_fps": round(self.avg_fps, 1),
            "failures": self.failures,
            "jump_rejects": self.jump_rejects,
            "obs_retries": self.obs_retries,
            "action_retries": self.action_retries,
            "reconnects": self.reconnects,
            "success": self.success,
            "avg_inference_ms": round(self.avg_inference_ms, 1),
            "avg_total_ms": round(self.avg_total_ms, 1),
            "action_history": self.action_history,
            "state_history": self.state_history,
        }


# ============================================================================
# 推理循环
# ============================================================================


def run_episode(
    robot,
    policy,
    cfg: DeployConfig,
    camera_keys: list[str],
    teleop_leader=None,
    episode_idx: int = 0,
) -> EpisodeStats:
    """运行单个 episode 的推理循环。"""
    dt = 1.0 / cfg.fps
    max_steps = int(cfg.episode_time_s * cfg.fps)

    policy.reset()
    last_action_dict: Optional[dict] = None
    last_teleop_action: Optional[dict] = None
    stats = EpisodeStats(episode_idx=episode_idx)
    start_time = time.time()

    # 可视化窗口
    windows = []
    if cfg.visualize:
        try:
            import cv2
            for cam in camera_keys:
                cv2.namedWindow(f"deploy_{cam}", cv2.WINDOW_NORMAL)
                windows.append(f"deploy_{cam}")
        except ImportError:
            pass

    logger.info(f"  Episode {episode_idx} 开始 | max_steps={max_steps} dt={dt:.3f}s")

    try:
        while stats.steps < max_steps:
            t_step = time.time()
            timing = StepTiming()

            # ---- 1. 获取观测（带重试）----
            t0 = time.time()
            obs = None
            for attempt in range(cfg.max_obs_retries):
                try:
                    obs = robot.get_observation()
                    break
                except Exception as e:
                    stats.obs_retries += 1
                    logger.warning(f"    get_observation 失败 ({attempt+1}/{cfg.max_obs_retries}): {e}")
                    if cfg.reconnect_on_failure and attempt == cfg.max_obs_retries - 1:
                        try:
                            logger.info("    尝试重连机器人...")
                            robot.disconnect()
                            time.sleep(0.5)
                            robot.connect(calibrate=False)
                            stats.reconnects += 1
                            logger.info("    重连成功")
                        except Exception as re:
                            logger.error(f"    重连失败: {re}")
                    time.sleep(0.05)
            if obs is None:
                stats.failures += 1
                continue
            timing.obs_time = time.time() - t0

            # 当前关节位置（用于限幅）
            present_pos = {m: obs.get(f"{m}.pos", 0.0) for m in MOTOR_NAMES}

            # ---- 2. 构造 policy 输入 ----
            t0 = time.time()
            try:
                batch = robot_obs_to_policy_batch(
                    obs, camera_keys, cfg.device, MOTOR_NAMES, cfg.task_prompt
                )
            except ValueError as e:
                stats.failures += 1
                logger.warning(f"    观测转换失败: {e}")
                break
            timing.preprocess_time = time.time() - t0

            # ---- 3. 推理 ----
            t0 = time.time()
            with torch.no_grad():
                if cfg.use_bf16:
                    with torch.autocast(device_type=cfg.device.split(":")[0], dtype=torch.bfloat16):
                        action_tensor = policy.select_action(batch)
                else:
                    action_tensor = policy.select_action(batch)
            timing.inference_time = time.time() - t0

            # ---- 4. 后处理 ----
            t0 = time.time()
            action_dict = action_tensor_to_dict(action_tensor, cfg.action_stats)

            # 遥操干预
            if teleop_leader is not None:
                try:
                    teleop_action = teleop_leader.get_action()
                    if last_teleop_action is not None:
                        deltas = [
                            abs(teleop_action.get(f"{m}.pos", 0) - last_teleop_action.get(f"{m}.pos", 0))
                            for m in MOTOR_NAMES
                        ]
                        if max(deltas) > 2.0:  # leader 在动
                            action_dict = teleop_action
                            logger.debug("  leader 干预")
                    last_teleop_action = teleop_action
                except Exception:
                    pass

            # 跳变检测
            is_safe, reason = check_action_jump(
                action_dict, last_action_dict, present_pos, cfg.max_delta_threshold
            )
            if not is_safe:
                stats.jump_rejects += 1
                logger.warning(f"    动作跳变拒绝: {reason}，使用上一步动作")
                if last_action_dict is not None:
                    action_dict = last_action_dict.copy()

            action_dict = clamp_action(action_dict, present_pos, cfg.max_joint_velocity)
            action_dict = smooth_action(action_dict, last_action_dict, cfg.action_smooth_alpha)
            timing.postprocess_time = time.time() - t0

            # ---- 5. 发送动作（带重试）----
            t0 = time.time()
            sent = False
            for attempt in range(cfg.max_action_retries):
                try:
                    robot.send_action(action_dict)
                    sent = True
                    break
                except Exception as e:
                    stats.action_retries += 1
                    logger.warning(f"    send_action 失败 ({attempt+1}/{cfg.max_action_retries}): {e}")
                    if cfg.reconnect_on_failure and attempt == cfg.max_action_retries - 1:
                        try:
                            robot.disconnect()
                            time.sleep(0.3)
                            robot.connect(calibrate=False)
                            stats.reconnects += 1
                        except Exception:
                            pass
                    time.sleep(0.02)
            timing.send_time = time.time() - t0
            if not sent:
                stats.failures += 1

            # 记录动作/状态历史
            stats.action_history.append({k: round(v, 3) for k, v in action_dict.items()})
            stats.state_history.append({k: round(v, 3) for k, v in present_pos.items()})

            last_action_dict = action_dict
            stats.steps += 1

            # ---- 6. 可视化 ----
            if cfg.visualize and windows:
                try:
                    import cv2
                    for cam in camera_keys:
                        img = obs.get(cam)
                        if isinstance(img, np.ndarray):
                            # 添加统计信息 overlay
                            overlay_img = img.copy()
                            info_text = f"Ep{episode_idx} Step{stats.steps} Inf:{timing.inference_time*1000:.0f}ms"
                            cv2.putText(overlay_img, info_text, (10, 25),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                            cv2.imshow(f"deploy_{cam}", overlay_img)
                    if cv2.waitKey(1) & 0xFF == 27:  # Esc
                        logger.info("  用户按 Esc 中断")
                        break
                except Exception:
                    pass

            # ---- 7. 频率控制 + 计时 ----
            timing.total_time = time.time() - t_step
            stats.timings.append(timing)

            sleep_time = dt - timing.total_time
            if sleep_time > 0:
                time.sleep(sleep_time)

            # 定期打印
            if stats.steps % cfg.fps == 0 and not cfg.quiet:
                elapsed_total = time.time() - start_time
                logger.info(
                    f"    step {stats.steps:4d} | {elapsed_total:.1f}s | "
                    f"avg_fps={stats.steps/elapsed_total:.1f} | "
                    f"inf={stats.avg_inference_ms:.0f}ms | "
                    f"total={stats.avg_total_ms:.0f}ms"
                )

    except KeyboardInterrupt:
        logger.info("  用户中断")

    finally:
        if cfg.visualize and windows:
            try:
                import cv2
                for w in windows:
                    cv2.destroyWindow(w)
            except Exception:
                pass

    stats.duration_s = time.time() - start_time
    stats.avg_fps = stats.steps / stats.duration_s if stats.duration_s > 0 else 0
    stats.success = stats.failures == 0

    return stats


# ============================================================================
# 结果保存
# ============================================================================


def save_results(results: list[EpisodeStats], cfg: DeployConfig):
    """保存推理结果到 JSON 文件。"""
    if not cfg.output_dir:
        return

    out_path = Path(cfg.output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    result_file = out_path / f"deploy_results_{int(time.time())}.json"

    success_count = sum(1 for r in results if r.success)
    total_steps = sum(r.steps for r in results)
    total_time = sum(r.duration_s for r in results)

    data = {
        "policy_path": cfg.policy_path,
        "dataset_repo": cfg.dataset_repo,
        "policy_type": cfg.policy_type,
        "scene": cfg.scene_name,
        "task_prompt": cfg.task_prompt,
        "num_episodes": len(results),
        "success_count": success_count,
        "success_rate": round(success_count / len(results), 3) if results else 0,
        "total_steps": total_steps,
        "total_time_s": round(total_time, 2),
        "config": {
            "fps": cfg.fps,
            "episode_time_s": cfg.episode_time_s,
            "max_joint_velocity": cfg.max_joint_velocity,
            "action_smooth_alpha": cfg.action_smooth_alpha,
            "max_delta_threshold": cfg.max_delta_threshold,
            "use_bf16": cfg.use_bf16,
        },
        "episodes": [r.to_dict() for r in results],
    }

    with open(result_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"结果已保存: {result_file}")


# ============================================================================
# 主流程
# ============================================================================


# ============================================================================
# 机器人归位
# ============================================================================


def get_home_position_from_dataset(dataset) -> Optional[dict]:
    """从数据集第一个 episode 的第一帧提取起始位置。"""
    if dataset is None:
        return None
    try:
        # 找 episode 0 的第一帧
        item = dataset[0]
        state = item['observation.state']
        if hasattr(state, 'numpy'):
            state = state.numpy()
        return {f"{m}.pos": float(v) for m, v in zip(MOTOR_NAMES, state.flatten())}
    except Exception:
        return None


def move_to_position(robot, target_pos: dict, speed: float = 5.0, timeout: float = 10.0):
    """
    平滑移动机器人到目标位置。

    Args:
        robot: SO101Follower 实例
        target_pos: {motor.pos: target_degree}
        speed: 每步最大移动量（度）
        timeout: 超时秒数
    """
    logger.info(f"  归位中 (速度={speed}°/步, 超时={timeout}s)...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        obs = robot.get_observation()
        current = {m: obs.get(f"{m}.pos", 0.0) for m in MOTOR_NAMES}

        # 计算每个关节需要移动的量
        step_action = {}
        max_delta = 0.0
        for motor in MOTOR_NAMES:
            key = f"{motor}.pos"
            if key not in target_pos:
                continue
            target = target_pos[key]
            curr = current.get(motor, target)
            delta = target - curr
            max_delta = max(max_delta, abs(delta))

            if abs(delta) <= speed:
                step_action[key] = target
            else:
                step_action[key] = curr + np.sign(delta) * speed

        # 已到达
        if max_delta < 1.0:
            logger.info(f"  归位完成 (剩余偏差={max_delta:.1f}°)")
            return True

        robot.send_action(step_action)
        time.sleep(0.05)  # 20Hz 归位频率

    logger.warning(f"  归位超时 (剩余偏差={max_delta:.1f}°)")
    return False


def run_deploy(argv=None):
    parser = argparse.ArgumentParser(
        description="SO-101 模型推理部署 v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # ACT 推理（场景驱动）
  so101 deploy -p Ready321/act_pick_redcube -d Ready321/pickup_redcube -s grab_redcube

  # SmolVLA 推理（自动 rename_map + task prompt）
  so101 deploy -p whosricky/svla-so101 -d Ready321/grab_redcube -s grab_redcube \\
      --policy_type smolvla --task "grab the red cube"

  # 评估 10 个 episode，保存完整动作序列
  so101 deploy -p Ready321/act_pick_redcube -d Ready321/pickup_redcube -s grab_redcube \\
      -n 10 -o results/

  # 带遥操干预和可视化
  so101 deploy -p <policy> -d <dataset> -s grab_redcube --teleop --visualize

  # BF16 推理 + 自定义安全参数
  so101 deploy -p <policy> -d <dataset> -s grab_redcube \\
      --bf16 --max_velocity 8.0 --smooth 0.8 --delta_threshold 25.0
        """,
    )
    parser.add_argument("--policy", "-p", required=True, help="策略模型路径（HF repo 或本地目录）")
    parser.add_argument("--dataset", "-d", required=True, help="训练数据集 repo_id（用于 metadata）")
    parser.add_argument("--policy_type", default="act", choices=["act", "diffusion", "smolvla", "pi0", "pi0_fast"])
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--scene", "-s", required=True, help="场景名（camera_config.yaml）")
    parser.add_argument("--episodes", "-n", type=int, default=1, help="episode 数（默认: 1）")
    parser.add_argument("--episode_time", type=float, default=60.0, help="每 episode 时长秒数")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--output_dir", "-o", default=None, help="保存评估结果目录")
    parser.add_argument("--visualize", action="store_true", help="OpenCV 实时显示相机画面")
    parser.add_argument("--teleop", action="store_true", help="允许 leader 遥操干预")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--bf16", action="store_true", help="使用 bfloat16 推理（节省显存）")
    parser.add_argument("--max_velocity", type=float, default=10.0, help="单步最大关节速度（度）")
    parser.add_argument("--smooth", type=float, default=0.7, help="动作平滑系数 0~1（默认 0.7）")
    parser.add_argument("--delta_threshold", type=float, default=30.0, help="动作跳变检测阈值（度）")
    parser.add_argument("--task", type=str, default="", help="VLA 模型的 task prompt")
    parser.add_argument("--rename_map", type=str, default=None,
                        help='JSON 格式 rename_map，例: \'{"camera1":"gripper","camera2":"top"}\'')
    parser.add_argument("--home", action="store_true", default=True,
                        help="推理前自动归位到训练数据起始位置（默认启用）")
    parser.add_argument("--no-home", action="store_true",
                        help="禁用自动归位")
    parser.add_argument("--home_speed", type=float, default=5.0,
                        help="归位速度，每步最大移动量（度，默认 5.0）")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = DeployConfig(
        policy_path=args.policy,
        dataset_repo=args.dataset,
        policy_type=args.policy_type,
        device=args.device,
        scene_name=args.scene,
        num_episodes=args.episodes,
        episode_time_s=args.episode_time,
        fps=args.fps,
        output_dir=args.output_dir,
        visualize=args.visualize,
        teleop=args.teleop,
        quiet=args.quiet,
        use_bf16=args.bf16,
        max_joint_velocity=args.max_velocity,
        action_smooth_alpha=args.smooth,
        max_delta_threshold=args.delta_threshold,
        task_prompt=args.task,
    )

    # 解析 rename_map
    rename_map = None
    if args.rename_map:
        rename_map = json.loads(args.rename_map)
        logger.info(f"使用 rename_map: {rename_map}")
    cfg.rename_map = rename_map

    # 串口权限
    os.system("sudo chmod 666 /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")

    logger.info("=" * 60)
    logger.info("  SO-101 模型推理部署 v2")
    logger.info("=" * 60)
    logger.info(f"  策略: {cfg.policy_path} ({cfg.policy_type})")
    logger.info(f"  数据集: {cfg.dataset_repo}")
    logger.info(f"  场景: {cfg.scene_name}")
    logger.info(f"  设备: {cfg.device}")
    logger.info(f"  Episodes: {cfg.num_episodes} x {cfg.episode_time_s}s @ {cfg.fps}Hz")
    if cfg.task_prompt:
        logger.info(f"  Task prompt: {cfg.task_prompt}")
    if cfg.teleop:
        logger.info("  遥操干预: 启用")
    if cfg.visualize:
        logger.info("  实时可视化: 启用")
    if cfg.use_bf16:
        logger.info("  BF16 推理: 启用")
    logger.info(f"  安全参数: max_vel={cfg.max_joint_velocity}° smooth={cfg.action_smooth_alpha} delta={cfg.max_delta_threshold}°")
    logger.info("=" * 60)

    # 1. 构建机器人
    logger.info("构建机器人...")
    robot, resolved, scene_cam_keys = build_robot_from_scene(cfg.scene_name, cfg.fps)
    logger.info(f"  场景摄像头: {scene_cam_keys}")

    # 2. 构建遥操（可选）
    teleop_leader = None
    if cfg.teleop:
        teleop_leader = build_teleop_from_scene(cfg.scene_name)
        if teleop_leader:
            logger.info("  遥操 leader 已配置")
        else:
            logger.warning("  遥操 leader 配置失败，将纯策略推理")

    # 3. 连接硬件（加重试，串口偶发丢包）
    logger.info("连接设备...")
    for attempt in range(3):
        try:
            robot.connect(calibrate=False)
            break
        except ConnectionError as e:
            if attempt < 2:
                logger.warning(f"  连接失败 (尝试 {attempt+1}/3): {e}")
                time.sleep(1)
                robot.disconnect()
            else:
                raise
    logger.info("  机器人已连接")
    if teleop_leader:
        teleop_leader.connect()
        logger.info("  遥操已连接")

    # 确认观测格式
    obs_sample = robot.get_observation()
    cam_keys_sample = [k for k in obs_sample.keys() if not k.endswith(".pos")]
    logger.info(f"  观测摄像头 keys: {cam_keys_sample}")

    # 4. 加载策略
    policy, dataset, policy_cam_keys, action_stats = load_policy(
        cfg.policy_path,
        cfg.dataset_repo,
        cfg.policy_type,
        cfg.device,
        rename_map=rename_map,
    )
    cfg.action_stats = action_stats

    # 4.5 归位到训练数据起始位置
    do_home = not args.no_home
    if do_home and dataset is not None:
        home_pos = get_home_position_from_dataset(dataset)
        if home_pos:
            logger.info(f"  归位目标: {home_pos}")
            # 打印当前位置 vs 目标
            obs_sample = robot.get_observation()
            for m in MOTOR_NAMES:
                cur = obs_sample.get(f"{m}.pos", 0)
                tgt = home_pos.get(f"{m}.pos", cur)
                delta = abs(tgt - cur)
                if delta > 2:
                    logger.info(f"    {m}: {cur:.1f}° → {tgt:.1f}° (差 {delta:.1f}°)")
            move_to_position(robot, home_pos, speed=args.home_speed, timeout=30.0)
            time.sleep(0.5)
        else:
            logger.warning("  无法提取归位位置，跳过归位")
    elif do_home:
        logger.warning("  数据集未加载，跳过归位")

    # 5. 自动推断 rename_map（如果用户没指定但摄像头名不匹配）
    if rename_map is None and policy_cam_keys and scene_cam_keys:
        missing = set(policy_cam_keys) - set(scene_cam_keys)
        if missing:
            logger.warning(f"策略期望摄像头 {policy_cam_keys}，但场景只有 {scene_cam_keys}")
            logger.warning("请检查 --rename_map 或场景配置")

    # 使用策略期望的摄像头 keys（如果场景包含的话）
    active_cam_keys = [k for k in policy_cam_keys if k in scene_cam_keys]
    if not active_cam_keys:
        active_cam_keys = scene_cam_keys
        logger.warning(f"策略摄像头与场景不匹配，使用场景摄像头: {active_cam_keys}")
    else:
        logger.info(f"  使用摄像头: {active_cam_keys}")

    # 6. 运行 episodes
    results: list[EpisodeStats] = []

    try:
        for ep in range(cfg.num_episodes):
            logger.info(f"\n=== Episode {ep + 1}/{cfg.num_episodes} ===")
            if not cfg.quiet:
                sound_warn()

            # 等待用户确认
            if cfg.num_episodes > 1:
                print("按 Enter 开始（输入 q 跳过，Ctrl+C 退出）...")
                try:
                    user = input()
                    if user.strip().lower() == "q":
                        logger.info("用户跳过")
                        continue
                except EOFError:
                    pass

            # episode 间归位（第一个 episode 已经初始归位过）
            if ep > 0 and do_home and home_pos:
                logger.info(f"  Episode {ep+1} 归位中...")
                smooth_home(robot, home_pos, speed=cfg.home_joint_speed, timeout=cfg.home_timeout_s)

            stats = run_episode(
                robot=robot,
                policy=policy,
                cfg=cfg,
                camera_keys=active_cam_keys,
                teleop_leader=teleop_leader,
                episode_idx=ep + 1,
            )
            results.append(stats)

            if not cfg.quiet:
                sound_episode_done()
            logger.info(
                f"  完成 | steps={stats.steps} | "
                f"time={stats.duration_s:.1f}s | "
                f"fps={stats.avg_fps:.1f} | "
                f"inf={stats.avg_inference_ms:.0f}ms | "
                f"failures={stats.failures} | "
                f"jumps_rejected={stats.jump_rejects} | "
                f"reconnects={stats.reconnects}"
            )

            if ep < cfg.num_episodes - 1:
                time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("\n用户中断")
    finally:
        logger.info("断开设备...")
        try:
            robot.disconnect()
        except Exception:
            pass
        if teleop_leader:
            try:
                teleop_leader.disconnect()
            except Exception:
                pass

    # 7. 汇总
    if not results:
        logger.info("没有运行任何 episode")
        return

    success_count = sum(1 for r in results if r.success)
    total_steps = sum(r.steps for r in results)
    total_time = sum(r.duration_s for r in results)

    logger.info("\n" + "=" * 60)
    logger.info("  推理结果汇总 v2")
    logger.info("=" * 60)
    logger.info(f"  总 Episodes: {len(results)}")
    logger.info(f"  成功: {success_count} | 失败: {len(results) - success_count}")
    logger.info(f"  成功率: {success_count/len(results)*100:.1f}%")
    logger.info(f"  总步数: {total_steps}")
    logger.info(f"  总时长: {total_time:.1f}s")
    all_timings = [t for r in results for t in r.timings]
    if all_timings:
        logger.info(f"  平均推理延迟: {np.mean([t.inference_time for t in all_timings])*1000:.1f}ms")
        logger.info(f"  平均端到端延迟: {np.mean([t.total_time for t in all_timings])*1000:.1f}ms")
    logger.info("=" * 60)

    for r in results:
        status = "OK" if r.success else "FAIL"
        logger.info(
            f"  [{status}] Ep{r.episode_idx:2d} | "
            f"{r.steps:4d} steps | "
            f"{r.avg_fps:5.1f} Hz | "
            f"inf={r.avg_inference_ms:5.0f}ms | "
            f"{r.duration_s:6.1f}s | "
            f"fail={r.failures} jump={r.jump_rejects} recon={r.reconnects}"
        )

    if not cfg.quiet:
        sound_all_done()

    # 保存结果
    save_results(results, cfg)


if __name__ == "__main__":
    run_deploy()
