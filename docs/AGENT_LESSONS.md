# Qball Agent 2.0:集 20 家之长的融合设计

> 调研日期 2026-09-16。原则:**只吸收对「本地、BYOK、小模型(deepseek-flash 级)、Windows、单用户」成立的机制**;
> 保持单 Agent 克制路线,多 Agent 只在进化/自动化边界使用。
> 调研对象:OpenCode / OpenHands / Cline / Aider / Goose / SWE-agent / mini-swe-agent / Continue /
> Gemini CLI / Qwen Code / Kimi CLI / Hermes Agent / Plandex / Open Interpreter /
> AutoGen / LangGraph / CrewAI / smolagents / MetaGPT / CAMEL

## 一、20 个项目速查(各自最值得 Qball 借鉴的一点)

| # | 项目 | 状态(2026-09) | 最值得借鉴的一点 |
|---|---|---|---|
| 1 | OpenCode | 极活跃 | 技能渐进披露 + 权限三态(allow/ask/deny,按命令 glob)+ 压缩做成独立小模型 agent |
| 2 | OpenHands | 极活跃 | 事件溯源:一切交互为追加式事件,状态=重放;区分"模型可自愈错误"与"运行时错误" |
| 3 | Cline | 极活跃 | Plan/Act 双模式(切换保留历史)+ 影子 git 检查点三态恢复 + 审批按类别分级 |
| 4 | Aider | 停更(机制经典) | 编辑格式按模型选档(whole→diff→udiff)+ 宽松补丁应用(关掉它错误涨 9 倍)+ 每次编辑自动 commit |
| 5 | Goose | 活跃 | Recipe(声明式任务模板 YAML)+ `retry` 块用**外部命令退出码**判成功,失败清空重跑 |
| 6 | SWE-agent | 维护模式 | ACI 设计:专用动作(非裸 shell)+ 编辑前 lint 不过则丢弃重试 + 浏览窗口/搜索硬上限 |
| 7 | mini-swe-agent | 极活跃 | 100 行循环 + 三重上限(step/cost/time)+ 连续格式错误 N 次即停 + 每步落盘轨迹 |
| 8 | Continue | 已冻结 | 模型角色分档(chat/autocomplete/edit/apply/embed…)+ 上下文 provider 声明式装配 |
| 9 | Gemini CLI | 消费级停服 | GEMINI.md 分层记忆(全局→项目→JIT)+ `/compress` 手动压缩 + 专用小模型做压缩/快照 |
| 10 | Qwen Code | 活跃 | BYOK 细节:envKey 不落盘、`streamIdleTimeoutMs`(慢速模型容忍)、fastModel 分档 |
| 11 | Kimi CLI | 新仓库活跃 | 工具输出硬上限(Read 1000 行/100KB、Grep 250)+ **写前 stale 检查** + 子代理递归白名单 |
| 12 | Hermes Agent | 极活跃 | 压缩工程顺序:先 flush 记忆→保最后 N 条→tool 对不拆→子会话留 lineage;可中断 API 调用 |
| 13 | Plandex | 活跃 | 累积 diff 沙箱(先审后 apply,失败回滚)+ 后台廉价小模型摘要兼作"工作记忆" |
| 14 | Open Interpreter | 活跃 | 审批=内容协议(confirmation 块,拒绝理由回灌模型)+ fail-closed 默认 + 输出硬截断 |
| 15 | AutoGen | 维护模式 | 终止条件做成可组合对象(`|`/`&`、有状态、可 reset),失败有兜底 |
| 16 | LangGraph | 极活跃 | checkpoint 两表结构 + interrupt/resume 协议 + pending writes(成功步恢复不重跑) |
| 17 | CrewAI | 极活跃 | Guardrail = {valid, feedback} + 有限重试;评审比再开一个自由对话 agent 便宜得多 |
| 18 | smolagents | 活跃 | "报错原文即观察"写回上下文让模型自修;final_answer 前跑校验函数 |
| 19 | MetaGPT | 低活跃 | cause_by + _watch 极简事件路由(每个角色只订阅自己关心的产物类型) |
| 20 | CAMEL | 活跃 | 固定"信封"约束小模型表达(Solution: 前缀、<TASK_DONE> 终止词),不许自由发挥 |

## 二、六条共性规律

1. **上下文工程是所有长期运行 Agent 的共同功课**:压缩(摘要替换旧消息)、截断(工具输出硬上限)、渐进披露(技能/文件按需加载)。裸截断(我们现在的 HISTORY_CHARS)是最差档。
2. **编辑协议按模型能力选档 + 宽松解析 + 失败反馈**:小模型写全文容易,改文件难;find/replace + "0 处/多处命中"的精确报错 + 重试,胜过换模型。
3. **计划/待办是防小模型跑偏的主要抓手**:Plan/Act、todo 列表、tell/chat 双模式——本质都是把"想清楚了再做"变成机制而非自觉。
4. **状态与恢复是一等公民**:事件溯源 / 检查点 / pending writes / 影子 git。崩溃、打断、回退都低成本。
5. **审批是内容协议**:结构化审批事件 + 拒绝理由回灌模型 + fail-closed 默认,而不是"UI 开关"。
6. **多 Agent 在收敛**:显式状态机 + 可组合终止 + 结构化产物;单机场景下,多 Agent 只在评估与自动化边界值得。

## 三、Qball Agent 2.0 升级清单

> ✅ **P0 五项已于 2026-09-16 全部落地**(fs.edit+防呆、上下文压缩、todo+计划模式、检查点撤销、项目规则),测试 250 断言全绿。

### P0 — 立即做(收益最大、改动可控)

1. **fs.edit 编辑工具 + 编辑协议链路**(来源:Aider / SWE-agent / Kimi)
   - find/replace,要求唯一匹配;失败回报"0 处命中/3 处命中 + 最近片段",模型可自修
   - **写前 stale 检查**:本会话未读过、或磁盘已被外部改动 → 拒绝并提示(防覆盖)
   - `fs.read` 加行数/字节上限 + 显式截断提示;`fs.list` 大目录截断
   - 审批卡片上直接渲染 diff 预览(UI 已有工具卡片骨架)
2. **上下文引擎升级**(来源:Plandex / Hermes / Gemini CLI)
   - 超过阈值自动压缩:用**廉价小模型**把旧消息摘要(摘要兼作"工作记忆":已做/待做/关键结论)
   - 保护规则:保留最后 N 条;tool call/result 成对不拆;笔记/规则类内容永不进摘要
   - 手动 `/compact` 命令 + 面板显示"上下文占用"
3. **待办与计划**(来源:Cline / OpenCode / Plandex)
   - `todo` 工具:模型可建/勾选待办;执行时把当前 todo 注入提示词(防走丢)
   - 计划模式:只读的"先出计划"档;计划以卡片呈现,用户点"开始执行"再进入 Act
4. **检查点与撤销**(来源:Cline 影子 git / LangGraph)
   - 每次工具批次前对工作区做快照(影子 git,独立于用户任何仓库)
   - UI:消息旁「撤销这次改动」(三态:仅文件 / 仅对话 / 全恢复)
5. **项目规则文件**(来源:Gemini CLI / Qwen / Cline / Continue)
   - 分层加载:全局 `~/.qball/QBALL.md` → 工作区 `QBALL.md`/`AGENTS.md` → 目录级
   - 与进化附加指令并存:规则=用户意图,附加指令=进化习得

### P1 — 下一批

6. **模型分档角色**(来源:Continue / Qwen fastModel / Gemini 压缩模型):对话 / 摘要 / 标题 / 记忆抽取各用各的模型,省钱提速
7. **审批增强**(来源:Open Interpreter / OpenHands):危险动作分级;拒绝时收集一句理由回灌模型
8. **会话管理**:显式 save/resume、`/compact [保留重点]`、会话导出 Markdown(来源:Gemini CLI / Goose / Kimi)
9. **预算护栏一等公民**(来源:mini-swe-agent):步数/工具调用数/成本三上限,UI 可见可调,超限优雅收尾

### P2 — 储备

10. **MCP 客户端**(来源:Goose / OpenCode / Kimi):接生态,工具数量级扩展
11. **子代理**(来源:OpenCode / Kimi):白名单递归保护 + "只回结论";适合深度调研类子任务
12. **事件溯源会话重构**(来源:OpenHands):会话=事件流,压缩/恢复/审计共用一套数据
13. **结构化输出契约**(来源:Goose json_schema):自动化任务要求 JSON 输出并外部校验

## 四、刻意不抄的(避免过度设计)

- SWE-agent 的专用 viewer 动作集 —— 我们已有 fs 工具,再造一套动作是倒退
- AutoGen 自由 GroupChat —— 单机场景不值;要终止条件只抄"可组合对象"这一点
- smolagents 全代码动作(Code-as-Action) —— Windows 沙箱成本高,收益不成比例
- MetaGPT / CAMEL 的角色扮演协作 —— 除非转型多 Agent 产品,否则是表演不是工程
- Plandex 的完整 plan 版本树 —— 我们抄"沙箱先审后 apply",不抄分支管理整套

## 五、与自进化引擎的关系

- 新机制立即成为进化的"新舞台":有了 fs.edit,进化任务库可加"改 bug 类"任务;有了 todo,可加"多步任务"评测
- 评审机制参考 CrewAI Guardrail({valid, feedback} + 有限重试):进化评分器可升级为"结构化反馈 + 定向重试"
- 硬上限参考 mini-swe-agent:进化执行也应显示步数/成本,超限优雅退出
- 体验建议通道(ux-ideas.md)继续运行;本清单的 P1/P2 可作为后续迭代池
