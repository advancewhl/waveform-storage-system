# 波形存储正式前端：安装与使用

本包用于桌面机公共安装目录，也可安装到新的前端电脑。前端直接 SSH 连接 waveform 服务器 `192.0.2.2:22`，不经过 jump-user。前端在 SSH 隧道中访问服务端本机的 Candidate V2 端口 `5001`；MongoDB 和 HDF5 均留在 waveform 服务器。界面由 `bin/waveform-operator/waveform-operator` 可执行文件启动，无须运行 `python main.py`。发布包不含密码、私钥或采集数据；现场可将已授权的桌面机私钥放在 `credentials/id_ed25519_waveform_operator`，启动器会为每个账号复制成权限为 600 的私钥文件。

## 1. 安装前确认

- 新电脑是 Linux x86_64，具有图形桌面和 OpenSSH 客户端。可执行文件按 GLIBC 2.28 基线构建，已经包含 Python 运行时和所需依赖；无须在目标机安装 Python 或联网运行 pip。
- 新电脑能直接访问 `192.0.2.2` 的 SSH 端口 22。包内 `security/known_hosts` 包含已核对的 waveform 服务器主机密钥。
- 桌面启动是非交互式的，必须预先配置 SSH 密钥登录或可用的 SSH agent。仅能手动输入密码登录时，图标启动会进入只看界面模式。

当前版本与配套服务端以仓库 `docs/CURRENT_STATUS.md` 和对应交付记录为准。**新电脑只需复制本前端包；不要把服务端包安装到前端电脑。**

## 2. 解包、配置、安装图标

把 `.tar.gz` 和同名 `.sha256` 放在新电脑同一目录，进入该目录执行：

```bash
sha256sum -c ./*.sha256
tar -xzf waveform-operator-direct-offline-20261004-executable-*.tar.gz -C "$HOME"
cd "$HOME/waveform-operator-direct-offline-20261004"
sha256sum -c MANIFEST.sha256
cp connection.env.example connection.env
```

`connection.env` 默认使用 `storage-user@192.0.2.2:22`。如新电脑通过专用私钥登录，在该文件中设置 `WAVEFORM_SSH_IDENTITY_FILE=/绝对路径/私钥`。已安装的桌面机继续使用其现有专用密钥和 `connection.env`，更新时保留该文件。

公共目录给多个账号使用时，在 `connection.env` 设置 `WAVEFORM_OPERATOR_STATE_DIR=/home/desktop-user-a/指定的日志目录`，并给这些账号授予该目录的创建权限。启动器和界面的日志、未完成采集恢复记录都会写入其中按 UID 分开的子目录，避免账号间的采集状态互相覆盖；每个账号的 SSH 隧道也使用独立的本机端口。日志目录和文件按 `umask 027` 创建，让目录所属组可以检查日志，其他普通账号不能读取彼此的记录。Matplotlib 缓存及每个账号的 SSH 私钥副本写入各自的主目录。公共目录本身对这些账号只需读取和执行权限；现场共享私钥须对获准使用的账号或组可读。

不加入共享凭证组的账号使用各自的 SSH 密钥。先以该账号在公共包目录运行 `./register-account.sh`，脚本会在该账号主目录生成权限为 600 的私钥，并把公钥提交到公共目录上级的 `.waveform-operator-key-requests`。服务器管理员核对请求文件的属主后，把公钥加入 waveform 服务器 `storage-user` 的 `authorized_keys`；随后该账号即可运行 `./run-operator.sh`。私钥始终留在该账号主目录，不放进公共目录。现场桌面机的日志根目录由 `desktop-user-a` 持有，路径配置在 `connection.env` 中。

先确认新电脑能够**不提示密码**地登录：

```bash
ssh -o BatchMode=yes -o ProxyJump=none -o ProxyCommand=none -p 22 \
  storage-user@192.0.2.2 'echo SSH_OK'
```

如果使用自定义私钥，在 `ssh` 命令中加 `-i /绝对路径/私钥`。若尚未配置密钥，可在新电脑生成 `ssh-keygen -t ed25519`，再经管理员批准把对应公钥加入 waveform 服务器该账户的 `authorized_keys`。不要复制私钥到服务器。

安装桌面入口：

```bash
./install-desktop.sh
```

脚本安装应用菜单入口，并在现有的 `~/Desktop` 或 `~/桌面` 中放置“波形存储”图标。以后点击图标启动，也可在包目录运行 `./run-operator.sh`。若桌面环境首次提示“允许启动”，选择允许即可。请把解包目录固定在当前位置；移动目录后须重新运行 `./install-desktop.sh`。

## 3. 日常使用

1. 打开程序。连接成功后显示正式界面，并查询一次服务器上的定时 PV，右侧显示“当前频率”，如 `5 Hz`。这个数值是当次读数，不是固定配置。
2. 在“采集控制”展开区域并勾选需要的 BPM，选择采集时长，点击“开始采集”。频率始终跟随服务端采集前重新读取的 PV，无须手动选择。正式启用清单为 47 台，每台文件检查 20000 点。
3. 采集期间底部状态栏显示进展。采集完成后看保存的轮次数；部分轮次成功时，界面显示“部分数据已保存”，可继续检索这些文件。
4. 在“历史检索与绘图”选择日期并点“检索数据”，再选择任务和数据段。点击“选择 BPM”，勾选该段有文件的设备，再点“确认并对齐”。随后可绘图、定位 bunchID、导出对齐结果或下载原始文件。历史查询使用完整的 `request_id + capture_sequence`，不自动拼接数据段。bunchID 是数据编号，不能直接当作秒数或采样率。

连接失败时程序仍可打开只看 UI 模式，但不能采集或查询历史。可主动运行 `./run-operator.sh --ui-only` 查看界面。启动日志和界面日志位于 `WAVEFORM_OPERATOR_STATE_DIR`；未设置时位于包目录的 `logs/`。界面不展示日志面板。

## 4. 数据库在哪里，怎样查询

waveform 服务器正在使用 MongoDB 数据库 `waveform_storage`。前端历史页面通过采集服务查询数据库；**新电脑无需安装 MongoDB，也无需直连数据库端口**。

数据库有三个主要集合：

| 集合 | 内容 |
| --- | --- |
| `capture_tasks` | 请求 ID、采集频率和时长、设备、状态历史、成功/失败数、错误信息。 |
| `waveform_files` | 已完成的单设备 HDF5 文件路径、日期、时刻、点数、设备 ID。 |
| `device_registry_versions` | 当时使用的非敏感设备清单快照。 |

**波形数组在 HDF5 文件中，不在 MongoDB 中。** `capture_tasks` 的 `failed` 可以表示部分设备失败；要看哪些设备确实有可用文件，应查该请求在 `waveform_files` 中的记录。历史页面只显示 `status=completed` 的文件。

已在 waveform 服务器放置只读查询脚本 `/home/storage-user/waveform-storage-v2/query-db.py`。它读取服务器自己的配置、通过本机 MongoDB 容器查询，不把数据库密码放进命令参数或输出。从新电脑通过直接 SSH 运行：

```bash
# 最近任务、任务总数和文件总数
ssh -p 22 storage-user@192.0.2.2 \
  'python3 /home/storage-user/waveform-storage-v2/query-db.py summary --limit 10'

# 按日期看已完成文件数及可用时刻；日期使用 YYYY-MM-DD
ssh -p 22 storage-user@192.0.2.2 \
  'python3 /home/storage-user/waveform-storage-v2/query-db.py date 2026-10-03'

# 按采集请求 ID 看任务状态、成功/失败数及已存文件
ssh -p 22 storage-user@192.0.2.2 \
  'python3 /home/storage-user/waveform-storage-v2/query-db.py task c48d04bd-8c07-4c61-8bc2-68681c494dd6 --limit 50'

# 按设备 ID 查最近的 HDF5 文件
ssh -p 22 storage-user@192.0.2.2 \
  'python3 /home/storage-user/waveform-storage-v2/query-db.py device AC_LA_INJ1_BI_SBPM01 --limit 20'
```

上述请求 ID 和日期是已验证的**示例**；查新任务时换成界面显示的请求 ID。使用专用 SSH 私钥时，在 `ssh` 与服务器地址之间加入 `-i /绝对路径/私钥`。已登录 waveform 服务器时可省略 `ssh ...`，直接运行 `python3 /home/storage-user/waveform-storage-v2/query-db.py ...`。

MongoDB 元数据写入采用尽力保存：若采集期间数据库写入故障，HDF5 仍可能落盘但历史索引缺失。遇到“任务有文件、历史页查不到”时，先按请求 ID 查询数据库，再核对服务端任务记录和 HDF5 路径，避免重复采集。

## 5. 常见检查

```bash
# 只检查新电脑到 waveform 服务的 SSH 连接
ssh -o BatchMode=yes -p 22 storage-user@192.0.2.2 'echo SSH_OK'

# 查看前端启动日志
tail -n 80 "$HOME/waveform-operator-direct-offline-20261004/logs/operator-launcher.log"

# 从新电脑检查服务端是否运行
ssh -p 22 storage-user@192.0.2.2 'systemctl is-active waveform-acquisition-v2.service'
```

SSH 通但程序仅显示只看 UI 时，检查密钥是否可在 `BatchMode=yes` 下使用、服务器主机指纹是否已保存，以及端口 5001 隧道是否可建立。当前服务端仅在自身 `127.0.0.1:5001` 监听，前端通过 SSH 转发访问，不需要对新电脑开放数据库或 ZMQ 端口。

## 当前频率

采集参数只读显示服务端定时 PV 的频率，不提供频率下拉框。启动时读取；采集前重新读取并跟随最新值。读取失败时显示“暂不可用”，并阻止提交采集请求。
