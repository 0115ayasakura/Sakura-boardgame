# 真实 Agent 控制接入

## 运行边界

默认决策驱动为 `agent`。插件通过 Sakura 公开的 `sakura.host.chat.current()` 和 `chat.submit({sessionId, message, resources: []})` 发起插件主动请求，让当前角色的完整 Agent 调用棋盘工具，而不是直接调用模型生成一段文本，也不伪造用户消息。

需要宿主提供 `sakura.host.chat` 能力和正常可用的角色、聊天会话、模型及工具调用。离线自动化测试使用假宿主验证协议，**不代表已经验证真实模型的走棋质量或正式安装的宿主兼容性**。部署与实机验证另行记录，不能用离线测试或算法演示替代实际工具提交证据。

## 使用方式

- 插件设置「决策驱动」选择 `agent` 时，网页的角色行动入口请求一次原子决策。Agent 必须先读取 `boardgame_agent_state`，再通过 `boardgame_agent_commit` 提交合法动作。
- 大富翁可能分为掷骰、选路、购买或事件选择等多次决策；五子棋必须明确选择行列，不允许省略坐标后让算法代算。
- 成功提交只有在返回 `result.continuation` 时才可用其中的新 token 在同一聊天里继续；每张票仍仅一次动作，最多串接8步，只处理角色当前的选路、选骰或事件等合法待选项，不跨到下一次全新掷骰、玩家操作、塔或终局。没有 continuation 就停止工具并统一简短收尾。
- `boardgame_agent_commit` 的外层 `action` 为动作类型；选菜单时固定为 `decide`，菜单值放在 `arguments.action`，例如 `{"action":"decide","arguments":{"action":"buy_draw"}}`（另须提供本次有效 token）。`choice` 不是别名，未知字段仍拒绝。掷骰、落子及塔步骤各自只接受其合法字段。
- 移动旁白区分骰点和已完成行走：`steps_left>0` 时仍在本次行走中；累计 `path` 可能含不耗步的传送或退回，不以路径节点数推算耗步。角色仍有 pending 时，不称已轮到玩家。
- 引擎仍唯一负责掷骰随机数、价格、收付、道具、合法走法和胜负。模型说“已经走了”不构成执行成功。
- 选择 `algorithm` 才启用原有本地算法。难度、棋风等算法参数不限制真实 Agent 的推理；它们不是模型能力等级。
- 会话忙碌时可排队等待空闲；宿主拒绝、调用失败或超时会明确显示，**不静默切换算法**。失败后需手动重试，避免轮询反复唤醒模型。
- `agent_fallback({id})` 仅在当前票据失败或过期时，由玩家明确点击后撤销旧票并算法完成当前动作；不会永久修改决策驱动。未知提交结果不能作为代操作入口。
- `pause_play` 持久保存 `playPaused` 并撤销旧票，暂停期间拒绝走棋请求；`resume_play` 恢复原状态，不直接走棋或派发聊天。旧票不能在恢复后继续提交，已有结果确认/返程流程仍保留。
- 隔离演示服务没有宿主 Agent，明确使用 `algorithm`，不能据此证明真实 Agent 通道已连通。

## 安全与异步行为

每次请求只产生一个短期 token，绑定对局、版本、角色、会话和角色棋子。网页只接收展示用任务 id，不接收 token。允许动作仅为掷骰、当前菜单选择、明确坐标落子和当前塔步骤；不允许 Agent 利用该通道开新局、悔棋、改设置或操作玩家座位。

宿主调用不持游戏锁；请求先完成外层事务，再启动后台派发。动作提交仍经过服务端短事务、引擎校验和存盘；失败不预扣资金、提前掷骰或消费道具。同 token 重复提交相同内容只返回原结果，不执行第二遍。

取消只撤销棋盘任务和后续提交权限，不撤销已提交棋步，也不承诺停止宿主正在生成的回复或语音。最新上游已导出 `chat.cancel`；本插件此次不调用它，避免取消共享会话中的其他活动。新局、状态版本改变、切换角色或会话、超时及卸载后，旧提交不能继续操作当前棋盘。任务和 token 不写入存档，插件重启后需重新请求。

Agent 正文可能没有调用工具，此时棋盘不移动，等待到期后显示超时。宿主受理请求并不等于动作完成；唯一执行依据是专用提交工具成功。

## 维护

发布必须包含完整插件目录，包括 `agent_turns.py` 和 `art/lib/agent_ui.js`，不要使用历史实验目录的固定文件清单覆盖部署。

回归入口：

```text
python -B boardgame_tests/run_tests.py --node <node可执行文件>
```

相关上游公开文档（基于仓库 main 核对，仍需以实际安装宿主能力为准）：

- [插件 Recipes：chat 主动请求](https://github.com/Rvosy/Sakura/blob/main/docs/development/plugin-recipes.md)
- [工具与 MCP 规范](https://github.com/Rvosy/Sakura/blob/main/docs/specs/tools-mcp.md)
- [会话与主动请求边界](https://github.com/Rvosy/Sakura/blob/main/docs/specs/conversation.md)
- [宿主服务实际注册与导出](https://github.com/Rvosy/Sakura/blob/main/app/core_host/plugin_runtime_application.py#L224-L229)
- [主动请求长度校验](https://github.com/Rvosy/Sakura/blob/main/app/core_host/chat_host.py#L136-L142)（唤醒提示只传短令牌，完整局面通过工具读取）
