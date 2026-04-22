"""
so101.capture — 摄像头画面采集预览
==================================

Usage:
    so101 capture                          # 打开所有摄像头
    so101 capture --role top               # 按场景角色打开
    so101 capture --filter Orbbec          # 按关键词过滤
"""

import os
import time
import cv2
from pathlib import Path
from so101 import config


def _open_camera(dev_path: str, width: int = 640, height: int = 480) -> cv2.VideoCapture:
    """
    尝试打开摄像头，优先 V4L2 后端，失败则回退默认后端。
    Orbbec 部分子设备节点在 V4L2 后端下无法打开，需要回退。
    """
    for backend in (cv2.CAP_V4L2, cv2.CAP_ANY):
        cap = cv2.VideoCapture(dev_path, backend)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                return cap
            cap.release()
    return cv2.VideoCapture()  # 未打开的空实例


def _resolve_role_dev(role: str) -> tuple:
    """
    从场景配置中解析角色对应的设备路径。
    返回 (dev_path, product) 或 (None, None)。
    """
    config.refresh_system_cameras()
    for scene_name in config.available_scenes():
        resolved = config.resolve_scene(scene_name)
        if resolved and role in resolved.get("cameras", {}):
            cam = resolved["cameras"][role]
            dev = cam.get("dev", "")
            product = cam.get("product", "unknown")
            if dev:
                return dev, product
    return None, None


def run_capture(role="", filter_str="", output_dir="outputs"):
    """
    打开摄像头窗口，实时预览并可按空格保存帧。

    role:       按 config.yaml 中的角色打开（如 "top", "wrist", "side"）
    filter_str: 按设备名关键词过滤
    output_dir: 保存帧的目录
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ---- 确定要打开的设备 ----
    camera_infos = []  # [(dev_path, product), ...]

    if role:
        dev, product = _resolve_role_dev(role)
        if not dev:
            print(f"角色 '{role}' 未找到对应摄像头。")
            print(f"  可用角色取决于场景配置，常见: top, wrist, side")
            return
        camera_infos.append((dev, product))
    else:
        for c in config.detect_cameras():
            if filter_str and filter_str.lower() not in c["product"].lower():
                continue
            if c["dev"]:
                camera_infos.append((c["dev"], c["product"]))

    if not camera_infos:
        print("没有找到可用的摄像头设备。")
        return

    print(f"找到 {len(camera_infos)} 个摄像头")

    # ---- 打开摄像头 ----
    windows = []
    for i, (dev, product) in enumerate(camera_infos):
        cap = _open_camera(dev)
        if not cap.isOpened():
            print(f"  [FAIL] {dev} ({product})")
            continue
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)

        wname = f"[{i}] {Path(dev).name} | {product} | {w}x{h}"
        cv2.namedWindow(wname, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(wname, min(w, 640), min(h, 480))
        windows.append((wname, cap, dev, product))
        print(f"  [ OK ] {dev} — {product} {w}x{h} @{fps:.0f}fps")

    if not windows:
        print("所有摄像头打开失败。")
        return

    # ---- 主循环 ----
    saved_count = 0
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    print(f"\n保存目录: {output_dir}/")
    print("按键: [空格/s] 保存帧 | [q/Esc] 退出\n")

    try:
        while True:
            key = 0xFF & cv2.waitKey(30)
            latest_frames = {}

            for wname, cap, dev, product in windows:
                ret, frame = cap.read()
                if ret:
                    cv2.putText(frame, dev, (8, 20),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
                    cv2.imshow(wname, frame)
                    latest_frames[wname] = (frame, dev)

            if key == 27 or key == ord("q"):
                break
            elif key == 32 or key == ord("s"):
                for wname, (frame, dev) in latest_frames.items():
                    safe = dev.replace("/", "_").strip("_")
                    fname = f"{safe}_{timestamp}_{saved_count:04d}.jpg"
                    fpath = os.path.join(output_dir, fname)
                    cv2.imwrite(fpath, frame)
                    print(f"  保存: {fpath}")
                saved_count += 1

    finally:
        for wname, cap, _, _ in windows:
            cap.release()
            try:
                cv2.destroyWindow(wname)
            except cv2.error:
                pass
        print(f"\n结束。共保存 {saved_count} 组帧到 {output_dir}/")
