"""
so101.teleop — 遥操作
=====================

直接调用 LeRobot API，不依赖原始项目文件。
支持三种模式：
  - 1to1:  单主臂控制单从臂
  - 1toN:  单主臂同步控制双从臂
  - dual:  双主臂独立控制双从臂

Usage:
    so101 teleop --mode 1to1 --arm left
"""

import os
import sys
import time
import traceback

from so101 import config


def _import_leader():
    from lerobot.teleoperators.so_leader import SO101LeaderConfig, SO101Leader
    return SO101LeaderConfig, SO101Leader


def _import_follower():
    from lerobot.robots.so_follower import SO101FollowerConfig, SO101Follower
    return SO101FollowerConfig, SO101Follower


def _connect_with_retry(robot, name, max_retries=3):
    """带重试的连接，友好提示权限问题。"""
    for attempt in range(max_retries):
        try:
            robot.connect()
            return True
        except PermissionError as e:
            if attempt == max_retries - 1:
                print(f"[错误] 无权限访问 {name}，请运行:")
                print(f"  sudo chmod 666 {getattr(robot, 'port', '?')}")
                raise
            print(f"  权限不足，重试中 ({attempt + 1}/{max_retries})...")
            time.sleep(1)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            print(f"  连接失败，重试中 ({attempt + 1}/{max_retries}): {e}")
            time.sleep(1)


def _teleop_loop(leader, follower, label=""):
    """
    标准的单主-单从遥操作循环。
    leader:  SO101Leader 实例
    follower: SO101Follower 实例
    label:    打印标签，如 "左臂"
    """
    last_action = None
    consecutive_errors = 0
    MAX_ERRORS = 20

    print(f"  [{label}] 遥操作循环已启动，Ctrl+C 退出")
    try:
        while True:
            action = None
            # 读取主臂姿态，带重试
            for _ in range(3):
                try:
                    action = leader.get_action()
                    break
                except Exception:
                    continue

            if action is None:
                action = last_action  # 用缓存
            if action is None:
                continue

            # 发送到从臂，带重试
            for _ in range(3):
                try:
                    follower.send_action(action)
                    last_action = action
                    consecutive_errors = 0
                    break
                except Exception:
                    consecutive_errors += 1
                    continue

            if consecutive_errors >= MAX_ERRORS:
                print(f"\n  [{label}] 连续 {MAX_ERRORS} 次通信失败，请检查USB连接！")
                break

    except KeyboardInterrupt:
        pass


def run_teleop(mode="1to1", arm_side="left"):
    """
    启动遥操作。

    mode:    "1to1" | "1toN" | "dual"
    arm_side: "left" | "right"  (仅 1to1 模式使用)
    """
    SO101LeaderCfg, SO101Leader = _import_leader()
    SO101FollowerCfg, SO101Follower = _import_follower()

    arms = config.arms_all()
    if not arms:
        print("[错误] 配置中没有机械臂，请先运行: so101 scan --arms")
        sys.exit(1)

    # 构建 LeRobot Config 对象
    def make_follower(name):
        cfg = config.arm(name)
        if not cfg:
            return None
        return SO101FollowerCfg(port=cfg["port"], id=name)

    def make_leader(name):
        cfg = config.arm(name)
        if not cfg:
            return None
        return SO101LeaderCfg(port=cfg["port"], id=name)

    fl_cfg = make_follower("follower_left")
    fr_cfg = make_follower("follower_right")
    ll_cfg = make_leader("leader_left")
    lr_cfg = make_leader("leader_right")

    print("========================================")
    print(f"  SO-101 遥操作  (模式: {mode})")
    print("========================================")

    def print_arm(label, cfg_obj):
        if cfg_obj:
            print(f"  {label}: {cfg_obj.port}")

    if mode == "1to1":
        if arm_side == "left":
            l_cfg, f_cfg = ll_cfg, fl_cfg
            label = "左臂"
        else:
            l_cfg, f_cfg = lr_cfg, fr_cfg
            label = "右臂"

        if not l_cfg or not f_cfg:
            print(f"[错误] {label} 的臂未配置，请运行 so101 scan")
            sys.exit(1)

        print_arm(f"主臂({label})", l_cfg)
        print_arm(f"从臂({label})", f_cfg)
        print()

        leader = SO101Leader(l_cfg)
        follower = SO101Follower(f_cfg)

        print(f"连接 {label} 设备...")
        _connect_with_retry(leader, f"主臂{label}")
        _connect_with_retry(follower, f"从臂{label}")
        print(f"{label} 连接成功！\n")

        try:
            _teleop_loop(leader, follower, label)
        finally:
            leader.disconnect()
            follower.disconnect()
            print(f"{label} 已断开。")

    elif mode == "1toN":
        # 右主臂 -> 双从臂
        if not lr_cfg:
            print("[错误] leader_right 未配置")
            sys.exit(1)
        if not fl_cfg or not fr_cfg:
            print("[错误] 双从臂未完整配置")
            sys.exit(1)

        print_arm("主臂(右)", lr_cfg)
        print_arm("从臂(左)", fl_cfg)
        print_arm("从臂(右)", fr_cfg)
        print()

        leader = SO101Leader(lr_cfg)
        f_left = SO101Follower(fl_cfg)
        f_right = SO101Follower(fr_cfg)

        print("连接设备...")
        _connect_with_retry(leader, "主臂(右)")
        _connect_with_retry(f_left, "从臂(左)")
        _connect_with_retry(f_right, "从臂(右)")
        print("全部连接成功！\n")

        last_action = None
        consecutive_errors = 0

        try:
            while True:
                action = None
                for _ in range(3):
                    try:
                        action = leader.get_action()
                        break
                    except Exception:
                        continue
                if action is None:
                    action = last_action
                if action is None:
                    continue
                last_action = action

                err_count = 0
                for f in [f_left, f_right]:
                    for _ in range(3):
                        try:
                            f.send_action(action)
                            break
                        except Exception:
                            err_count += 1
                            continue
                if err_count >= 4:
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0
                if consecutive_errors >= 20:
                    print("\n通信失败过多，请检查USB！")
                    break
        except KeyboardInterrupt:
            pass
        finally:
            leader.disconnect()
            f_left.disconnect()
            f_right.disconnect()
            print("已断开。")

    elif mode == "dual":
        if not ll_cfg or not lr_cfg or not fl_cfg or not fr_cfg:
            print("[错误] 双臂遥操作需要全部 4 个臂配置完整")
            sys.exit(1)

        print_arm("主臂(左)", ll_cfg)
        print_arm("主臂(右)", lr_cfg)
        print_arm("从臂(左)", fl_cfg)
        print_arm("从臂(右)", fr_cfg)
        print()

        leader_l = SO101Leader(ll_cfg)
        leader_r = SO101Leader(lr_cfg)
        follower_l = SO101Follower(fl_cfg)
        follower_r = SO101Follower(fr_cfg)

        print("连接设备...")
        for lbl, r in [("主左", leader_l), ("主右", leader_r),
                       ("从左", follower_l), ("从右", follower_r)]:
            _connect_with_retry(r, lbl)
        print("全部连接成功！\n")

        last_l, last_r = None, None
        try:
            while True:
                # 左臂
                for _ in range(3):
                    try:
                        al = leader_l.get_action()
                        break
                    except Exception:
                        al = None
                if al is None:
                    al = last_l
                if al is not None:
                    for _ in range(3):
                        try:
                            follower_l.send_action(al)
                            last_l = al
                            break
                        except Exception:
                            continue

                # 右臂
                for _ in range(3):
                    try:
                        ar = leader_r.get_action()
                        break
                    except Exception:
                        ar = None
                if ar is None:
                    ar = last_r
                if ar is not None:
                    for _ in range(3):
                        try:
                            follower_r.send_action(ar)
                            last_r = ar
                            break
                        except Exception:
                            continue

        except KeyboardInterrupt:
            pass
        finally:
            for r in [leader_l, leader_r, follower_l, follower_r]:
                r.disconnect()
            print("已断开。")
