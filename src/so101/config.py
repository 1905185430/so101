"""
so101.config — 统一配置读取与设备探测
========================================

合并了原 camera_config_lib.py 的所有功能：
  - 配置文件读写（camera_config.yaml）
  - 系统摄像头/机械臂探测
  - 场景解析与健康检查
  - 设备注册

使用示例：
    from so101.config import (
        load_config, save_config,
        detect_cameras, detect_arms,
        resolve_scene, check_scene,
        cameras_by_role, arms_all, arm,
        register_camera, register_arm,
        available_scenes,
    )
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import yaml


# ============================================================================
# 路径
# ============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = _PROJECT_ROOT / "config" / "camera_config.yaml"

# 彩色流 / 深度流格式
COLOR_FORMATS = {"YUYV", "MJPG", "YV12", "I420", "RGB3", "BGR3", "JPEG"}
DEPTH_FORMATS = {"GREY", "BA81", "Z16", "Y16", "Y8", "NW16", "BYR2"}

# 系统摄像头缓存
SYSTEM_CAMERAS: dict[str, dict] = {}

# LeRobot 数据集缓存根目录
LEROBOT_CACHE_ROOT = Path.home() / ".cache" / "huggingface" / "lerobot"


# ============================================================================
# 配置加载 / 保存
# ============================================================================

_config_cache: Optional[dict] = None


def load_config() -> dict:
    """加载 camera_config.yaml。"""
    global _config_cache
    if _config_cache is None:
        if not CONFIG_FILE.exists():
            return {"cameras": {}, "arms": {}, "scenes": {}}
        with open(CONFIG_FILE) as f:
            _config_cache = yaml.safe_load(f) or {"cameras": {}, "arms": {}, "scenes": {}}
    return _config_cache


def save_config(cfg: dict):
    """保存配置到 camera_config.yaml 并刷新缓存。"""
    global _config_cache
    _config_cache = cfg
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def reload():
    """强制重新加载配置（scan 后使用）。"""
    global _config_cache
    _config_cache = None
    return load_config()


# ============================================================================
# 底层工具
# ============================================================================

def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def serial_key(serial: str) -> str:
    """归一化序列号：取下划线分隔的最后一段。"""
    if not serial:
        return ""
    return serial.strip().split("_")[-1]


def serial_short(serial: str) -> str:
    """简短 serial 用于显示。"""
    return serial.split("_")[-1].strip() if serial else "?"


# ============================================================================
# 系统设备探测（只读，不修改配置）
# ============================================================================

def _get_v4l2_formats(dev_path: str) -> list[str]:
    output = _run(["v4l2-ctl", "-d", dev_path, "--list-formats-ext"])
    formats = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("[") and "'" in line:
            fmt = line.split("'")[1]
            if fmt:
                formats.append(fmt)
    return formats


def _get_udev_info(dev_path: str) -> dict:
    info = {"serial": "", "product": "", "by_id": "", "by_path": "", "id_path": ""}
    output = _run(["udevadm", "info", "-q", "property", "-n", dev_path])
    for line in output.splitlines():
        if line.startswith("ID_SERIAL="):
            info["serial"] = line.split("=", 1)[1]
        elif line.startswith("ID_V4L_PRODUCT="):
            info["product"] = line.split("=", 1)[1]
        elif line.startswith("ID_PATH="):
            info["id_path"] = line.split("=", 1)[1]
        elif line.startswith("DEVLINKS="):
            for link in line.split("=", 1)[1].split():
                if "/v4l/by-id/" in link:
                    info["by_id"] = link
                elif "/v4l/by-path/" in link:
                    info["by_path"] = link
    return info


def detect_cameras() -> list[dict]:
    """
    自动探测系统中所有可用彩色摄像头。

    Returns:
        [{"dev": "/dev/video10", "formats": ["YUYV"],
          "serial": "Orbbec_R__...", "product": "Orbbec Gemini 335",
          "by_id": "/dev/v4l/by-id/...", "by_path": "...", "id_path": "..."}, ...]
    """
    cameras = []
    for p in sorted(
        [p for p in Path("/dev").glob("video*") if p.name[5:].isdigit()],
        key=lambda p: int(p.name[5:]),
    ):
        formats = _get_v4l2_formats(str(p))
        if not formats:
            continue
        has_color = any(f in COLOR_FORMATS for f in formats)
        has_depth = any(f in DEPTH_FORMATS for f in formats)
        if not has_color or has_depth:
            continue

        udev = _get_udev_info(str(p))
        cameras.append({
            "dev": str(p),
            "formats": formats,
            "serial": udev["serial"],
            "product": udev["product"],
            "by_id": udev["by_id"],
            "by_path": udev["by_path"],
            "id_path": udev["id_path"],
        })
    return cameras


def detect_arms() -> list[dict]:
    """
    自动探测系统中所有 SO-101 串口设备（CH9101F USB Serial）。

    Returns:
        [{"port": "/dev/serial/by-id/...-if00", "serial": "5B42073876", "name": "SO-101"}, ...]
    """
    arms = []
    serial_dir = Path("/dev/serial/by-id")
    if not serial_dir.exists():
        return arms

    for p in sorted(serial_dir.iterdir()):
        port_str = str(p)
        if "1a86_USB_Single_Serial" not in port_str:
            continue
        name_part = p.name.rsplit("-if", 1)[0]
        serial = name_part.rsplit("_", 1)[-1]
        arms.append({
            "port": port_str,
            "serial": serial,
            "name": "SO-101",
        })
    return arms


def refresh_system_cameras():
    """刷新系统摄像头缓存。resolve_scene / check_scene 前调用。"""
    global SYSTEM_CAMERAS
    SYSTEM_CAMERAS = {}
    for dev in detect_cameras():
        key = serial_key(dev["serial"])
        if key and key not in SYSTEM_CAMERAS:
            SYSTEM_CAMERAS[key] = dev


# ============================================================================
# 配置查询 API
# ============================================================================

def cameras_all() -> dict[str, dict]:
    """返回所有注册的摄像头。"""
    return load_config().get("cameras", {})


def cameras_by_role() -> dict[str, str]:
    """返回 角色 -> by-id 路径 映射。"""
    result = {}
    for cam in load_config().get("cameras", {}).values():
        role = cam.get("role", "")
        if role:
            result[role] = cam.get("by_id", "")
    return result


def arms_all() -> dict[str, dict]:
    """返回所有注册的机械臂。"""
    return load_config().get("arms", {})


def arm(name: str) -> Optional[dict]:
    """按名字获取机械臂配置。"""
    return arms_all().get(name)


def available_scenes() -> list[str]:
    """返回所有已定义的场景名。"""
    return list(load_config().get("scenes", {}).keys())


# ============================================================================
# 场景解析
# ============================================================================

def resolve_scene(scene_name: str) -> Optional[dict]:
    """
    解析场景配置，返回结构化字典（含 resolved 设备路径）。

    Returns:
        {
            "scene": "grab_redcube",
            "task": "grab red cube",
            "cameras": {
                "top": {"role": "top", "name": "orbbec_1", "dev": "/dev/video10",
                        "by_id": "...", "product": "...", "serial_key": "CP...",
                        "width": 640, "height": 480, "fps": 30,
                        "description": "...", "reference_image": "..."},
                ...
            },
            "follower": {"id": "so101_cong_left", "port": "/dev/serial/by-id/..."},
            "leader":   {"id": "so101_zhu_left",  "port": "/dev/serial/by-id/..."},
            "missing": [],
        }
    """
    if not SYSTEM_CAMERAS:
        refresh_system_cameras()

    cfg = load_config()
    scenes = cfg.get("scenes", {})
    if scene_name not in scenes:
        return None

    scene = scenes[scene_name]
    cameras_cfg = cfg.get("cameras", {})

    resolved = {
        "scene": scene_name,
        "task": scene.get("task", ""),
        "cameras": {},
        "follower": None,
        "leader": None,
        "missing": [],
    }

    # 解析机械臂引用：follower/leader 可以是 arms 区的键名(str)或内联 dict
    arms_cfg = cfg.get("arms", {})
    for arm_type in ["follower", "leader"]:
        arm_ref = scene.get(arm_type)
        if arm_ref is None:
            continue
        if isinstance(arm_ref, str):
            # 引用 arms 区的键名
            if arm_ref in arms_cfg:
                a = arms_cfg[arm_ref]
                resolved[arm_type] = {"id": a.get("name", arm_ref), "port": a.get("port", "")}
            else:
                resolved[arm_type] = {"id": arm_ref, "port": ""}
        elif isinstance(arm_ref, dict):
            # 内联格式（兼容旧配置）
            resolved[arm_type] = {"id": arm_ref.get("id", ""), "port": arm_ref.get("port", "")}

    for role, cam_ref in scene.get("cameras", {}).items():
        if cam_ref in cameras_cfg:
            cam = cameras_cfg[cam_ref]
            key = serial_key(cam.get("serial", ""))
            by_id = cam.get("by_id", "")
        else:
            key = serial_key(cam_ref) if "/" not in cam_ref else ""
            by_id = cam_ref

        if key and key in SYSTEM_CAMERAS:
            dev_info = SYSTEM_CAMERAS[key]
            resolved["cameras"][role] = {
                "role": role,
                "name": cam_ref if cam_ref in cameras_cfg else key,
                "dev": dev_info["dev"],          # 检测到的路径，已验证格式
                "by_id": dev_info["by_id"],      # 检测到的 by-id（可能与 yaml 不同）
                "by_path": dev_info["by_path"],  # 检测到的 by-path（最稳定）
                "product": dev_info["product"],
                "serial_key": key,
                "width": cam.get("width", 640) if cam_ref in cameras_cfg else 640,
                "height": cam.get("height", 480) if cam_ref in cameras_cfg else 480,
                "fps": cam.get("fps", 30) if cam_ref in cameras_cfg else 30,
                "fourcc": cam.get("fourcc") if cam_ref in cameras_cfg else None,
                "description": cameras_cfg.get(cam_ref, {}).get("description", "")
                    if cam_ref in cameras_cfg else "",
                "reference_image": cameras_cfg.get(cam_ref, {}).get("reference_image", "")
                    if cam_ref in cameras_cfg else "",
            }
        else:
            # 未在 SYSTEM_CAMERAS 中找到（被 depth 格式过滤掉了）。
            # 优先用 yaml 中的 by_path（基于 USB 物理拓扑，最稳定），
            # 其次用 by_id，最后用 yaml 中的 dev。
            yaml_by_path = cam.get("by_path", "") if cam_ref in cameras_cfg else ""
            yaml_by_id = by_id
            yaml_dev = cam.get("dev", "") if cam_ref in cameras_cfg else ""
            fallback = yaml_by_path or yaml_by_id or yaml_dev
            resolved["missing"].append(role)
            resolved["cameras"][role] = {
                "role": role,
                "name": cam_ref,
                "dev": fallback,
                "by_id": yaml_by_id,
                "by_path": yaml_by_path,
                "product": cameras_cfg.get(cam_ref, {}).get("product", "") if cam_ref in cameras_cfg else "",
                "serial_key": key,
                "width": 640,
                "height": 480,
                "fps": 30,
                "fourcc": cameras_cfg.get(cam_ref, {}).get("fourcc") if cam_ref in cameras_cfg else None,
                "description": cameras_cfg.get(cam_ref, {}).get("description", "") if cam_ref in cameras_cfg else "",
                "reference_image": cameras_cfg.get(cam_ref, {}).get("reference_image", "") if cam_ref in cameras_cfg else "",
            }

    return resolved


def check_scene(scene_name: str) -> tuple[bool, list[str]]:
    """
    检查场景所有设备是否在线。

    Returns:
        (all_ok, missing_list)
    """
    resolved = resolve_scene(scene_name)
    if resolved is None:
        return False, [f"场景 '{scene_name}' 不存在"]

    missing = resolved.get("missing", [])
    missing_serial = []

    for arm_type in ["follower", "leader"]:
        arm_cfg = resolved.get(arm_type, {})
        if arm_cfg:
            port = arm_cfg.get("port", "")
            if port and not Path(port).exists():
                missing_serial.append(f"{arm_type} 串口: {port}")

    all_ok = len(missing) == 0 and len(missing_serial) == 0
    return all_ok, missing + missing_serial


# ============================================================================
# 设备注册（供 scan 命令使用）
# ============================================================================

def register_camera(by_id: str, serial: str, product: str, role: str = "") -> str:
    """
    将摄像头注册到 config。自动分配注册名。
    若同名 serial 已存在则跳过。返回注册名。
    """
    cfg = load_config()
    cfg.setdefault("cameras", {})

    key = serial_key(serial)

    for name, cam in cfg["cameras"].items():
        if serial_key(cam.get("serial", "")) == key:
            return name

    prefix = "orbbec" if "Orbbec" in product else "icspring" if "icspring" in product else "cam"
    existing = [k for k in cfg["cameras"] if k.startswith(prefix)]
    idx = 1
    while f"{prefix}_{idx}" in existing:
        idx += 1
    name = f"{prefix}_{idx}"

    cam_type = "orbbec" if "Orbbec" in product else "icspring" if "icspring" in product else "usb_cam"
    cfg["cameras"][name] = {
        "serial": serial,
        "by_id": by_id,
        "product": product,
        "type": cam_type,
    }
    if role:
        cfg["cameras"][name]["role"] = role

    save_config(cfg)
    return name


def register_arm(port: str, serial: str, arm_role: str) -> str:
    """
    将机械臂注册到 config。arm_role 如 "follower_left"。返回 arm_role。
    """
    cfg = load_config()
    cfg.setdefault("arms", {})

    if arm_role in cfg["arms"]:
        # 更新端口（热插拔可能变）
        cfg["arms"][arm_role]["port"] = port
        cfg["arms"][arm_role]["serial"] = serial
        save_config(cfg)
        return arm_role

    name_map = {
        "follower_left": "so101_cong_left",
        "follower_right": "so101_cong_right",
        "leader_left": "so101_zhu_left",
        "leader_right": "so101_zhu_right",
    }

    cfg["arms"][arm_role] = {
        "serial": serial,
        "port": port,
        "role": arm_role.split("_")[0],
        "name": name_map.get(arm_role, arm_role),
    }

    save_config(cfg)
    return arm_role


# ============================================================================
# 配置展示
# ============================================================================

def print_system_cameras():
    """打印系统当前探测到的摄像头（只读）。"""
    cameras = detect_cameras()
    if not cameras:
        print("未检测到彩色摄像头！")
        print("提示：检查摄像头是否连接，或运行: so101 scan")
        return

    print(f"系统当前有 {len(cameras)} 个彩色摄像头：\n")
    print(f"  {'#':<3} {'设备':<16} {'产品':<30} {'Serial':<16} by-id")
    print(f"  {'---':<3} {'---':<16} {'---':<30} {'---':<16} {'---'}")
    for i, c in enumerate(cameras):
        short = serial_short(c["serial"])
        print(f"  {i+1:<3} {c['dev']:<16} {c['product']:<30} {short:<16} {c['by_id']}")
    print()


def print_system_arms():
    """打印系统当前探测到的机械臂（只读）。"""
    arms = detect_arms()
    if not arms:
        print("未检测到 SO-101 机械臂！")
        print("提示：检查机械臂 USB 是否连接，或运行: so101 scan")
        return

    print(f"系统当前有 {len(arms)} 个 SO-101 机械臂：\n")
    for a in arms:
        print(f"  Serial: {a['serial']}  ->  {a['port']}")
    print()
