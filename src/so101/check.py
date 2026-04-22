"""
so101.check — 采前健康检查
============================

so101 check              检查所有场景
so101 check -s grab_redcube  检查指定场景

检查项：
  1. 配置文件是否存在
  2. 每个场景的摄像头是否在线
  3. 每个场景的机械臂串口是否可访问
  4. 磁盘空间是否充足
  5. 串口权限是否正确
"""

import os
import shutil
import sys
from pathlib import Path

from so101 import config


def _check_config() -> tuple[bool, str]:
    """检查配置文件。"""
    if config.CONFIG_FILE.exists():
        return True, "OK"
    return False, "配置文件不存在，请先运行 so101 scan"


def _check_camera(role: str, cam_info: dict) -> tuple[bool, str]:
    """检查单个摄像头。"""
    dev = cam_info.get("dev", "")
    by_id = cam_info.get("by_id", "")
    product = cam_info.get("product", "")

    # 检查 dev 节点是否存在
    if dev and Path(dev).exists():
        return True, f"{product}  {dev}"
    # 检查 by-id 链接是否存在
    if by_id and Path(by_id).exists():
        return True, f"{product}  {by_id}"
    return False, f"{product}  设备不在线"


def _check_arm(arm_type: str, arm_cfg: dict) -> tuple[bool, str]:
    """检查单个机械臂。"""
    if not arm_cfg:
        return True, "未配置（跳过）"

    port = arm_cfg.get("port", "")
    arm_id = arm_cfg.get("id", "?")

    if not port:
        return False, f"id={arm_id}  端口未配置"

    if not Path(port).exists():
        return False, f"id={arm_id}  串口不存在: {port}"

    # 检查读写权限
    if not os.access(port, os.R_OK | os.W_OK):
        return False, f"id={arm_id}  无读写权限: {port}  (sudo chmod 666 {port})"

    return True, f"id={arm_id}  {port}"


def _check_disk(required_gb: float = 10.0) -> tuple[bool, str]:
    """检查磁盘空间。"""
    usage = shutil.disk_usage("/")
    free_gb = usage.free / (1024 ** 3)
    ok = free_gb >= required_gb
    # 估算可录制的 episode 数（每 episode ~200MB 视频）
    est_episodes = int(free_gb / 0.2)
    msg = f"剩余 {free_gb:.0f}GB (~{est_episodes}ep)"
    if not ok:
        msg += f"  [需要 >= {required_gb:.0f}GB]"
    return ok, msg


def _check_serial_permissions() -> tuple[bool, str]:
    """检查串口设备权限。"""
    acm_devices = list(Path("/dev").glob("ttyACM*"))
    usb_devices = list(Path("/dev").glob("ttyUSB*"))
    all_serial = acm_devices + usb_devices

    if not all_serial:
        return True, "无串口设备（未连接机械臂？）"

    no_access = []
    for dev in all_serial:
        if not os.access(str(dev), os.R_OK | os.W_OK):
            no_access.append(str(dev))

    if no_access:
        return False, f"无权限: {', '.join(no_access)}  (sudo chmod 666 /dev/ttyACM*)"
    return True, "所有串口可访问"


def check_scene(scene_name: str, verbose: bool = True) -> bool:
    """
    检查指定场景的所有设备。返回 True 表示全部就绪。

    打印检查报告到终端。
    """
    config.refresh_system_cameras()
    all_ok, missing = config.check_scene(scene_name)
    resolved = config.resolve_scene(scene_name)

    if resolved is None:
        if verbose:
            print(f"[错误] 场景 '{scene_name}' 不存在！")
        return False

    if verbose:
        print(f"\n{'='*50}")
        print(f"  场景: {scene_name}")
        print(f"  任务: {resolved.get('task', '?')}")
        print(f"{'='*50}")

    checks = []

    # 1. 摄像头
    for role, cam in resolved["cameras"].items():
        ok, detail = _check_camera(role, cam)
        tag = "OK" if ok else "MISS"
        checks.append(ok)
        if verbose:
            print(f"  [{tag:^4}]  {role:<8}  {detail}")

    # 2. 机械臂
    for arm_type in ["follower", "leader"]:
        arm_cfg = resolved.get(arm_type, {})
        ok, detail = _check_arm(arm_type, arm_cfg)
        tag = "OK" if ok else "MISS"
        checks.append(ok)
        if verbose:
            print(f"  [{tag:^4}]  {arm_type:<8}  {detail}")

    if verbose:
        print()

    return all(checks)


def run_check(scene: str = "") -> bool:
    """
    执行健康检查。返回 True 表示全部通过。

    scene: 指定场景名，为空则检查所有场景。
    """
    # 全局检查
    print("=" * 50)
    print("  SO-101 采前健康检查")
    print("=" * 50)

    global_ok = True

    # 1. 配置文件
    ok, detail = _check_config()
    tag = "OK" if ok else "FAIL"
    print(f"  [{tag:^4}]  配置文件    {detail}")
    if not ok:
        global_ok = False

    # 2. 磁盘空间
    ok, detail = _check_disk()
    tag = "OK" if ok else "WARN"
    print(f"  [{tag:^4}]  磁盘空间    {detail}")

    # 3. 串口权限
    ok, detail = _check_serial_permissions()
    tag = "OK" if ok else "WARN"
    print(f"  [{tag:^4}]  串口权限    {detail}")
    if not ok:
        global_ok = False

    print()

    # 4. 场景检查
    scenes = [scene] if scene else config.available_scenes()
    if not scenes:
        print("  未配置任何场景。请编辑 config/camera_config.yaml")
        return False

    scene_results = {}
    for s in scenes:
        ok = check_scene(s)
        scene_results[s] = ok
        if not ok:
            global_ok = False

    # 汇总
    print("=" * 50)
    if global_ok:
        print("  全部通过！可以开始录制:")
        if scene:
            print(f"    so101 record -s {scene}")
        else:
            print(f"    so101 record -s <scene_name>")
    else:
        print("  存在问题，请修复后重试。")
    print("=" * 50)

    return global_ok
