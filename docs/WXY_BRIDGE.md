# WXY 与 QQ 绑定扩展

补充 [基础接入契约](https://github.com/Sonnety14/qqbot-plugin-electricity_bridge/blob/main/docs/INTEGRATION.md)。旧 bridge 版本不包含这些新接口；部署者必须升级桥接后端，不能只升级机器人。用户直接通过 QQ 绑定，不要求网站账号或网站 QQ 关联。参考实现见 [integration](../integration/README.md)。

所有接口 POST，前缀 `/comments/bridge/campus/electric/`，沿用 scoped Bearer 鉴权，仅可信私网。QQ 来源为平台事件，不接受用户正文指定收件人。

| 路径 | 请求 | 返回与处理 |
| --- | --- | --- |
| qq/start | `{qq}` | `{id, expires_in:600}`；按可信 QQ 事件中的 QQ 号取得或建立独立身份，检查 24 小时冷却，建立 source=qq、state=qq_waiting 任务 |
| qq/room | `{qq,id,room}` | 验证归属、auth_version、有效期与额度，解析房间进入 validating；返回 2xx JSON |
| qq/status | `{qq,id}` | `{state,snapshot?}`；只读自身任务，done 返回最终快照，失败 failed/expired |
| qq/alerts | `{qq}` | 原子切换已绑定 QQ 的通知偏好，返回 `{alerts_enabled:bool}`；默认开启，未绑定返回 409 |

`bindings` 返回每项的 `alerts_enabled`（布尔值或 0/1）；缺省按开启兼容。插件每轮同步更新偏好，发送前再次读取，防止旧缓存继续推送。关闭通知不停止查询，也不更改其他人的开关。

快照字段为 room、prepaid、subsidy、total、updated。金额为十进制字符串；updated 是北京时间查询时间。WXY 不提供分项金额，兼容值 prepaid=total、subsidy="0.00"，前端不应把它们描述为真实分项。

work 的 credentials 示例（合成数据）：

```json
{"provider":"wxy","selection":{"area":"天佑斋","building":1,"floor":2,"room_code":"2001","room":"天佑斋 012001"}}
```

学校账号、密码和共享 Cookie 不进入桥接任务。work/result/bindings/health 的 ID、租约、版本和 QQ 字段沿用基础契约；user_id 现在表示稳定的独立电费身份 ID，不表示用户必须有网站账号。验证 ready 后自动提交 queued；安装 done 后才生效。旧网站 room/start/upload/confirm 写入入口关闭并返回 410，网站端删除电费绑定按钮。

网站强制规则：

1. 内部接口要求 scoped Bearer 鉴权和可信私网部署；QQ 取自平台事件，不接受用户正文指定。绑定入口限流，校验 QQ 格式。
2. 每步核对独立身份、QQ、auth_version、任务及租约归属；网站账号解绑不影响电费绑定。
3. 不替换 validating/queued/processing 任务；输入期 10 分钟，客户端不可续期。
4. 房间支持区域及格式固定校验；鸿哲斋只允许 2、3 栋；上游结果还需独立匹配。
5. 每个独立 QQ 身份保存最后一次成功绑定时间，满 86400 秒后允许再次绑定；成功绑定、版本切换与冷却更新同事务，失败回滚，解绑不清空冷却。
6. 失败保留原绑定，QQ 等待超时不等于后端失败；使用 electric_qq_* 独立表保存正式绑定、任务、冷却和通知偏好。

迁移前做 SQLite 一致性备份。参考迁移保留旧内部 ID、版本、快照、最后成功时间及通知偏好，继续使用原加密文件和每日去重记录；旧表只作回退依据，不能在每次启动重新覆盖新数据。重复 QQ 身份会阻止迁移，须人工核对，不能静默合并。未完成的旧任务失效，用户重新发送“绑定宿舍”。

上线前在隔离数据库验证无需网站账号、跨 QQ 拒绝、任务/租约校验、24 小时边界、失败回滚、独立开关和迁移幂等，再验收真实 QQ 链路。
