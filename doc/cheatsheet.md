# SO-101 常用命令速查

## 设备管理

```bash
so101 scan                    # 首次：探测摄像头+机械臂，写入配置
so101 scan --cameras          # 仅扫描摄像头
so101 scan --arms             # 仅扫描机械臂
so101 list                    # 列出当前系统设备
so101 list --cameras          # 只列摄像头
so101 check                   # 采前自检（所有场景）
so101 check -s grab_redcube   # 检查指定场景
```

## 数据录制

```bash
so101 record -s grab_redcube              # 默认 50 episodes × 60s
so101 record -s grab_redcube -n 30        # 30 episodes
so101 record -s grab_redcube -n 20 --episode-time 30  # 20ep × 30s
so101 record -s grab_redcube --resume     # 追加到已有数据集
so101 record -s grab_redcube --overwrite  # 覆盖已有数据集
so101 record -s grab_redcube --name my_data            # 自定义仓库名
so101 record -s grab_redcube --vcodec h264             # 用 h264（比 av1 快）
so101 record -s grab_redcube --vcodec h264_nvenc       # GPU 硬编码（更快）
so101 record -s grab_redcube --dataset-repo-id Ready321/redcube_v2
so101 record -s grab_redcube --name 
so101 record -s grab_redcube --resume --dataset-repo-id Ready321/pickup_redcube_scene_2

# 东十七实验室 抓去单个redcube
so101 record -s grab_redcube --dataset-repo-id Ready321/redcube_dong17_v1 -n 20 --episode-time 30
so101 record -s grab_redcube --resume --dataset-repo-id Ready321/redcube_dong17_v1

# 东十七实验室 Push the red cube next to the green cube
so101 record -s grab_redcube --dataset-repo-id Ready321/push_red_near_green -n 20 --episode-time 30
so101 record -s grab_redcube --resume --dataset-repo-id Ready321/push_red_near_green

huggingface-cli upload \
  --repo-type dataset \
  --repo-id Ready321/Push the red cube next to the green cube \
  ~/.cache/huggingface/lerobot/Ready321/Push the red cube next to the green cube
```
/home/xuan/.cache/huggingface/lerobot/Ready321/Push the red cube next to the green cube


录制中键盘控制：
- Enter → 开始 / 结束重置
- 右箭头 → 提前结束当前 episode
- 左箭头 → 重录当前 episode
- ESC → 停止录制
- Ctrl+C → 中断并保存已录制数据

## 模型推理部署（deploy v2）

```bash
# ACT 推理（场景驱动，自动归位）
conda run -n lerobot so101 deploy \
  -p Ready321/act_pick_redcube \
  -d Ready321/pickup_redcube_20260421_095438 \
  -s grab_redcube \
  -n 3 --episode_time 15 --fps 30

# 关键参数
--max_velocity 10       # 每步最大移动量（度），默认 10
--smooth 1.0            # 动作平滑系数（1.0=无平滑，0.7=较强平滑）
--delta_threshold 999   # 跳变检测阈值（999=禁用）
--no-home               # 跳过自动归位
--home_speed 5.0        # 归位速度（度/步）
--bf16                  # bfloat16 推理（省显存）
--visualize             # OpenCV 实时显示
--teleop                # 允许 leader 遥操干预
--task "grab the cube"  # VLA 模型 task prompt
-o results/             # 保存结果目录

# SmolVLA 推理
conda run -n lerobot so101 deploy \
  -p whosricky/svla-so101 \
  -d Ready321/grab_redcube \
  -s grab_redcube \
  --policy_type smolvla \
  --task "grab the red cube"

# 推荐参数组合
# 初次测试：--max_velocity 10 --smooth 1.0 --delta_threshold 999
# 平滑运行：--max_velocity 5 --smooth 0.85 --delta_threshold 30
# 快速运行：--max_velocity 15 --smooth 1.0 --delta_threshold 999
```

部署流程：
1. 加载策略 + 数据集 metadata
2. 自动提取 action 反归一化统计量
3. 自动归位到训练数据起始位置（~30s）
4. 逐帧推理：采集 → 预处理 → 推理 → 后处理 → 发送
5. 保存完整结果（动作历史 + 延迟统计 + 状态轨迹）

## 数据集管理

```bash
so101 dataset check                          # 健康检查（损坏/空数据集）
so101 dataset clean                          # 预览空数据集
so101 dataset clean --yes                    # 清理空数据集
so101 dataset repair <name>                  # 修复 parquet 损坏（从视频恢复）
so101 dataset ls                             # 列出本地所有数据集
so101 dataset info --repo Ready321/my_data   # 查看详情
so101 dataset push --repo Ready321/my_data   # 推送到 HuggingFace Hub
so101 dataset gpu                            # 检查 GPU 硬编码支持
```

## 遥操作

```bash
so101 teleop                  # 默认 1to1 左臂
so101 teleop --arm right      # 1to1 右臂
so101 teleop --mode 1toN      # 单主臂控制双从臂
so101 teleop --mode dual      # 双主臂独立控制双从臂
```

## 校准

```bash
so101 calibrate               # 默认校准 follower_left
so101 calibrate --arm follower_right
so101 calibrate --arm leader_left
```

## 摄像头预览

```bash
so101 capture                 # 打开所有摄像头预览
so101 capture --role top      # 只看 top 摄像头
so101 capture --filter Orbbec # 只看 Orbbec 摄像头
so101 capture --output /tmp/caps  # 保存到指定目录
```
按空格保存帧，ESC/q 退出。

---

## 环境准备

```bash
conda activate lerobot                                    # 激活环境
sudo usermod -aG dialout $USER   # 串口权限（永久，需重新登录）
sudo chmod 666 /dev/ttyACM* /dev/ttyUSB*                 # 串口权限（临时）
HF_ENDPOINT=https://hf-mirror.com                        # 国内 HF 镜像
pip install -e ~/so101/                                   # 安装/更新 so101 包
```

## HuggingFace 操作

### 登录 & 环境

```bash
huggingface-cli login                                     # 登录
export HF_ENDPOINT=https://hf-mirror.com                  # 国内镜像（上传也要设）
export HF_TOKEN=your_token_here
```

### 上传

```bash
# 整个数据集（首次建仓库）
huggingface-cli upload \
  --repo-type dataset \
  --repo-id Ready321/my_data \
  ~/.cache/huggingface/lerobot/Ready321/my_data

# 先传小文件（避免超时），再逐个视频
huggingface-cli upload \
  --repo-type dataset \
  --repo-id Ready321/my_data \
  --ignore-patterns "videos/**" \
  ~/.cache/huggingface/lerobot/Ready321/my_data

# 逐个视频上传（大数据集推荐）
for f in ~/.cache/huggingface/lerobot/Ready321/my_data/videos/observation.images.top/chunk-000/*.mp4; do
  huggingface-cli upload --repo-type dataset --repo-id Ready321/my_data "$f"
done
```

### 下载

```bash
huggingface-cli download \
  --repo-type dataset \
  --local-dir ~/.cache/huggingface/lerobot/Ready321/my_data \
  Ready321/my_data
```

---

## 路径速查

| 用途 | 路径 |
|------|------|
| 本地数据集 | `~/.cache/huggingface/lerobot/Ready321/` |
| 配置文件 | `~/so101/config/camera_config.yaml` |
| 工作文档 | `~/le_xuan/` |
| so101 包源码 | `~/so101/src/so101/` |

## 常见问题

| 问题 | 解决 |
|------|------|
| 串口无权限 | `sudo usermod -aG dialout $USER`（永久）或 `sudo chmod 666 /dev/ttyACM*` |
| 摄像头打不开 | `so101 scan` 重新注册，检查 by-id 链接 |
| Orbbec 读不了帧 | OpenCVCameraConfig 必须设 `backend=Cv2Backends.V4L2` |
| Ctrl+C 后 parquet 损坏 | 用 `VideoEncodingManager` + `try/finally: dataset.finalize()` |
| 录制卡顿 | `streaming_encoding=True` + `encoder_threads=2` |
| 国内 HF 上传慢 | 设 `HF_ENDPOINT=https://hf-mirror.com` |
| deploy 策略不动 | 检查是否自动归位；action 需反归一化 |
| 动作太快/抖动 | 降 `--max_velocity`，提高 `--smooth` |
| index_or_path 传整数 | 必须传字符串路径 `/dev/video10`，不能传 `10` |
| conda pip 路径冲突 | `conda run -n lerobot so101 ...` 运行 |
