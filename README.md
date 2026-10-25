# 电费余额提醒 · AstrBot

版本 **v0.8.3**，与 AstrBot 当前显示一致。本次增加 SWJTU 微校园电费查询、QQ 私聊绑定宿舍、CAS 统一认证自动重登；原微信授权模块保留兼容。默认每天北京时间 09:00 查询，总余额严格低于 20 元时 QQ 私聊提醒，每人每天最多一条自动通知。

## 适用范围与配套项目

新模式针对 **西南交通大学微校园 `wxy.swjtu.edu.cn`**，使用 `cas.swjtu.edu.cn` 统一认证。支持犀浦校区天佑斋及鸿哲斋 2、3 栋，都通过“天佑斋电控缴费”分类查询。不包含充值、缴费功能，不是通用学校适配器。

必须配套账号网站和 **[electricity_bridge 仓库](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge)** 使用。旧授权助手和基础接口见该仓库；新微校园模式不需要普通用户使用助手，但网站必须实现 [WXY_BRIDGE.md](docs/WXY_BRIDGE.md) 的扩展。**旧版 bridge 不直接支持新的 QQ 绑定宿舍接口。** 本仓库不是完整账号网站，不自动读取其他插件的私人配置。

## 用户使用

先在部署者的网站登录并验证绑定 QQ，然后任选：

1. 网站填写宿舍，核对余额后确认。
2. 私聊机器人发送 `绑定宿舍`，10 分钟内发送宿舍号；核对成功后自动保存并同步网站。提交前发送 `取消绑定` 只退出本次操作，不解除原绑定。

格式为区域名 + 两位楼栋 + 楼层 + 三位房间序号。例如 `天佑斋 012001` 表示 1 栋 2 楼 1 号；这是合成示例，不是作者真实宿舍。1–9 楼为一位，10–19 楼为两位；鸿哲斋只支持 2、3 栋。

※ 网站与 QQ 共用每个账号每个北京时间自然月一次的成功绑定额度，失败不扣次数，解绑不重置次数；由网站事务强制执行。

私聊 `电费查询` / `电费状态` 显示最近已保存的余额和查询时间，**不是实时向学校发起查询**。群聊不接受这些操作。

## 部署

1. 完成 electricity_bridge 的账号网站接入，并实现本文所链接的 WXY 扩展。
2. 在 AstrBot 安装 `https://github.com/Sonnety14/qqbot-plugin-electricity`；不要重复安装同名插件。
3. 使用 AstrBot 4.28.2+、Python 3.12，安装 requirements.txt；QQ 适配器需支持 OneBot send_private_msg。
4. 配置下表桥接参数并启用 bridge_enabled，初始化学校认证后启用 enabled。

| 参数 | 默认 | 用途 |
| --- | --- | --- |
| enabled | false | 每日查询提醒 |
| bridge_enabled | false | 网站任务同步 |
| platform_id | 空 | AstrBot 平台实例 ID，不是 QQ 号 |
| threshold | 20 | 低余额阈值，元 |
| hour | 9 | 北京时间整点，0–23 |
| backend_origin | 空 | 可信私网 HTTP IP origin，无路径 |
| backend_host | 空 | 网站虚拟主机 Host |
| bridge_token | 空 | scoped 桥接令牌 |
| account_url | 空 | 用户账号设置的 HTTPS 页面 |
| qq | 空 | 只用于旧单用户迁移 |

环境变量 ELECTRICITY_BRIDGE_TOKEN 优先于配置。基础协议令牌由网站在私有环境派生：`HMAC-SHA256(server_master_secret, "campus-bindings-v1").hexdigest()`。不能直接使用主密钥，不能输出共享日志。桥接只接受可信私网 IP HTTP origin；公网反向代理必须拒绝内部 bridge 前缀。容器的 localhost 指容器自身。

### 初始化或更新学校账号

管理员使用有权访问该查询服务的学校账号。**先停用插件**，在 AstrBot 的 Python 环境运行：

```bash
python scripts/configure_wxy.py --data-dir /your/astrbot/data/plugin_data/astrbot_plugin_electricity
```

替换占位路径为实际插件数据目录。交互输入账号及隐藏密码，密码不经命令参数传递；验证成功后加密保存，失败恢复原配置。然后重启插件。不要删除 accounts.key，也不要把学校密码发给机器人或提交 Git。

遇到验证码、额外验证或账号错误时停止。通过学校官方入口处理后再尝试；不破解验证码、不绕过二次验证。普通绑定用户不需要提供学校密码。

## 实现方法

- **wxy_provider.py**：只允许六个只读 API。逐级读取校区、楼栋、楼层、房间，通过名称唯一匹配，不使用列表第一个默认值、不猜 ID。最终响应的房间标识必须匹配，否则拒绝更新。学校未提供抄表时间，显示的是查询时间。
- **wxy_auth.py**：先复用 CAS 会话；必要时获取动态盐、execution，检查验证码要求，按官方 AES-CBC 流程提交一次密码。CAS 回跳后还需提交固定身份初始化表单到微校园，再验证电费 API。HTTPS 跳转限学校两个域名，验证 CA 和主机名。
- **wxy_session.py**：共享查询和重登串行化，识别认证响应与实测 91001 超时代码。重登成功后重试一次，宿舍查询触发时先核对房间再替换会话。每次尝试至少间隔一小时且跨重启保留；验证码/登录拒绝会持久暂停，管理员处理后重新运行配置脚本。
- **调度**：每小时 00/30 分检查共享会话，保存上游轮换 Cookie。自然会话寿命受学校控制，不保证永久无人值守。
- **main.py**：网站租约任务、版本安装和 QQ 流程。正式绑定由网站确定，发送通知前重新检查账号、QQ、版本归属。学校共享凭据不下发给网站。
- **worker.py**：SQLite 记录、Decimal 金额比较、每日通知预留去重与失败重试。等于阈值不提醒；发送结果未知不盲目重发。整个流程不调用 LLM。

旧 provider.py 保留微信授权余额页兼容，仍需按 electricity_bridge 的旧助手流程授权；旧上游是 HTTP。新 WXY 使用 HTTPS，兼容学校 TLS 1.2 密码套件但不关闭证书验证。

## 数据与备份

```text
accounts.key                       本机 Fernet 密钥
wxy-auth.enc                       加密学校凭据、CAS Cookie、退避/暂停状态
wxy-session.enc                    加密微校园会话
wxy-health.sqlite3                 查询与登录起止时间、结果和错误类型
accounts/<user_id>/<version>.enc    各账号宿舍选择或旧授权
accounts/<user_id>/checks.sqlite3   余额、查询与通知记录
```

运行数据均位于 AstrBot 插件数据目录。凭据密文权限 0600，Windows 还需设置 ACL。数据库包含宿舍与余额，并非整体加密；密钥加密文可解密，整个数据目录、配置及备份均不能公开。升级前停用插件并备份，保留 accounts.key，旧版本数据不会自动全部删除。

旧公开源码已备份至 [backup/pre-wxy-20261025](https://github.com/Sonnety14/qqbot-plugin-electricity/tree/backup/pre-wxy-20261025)。源码回滚不能代替运行数据备份。

## 测试和排错

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

测试使用合成数据和 HTTP 模拟，不访问学校、不发送 QQ。覆盖旧模式、房间校验、验证码、受限跳转、超时识别、互斥登录、跨重启暂停及退避。

- QQ 绑定 404：网站缺少 qq/start、qq/room、qq/status 扩展。
- 桥接 401/403：检查私网、Host、scoped 令牌、QQ 归属。
- 登录暂停：通过学校解决账号/验证码问题，再运行配置脚本，不循环重试密码。
- 未提醒：正常余额不发通知；检查开关、平台、绑定版本、时间与当天通知记录。
- 余额未变化：命令读缓存，上游电表也可能延迟。

仓库不包含个人账号、真实宿舍、生产服务器地址、私人网站配置、Cookie、密钥或数据库。学校公共域名和 GitHub 项目地址是适配与安装所需的公开信息。

## 许可

[MIT License](LICENSE)。第三方学校服务与 electricity_bridge 组件遵守其各自权限和许可。
