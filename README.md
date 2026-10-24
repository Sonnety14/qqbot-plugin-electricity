# 电费余额提醒 · AstrBot

版本 **v0.8.3**，与原 AstrBot 安装版版本号一致。按网站账号隔离电费授权、查询记录和 QQ 提醒，默认每天北京时间 **09:00** 查询，**总余额严格低于 20 元**时提醒。

## 必须配套 electricity_bridge 使用

本插件是机器人侧的查询与通知模块，**不能单独完成用户绑定**。请配套部署：

**[electricity_bridge 仓库：Sonnety14/qqbot-plugin-electricity_bridge](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge)**

- [网站接入契约与接口说明](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge/blob/main/docs/INTEGRATION.md)
- [Windows 授权助手使用说明](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge/blob/main/docs/WINDOWS_GUIDE.md)
- [助手下载与版本发布](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge/releases)

该仓库提供 Windows 助手及网站路由适配模块，**不是安装即用的完整账号网站**。部署者仍须接入自己的登录、已验证 QQ 绑定、CSRF、数据库、前端确认页面和限流，具体要求见上述接入契约。不要同时安装两个仓库中的同名 `astrbot_plugin_electricity` 副本；本仓库提供独立发布的机器人插件。

当前只适配 **`wx.cdzht.com` 电费余额页**，不支持所有学校、宿舍或电费系统。它只读余额，不充值、不切换房间、不自动完成微信授权。

## 部署顺序

1. 按 electricity_bridge 的接入文档部署网站侧协议，确保用户能登录并验证自己的 QQ，前端能显示房间、余额和确认按钮。
2. 安装本插件：在 AstrBot 插件管理中填写 `https://github.com/Sonnety14/qqbot-plugin-electricity`，或将仓库内容放入 `data/plugins/astrbot_plugin_electricity/`。
3. 安装 `requirements.txt` 的依赖，使用 AstrBot **4.28.2 或以上**及其 Python 环境，建议 Python 3.12。QQ 适配器需支持 OneBot `send_private_msg`。
4. 配置下表中的平台 ID、桥接地址、Host 和专用令牌，启用 `bridge_enabled`；确认网站绑定任务能够同步。
5. 启用 `enabled`，让已经正式绑定的账号参加每日查询与提醒。保持网站后端和机器人之间的私网连通。

初始配置没有任何平台、账号、服务器或密钥，两个启用开关都为 `false`。插件不会自动读取校园插件或其他插件的配置。

## 配置

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `enabled` | `false` | 开启已绑定账号的每日查询和通知；不控制网站绑定任务同步 |
| `platform_id` | 空 | AstrBot QQ 平台实例 ID，启用桥接时必填；不是 QQ 号 |
| `threshold` | `20` | 全局低余额阈值，单位元；使用有限、非负数 |
| `hour` | `9` | 北京时间查询整点，填写 0–23 的整数 |
| `bridge_enabled` | `false` | 开启网站任务、绑定列表和状态同步 |
| `backend_origin` | 空 | 私网后端 HTTP origin，只能是 IP 和可选端口，不带路径 |
| `backend_host` | 空 | 后端要求的虚拟主机 Host，不带协议或路径 |
| `bridge_token` | 空 | 专用 scoped 令牌；推荐通过环境变量提供 |
| `account_url` | 空 | 可选，用户更新授权的 HTTPS 页面，出现在授权异常提示中 |
| `qq` | 空 | 仅用于旧单用户数据迁移匹配；新安装留空 |

示例中 `http://127.0.0.1:8000` 只表示本机服务的地址格式，不是预设部署地址。容器中的 `127.0.0.1` 指向容器自身；应填写机器人实际可访问的后端私网 IP。`backend_host` 应填写你自己的后端虚拟主机名，例如 `example.invalid` 仅能用于说明格式。

`configuration.py` 拒绝公网 IP、域名 origin、携带用户名/密码、路径、查询串或片段的桥接地址，也拒绝不合法 Host 和令牌。该版本桥接传输仅支持私网 HTTP；应置于可信隔离网络中，公网反向代理必须拒绝整个内部 bridge 路由前缀。

### 桥接令牌

`ELECTRICITY_BRIDGE_TOKEN` 环境变量优先于配置字段 `bridge_token`。环境变量应配置在 AstrBot 服务或容器中；不要写进仓库。

插件需要的是网站接入契约中的 **scoped 令牌**，不是服务端主密钥、一次性连接码或用户电费 Cookie。部署者在受控环境中按配套协议派生：

```python
import hashlib
import hmac

scoped_token = hmac.new(
    server_master_secret.encode(),
    b"campus-bindings-v1",
    hashlib.sha256,
).hexdigest()
```

`server_master_secret` 由你的网站安全配置提供。将派生结果私下配置到机器人，不要打印到共享日志、截图或 README 中。若直接填写面板的 `bridge_token`，它会随 AstrBot 配置以明文保存，应限制配置文件和备份的访问权限。

## 用户首次绑定

以下步骤在你实际部署的网站完成，本插件不附带私人网站入口。

1. 按网站要求添加机器人为 QQ 好友，登录网站并验证绑定自己的 QQ。
2. 打开“绑定电费”，从 electricity_bridge 的发布页下载 Windows 助手并完整解压。
3. 在网站生成一次性连接码，通常以 `ELECTRIC-` 开头。
4. 打开助手，填写自己信任的网站 HTTPS 根地址和连接码，点击开始连接。
5. 等助手准备好后，在 Windows 微信打开**自己的房间**电费余额页并刷新。
6. 回网站核对房间、余额和数据时间，主动点击“确认绑定”。房间不对时不要确认。
7. 网站显示正式绑定完成后，用绑定的 QQ 私聊机器人发送 `电费状态` 检查最近记录。

学校或第三方授权只在正常微信流程中完成。不要把微信密码、连接码或 Cookie 发给机器人；不要使用他人的房间或授权。助手下载包与使用限制以 electricity_bridge 的发布说明为准。

## 查询、提醒与续期

私聊发送 `电费状态` 或 `电费查询`，机器人根据网站当前有效的 QQ 绑定查找对应账号，显示**最近一次已保存的结果**，不会立即请求上游余额。未绑定、绑定不唯一或任务尚未安装完成时，可能没有该插件的回复；先检查网站状态。

- 每个账号独立查询，默认每天 09:00；当天启动较晚时，在设定整点之后补查当日尚未完成的记录。
- 总余额 **小于**阈值才提醒，等于阈值不提醒。正常且不低余额时不发通知。
- 每人每天最多预留一条自动通知，包含低余额、授权失效、房间变化、陈旧数据或连续失败等通知；不是这些类型分别每天一条。
- 查询失败最多尝试 3 次，间隔至少 5 分钟；通知发送结果不明确时记录为 `unknown`，当天不自动重发，避免刷屏。
- 授权失效或房间变化会暂停该账号，不暂停其他账号。
- 数据时间早于当前 48 小时以上，或晚于当前 10 分钟以上时，提示人工核对，不据此报欠费。
- 新安装的正式绑定会将网站已验证的快照保存为当日成功记录，所以当天可能直接展示绑定时的余额，不再重复查询或立即发送低余额通知。

收到授权异常提示时，在微信正常完成重新授权，回网站生成新连接码，使用助手重新提交并确认。仅在网站确认正式的新绑定版本后，机器人启用新凭据；失败不会覆盖旧的活动版本。不要手工修改加密文件来续期。

## 实现方法

### 网站与机器人协议

机器人默认每 5 秒启动一轮处理，调用固定相对路径：

| POST 路径 | 作用 |
| --- | --- |
| `/comments/bridge/campus/electric/work` | 领取验证或安装任务及租约 |
| `/comments/bridge/campus/electric/result` | 反馈验证结果或已安装结果 |
| `/comments/bridge/campus/electric/bindings` | 获取当前有效账号、QQ 和正式版本 |
| `/comments/bridge/campus/electric/health` | 回报对应版本的活动或暂停状态 |

这些历史路径名是与 electricity_bridge 的协议兼容要求，不代表必须安装校园插件。接口需要私网限制和专用 Bearer 令牌。网站接入契约规定用户认证、租约及版本状态机，本插件不接受助手自行指定提醒收件人。

### 多用户隔离

`main.py` 按网站 `user_id` 建立账号工作器；网站确定 QQ 与正式绑定版本。每个工作器有独立 HTTP 客户端、数据库、锁和暂停状态，最多 4 个账号同时查询。发送 QQ 消息前再次向网站确认账号、版本与 QQ 仍一致；验证失败则不发送。

安装任务的凭据先加密写入版本文件，只有网站返回的正式绑定列表包含该版本才启用。解绑或账号失效后，工作器会从活动集合移除；取消处理不等于自动擦除历史文件。

### 余额读取与调度

`provider.py` 只发 GET 到固定余额页 `http://wx.cdzht.com/meter_main`，携带该账号的授权 Cookie 和客户端标识，拒绝跟随 OAuth 重定向，限制响应体为 256000 字节。解析房间标识、预付费余额、补助电余额、总余额与更新时间，房间必须与绑定值严格一致。

`worker.py` 使用 `Decimal` 比较金额，并通过 SQLite 每日记录和发送前预留防重复。房间、日期和失效校验都由代码执行，不使用大语言模型，不访问充值接口。

上游余额页目前使用 **HTTP**，因此这段网络传输不是端到端加密；本地加密存储不能消除其明文传输风险。助手到网站使用 HTTPS，机器人到桥接服务使用可信私网 HTTP。

## 数据与隐私

运行数据在 AstrBot 数据目录的 `plugin_data/astrbot_plugin_electricity/`：

```text
accounts.key                        本机 Fernet 加密密钥
accounts/<user_id>/<version>.enc     各账号、各版本的加密授权
accounts/<user_id>/checks.sqlite3    独立查询快照、暂停状态、通知记录
```

凭据文件使用随机本机密钥加密；POSIX 下密钥和版本文件权限为 0600，账号目录为 0700。Windows 部署须另外用 ACL 限制服务账号访问，不能把 POSIX 权限位视为完整访问控制。

查询库包含房间和余额快照，**并未整体加密**；知道 `accounts.key` 且能读取密文的人也能解密授权。因此应保护整个数据目录、配置和备份。旧版本密文、已撤销账号目录及查询历史不会自动全部删除，应由维护者按保留策略清理。

升级时保留整个数据目录，特别是 `accounts.key`；丢失密钥将无法解密已有凭据。备份前停止插件或 AstrBot，复制完整数据与配置，安全保存且不要提交 Git。

### 旧单用户数据迁移

新安装不需要 `credentials.json`。仅当旧凭据的 `binding_version` 与网站正式版本一致，且旧配置 `qq` 与该绑定匹配时，插件才迁移旧凭据和查询记录。迁移不删除原文件；确认迁移及备份后，维护者应自行处理旧明文文件。

仓库仅包含源码、空配置和合成测试。没有真实 QQ、群号、房间、Cookie、令牌、服务器 IP、生产数据库、个人网站地址或密钥；公开 GitHub 作者及配套仓库链接用于归属和安装说明。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

测试使用虚构账号、房间、凭据、临时数据库和 HTTP 模拟，不查询真实电表、不发送真实 QQ 消息。覆盖阈值边界、每日去重、授权失效、房间变化、陈旧数据、重试、发送超时、多账号隔离、加密迁移、续期确认和撤销。

若在已安装 AstrBot 的 Python 环境中验证真实插件 API，可分别执行：

```bash
python tests/check_astrbot_runtime.py
python tests/check_multi_runtime.py
```

## 常见问题

- **网站没有绑定页面**：仅安装插件不够；先按 electricity_bridge 接入契约实现账号和前端流程。
- **桥接 401/403**：检查使用的是派生的 scoped 令牌、私网访问、正确 Host，且环境变量没有覆盖成旧令牌。不要把主密钥直接填给机器人。
- **绑定一直等待**：检查 `bridge_enabled`、平台 ID、私网连通性、任务租约以及后端日志，避免在日志中输出任务正文或授权。
- **没有每日提醒**：核对 `enabled`、整点、阈值、网站正式绑定和 QQ 私聊可达性；余额不低于阈值不会发送正常提醒。
- **查询不是实时值**：命令读取缓存，上游电表也可能延迟；同时查看数据时间和查询日期。
- **授权失效**：通过配套网站和助手重新授权并确认；插件不会自动绕过微信授权。

## 许可

本插件代码采用 [MIT License](LICENSE)。electricity_bridge 的助手和其他组件应遵守其各自的第三方许可说明；本仓库许可证不授予第三方电费系统或微信服务的权限。
