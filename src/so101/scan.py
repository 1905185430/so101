"""
so101.scan — 设备扫描与注册
==============================

so101 scan           扫描所有设备（摄像头+机械臂）
so101 scan --cameras 仅扫描摄像头
so101 scan --arms    仅扫描机械臂

扫描结果写入 config/camera_config.yaml。
"""

import sys
from so101 import config


def _scan_cameras():
    """扫描并注册摄像头。"""
    print("=" * 50)
    print("  摄像头扫描")
    print("=" * 50)

    cameras = config.detect_cameras()
    if not cameras:
        print("  未检测到彩色摄像头！")
        print("  提示：检查摄像头 USB 连接")
        return []

    print(f"  检测到 {len(cameras)} 个彩色摄像头：\n")
    registered = []
    for c in cameras:
        name = config.register_camera(
            by_id=c["by_id"],
            serial=c["serial"],
            product=c["product"],
        )
        registered.append(name)
        short = config.serial_short(c["serial"])
        print(f"  [{name:<12}] {c['dev']:<14} {c['product']:<28} {short}")

    print(f"\n  已注册 {len(registered)} 个摄像头到配置")
    return registered


def _scan_arms():
    """扫描并注册机械臂。"""
    print("=" * 50)
    print("  机械臂扫描")
    print("=" * 50)

    arms = config.detect_arms()
    if not arms:
        print("  未检测到 SO-101 机械臂！")
        print("  提示：检查机械臂 USB 连接")
        return []

    print(f"  检测到 {len(arms)} 个 SO-101 机械臂：\n")

    # 交互式分配角色
    roles = ["follower_left", "leader_left", "follower_right", "leader_right"]
    role_descriptions = {
        "follower_left": "左从臂",
        "leader_left": "左主臂",
        "follower_right": "右从臂",
        "leader_right": "右主臂",
    }
    registered = []
    used_serials = set()

    # 先查看已有配置
    existing_arms = config.arms_all()

    # 自动匹配：如果已有配置的 serial 和探测到的一致，直接更新 port
    for a in arms:
        for role_name, arm_cfg in existing_arms.items():
            if arm_cfg.get("serial") == a["serial"]:
                config.register_arm(a["port"], a["serial"], role_name)
                registered.append(role_name)
                print(f"  [已匹配] {role_descriptions.get(role_name, role_name)} "
                      f"({role_name})  Serial={a['serial']}  -> {a['port']}")
                used_serials.add(a["serial"])
                break

    # 未匹配的设备，交互分配
    unmatched = [a for a in arms if a["serial"] not in used_serials]
    if unmatched:
        available_roles = [r for r in roles if r not in existing_arms]
        if not available_roles:
            print("\n  所有角色已分配，新设备需手动编辑配置")
        else:
            print("\n  以下设备未分配角色：")
            for a in unmatched:
                print(f"    Serial={a['serial']}  Port={a['port']}")
            print(f"\n  可用角色：{', '.join(available_roles)}")
            print("  提示：手动编辑 config/camera_config.yaml 分配，或重新运行 so101 scan")

    if registered:
        print(f"\n  已注册 {len(registered)} 个机械臂到配置")

    return registered


def run_scan(cameras=True, arms=True):
    """执行扫描。"""
    config.reload()  # 先刷新缓存

    cam_results = []
    arm_results = []

    if cameras:
        cam_results = _scan_cameras()
        print()

    if arms:
        arm_results = _scan_arms()
        print()

    # 汇总
    if not cam_results and not arm_results:
        print("未检测到任何设备。请检查 USB 连接后重试。")
        return

    print("=" * 50)
    print("  扫描完成")
    print("=" * 50)
    print(f"  摄像头: {len(cam_results)} 个")
    print(f"  机械臂: {len(arm_results)} 个")
    print(f"  配置文件: {config.CONFIG_FILE}")
    print()
    print("  下一步: so101 check  (检查设备可用性)")
