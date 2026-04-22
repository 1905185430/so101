"""
so101.record — 数据录制
========================

基于 lerobot v0.5.1 API，场景驱动录制。

改进点：
  1. streaming_encoding=True — 实时编码，save_episode() 毫秒级
  2. VideoEncodingManager — 异常退出自动 finalize + 清理临时图片
  3. try/finally — finalize 在 finally 中，Ctrl+C 不丢数据
  4. --resume — 追加录制
  5. --vcodec — 可选编码器 (h264/hevc/av1/auto)
  6. --overwrite — 覆盖已有数据集
  7. --name — 自定义数据集名
  8. 声音提示 — 开始/完成/重置/全部完成
"""

import os
import sys
import shutil
import argparse
import logging
import threading
from datetime import datetime
from pathlib import Path

from so101 import config
from so101.sound_helpers import (
    sound_start, sound_episode_done, sound_reset, sound_all_done, sound_warn,
)

logger = logging.getLogger(__name__)

DEFAULT_FPS = 30
DEFAULT_EPISODES = 50
DEFAULT_EPISODE_TIME = 60
DEFAULT_VCODEC = "libsvtav1"


def parse_record_args(argv=None) -> argparse.Namespace:
    """解析录制参数（供 CLI 调用）。"""
    parser = argparse.ArgumentParser(
        description="SO-101 数据录制（场景驱动）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scene", "-s", required=True,
        help="场景名（对应 camera_config.yaml 中的 scenes.<name>）",
    )
    parser.add_argument(
        "--episodes", "-n", type=int, default=DEFAULT_EPISODES,
        help=f"采集 episode 总数（默认: {DEFAULT_EPISODES}）",
    )
    parser.add_argument(
        "--episode-time", type=int, default=DEFAULT_EPISODE_TIME,
        help=f"每个 episode 最长录制秒数（默认: {DEFAULT_EPISODE_TIME}）",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="覆盖已有数据集",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="追加到已有数据集（与 --overwrite 互斥）",
    )
    parser.add_argument(
        "--name",
        help="HuggingFace 仓库名（仅名称，不含用户名），默认从场景名生成",
    )
    parser.add_argument(
        "--dataset-repo-id",
        help="完整的 HuggingFace repo_id（覆盖 --name）",
    )
    parser.add_argument(
        "--vcodec", default=DEFAULT_VCODEC,
        choices=["h264", "hevc", "libsvtav1", "auto"],
        help=f"视频编码器（默认: {DEFAULT_VCODEC}）",
    )
    return parser.parse_args(argv)


# ============================================================================
# 构建 LeRobot 配置
# ============================================================================

def build_robot_config(resolved: dict):
    """从 resolved 场景配置构建 LeRobot 机器人实例。"""
    from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig
    from lerobot.cameras.configs import Cv2Backends
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

    follower_cfg = resolved["follower"]
    leader_cfg = resolved["leader"]

    # 构建摄像头配置
    # 优先使用 resolve_scene 检测到的 dev 路径（已验证格式正确），
    # by_id/by_path 仅作参考（Orbbec 多子设备场景下索引可能漂移）。
    # 注意：conda 环境的 opencv 对 YUYV 格式有兼容性问题，强制使用 MJPG。
    cameras = {}
    for role, cam in resolved["cameras"].items():
        dev_path = cam.get("dev", "") or cam.get("by_id", "")
        if not dev_path:
            continue
        cameras[role] = OpenCVCameraConfig(
            index_or_path=dev_path,
            width=cam.get("width", 640),
            height=cam.get("height", 480),
            fps=cam.get("fps", 30),
            fourcc=cam.get("fourcc", "MJPG"),
            backend=Cv2Backends.V4L2,
        )

    robot_config = SO101FollowerConfig(
        id=follower_cfg["id"],
        port=follower_cfg["port"],
        cameras=cameras,
    )
    teleop_config = SO101LeaderConfig(
        id=leader_cfg["id"],
        port=leader_cfg["port"],
    )

    return robot_config, teleop_config


# ============================================================================
# 数据集准备
# ============================================================================

def prepare_dataset(
    robot,
    fps: int,
    repo_id: str,
    overwrite: bool,
    resume: bool,
    vcodec: str,
):
    """创建/复用数据集，处理目录冲突。"""
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.feature_utils import hw_to_dataset_features

    dataset_local_path = config.LEROBOT_CACHE_ROOT / repo_id

    # ── resume 模式 ──
    if resume:
        if not dataset_local_path.exists():
            print(f"[错误] --resume 需要已有数据集，但未找到：{dataset_local_path}")
            print("  提示：先不加 --resume 创建数据集，再用 --resume 追加")
            sys.exit(1)
        print(f"[追加] 在已有数据集上继续录制：{repo_id}")
        num_cameras = len(robot.cameras) if hasattr(robot, "cameras") else 0
        return LeRobotDataset.resume(
            repo_id,
            root=str(dataset_local_path),
            vcodec=vcodec,
            streaming_encoding=True,
            encoder_threads=1,          # CPU 紧张时从 2 降到 1
            image_writer_threads=2 * num_cameras if num_cameras > 0 else 4,
        )

    # ── 新建数据集 ──
    action_features = hw_to_dataset_features(robot.action_features, "action")
    obs_features = hw_to_dataset_features(robot.observation_features, "observation")
    dataset_features = {**action_features, **obs_features}

    if dataset_local_path.exists():
        if overwrite:
            print(f"[覆盖模式] 删除已有数据集：{dataset_local_path}")
            shutil.rmtree(dataset_local_path)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            username = repo_id.split("/")[0]
            base_name = repo_id.split("/")[1]
            new_name = f"{base_name}_{timestamp}"
            repo_id = f"{username}/{new_name}"
            dataset_local_path = config.LEROBOT_CACHE_ROOT / repo_id
            print(f"[自动重命名] 数据集目录已存在，新数据集：{repo_id}")
            print(f"  旧目录保留在：{config.LEROBOT_CACHE_ROOT / (username + '/' + base_name)}")
            print(f"  如需覆盖，请加 --overwrite")
    else:
        print(f"[新建] 数据集将创建在：{dataset_local_path}")

    print(f"最终 repo_id = {repo_id}")

    num_cameras = len(robot.cameras) if hasattr(robot, "cameras") else 0
    dataset = LeRobotDataset.create(
        repo_id=repo_id,
        fps=fps,
        features=dataset_features,
        robot_type=robot.name,
        use_videos=True,
        image_writer_threads=2 * num_cameras if num_cameras > 0 else 4,
        vcodec=vcodec,
        streaming_encoding=True,
        encoder_threads=1,          # CPU 紧张时从 2 降到 1
    )
    return dataset


# ============================================================================
# 主流程
# ============================================================================

def run_record(argv=None):
    """执行录制。"""
    args = parse_record_args(argv)

    # 参数互斥
    if args.overwrite and args.resume:
        print("[错误] --overwrite 和 --resume 不能同时使用")
        sys.exit(1)

    scene_name = args.scene

    # 刷新系统摄像头
    config.refresh_system_cameras()

    # 设备检查
    all_ok, _ = config.check_scene(scene_name)
    from so101.check import check_scene
    if not check_scene(scene_name):
        print("\n设备检查未通过，请修复后再运行。")
        sys.exit(1)

    # 解析场景
    resolved = config.resolve_scene(scene_name)
    if resolved is None:
        print(f"[错误] 场景 '{scene_name}' 不存在！")
        sys.exit(1)

    task_description = resolved.get("task", scene_name)

    # 确定 repo_id
    if args.dataset_repo_id:
        repo_id = args.dataset_repo_id
    elif args.name:
        repo_id = f"Ready321/{args.name}"
    else:
        repo_id = f"Ready321/so101_{scene_name}"

    # 串口权限
    ret = os.system("sudo chmod 666 /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")
    if ret != 0:
        print("[警告] 串口权限修改可能失败，如果后续连接失败请手动执行：")
        print("  sudo chmod 666 /dev/ttyACM* /dev/ttyUSB*")

    # 构建机器人
    print("[配置] 构建机器人...")
    robot_config, teleop_config = build_robot_config(resolved)

    from lerobot.robots.so_follower import SO101Follower
    from lerobot.teleoperators.so_leader import SO101Leader
    from lerobot.datasets.video_utils import VideoEncodingManager
    from lerobot.utils.control_utils import init_keyboard_listener
    from lerobot.utils.visualization_utils import init_rerun
    from lerobot.scripts.lerobot_record import record_loop
    from lerobot.processor import make_default_processors

    robot = SO101Follower(robot_config)
    teleop = SO101Leader(teleop_config)

    # 采集信息
    print(f"[采集] 场景={scene_name}  任务={task_description}  "
          f"episodes={args.episodes}x{args.episode_time}s  "
          f"vcodec={args.vcodec}  repo={repo_id}\n")

    # 准备数据集
    dataset = prepare_dataset(
        robot=robot,
        fps=DEFAULT_FPS,
        repo_id=repo_id,
        overwrite=args.overwrite,
        resume=args.resume,
        vcodec=args.vcodec,
    )

    # 键盘监听 & 可视化
    _, events = init_keyboard_listener()
    init_rerun(session_name=f"recording_{scene_name}")

    # 连接硬件
    print("[设备] 连接中...")
    robot.connect()
    teleop.connect()
    print("[设备] 连接成功\n")

    # 处理器
    teleop_action_processor, robot_action_processor, robot_observation_processor = (
        make_default_processors()
    )

    # 录制主循环
    episode_idx = 0
    with VideoEncodingManager(dataset):
        try:
            while episode_idx < args.episodes and not events["stop_recording"]:

                # ── 录制 ──
                print(f"\n>>> 第 {episode_idx + 1} / {args.episodes} episode  [{task_description}]")
                print("    按 Enter 开始录制（Ctrl+C 退出）...")
                try:
                    input()
                except KeyboardInterrupt:
                    print("\n[中断] 用户取消，正在保存已录制数据...")
                    break

                sound_start()

                record_loop(
                    robot=robot,
                    events=events,
                    fps=DEFAULT_FPS,
                    teleop_action_processor=teleop_action_processor,
                    robot_action_processor=robot_action_processor,
                    robot_observation_processor=robot_observation_processor,
                    teleop=teleop,
                    dataset=dataset,
                    control_time_s=args.episode_time,
                    single_task=task_description,
                    display_data=True,
                )

                # ── 重置 ──
                if not events["stop_recording"] and (
                    episode_idx < args.episodes - 1 or events["rerecord_episode"]
                ):
                    print(f"\n<<< 重置阶段：请操作主臂归位从臂")
                    print("    完成后按 Enter 继续...")
                    sound_reset()

                    stop_reset = threading.Event()

                    def _make_reset_loop(stop_evt, evt_copy):
                        """工厂函数，避免闭包捕获外层可变变量。"""
                        def _loop():
                            while not stop_evt.is_set():
                                record_loop(
                                    robot=robot,
                                    events=evt_copy,
                                    fps=DEFAULT_FPS,
                                    teleop_action_processor=teleop_action_processor,
                                    robot_action_processor=robot_action_processor,
                                    robot_observation_processor=robot_observation_processor,
                                    teleop=teleop,
                                    control_time_s=5,
                                    single_task=task_description,
                                    display_data=False,
                                )
                        return _loop

                    t = threading.Thread(
                        target=_make_reset_loop(stop_reset, events), daemon=True
                    )
                    t.start()

                    try:
                        input()
                    except KeyboardInterrupt:
                        pass
                    stop_reset.set()
                    t.join(timeout=2)

                # ── 保存 episode ──
                if events["rerecord_episode"]:
                    print("\n!! 重新录制当前 episode")
                    events["rerecord_episode"] = False
                    events["exit_early"] = False
                    dataset.clear_episode_buffer()
                    continue

                dataset.save_episode()
                print(f"    [saved] episode {episode_idx + 1} 完成")
                sound_episode_done()
                episode_idx += 1

            # 全部完成
            print(f"\n=== 采集完成，共 {episode_idx} episodes ===")
            sound_all_done()

        except KeyboardInterrupt:
            print("\n[中断] Ctrl+C，正在保存已录制数据...")

        finally:
            # finalize 由 VideoEncodingManager.__exit__ 调用
            print("[清理] 正在关闭设备...")

            # 如果录了至少一条episode，给用户一个遥操归位的机会
            if episode_idx > 0:
                print("\n=== 归位阶段 ===")
                print("  现在可以手动遥操将手臂移至安全归位位置")
                print("  归位完成后按 Enter 确认断电（Ctrl+C 跳过）")

                stop_reset = threading.Event()

                def _make_reset_loop(stop_evt, evt_copy):
                    def _loop():
                        while not stop_evt.is_set():
                            record_loop(
                                robot=robot,
                                events=evt_copy,
                                fps=DEFAULT_FPS,
                                teleop_action_processor=teleop_action_processor,
                                robot_action_processor=robot_action_processor,
                                robot_observation_processor=robot_observation_processor,
                                teleop=teleop,
                                control_time_s=5,
                                single_task=task_description,
                                display_data=False,
                            )
                    return _loop

                t = threading.Thread(
                    target=_make_reset_loop(stop_reset, events), daemon=True
                )
                t.start()

                try:
                    input()
                except KeyboardInterrupt:
                    print("\n[跳过] 用户取消归位阶段")
                stop_reset.set()
                t.join(timeout=2)

            print("[清理] 断开设备连接...")
            try:
                robot.disconnect()
            except Exception:
                pass
            try:
                teleop.disconnect()
            except Exception:
                pass

    # 上传
    print(f"[完成] 共录制 {episode_idx} 个 episode")
    print(f"[完成] 数据集: {repo_id}")
    print(f"[完成] 本地路径: {config.LEROBOT_CACHE_ROOT / repo_id.split('/', 1)[1]}")

    try:
        push = input("\n上传到 HuggingFace Hub? [y/N]: ").strip().lower()
    except KeyboardInterrupt:
        print("\n[跳过] 上传步骤")
        push = "n"

    if push == "y":
        print("[上传] push_to_hub() ...")
        dataset.push_to_hub()
        print("[上传] 完成!")
    else:
        print("[提示] 本地已保存，后续可手动上传：")
        print(f"  so101 dataset push --repo {repo_id}")
