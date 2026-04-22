"""
so101.capture — 摄像头画面采集
==============================

Usage:
    from so101.capture import run_capture
    run_capture(role="top", filter_str="Orbbec", output_dir="outputs")
"""

import os
import cv2
from pathlib import Path
from so101 import config


def run_capture(role="", filter_str="", output_dir="outputs"):
    """
    打开摄像头窗口，实时预览并可按空格保存帧。

    role:       按 config.yaml 中的角色打开（如 "top", "wrist"）
    filter_str: 按设备名关键词过滤
    output_dir: 保存帧的目录
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if role:
        by_id_map = config.cameras_by_role()
        if role not in by_id_map:
            print(f"角色 '{role}' 不在配置中，可用: {list(by_id_map.keys())}")
            return
        devices = [by_id_map[role]]
        print(f"打开摄像头（角色: {role}）: {devices[0]}")
    else:
        sys_cams = config.detect_cameras()
        devices = []
        for c in sys_cams:
            if filter_str and filter_str.lower() not in c["product"].lower():
                continue
            if c["dev"]:
                devices.append(c["dev"])
        print(f"找到 {len(devices)} 个摄像头")

    if not devices:
        print("没有找到可用的摄像头设备。")
        return

    windows = []
    for i, dev in enumerate(devices):
        cap = cv2.VideoCapture(dev)
        if not cap.isOpened():
            print(f"无法打开: {dev}")
            continue
        wname = f"Camera {i}: {Path(dev).name}"
        cv2.namedWindow(wname)
        windows.append((wname, cap))
        print(f"  [{i}] {dev} -> {wname}")

    if not windows:
        print("所有摄像头打开失败。")
        return

    saved_count = 0
    print("\n按 空格 保存当前帧，Esc 或 q 退出\n")

    while True:
        key = 0xFF & cv2.waitKey(30)
        has_any = False

        for wname, cap in windows:
            ret, frame = cap.read()
            if ret:
                has_any = True
                cv2.imshow(wname, frame)

        if key == 27 or key == ord("q"):
            break
        elif key == 32:  # 空格
            for wname, cap in windows:
                ret, frame = cap.read()
                if ret:
                    fname = f"{wname.replace(':', '_').replace(' ', '_')}_{saved_count:04d}.jpg"
                    cv2.imwrite(os.path.join(output_dir, fname), frame)
                    print(f"  保存: {fname}")
            saved_count += 1

    for wname, cap in windows:
        cap.release()
        cv2.destroyWindow(wname)
    print("Done.")
