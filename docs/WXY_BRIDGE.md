# WXY 与 QQ 绑定扩展

补充 [基础接入契约](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge/blob/main/docs/INTEGRATION.md)。旧 bridge 版本不包含这些新接口；部署者必须在自己的账号网站实现，不能只升级机器人。

所有接口 POST，前缀 `/comments/bridge/campus/electric/`，沿用 scoped Bearer 鉴权，仅可信私网。QQ 来源为平台事件，不接受用户正文指定收件人。

| 路径 | 请求 | 返回与处理 |
| --- | --- | --- |
| qq/start | `{qq}` | `{id, expires_in:600}`；查唯一有效已验证 QQ 的网站账号，检查额度，建立 source=qq、state=qq_waiting 任务 |
| qq/room | `{qq,id,room}` | 验证归属、auth_version、有效期与额度，解析房间进入 validating；返回 2xx JSON |
| qq/status | `{qq,id}` | `{state,snapshot?}`；只读自身任务，done 返回最终快照，失败 failed/expired |

快照字段为 room、prepaid、subsidy、total、updated。金额为十进制字符串；updated 是北京时间查询时间。WXY 不提供分项金额，兼容值 prepaid=total、subsidy="0.00"，前端不应把它们描述为真实分项。

work 的 credentials 示例（合成数据）：

```json
{"provider":"wxy","selection":{"area":"天佑斋","building":1,"floor":2,"room_code":"2001","room":"天佑斋 012001"}}
```

学校账号、密码和共享 Cookie 不进入网站任务。work/result/bindings/health 的 ID、租约、版本、QQ 与账号字段沿用基础契约。验证返回 ready 后，source=qq 自动提交 queued，source=web 保留人工确认；安装 done 后才生效。

网站强制规则：

1. 网站请求登录认证、已验证 QQ、CSRF/Origin 和限流；内部接口要求桥接鉴权。
2. 每步重新核对 QQ/账号/auth_version/任务归属；账号或 QQ 撤销后旧任务失效。
3. 不替换 validating/queued/processing 任务；输入期 10 分钟，客户端不可续期。
4. 房间支持区域及格式固定校验；鸿哲斋只允许 2、3 栋；上游结果还需独立匹配。
5. `(user_id,北京时间自然月)` 唯一记录，成功绑定、版本切换与额度消耗同事务，失败不扣次数，解绑不重置。
6. 失败保留原绑定，QQ 等待超时不等于后端失败；网站与 QQ 共用正式绑定表。

上线前用隔离数据库验证跨 QQ 拒绝、账号撤销、租约重复、并发额度、失败回滚与两种确认流程，再做真实账号验收。
