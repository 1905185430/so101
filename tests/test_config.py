"""
tests/test_config.py — 配置和设备探测测试
==========================================

运行方法（需要 lerobot 环境）：
    conda run -n lerobot pytest tests/test_config.py -v

也可以直接运行：
    python tests/test_config.py
"""

import os
import sys

# 确保导入的是项目内的 so101，不是 site-packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from so101 import config


def test_config_loads():
    """配置文件存在且可解析。"""
    cfg = config._load()
    assert "cameras" in cfg, "配置缺少 cameras 区"
    assert "arms" in cfg, "配置缺少 arms 区"
    print("[OK] 配置文件加载成功")


def test_cameras_have_by_id():
    """所有已注册的摄像头都有 by-id 路径。"""
    for name, cam in config.cameras_all().items():
        by_id = cam.get("by_id", "")
        assert by_id, f"摄像头 {name} 缺少 by_id"
        assert by_id.startswith("/dev"), f"摄像头 {name} by_id 格式异常: {by_id}"
    print(f"[OK] {len(config.cameras_all())} 个摄像头配置完整")


def test_arms_have_port():
    """所有已注册的机械臂都有 port 路径。"""
    for name, arm in config.arms_all().items():
        port = arm.get("port", "")
        assert port, f"机械臂 {name} 缺少 port"
        assert port.startswith("/dev"), f"机械臂 {name} port 格式异常: {port}"
    print(f"[OK] {len(config.arms_all())} 个机械臂配置完整")


def test_resolve_scene():
    """resolve_scene 能正确替换摄像头名和臂名。"""
    cfg = config._load()
    if not cfg.get("scenes"):
        print("[SKIP] 无场景定义，跳过")
        return

    scene_name = list(cfg["scenes"].keys())[0]
    scene = config.resolve_scene(scene_name)

    assert "cameras" in scene
    assert "follower" in scene
    assert "leader" in scene

    # 摄像头应该解析为 by-id 路径
    for role, path in scene["cameras"].items():
        assert path.startswith("/dev"), f"场景 {scene_name} 的 {role} 未解析为 by-id 路径"

    print(f"[OK] 场景 '{scene_name}' 解析正确: "
          f"{len(scene['cameras'])} 个摄像头, "
          f"follower={scene['follower'] is not None}, "
          f"leader={scene['leader'] is not None}")


def test_detect_cameras():
    """系统能探测到彩色摄像头。"""
    cameras = config.detect_cameras()
    assert len(cameras) > 0, "未检测到任何彩色摄像头（请确认摄像头已连接）"

    for c in cameras:
        assert c["dev"].startswith("/dev/video"), f"设备路径异常: {c['dev']}"
        assert c["by_id"].startswith("/dev"), f"by-id 路径异常: {c['by_id']}"

    print(f"[OK] 系统探测到 {len(cameras)} 个彩色摄像头:")
    for c in cameras:
        serial_short = c["serial"].split("_")[-1] if c["serial"] else "?"
        print(f"     {c['dev']} | {c['product'][:30]} | {serial_short}")


def test_detect_arms():
    """系统能探测到 SO-101 机械臂。"""
    arms = config.detect_arms()
    # 不强制要求在线（可能只开了部分臂）
    print(f"[INFO] 系统探测到 {len(arms)} 个 SO-101 机械臂:")
    for a in arms:
        print(f"     {a['serial']} -> {a['port']}")

    # 验证 serial 格式（10 位十六进制，CH9101F USB Serial）
    for a in arms:
        serial = a["serial"]
        assert len(serial) == 10 and all(c in "0123456789ABCDEFabcdef" for c in serial), \
            f"Serial 格式异常: {serial}"


def test_register_and_reload():
    """register_camera 写入配置后 reload 生效。"""
    import tempfile, yaml

    # 创建临时配置文件
    import pathlib
    tmp = pathlib.Path(tempfile.mktemp(suffix=".yaml"))
    tmp.write_text("cameras: {}\narms: {}\nscenes: {}\n")

    # 临时替换配置路径（使用 pathlib.Path，保持类型一致）
    old_file = config.CONFIG_FILE
    old_cache = config._config_cache
    config._config_cache = None

    import so101.config as _c
    _c.CONFIG_FILE = tmp  # 必须是 Path 对象

    try:
        name = config.register_camera(
            by_id="/dev/v4l/by-id/test-video-index0",
            serial="TestCamera_12345",
            product="Test Cam",
            role="test"
        )
        assert name.startswith("cam") or name.startswith("orbbec") or name.startswith("icspring")

        # reload 后能读到
        config.reload()
        cams = config.cameras_all()
        assert any(name in cams for name in cams), "注册后 reload 失败"

        print(f"[OK] register_camera -> '{name}' 写入并 reload 成功")
    finally:
        config._config_cache = old_cache
        _c.CONFIG_FILE = old_file
        tmp.unlink(missing_ok=True)


def main():
    print("=" * 50)
    print("  so101 配置测试")
    print("=" * 50)
    print()

    tests = [
        test_config_loads,
        test_cameras_have_by_id,
        test_arms_have_port,
        test_resolve_scene,
        test_detect_cameras,
        test_detect_arms,
        test_register_and_reload,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {t.__name__}: {e}")
            failed += 1

    print()
    print("=" * 50)
    print(f"  结果: {passed} passed, {failed} failed")
    print("=" * 50)
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
