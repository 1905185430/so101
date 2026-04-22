# so101 — SO-101 机器人臂工具链

SO-101 双臂机器人的统一控制、录制、部署工具。基于 LeRobot 框架，支持场景化数据采集、VLA/ACT 模型推理、遥操作。

- **硬件**: SO-101 follower/leader 机械臂 + Orbbec 深度相机 + icSpring 腕部相机
- **软件**: Python 3.12, LeRobot >= 0.5.1, OpenCV, PyYAML
- **安装方式**: `pip install -e ~/so101/`（editable 模式，改源码即时生效）
- **CLI 入口**: `so101`（由 `so101.cli:main` 提供）
- **配置文件**: `config/camera_config.yaml`（单一数据源，所有模块共享）

## 目录结构

```
so101/
├── src/so101/                  # Python 包（pip install -e 生效）
│   ├── __init__.py             # 空
│   ├── cli.py                  # CLI 入口，argparse 子命令分发
│   ├── config.py               # 统一配置：读取 camera_config.yaml，解析场景/设备
│   ├── scan.py                 # 扫描摄像头+串口，写入配置
│   ├── check.py                # 采前健康检查（摄像头帧率/串口通信/场景完整性）
│   ├── calibrate.py            # 机械臂校准
│   ├── teleop.py               # 遥操作（1to1/1toN/dual 模式）
│   ├── record.py               # 场景驱动数据录制（LeRobot Dataset）
│   ├── capture.py              # 摄像头画面采集/预览
│   ├── deploy.py               # VLA/ACT 模型推理部署
│   ├── eval.py                 # 模型评估
│   ├── dataset.py              # 数据集管理（ls/info/push/merge/view）
│   ├── dataset_manager.py      # 数据集底层操作（检查/修复/清理）
│   ├── dataset_viewer.py       # Flask 网页可视化（端口 5555）
│   └── sound_helpers.py        # 声音提示（纯 Python 正弦波 beep）
├── config/
│   ├── camera_config.yaml      # 【核心配置】摄像头 + 机械臂 + 场景定义
│   └── calibration/            # 校准数据目录
├── scripts/
│   └── setup.sh                # 一键环境安装脚本
├── tests/
│   └── test_config.py          # 配置模块测试
├── doc/
│   ├── cheatsheet.md           # 命令速查表
│   └── 项目设计.md              # 架构设计文档
├── pyproject.toml              # 包定义（version, deps, entry point）
└── README.md                   # 本文件
```

## 配置系统

所有设备定义集中在 `config/camera_config.yaml`，三个区域：

| 区域 | 用途 | 示例 |
|------|------|------|
| `cameras:` | 摄像头硬件注册表（serial/by_id/格式/角色） | orbbec_1, icspring |
| `arms:` | 机械臂串口注册表（serial/port/name） | follower_right, leader_right |
| `scenes:` | 场景组合（引用 cameras + arms + 任务描述） | grab_redcube |

场景中 `follower`/`leader` 是字符串引用 `arms:` 的键名，`cameras` 中的值引用 `cameras:` 的键名。

Python 侧通过 `so101.config` 模块读取（`resolve_scene()`, `load_config()` 等），不要在代码中硬编码设备路径。

## CLI 命令速览

```bash
so101 scan                        # 探测摄像头+串口，写入配置
so101 check -s grab_redcube       # 检查指定场景是否就绪
so101 calibrate --arm follower_left
so101 teleop                      # 遥操作（默认 1to1 左臂）
so101 record -s grab_redcube      # 场景驱动数据录制
so101 record -s grab_redcube --resume --name my_data   # 追加到已有数据集
so101 deploy -p Ready321/act_model -d Ready321/dataset -s grab_redcube
so101 dataset view                # 网页数据集管理（Flask:5555）
```

完整命令参考：`so101 --help`，`doc/cheatsheet.md`

## 修改代码指南

- **添加新子命令**: `cli.py` 中 `_build_parser()` 加 `sub.add_parser()`，`main()` 加对应 `elif` 分支
- **修改设备检测逻辑**: `scan.py`（摄像头发现）和 `config.py`（配置解析）
- **修改录制流程**: `record.py`（主循环在 `run_record()`，数据集操作在 `prepare_dataset()`）
- **修改部署推理**: `deploy.py`（策略加载 + 推理循环）
- **添加新场景**: 编辑 `config/camera_config.yaml` 的 `scenes:` 区，无需改代码
- **修改配置解析**: `config.py` 的 `resolve_scene()` 和 `load_config()`

## 依赖

- Ubuntu 22.04
- conda env `lerobot`（Python 3.12）
- LeRobot >= 0.5.1: `pip install -e ~/lerobot/.[feetech]`
- pyyaml, opencv-python（pyproject.toml 中声明）

## 已知问题与注意事项

- **串口权限**: `sudo usermod -aG dialout $USER`（永久，需重新登录）
- **Orbbec 后端**: OpenCVCameraConfig 必须传字符串路径（`/dev/video10`）走 V4L2 后端，传整数会用 FFMPEG 读不了帧
- **Parquet 损坏**: 录制中断会损坏 parquet，需在 `try/finally` 中调 `dataset.finalize()`（已用 `VideoEncodingManager` 处理）
- **conda pip 冲突**: shebang 可能指向 base python，用 `conda run -n lerobot so101 ...` 运行
- **KDE Plasma 负载**: KDE 后台服务会抢占 CPU 影响串口通信，遥操作时建议停 baloo/kdeconnect
- **流式编码**: `streaming_encoding=True` + `encoder_threads=2` 让 save_episode 从几秒降到毫秒级
