# Zero 设计说明（题目一 1–5 问）

> ≤1 页。架构图见 [README](../README.md)，决策依据见 [decisions.md](decisions.md)（D0–D8）。
> 初期部署：单机 gunicorn + UvicornWorker 多进程、SQLite(WAL)。标注「设计」的能力不在 Q2 构建范围（Q2 = 可运行实现集）。

**1. 模块划分与执行流程**
模块与接口：HTTP API(FastAPI)；并发闸 TurnSlot(`claim`，终态隐式释放槽)；策略子系统 Config/Registry/Planner(`plan(PlanInput)→TurnPlan`)/ContextBuilder/Fallback；执行器 TurnRunner(`run_turn`+任务注册表)+ModelClient(`stream_chat`)+ToolRegistry；流式 DeltaChannel(`publish/subscribe`，offset 回放)；持久化 Repositories+SQLite（fenced 写）；恢复 Recovery(`reclaim_now`+心跳/周期 reaper)。**主干单向**：API→并发闸→策略→执行器→(模型/工具/流式/持久化)；查询路径 API→Repo 直读、不经执行器。**策略是纯规划器**（无 IO/时钟/随机/env），只产出 TurnPlan；执行器统一跑循环、流式、持久化。
一次请求依次经过五站：① 上下文构建（executor 装载候选）→ ② 策略选择（plan 在池内选上下文/指令/工具）→ ③ 模型与工具调用（模型↔工具循环）→ ④ 数据持久化（占位/部分/最终/审计）→ ⑤ 流式响应（增量→SSE）。流程：抢槽短事务(user msg+assistant 占位+seq)→解析 planned→plan→[异常回退 default]→模型↔工具→增量 SSE→刷部分/最终+审计→终态释放槽。

**2. 数据与状态管理**
权威源：**DB 为消息/轮次/审计/学习状态唯一权威**；材料原文权威、解析/索引为可重建派生；内存流缓冲仅瞬态。四类：①材料原文+派生分块索引（**Q2 无原文文件，预置片段以 DB 为权威**；对象存储/解析/向量为设计）②messages/turns ③learning_state（可变）/答题记录（append-only）④turn 审计（planned/actual、model_calls/tool_calls/turn_context，append-only）。
**完整历史≠模型上下文**：历史全量存 DB；每轮上下文由 ContextBuilder 选出（token 预算+条数兜底、轮原子、排除 partial/未完成轮）+材料（工具为主、预选≤3）+学习状态 digest，以**纯引用**写 turn_context（含工具取回 refs，覆盖模型实际所见）。**超窗**：Q2 旧轮不进入上下文、完整历史仍可查；滚动摘要(P2)与向量 top-k(设计)为演进，Q2 不实现。

**3. 并发与故障处理**
并发：每对话**整轮串行**，running 槽部分唯一索引；**先抢槽再落库 user msg**→seq 顺序=执行顺序→成对有序不交错(T3)；跨进程 WAL+busy_timeout 保证原子；等待 poll+超时 **409(conversation_busy, Retry-After)**。**幂等先于等待**：同 `client_msg_id` 先查已有 turn 直接返回，不排队（否则重试会等自己）。重试分两种：传输层（同 id→返回原 turn）与业务层（新 turn+`parent_turn_id`，禁终态复活）。
断线：**后台继续(A)**，断连不取消、跑完 completed；显式 cancel→`cancel_requested`→owner 中止→interrupted(user_cancel)；模型失败→failed+error_code。
**多进程限制（如实披露）**：生成在 owning worker 内存，跨 worker 重连无实时增量；幂等重试落到他 worker 走 **DB 轮询流**（2A 最小落地），实时推送演进 2B/2C→2E。
重启：**instance UUID+心跳新鲜度**回收 stale-owner running→interrupted（保留 partial），区分全量/滚动、不误杀兄弟，回收延迟≤心跳 TTL；drain 停接收后限时跑完，超出标 interrupted。回收时 **fence_token+1**，stale-owner 后续写入（终态/刷盘/审计）经条件写（`status='running' AND owner AND fence`）全部落空→**fencing 防复活**，执行器 0 行命中即中止。

**4. 策略扩展与实验管理**
接口：`plan(PlanInput)→TurnPlan`（instructions/context 引用/allowed_tools/plan_schema_version）；纯规划、不调模型不写库。新增策略=实现接口+注册一行(name→version→factory)+配置引用（默认/对话）+可选注册新工具；**不改存储/HTTP/执行器**。回退：仅 plan 构建阶段异常→default(explain-first)+fallback_reason 枚举；default 失败→failed+信号+保留 user msg；执行器失败不回退。回滚：旧版本保留注册，改配置指旧版本、无需发版（plan_schema_version 兼容校验）。**可检验行为做成工具**（ask_question/provide_explanation/judge_answer）：策略指令引导模型调用，行为成为结构化 tool_call 记录，评估检查断言数据库而非自由文本启发式（D8.4）。
分配：Config 模块决定并写入 conversations；Q2 = 按对话显式配置（**Assignment 模块仅设计 P2**）。稳定分组：`sha256(experiment_id:conversation_id) % 100` 分桶、创建时写库，同一对话恒同桶。**planned/actual 都落审计**；效果分析按 **actual_strategy** 分组并报告 fallback_rate，避免回退稀释对照臂。

**5. 删除与系统演进**
删除顺序：**先置 `deleted_at` + 写 cancel → 等终态 → 再 tombstone**；完成写入校验 `deleted_at`，已删则丢弃结果（防“删后复活”）。
删对话：tombstone 消息/审计引用（指向空、内容不可复现）；删话题：级联材料、派生索引、学习状态、答题记录、审计引用，并取消执行中轮次；材料擦除=KMS 销毁 DEK/KEK+消息 tombstone（信封加密，设计）。
兼容：API **只增字段、不改语义**（旧客户端忽略未知字段）；`schema_version` 启动顺序迁移、脚本入库；plan_schema_version/context_builder_version 版本化；存储经 Repo 接口 SQLite→PG 局部替换；seams 留接口+配置槽、不造投机实现。

**选答 · 50× 扩展**
瓶颈序：①模型 API 限流/成本 ②流式连接/单机并发 ③SQLite 单写者+单文件无法跨机 ④读负载。路径：服务无状态+LB（先换 PG 共享权威状态）→模型调用队列/限流/缓存→pgbouncer+读副本→按 conversation/user 分片→检索独立服务→生成任务持久队列（2E/3E 同时解跨 worker 扇出+孤儿，幂等强制）。
