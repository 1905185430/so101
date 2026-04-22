#!/usr/bin/env python3
"""
cam_resolve.py — 读取 camera_config.yaml，自动匹配系统摄像头，输出每个角色的设备号和最小分辨率。

用法:
    python3 cam_resolve.py                          # 打印表格
    python3 cam_resolve.py --json                   # 输出 JSON
    python3 cam_resolve.py --scene grab_redcube     # 只看某个场景
    python3 cam_resolve.py --lerobot                # 生成 lerobot-record --robot.cameras 的 JSON
    python3 cam_resolve.py --update                 # 检测并更新 camera_config.yaml 中的 dev 路径
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import yaml

# ── 常量 ──────────────────────────────────────────────
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "camera_config.yaml"
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path.home() / "so101" / "config" / "camera_config.yaml"

COLOR_FORMATS = {"YUYV", "MJPG", "GREY", "RGB3", "BGR3"}
# 真正能输出 RGB 的格式（排除 GREY 灰度、BA81 Bayer）
GOOD_COLOR_FORMATS = {"YUYV", "MJPG", "RGB3", "BGR3"}
# 优先选真正的彩色格式
PREFERRED_FORMATS = ["MJPG", "YUYV", "RGB3", "BGR3"]


# ── 系统摄像头探测 ─────────────────────────────────────
def detect_system_cameras() -> dict:
    """返回 {serial_short: [{dev, formats, resolutions}, ...]} 的字典。"""
    result = {}
    for p in sorted(
        [p for p in Path("/dev").glob("video*") if p.name[5:].isdigit()],
        key=lambda p: int(p.name[5:]),
    ):
        dev = str(p)

        # udev 信息
        udev = subprocess.run(
            ["udevadm", "info", "-q", "property", "-n", dev],
            capture_output=True, text=True,
        )
        props = {}
        for line in udev.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
        serial = props.get("ID_SERIAL_SHORT", "")

        # 格式
        ctl = subprocess.run(
            ["v4l2-ctl", "--list-formats", "-d", dev],
            capture_output=True, text=True,
        )
        fmt_codes = []
        for line in ctl.stdout.split("\n"):
            if "'" in line:
                code = line.split("'")[1]
                fmt_codes.append(code)

        # 只要包含 MJPG/YUYV 等真正彩色格式的节点
        good_fmts = [f.strip() for f in fmt_codes if f.strip() in GOOD_COLOR_FORMATS]
        if not good_fmts:
            continue

        # 分辨率
        resolutions = _get_resolutions(dev)

        entry = {
            "dev": dev,
            "formats": fmt_codes,
            "good_formats": good_fmts,
            "resolutions": resolutions,
            "has_preferred": any(f in PREFERRED_FORMATS for f in good_fmts),
        }
        result.setdefault(serial, []).append(entry)

    return result


def _get_resolutions(dev: str) -> list[dict]:
    """返回 [{w, h, fps: [...]}, ...] 按宽度升序。"""
    ctl = subprocess.run(
        ["v4l2-ctl", "--list-formats-ext", "-d", dev],
        capture_output=True, text=True,
    )
    resolutions = {}
    current_res = None
    for line in ctl.stdout.split("\n"):
        line = line.strip()
        if line.startswith("Size: Discrete"):
            parts = line.split(":")[-1].strip().replace("Discrete ", "").split("x")
            current_res = (int(parts[0]), int(parts[1]))
            if current_res not in resolutions:
                resolutions[current_res] = []
        elif "Interval: Discrete" in line and current_res:
            fps_part = line.split("(")[-1].rstrip(")")
            fps_val = float(fps_part.replace(" fps", ""))
            fps_int = int(fps_val) if fps_val == int(fps_val) else fps_val
            if fps_int not in resolutions[current_res]:
                resolutions[current_res].append(fps_int)

    out = []
    for (w, h), fps_list in sorted(resolutions.items()):
        out.append({"w": w, "h": h, "fps": sorted(set(fps_list), reverse=True)})
    return out


def _test_open(dev: str, backend) -> bool:
    cap = cv2.VideoCapture(dev, backend)
    if not cap.isOpened():
        return False
    ret, _ = cap.read()
    cap.release()
    return ret


# ── 主逻辑 ─────────────────────────────────────────────
def detect_best_cameras() -> dict:
    """返回 {serial_short: best_entry} 字典，每个 serial 只保留最佳节点。"""
    sys_cams = detect_system_cameras()
    best_map = {}
    for serial, entries in sys_cams.items():
        best = None
        for e in entries:
            if e["has_preferred"]:
                best = e
                break
        if not best:
            best = entries[0]
        best_map[serial] = best
    return best_map


def update_config():
    """检测摄像头并更新 camera_config.yaml 中的 dev 字段。"""
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    cameras_cfg = cfg.get("cameras", {})
    best_map = detect_best_cameras()

    updated = []
    not_found = []

    for cam_name, cam_def in cameras_cfg.items():
        serial = cam_def.get("serial", "")

        # 提取 serial_short
        serial_short = serial
        for prefix in ["icSpring_icspring_camera_", "Orbbec_R__Orbbec_Gemini_335_",
                       "Sonix_Technology_Co.__Ltd._USB2.0_HD_UVC_WebCam"]:
            if serial.startswith(prefix):
                serial_short = serial[len(prefix):]
                break

        if serial_short in best_map:
            new_dev = best_map[serial_short]["dev"]
            old_dev = cam_def.get("dev", "(未设置)")
            cam_def["dev"] = new_dev
            if old_dev != new_dev:
                updated.append((cam_name, old_dev, new_dev))
            else:
                updated.append((cam_name, old_dev, new_dev))
        else:
            not_found.append(cam_name)

    # 写回 YAML（尽量保留格式）
    try:
        from ruamel.yaml import YAML
        ry = YAML()
        ry.preserve_quotes = True
        with open(CONFIG_PATH) as f:
            data = ry.load(f)
        for cam_name, cam_def in cameras_cfg.items():
            if cam_name in data.get("cameras", {}):
                data["cameras"][cam_name]["dev"] = cam_def.get("dev", "")
        with open(CONFIG_PATH, "w") as f:
            ry.dump(data, f)
    except ImportError:
        # 回退：PyYAML（格式会丢失，但内容正确）
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # 打印结果
    print(f"\n已更新 {CONFIG_PATH}:\n")
    print(f"  {'摄像头':<12} {'旧路径':<20} {'新路径':<20}")
    print(f"  {'-'*52}")
    for cam_name, old_dev, new_dev in updated:
        marker = " ← 变更" if old_dev != new_dev else ""
        print(f"  {cam_name:<12} {old_dev:<20} {new_dev:<20}{marker}")

    if not_found:
        print(f"\n  未找到: {', '.join(not_found)}")

    print()


def resolve(scene_name: str = "", for_lerobot: bool = False):
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    cameras_cfg = cfg.get("cameras", {})
    scenes_cfg = cfg.get("scenes", {})
    sys_cams = detect_system_cameras()

    # 确定要解析的场景
    if scene_name:
        if scene_name not in scenes_cfg:
            print(f"场景 '{scene_name}' 不存在。可用: {list(scenes_cfg.keys())}")
            return
        scenes = {scene_name: scenes_cfg[scene_name]}
    else:
        scenes = scenes_cfg

    rows = []  # (role, cam_name, serial, dev, min_res, all_res, backend)
    lerobot_cams = {}

    for sname, scene in scenes.items():
        if for_lerobot:
            print(f"\n场景: {sname}  任务: {scene.get('task', '')}")

        for role, cam_ref in scene.get("cameras", {}).items():
            cam_def = cameras_cfg.get(cam_ref, {})
            serial = cam_def.get("serial", "")

            # 去掉 serial 前缀（如 "icSpring_icspring_camera_"）
            serial_short = serial
            for prefix in ["icSpring_icspring_camera_", "Orbbec_R__Orbbec_Gemini_335_",
                           "Sonix_Technology_Co.__Ltd._USB2.0_HD_UVC_WebCam"]:
                if serial.startswith(prefix):
                    serial_short = serial[len(prefix):]
                    break

            # 匹配系统设备
            matched = sys_cams.get(serial_short, [])
            if not matched:
                # 也尝试完整 serial
                for s, entries in sys_cams.items():
                    for e in entries:
                        if serial in e["dev"] or s in serial:
                            matched.append(e)

            if not matched:
                rows.append((role, cam_ref, serial_short, "NOT FOUND", "-", [], "NONE"))
                continue

            # 选最佳节点：优先有首选格式的（MJPG/YUYV）
            best = None
            for m in matched:
                if m["has_preferred"]:
                    best = m
                    break
            if not best:
                best = matched[0]

            dev = best["dev"]
            res_list = best["resolutions"]
            min_res = f"{res_list[0]['w']}x{res_list[0]['h']}" if res_list else "-"
            all_res = [f"{r['w']}x{r['h']}" for r in res_list]
            fmt_str = ",".join(best["good_formats"])

            rows.append((role, cam_ref, serial_short, dev, min_res, all_res, fmt_str))

            if for_lerobot and res_list:
                min_r = res_list[0]
                lerobot_cams[role] = {
                    "type": "opencv",
                    "index_or_path": dev,
                    "width": min_r["w"],
                    "height": min_r["h"],
                    "fps": min_r["fps"][0] if min_r["fps"] else 30,
                    "backend": "V4L2",
                }

    if for_lerobot:
        print(f"\nlerobot-record --robot.cameras='{json.dumps(lerobot_cams, ensure_ascii=False)}'")
        return

    # 打印表格
    print(f"\n{'角色':<8} {'配置名':<12} {'设备':<15} {'最小分辨率':<12} {'所有分辨率':<40} {'格式':<12}")
    print("-" * 105)
    for role, cam_ref, serial, dev, min_res, all_res, fmt_str in rows:
        res_str = ", ".join(all_res)
        print(f"{role:<8} {cam_ref:<12} {dev:<15} {min_res:<12} {res_str:<40} {fmt_str:<12}")

    # JSON 输出
    return rows


def main():
    parser = argparse.ArgumentParser(description="解析摄像头配置，匹配系统设备")
    parser.add_argument("--scene", "-s", default="", help="指定场景名（默认全部）")
    parser.add_argument("--json", "-j", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--lerobot", "-l", action="store_true",
                        help="输出 lerobot-record --robot.cameras 的 JSON 参数")
    parser.add_argument("--update", "-u", action="store_true",
                        help="检测摄像头并更新 camera_config.yaml 中的 dev 路径")
    args = parser.parse_args()

    if args.update:
        update_config()
        return

    if args.json:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        sys_cams = detect_system_cameras()
        print(json.dumps(sys_cams, indent=2, ensure_ascii=False))
        return

    resolve(args.scene, for_lerobot=args.lerobot)


if __name__ == "__main__":
    main()
