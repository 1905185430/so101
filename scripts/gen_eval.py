#!/usr/bin/env python3
"""
gen_eval.py — 根据 camera_config.yaml 生成 run_eval.sh

用法:
    python3 gen_eval.py                              # 预览，不写文件
    python3 gen_eval.py -o ~/run_eval.sh             # 写入文件
    python3 gen_eval.py --scene grab_redcube         # 指定场景
    python3 gen_eval.py --model act                  # 指定模型（决定相机命名）
    python3 gen_eval.py --resolution 640x480         # 强制指定分辨率
    python3 gen_eval.py --resolution min             # 使用最小分辨率
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

CONFIG_PATH = Path.home() / "so101" / "config" / "camera_config.yaml"


def get_min_resolution(dev: str) -> str:
    """用 v4l2-ctl 获取设备的最小分辨率。"""
    result = subprocess.run(
        ["v4l2-ctl", "--list-formats-ext", "-d", dev],
        capture_output=True, text=True,
    )
    min_w, min_h = 99999, 99999
    for line in result.stdout.split("\n"):
        if "Size: Discrete" in line:
            parts = line.split(":")[-1].strip().replace("Discrete ", "").split("x")
            w, h = int(parts[0]), int(parts[1])
            if w * h < min_w * min_h:
                min_w, min_h = w, h
    if min_w == 99999:
        return ""
    return f"{min_w}x{min_h}"

# 模型对应的相机字段名映射
# SmolVLA 用 camera1/2/3，ACT 用 top/wrist/side
MODEL_CAM_NAMES = {
    "smolvla": ["camera1", "camera2", "camera3"],
    "act":     ["top", "wrist", "side"],
    "pi0":     ["top", "wrist", "side"],
}


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_scene(cfg: dict, scene_name: str) -> dict:
    scenes = cfg.get("scenes", {})
    if scene_name:
        if scene_name not in scenes:
            print(f"场景 '{scene_name}' 不存在。可用: {list(scenes.keys())}")
            sys.exit(1)
        return scenes[scene_name]
    # 默认取第一个
    if not scenes:
        print("camera_config.yaml 中没有定义场景")
        sys.exit(1)
    name = list(scenes.keys())[0]
    print(f"# 未指定场景，使用: {name}")
    return scenes[name]


def resolve_cameras(cfg: dict, scene: dict) -> list:
    """返回 [(role, cam_name, dev, resolutions, default_res), ...]"""
    cameras_cfg = cfg.get("cameras", {})
    result = []

    for role, cam_ref in scene.get("cameras", {}).items():
        cam = cameras_cfg.get(cam_ref, {})
        dev = cam.get("dev", "")
        width = cam.get("width", 640)
        height = cam.get("height", 480)
        fps = cam.get("fps", 30)

        if not dev:
            print(f"警告: {cam_ref} 没有 dev 路径，先运行: python3 scripts/cam_resolve.py --update")
            continue

        # 从配置读默认分辨率
        default_res = f"{width}x{height}"
        result.append((role, cam_ref, dev, default_res, fps))

    return result


def pick_resolution(res_str: str, default_res: str, min_res: str) -> tuple:
    """根据 --resolution 参数选择分辨率。返回 (width, height)。"""
    if res_str == "min":
        w, h = min_res.split("x")
    elif res_str == "default" or not res_str:
        w, h = default_res.split("x")
    else:
        w, h = res_str.split("x")
    return int(w), int(h)


def build_cameras_json(cams: list, cam_names: list, resolution: str) -> str:
    """构建 --robot.cameras 的 JSON 字符串。"""
    cameras = {}
    for i, (role, cam_name, dev, default_res, fps) in enumerate(cams):
        field_name = cam_names[i] if i < len(cam_names) else role

        # 分辨率选择
        if resolution == "min":
            res = get_min_resolution(dev)
            if not res:
                res = default_res
            w, h = res.split("x")
        elif resolution and resolution != "default":
            w, h = resolution.split("x")
        else:
            w, h = default_res.split("x")

        w, h = int(w), int(h)

        cameras[field_name] = {
            "type": "opencv",
            "index_or_path": dev,
            "width": w,
            "height": h,
            "fps": fps,
            "backend": "V4L2",
        }

    return json.dumps(cameras, ensure_ascii=False)


def build_arm_cfg(cfg: dict, scene: dict) -> dict:
    """从配置读取机械臂信息。"""
    arms_cfg = cfg.get("arms", {})
    result = {}

    for arm_type in ["follower", "leader"]:
        ref = scene.get(arm_type)
        if ref and ref in arms_cfg:
            arm = arms_cfg[ref]
            result[arm_type] = {
                "port": arm.get("port", ""),
                "id": arm.get("name", ref),
            }

    return result


def generate_script(cfg: dict, scene: dict, scene_name: str,
                    model: str, resolution: str, repo_id: str,
                    policy_path: str, num_episodes: int,
                    episode_time: int, extra_args: str) -> str:
    """生成完整的 run_eval.sh 内容。"""

    cams = resolve_cameras(cfg, scene)
    if not cams:
        print("没有可用的摄像头")
        sys.exit(1)

    arm_cfg = build_arm_cfg(cfg, scene)
    cam_names = MODEL_CAM_NAMES.get(model.lower(), ["top", "wrist", "side"])
    task = scene.get("task", "")
    cameras_json = build_cameras_json(cams, cam_names, resolution)

    follower = arm_cfg.get("follower", {})
    leader = arm_cfg.get("leader", {})

    # 模型特定参数（仅作默认提示，不自动添加，避免和 extra_args 重复）
    # 用户通过 --extra-args 添加模型特有参数，如:
    #   smolvla: --policy.compile_model=false --policy.empty_cameras=1
    #   act: (无额外参数)

    lines = [
        "#!/bin/bash",
        f"# 自动生成 by gen_eval.py  —  场景: {scene_name}  模型: {model}",
        f"# 摄像头配置来源: {CONFIG_PATH}",
        f"# 生成时间请运行: python3 ~/so101/scripts/gen_eval.py",
        "",
        f"rm -rf ~/.cache/huggingface/lerobot/{repo_id} 2>/dev/null",
        "",
        f"lerobot-record \\",
        f"  --robot.type=so101_follower \\",
        f"  --robot.port={follower.get('port', 'TODO')} \\",
        f"  --robot.id={follower.get('id', 'TODO')} \\",
        f"  --teleop.type=so101_leader \\",
        f"  --teleop.port={leader.get('port', 'TODO')} \\",
        f"  --teleop.id={leader.get('id', 'TODO')} \\",
        f"  --robot.cameras='{cameras_json}' \\",
        f"  --display_data=false \\",
        f"  --dataset.single_task=\"{task}\" \\",
        f"  --dataset.repo_id={repo_id} \\",
        f"  --dataset.episode_time_s={episode_time} \\",
        f"  --dataset.num_episodes={num_episodes} \\",
        f"  --dataset.streaming_encoding=true \\",
        f"  --dataset.encoder_threads=2 \\",
        f"  --dataset.push_to_hub=false \\",
        f"  --policy.path={policy_path} \\",
    ]

    if extra_args:
        for arg in extra_args.split():
            lines.append(f"  {arg} \\")

    # 去掉最后一行的反斜杠
    if lines[-1].endswith(" \\"):
        lines[-1] = lines[-1][:-2]

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="根据 camera_config.yaml 生成 run_eval.sh")
    parser.add_argument("--scene", "-s", default="", help="场景名（默认第一个）")
    parser.add_argument("--model", "-m", default="smolvla",
                        choices=["smolvla", "act", "pi0"],
                        help="模型类型，决定相机命名（默认 smolvla）")
    parser.add_argument("--resolution", "-r", default="",
                        help="分辨率: 'min' 用最小, 'default' 用配置值, '640x480' 强制指定")
    parser.add_argument("--repo-id", default="", help="数据集 repo_id")
    parser.add_argument("--policy-path", default="", help="策略模型路径")
    parser.add_argument("--num-episodes", "-n", type=int, default=10, help="episode 数量")
    parser.add_argument("--episode-time", "-t", type=int, default=30, help="每 episode 秒数")
    parser.add_argument("--extra-args", default="", help="额外参数（空格分隔）")
    parser.add_argument("--output", "-o", default="", help="输出文件路径（默认打印到 stdout）")
    args = parser.parse_args()

    cfg = load_config()
    scene_name = args.scene or list(cfg.get("scenes", {}).keys())[0]
    scene = get_scene(cfg, args.scene)

    # 默认值
    repo_id = args.repo_id or f"Ready321/eval_{args.model}_{scene_name}"
    policy_path = args.policy_path or f"Ready321/{args.model}_{scene_name}"

    script = generate_script(
        cfg=cfg,
        scene=scene,
        scene_name=scene_name,
        model=args.model,
        resolution=args.resolution,
        repo_id=repo_id,
        policy_path=policy_path,
        num_episodes=args.num_episodes,
        episode_time=args.episode_time,
        extra_args=args.extra_args,
    )

    if args.output:
        out_path = Path(args.output).expanduser()
        out_path.write_text(script)
        out_path.chmod(0o755)
        print(f"已写入: {out_path}")
    else:
        print(script)


if __name__ == "__main__":
    main()
