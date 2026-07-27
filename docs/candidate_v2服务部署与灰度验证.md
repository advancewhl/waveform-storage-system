# Candidate v2 服务部署与灰度验证

## 1. 边界

新服务与原 `saveApp` 并行部署：

```text
legacy saveApp        0.0.0.0:5000
candidate v2          127.0.0.1:5001
```

初次验证不得停止、覆盖或替换旧服务。candidate v2 默认只监听服务器
localhost，操作员工作站通过 SSH 隧道访问。

服务行为：

- 严格校验协议 v2 和服务端设备注册表；
- 立即返回 `accepted`，采集在单独线程中执行；
- 同一时刻只允许一个采集任务；
- 相同 `request_id` 和相同请求不会重复触发；
- 相同 `request_id` 配不同请求会被拒绝；
- 状态原子写入每任务一个 JSON 文件；
- 重启前未结束的任务标记为 `INTERRUPTED_BY_RESTART`，不会自动重放；
- SIGTERM 会取消正在等待的任务并复位服务；
- SSH 必须使用已验证的 `known_hosts`，不接受未知主机密钥；
- 只选择触发时间之后、匹配注册表 pattern、非临时后缀、大小和修改时间稳定的文件；
- 单 BPM HDF5 使用已验证的 `BunchIDData uint64 (1,N)` 和
  `Data int64 (2,N)` schema，并通过临时文件原子替换。

## 2. 生成单设备灰度注册表

在开发机仓库根目录执行：

```bash
python tools/device_registry.py select-deployment \
  --registry config/devices.json \
  --device AC_LA_INJ1_BI_SBPM08 \
  --output deploy/devices.gray.json

python tools/device_registry.py validate \
  --registry deploy/devices.gray.json
```

该命令只允许选择原注册表中已经
`enabled=true, acquisition_enabled=true` 的设备。服务器和 GUI 必须使用同一份
`devices.gray.json`。

## 3. 服务器目录

在 waveform 服务器上创建：

```bash
mkdir -p ~/waveform-storage-v2/{app,config,state,staging}
chmod 700 ~/waveform-storage-v2
```

将固定 Git commit 的源码放入 `~/waveform-storage-v2/app`，将灰度注册表放到：

```text
/home/storage-user/waveform-storage-v2/config/devices.json
```

不要把开发机的 `.env`、密码、私钥或 HDF5 样例复制进源码目录。

## 4. Python 环境

在服务器源码目录执行：

```bash
conda env create -n waveform-storage-v2 -f environment.yml
conda activate waveform-storage-v2
python -m pip install -e . --no-deps
python tools/run_quality.py
```

实际环境路径必须与 systemd 单元的 `ExecStart` 一致。

## 5. SSH 主机密钥与服务配置

复制配置模板：

```bash
cp deploy/waveform-acquisition-v2.env.example \
  /home/storage-user/waveform-storage-v2/service.env
chmod 600 /home/storage-user/waveform-storage-v2/service.env
```

`known_hosts` 中的设备主机密钥必须与设备负责人或既有受信记录核对，不能只因为
`ssh-keyscan` 返回了某个值就直接信任。

编辑 `service.env`，至少确认：

- `WAVEFORM_SERVER_REGISTRY`；
- `WAVEFORM_LOCAL_DATA_ROOT`；
- `WAVEFORM_DEVICE_SSH_USER`；
- SSH key 或密码；
- `WAVEFORM_DEVICE_SSH_KNOWN_HOSTS`；
- EPICS timeout 和 pulse；
- 首轮 `WAVEFORM_SSH_MAX_WORKERS=1`。
- 首轮 `WAVEFORM_MAX_CAPTURE_DEVICES=1`；
- 允许的最大采集时长 `WAVEFORM_MAX_CAPTURE_SECONDS`。

配置文件不由 Python 自动加载；systemd 通过 `EnvironmentFile=` 加载。

## 6. 不联网的部署预检

```bash
set -a
source /home/storage-user/waveform-storage-v2/service.env
set +a

python apps/acquisition_server/main.py --check
```

该命令校验路径、注册表、approved 设备、known_hosts 和安全配置，不绑定端口、不读写
EPICS、不连接设备 SSH。

## 7. 前台启动与健康检查

首次不要直接注册 systemd。先在 waveform 服务器前台运行：

```bash
python apps/acquisition_server/main.py
```

另一个服务器终端执行：

```bash
python tools/acquisition_v2_client.py health
```

预期得到 `accepted=true, status=succeeded`。确认状态目录只产生任务查询之外的预期内容，
日志不包含密码。

## 8. systemd

确认前台启动和停止正常后：

```bash
sudo cp deploy/systemd/waveform-acquisition-v2.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now waveform-acquisition-v2.service
sudo systemctl status waveform-acquisition-v2.service
journalctl -u waveform-acquisition-v2.service -n 100 --no-pager
```

确认：

```bash
ss -lntp | grep ':5001'
ss -lntp | grep ':5000'
```

5001 应为新服务，5000 仍应为旧 `saveApp`。

## 9. 工作站 SSH 隧道和 GUI

工作站保持隧道：

```bash
ssh -N -L 5001:127.0.0.1:5001 storage-user@192.0.2.2
```

另一个工作站终端：

```bash
export WAVEFORM_CANDIDATE_ZMQ_ENDPOINT=tcp://127.0.0.1:5001

python tools/acquisition_v2_client.py health

python apps/operator_gui/main.py \
  --mode candidate_v2 \
  --registry deploy/devices.gray.json
```

GUI 会再次要求确认。只点击一次；收到 `accepted` 后不要因为任务仍在运行而重发。
任务 UUID 可用以下命令查询：

```bash
python tools/acquisition_v2_client.py status <request_id>
```

确需取消时必须显式确认：

```bash
python tools/acquisition_v2_client.py cancel <request_id> --yes
```

取消在安全边界生效，不会强行中断正在进行的 SSH 文件传输。

## 10. 单台验收

依次确认：

1. 服务端日志记录 accepted、running 和终态；
2. EPICS PV 完成 `0 → 1 → 等待 pulse → 0`；
3. 设备远程目录出现触发之后的新 TXT；
4. TXT 在稳定检查后下载到 staging；
5. HDF5 生成到 `/SHINE_Data/AC/LA/YYYYMMDD/raw/BIData/`；
6. 文件名包含实际时刻和设备 ID；
7. `BunchIDData` 为 `uint64 (1,N)`；
8. `Data` 为 `int64 (2,N)`；
9. `Device/Data_0/Data_1` 属性正确；
10. GUI 能检索、读取和绘制该文件。

任何一步失败都保持单台，不扩大设备数量。客户端超时、服务端状态未知、PV 无法复位、
远程文件仍变化、HDF5 schema 错误或服务重启都属于停止条件。

## 11. 扩大与切换

灰度顺序：

```text
1 台 → 3 台 → 1 个受审区域 → 12 台正式设备
```

每次通过 `select-deployment` 生成新的固定注册表，校验后同时更新服务器和 GUI。
在取得旧 `saveApp` 的启动、停止、回滚方法并确认所有旧客户端已经迁移前，不抢占 5000，
不删除旧二进制和配置。
