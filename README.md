# Zero

## 架构图

```mermaid
flowchart TB
    Client["客户端"]

    subgraph S1["① 上下文构建"]
        direction LR
        LOAD["装载候选（executor）<br/>会话历史 / 学习状态 / 材料候选"]
        POOL["候选池"]
        LOAD --> POOL
    end

    subgraph S2["② 策略选择"]
        direction LR
        PLAN["strategy.plan<br/>池内选择与组装"]
        FB{"plan 异常?"}
        DEF["回退 default（explain-first）"]
        PLAN --> FB
        FB -->|是| DEF
    end

    subgraph S3["③ 模型与工具调用"]
        direction LR
        MODEL["模型调用<br/>qwen3.8-max 流式"]
        TOOL["工具执行"]
        MODEL -->|tool_call| TOOL
        TOOL -->|结果回填| MODEL
    end

    subgraph S5["⑤ 流式响应"]
        direction LR
        CH["增量通道<br/>publish / subscribe"]
        CH --> SSE["SSE 推送"]
    end

    subgraph S4["④ 数据持久化"]
        DB[("SQLite WAL")]
    end

    Client -->|HTTP| LOAD
    POOL -->|TurnPlan| PLAN
    FB -->|TurnPlan| MODEL
    DEF -->|TurnPlan| MODEL
    MODEL -->|增量| CH
    SSE -->|SSE| Client
    LOAD -. 读 .-> DB
    MODEL -. 写 .-> DB
```

**图注**

- **表结构**：messages / turns / model_calls / tool_calls / turn_context / learning_state / materials / worker_liveness / eval_runs 等表定义见 `docs/decisions.md`（D1/D8）。
- **取消**：显式 cancel 端点写 cancel_requested，owner 轮询中止 → interrupted(user_cancel)；断连不取消、后台继续至 completed。
- **恢复**：instance UUID + 心跳新鲜度回收 stale-owner 的 running 轮次 → interrupted，区分全量与滚动重启、不误杀兄弟进程。
- **评估**：eval_runs 以 run_id + seed_manifest_hash 标记两次运行，直读审计生成逐轮记录与策略对比表。
- **并发**：每对话至多一个 running（部分唯一索引）；同 client_msg_id 幂等去重，重试返回已有轮次。
