# Zero 设计决策记录（Decision Log）

> 用途：记录题目一/题目二的关键设计决策，作为有效思考证据。
> 规则：
> 1. **用户主导**：助手提供候选方案与全面利弊分析，由用户拍板。
> 2. **先决策后动手**：所有关键决策完成后才进入实现。
> 3. **放弃必留因**：每个被放弃的候选方案必须写明清晰原因。
> 4. **上游优先**：会约束其他决策的决策优先讨论（见依赖排序）。
> 5. **可追溯**：每个决策标注影响的需求 ID（见 requirements.md）与下游决策。
> 6. **后果完备**：比较每个候选方案时，必须枚举该方案引发的**全部后果**（尤其部署/并发/故障/扩展后果），不只列通用利弊；共性后果与差异后果都要标明，不得只给推荐项的后果。
>
> 状态枚举：`待决策` / `讨论中` / `已决策` / `已实现验证`

---

## 一、决策依赖与优先级排序

排序原则：被多个下游依赖、且改变其可选方案的决策优先。

| 顺序 | 决策 | 上游依赖 | 约束的下游 | 状态 |
|---|---|---|---|---|
| 1 | **D0 技术栈与数据库**（语言/框架/DB/部署） | 无（最上游） | D3 并发原语、D2 恢复与流式承载、D1/D4 持久化接口、部署形态 | 已决策 |
| 2 | **D4 权威源分层** | D0 | D1 表设计、D3 锁基底、D8 审计存储、删除逻辑 | 已决策 |
| 3 | **D1 轮次模型与状态机** | D4, D0 | D2 状态/部分态、D3 seq 与 running 槽、D6/D8 审计字段 | 已决策 |
| 4 | **D2 断线语义** | D1 | D3 槽位归属与持有期、D1 部分落库/interrupted、执行器任务模型 | 已决策 |
| 5 | **D3 并发串行化** | D0, D1, D2 | 消息有序/不丢不重、重试幂等机制 | 讨论中 |
| 6 | **D-model 模型 API 供应商/模型** | 独立（集成层） | 执行器模型循环、工具协议(B7)、流式分块格式(A1)、token 计量(D8/E3) | 待决策 |
| 7 | **D5 策略职责边界（纯规划器）** | D1 | D6 注册内容、D7 调用方、执行器契约、回退阶段(B6) | 待决策 |
| 8 | **D6 注册表 + 版本化配置** | D5, D1 | 切换/回滚、planned vs actual 审计 | 待决策 |
| 9 | **D7 上下文构建器** | D5, D4 | 完整历史 vs 模型上下文分离、长对话/大材料处理(B8) | 待决策 |
| 10 | **D8 审计 schema = 评估数据源** | D1, D5, D6, 执行器 | 评估数据契约（token/耗时/工具次数/策略版本/检查项） | 待决策 |

### 依赖 rationale（为什么是这个顺序）

- **D0 最先**：数据库决定 D3 能用的并发原语（SQLite 部分唯一索引 vs PostgreSQL advisory lock），决定 D2 重启恢复/部分刷盘/跨进程扇出的实现方式；框架决定流式与后台任务的承载（async vs sync）。几乎全部实现决策受它约束。
- **D4 先于 D1**：先确立"DB 为消息/轮次/审计/学习状态唯一权威、材料原文权威+派生可重建"，才能定表结构与锁基底。原则性强、候选少，快速定。
- **D1 先于 D2/D3**：turn 实体与状态机是骨架，D2 的 interrupted/部分态、D3 的 seq 分配与 running 槽、D6/D8 的审计字段都挂在它上面。
- **D2 先于 D3**：断线后"后台继续"还是"取消"决定执行器任务模型，进而决定 D3 的槽位由谁持有、持有多久（后台任务持有 vs 请求持有）。顺序反了会返工。
- **D-model 独立**：不改变模块架构，但约束执行器实现细节；须在编码执行器前定，线性流程中放在并发之后、策略之前。
- **D5→D6→D7 簇**：策略边界决定注册什么、上下文构建器由谁调用；相对独立于 D2/D3 并发簇，可在其后集中讨论。
- **D8 最后**：审计 schema 是 D1/D5/D6/执行器的产物汇总，最后固化评估数据契约。

> 注：D5/D6/D7（策略簇）与 D2/D3（执行/并发簇）彼此弱耦合，必要时可并行讨论；本表给出线性主序。

---

## 二、单个决策的记录格式

```
## Dx 标题
状态：...
影响需求：A?/B?/...
上游：...  下游：...

### 背景与约束
（验收项、业务设定、工期/部署约束）

### 候选方案与利弊
方案 A：利 / 弊 / 适配性
方案 B：利 / 弊 / 适配性
...

### 选定方案
（用户拍板结果 + 日期）

### 放弃方案及原因
- 方案 B：原因……
- 方案 C：原因……

### 对下游的影响
（给后续决策的约束/接口）
```

---

## D0 技术栈与数据库

状态：已决策（2026-11-21）
影响需求：C4（多进程）、A1/A3（流式/持久化）、D2/D3 实现基底、H2/H8（评审可运行）
上游：无  下游：D1/D2/D3/D4

### 背景与约束
- 题目推荐 Python；初期**单机多进程**部署；T3 并发验收需两进程或两独立 DB 连接。
- 工期约 1 天实现；评审需能 clone 即跑（H8）；密钥走环境变量（H3）。
- 服务为 IO 密集（模型流式调用），非 CPU 密集。

### 候选方案与利弊

#### 语言
| 候选 | 利 | 弊 |
|---|---|---|
| **Python** | 作业推荐；模型 SDK 生态最全（openai/anthropic/dashscope）；asyncio 原生流式+后台任务；pydantic 校验；开发快 | GIL 下 CPU 密集弱（本服务 IO 密集，无影响）；多进程靠 uvicorn workers |
| Node/TS | 异步/流式天然 | 偏离推荐；模型 SDK 生态略逊；评审熟悉度成本 |
| Go | 并发强、单二进制部署 | AI SDK 生态弱、代码量大、1 天工期风险高 |

#### Web 框架
| 候选 | 利 | 弊 |
|---|---|---|
| **FastAPI** | 原生 async；StreamingResponse/SSE 友好；pydantic 校验；依赖注入利于策略/存储解耦；自动 OpenAPI 文档（评审友好） | 相对新但已成熟 |
| Flask | 简单熟悉 | 同步为主；流式+后台任务+并发需线程/异步 hacks；D2 后台继续方案实现别扭 |
| Django | ORM/admin 全 | 过重；异步流式不顺手；本题不需要全套 |
| aiohttp | 轻量 async | 结构化/校验/文档需自建，脚手架成本高于 FastAPI |

#### 数据库（关键轴，直接决定 D3/D2 实现）
| 候选 | 利 | 弊 | 对 D3/D2 的适配 |
|---|---|---|---|
| **SQLite(WAL)** | 零外部依赖、评审即跑；单文件易备份/重启测试；WAL 多进程读+串行写；支持部分唯一索引；事务原子 | 写并发全库串行（本题规模小无碍）；无 advisory lock；无 LISTEN/NOTIFY；跨机共享弱（初期单机 OK） | D3 用部分唯一索引 `(conversation_id) WHERE status='running'` 抢槽+轮询；D2 后台任务在接收进程内，他进程 GET 读 DB；重启扫 running 恢复 |
| **PostgreSQL** | advisory lock（会话级、崩溃自动释放）；MVCC 写并发强；LISTEN/NOTIFY 跨进程事件；JSONB 审计灵活；生产真实感；50× 演进自然 | 需额外服务（docker/brew）；评审环境+README 成本；连接池管理；工期分心 | D3 用 `pg_advisory_lock`（会话级、持锁专用连接，崩溃自动释放）；D2 恢复同理 |
| MySQL | 常见 | advisory lock 用 GET_LOCK 语义略异；相比 PG 无独特优势；同样需服务 | 与 PG 类似但无优势 |

#### 部署形态
| 候选 | 利 | 弊 |
|---|---|---|
| **uvicorn 多 worker（多进程）** | 符合题目"单机多进程"；共享 DB；T3 可用两进程验证 | 每进程独立事件循环，后台任务/流式扇出限本进程（跨进程靠 DB） |
| 单进程多 async | 简单 | 不符合多进程验收语境 |

### 推荐组合（供用户拍板，非最终）
Python + FastAPI + **SQLite(WAL)** + uvicorn 多 worker。
核心权衡轴：**SQLite（工期/评审友好）vs PostgreSQL（并发原语优雅/生产感）**——两者都能满足验收，差别在 D3 锁机制与部署成本。PostgreSQL 可作为题目一 50× 演进答案。

### 深入答疑：SQLite 写串行 vs advisory lock（思考证据）

**SQLite 写串行不会卡 T3**，前提：写事务短小、模型生成在事务外。
- 区分两种串行：SQLite 写锁=全库、仅写事务期间（毫秒）；每对话整轮串行=单对话、整轮生成（数秒），后者是 D3 设计意图（验收要求第二轮看到第一轮结果），与 DB 无关，PG 持锁整轮等待时长相同。
- 正确边界：短事务抢槽+落库用户消息+分配 seq → COMMIT 释放写锁 → 事务外流式生成（节流短事务刷部分）→ 短事务提交回复+completed 释放槽。
- 真正会卡的是设计错误：整轮包在一个事务里（全库写锁占数十秒）、未开 WAL、未设 busy_timeout。
- 真实差异仅在规模：SQLite 全库写串行限制跨对话写吞吐；PG MVCC 并行写。T3 仅 2 请求、初期小规模无碍，属 50× 演进点。

**advisory lock（会话级）优雅在 5 点**：①阻塞等待非轮询；②崩溃随连接断开自动释放（无需租约/清理）；③对话级隔离非全库；④持锁期间可做短事务、无需长事务（对比 FOR UPDATE 行锁需开长事务）；⑤无需额外互斥表。
- 代价：钉住一个池连接整轮（规模下池压力）；bigint key 可能碰撞（hashtextextended/双 int32 缓解）；归还连接前须确保 unlock；需 PG 服务。
- 净结论：两者都过 T3；advisory lock 红利主要在规模/省锁代码场景，本题边际红利有限；SQLite 抢槽额外代码（部分唯一索引+轮询）量不大，崩溃恢复反正要做。

### 部署方案完整后果分析（后果完备，按候选枚举）

后果检查清单（14 维，每个决策通用）：①进程/循环模型 ②跨 worker 实时扇出 ③停机/崩溃孤儿与恢复 ④SQLite 跨进程协调与写竞争 ⑤D3 抢槽跨进程正确性 ⑥进程监管/优雅重载/滚动重启/回收 ⑦心跳/超时误杀长轮 ⑧资源占用 ⑨T3 测试实现 ⑩评审/部署成本 ⑪水平扩展路径 ⑫sticky 路由 ⑬开发热重载 ⑭可观测性。

- **uvicorn --workers N**：②有（进程内任务/队列+内核分发连接，重连落任意 worker→GET 轮询）；③有（脱离任务不被在途请求 drain，超窗取消/崩溃孤儿→D1 恢复）；④WAL+busy_timeout 短写全库串行；⑤部分唯一索引跨进程原子；⑥监管基础；⑦异步 worker 长生成不阻塞心跳、无误杀；⑩零额外依赖最简；⑪SQLite 单文件锁死单机；⑫无 sticky；⑬reload 孤儿。
- **gunicorn + UvicornWorker**：①②③④⑤⑦⑧⑨⑪⑫⑭ 与 uvicorn **完全相同**；⑥更强（graceful_timeout/滚动重启/max_requests 回收）；③补充：max_requests 回收周期性引入孤儿；⑦补充：异步心跳不被长生成阻塞，不误杀（区别同步 worker）；⑩多一依赖。**净差异仅⑥监管。**
- **单进程 workers=1**：②**消失**（唯一进程，队列对所有重连可见）——唯一消除②的候选；③仍有；④无跨进程竞争；⑥无监管进程死=全停；⑧无法用多核；⑨T3 只能两独立连接、无法验证跨进程锁；**违反 C4 多进程→弃**。
- **多实例+nginx**：②③同 uvicorn；⑫**可 sticky**（ip_hash/cookie）部分缓解②（实例死亡失效）；⑥nginx 健康检查+LB，进程监管仍需 systemd；⑩单机运维过度。
- **docker N 副本**：②③同；④⑪**致命**：多副本共享 SQLite 不可靠（WAL 依赖同机共享内存，跨容器/网络 fs 锁不安全）→**实质强制 PG 或单副本**；⑩评审需 docker 成本最高。

**框架×部署交互（改变⑦）**：Flask + gunicorn 同步 worker → 长流式请求阻塞心跳 → **timeout 误杀长轮**（异步框架无此后果）；Flask 做 D2-A 还需退到线程，②③依旧。

**结论**：②③架构性，存在于所有多进程异步候选，换服务器消不掉；唯一消除②的单进程违反 C4；docker 多副本额外触发④⑪强制 PG；nginx 独有 sticky 部分缓解②但运维过度；uvicorn vs gunicorn 真实差异仅⑥监管；Flask 新增⑦误杀→支持异步框架。

### 选定方案（已决策 2026-11-21，用户确认）
- 语言：**Python**
- 框架：**FastAPI**（async + Depends 注入 + pydantic 校验 + 自动 OpenAPI）
- 数据库：**SQLite(WAL)**；PostgreSQL 作 50× 演进
- 部署：**gunicorn + UvicornWorker -w N**（N=2~4，监管成熟度；参数后果见上表）
- 演进 seams：采纳最小集（3 行为纪律 + Runner/DeltaChannel/Repo/TurnSlot/Recovery/ModelClient 六接口 + DI 配置槽），留槽不造投机实现。

### 放弃方案及原因（已定子项）
**数据库**
- PostgreSQL（初期放弃）：需额外服务、评审环境+README 成本、工期分心；advisory lock 红利在本题单机小规模边际有限。**保留为 50× 演进方案**（MVCC 并发写 + 网络共享状态使能水平扩展 + 读副本）。
- MySQL：相比 PG 无独特优势、同样需服务、GET_LOCK 语义略异。

**部署**
- uvicorn --workers：监管基础，无逐 worker 优雅重载/滚动重启/回收；用户重视监管成熟度故选 gunicorn。注明两者架构后果②③相同，gunicorn 仅⑥更强。
- 单进程 workers=1：违反 C4 多进程设定；虽唯一消除②但不可选。
- 多实例+nginx：单机运维过度；sticky 部分缓解②但实例死亡失效，不值。
- docker N 副本：评审需 docker 成本高；且多副本+SQLite 共享不安全（WAL 跨容器/网络 fs）实质强制 PG，与初期 SQLite 决策冲突。

**语言**
- Node/TS：偏离作业推荐；模型 SDK 生态略逊；评审熟悉度成本。
- Go：AI SDK 生态弱、代码量大、1 天工期风险高。

**框架**
- Flask：同步基因，async 视图每请求新建事件循环、无持久循环→D2-A 脱离任务无法跨请求存活；叠加 gunicorn 同步 worker 长流式阻塞心跳→timeout 误杀长轮（后果⑦）。结构性不匹配。
- Django：过重（ORM/admin/auth 大量用不上）；异步 ORM/流式不顺手；无原生 DI。
- aiohttp：持久循环可行但 DI/校验/文档全自建，脚手架成本高于 FastAPI。
- Starlette（裸）：同 FastAPI 底层但校验/DI/文档自建，无增益。
- Litestar：技术等价但生态/评审熟悉度逊，备选未选。
- Sanic/Quart：非主流增益，生态较小。

### 对下游的影响
- 若 SQLite：D3 采用部分唯一索引抢槽+轮询等待；D2 跨进程扇出用 DB 轮询/同进程订阅。
- 若 PostgreSQL：D3 可采用会话级 advisory lock；D2 可用 LISTEN/NOTIFY。
- 框架定为 FastAPI（async）后，D2 后台任务用 asyncio.Task 承载。

### gunicorn 监管参数后果（选 gunicorn 引入，实现时定参数）
| 参数 | 后果 | 建议默认+理由 |
|---|---|---|
| workers N | N 越大 SQLite 短写竞争略增+内存/FD 增 | N=2~4，≥2 满足多进程与 T3 |
| graceful_timeout(默认30s) | 长生成数十秒可能超窗→孤儿；调大让轮次跑完但拖慢重启 | 60~120s 折中；SIGKILL/崩溃仍孤儿 |
| max_requests(回收) | 回收=重启→杀死脱离生成任务→**周期性孤儿**，与 D2-A 冲突 | **关闭或很高+jitter**，避免误杀活跃轮次 |
| timeout(心跳) | 异步 worker 心跳不被长生成阻塞→长轮不误杀 | 保持默认勿调低 |
| preload_app | asyncio 循环/SQLite 连接不能跨 fork 共享，UvicornWorker 下易出问题 | **不 preload**，每 worker fork 后各自初始化 |
| 应用层 drain 钩子 | lifespan shutdown 停接收后 await 任务注册表(带 deadline)→减少优雅孤儿；服务器无关 | 实现（挂 D1/D2） |

不变：架构后果②③仍在，gunicorn 消不掉；D1 启动恢复强制。

### 50× 演进定位（PostgreSQL 角色，喂 I1 选答）
初期 SQLite 足够：长 IO 轮次+少量毫秒短写，单写者天花板需上千活跃对话才逼近；D3 每对话串行不增加全局竞争；WAL 并发读。
50× 瓶颈排序：①模型 API 并发/限流/成本（外部，PG 不解）②流式长连接/单机并发（fd/内存/loop/NIC）③SQLite 单写者 ceiling+单文件无法跨机共享 ④读负载 ⑤检索。
PG 真实角色（不止写吞吐）：MVCC 并发写去单写者天花板 + **网络化共享权威状态（水平扩展使能器）** + 读副本。PG 必要非充分。
分阶段：服务无状态化+LB（需先换 PG）→ 模型调用队列/限流/缓存 → pgbouncer+读副本 → 按 conversation/user 分片 → 检索独立服务 → 生成任务异步队列与 web 解耦。
设计红利：D3 抢槽机制 DB 无关，SQLite→PG 迁移不变量原样保留（可选 advisory lock 换阻塞等待），不 forcing 并发返工。

### 架构后果②③完善路径（喂 D1/D2/I1，后果完备）

**②跨 worker 扇出**（根因：生成任务+订阅队列在 owning worker 内存）：
- 2A DB 轮询：任意 worker resume-stream 端点轮询 partial+status 推增量。零设施/跨机；延迟=轮询间隔、读负载随并发流增、平滑度受刷盘粒度限。最便宜先做。
- 2B PG LISTEN/NOTIFY：刷部分后 NOTIFY(turn_id,offset)，他 worker LISTEN 回查推流。真推送/复用 PG；依赖 PG、payload≤8000B、LISTEN 钉连接、重连漏通知需 reconcile。迁 PG 时选。
- 2C Redis Streams：XADD 增量、XREAD 订阅。低延迟/解耦/可回放补漏；引 Redis、需回落 DB、保留内存。
- 2D sticky 路由：LB 路由回 owning worker。无 app 改动保平滑；仅 owner 活着有效、死亡/回收失效仍需 DB 回落、共享 socket gunicorn 难 sticky 需多实例+nginx。脆弱部分缓解。
- 2E 独立生成层+共享后端：生成挪任务层(Celery/arq/dramatiq+broker)，web 入队订阅。web/生成独立扩展、同时解③；最大改造、幂等/重试、Q2 过度。50× 终态。
- 阶梯 2A→2B/2C→2E；Q2 不需②完善（GET 满足 G6），记限制+演进。

**③停机/崩溃孤儿**（区分优雅停机可减 vs 崩溃只能检测恢复）：
- 3A drain 钩子：shutdown 停接收→asyncio.wait(任务注册表,deadline)跑完提交。消优雅孤儿/服务器无关；拖慢重启、超窗仍孤儿、不解崩溃、deadline<graceful_timeout。初期就做。
- 3B owner 感知启动恢复：只回收 owner 已死的 running（心跳/租约/pid 存活），标 failed/保留部分/释放槽。**多进程不能 naive 标所有 running（会杀兄弟活跃轮次）**。C3/T1 必需。
- 3C 租约+心跳+reaper：turn 带 owner+lease，心跳续租，原子回收过期。无需重启自动检测；心跳负载/TTL/防双收。3B 生产级。
- 3D 可续跑：孤儿从头重发模型(用持久化 TurnPlan/快照)或带前缀续写。UX 好；浪费 token/不确定、API 普遍不支持流中途续跑、需幂等。P2。
- 3E 持久任务队列：job 入 broker ack/重投，进程死红投。根本解③；至少一次→幂等强制、broker 运维。50× 终态(=2E)。

**关键前移**：用户选 gunicorn 滚动重启 → 3B owner 感知是**初期正确性必需**（否则滚动重启杀兄弟轮次），前移进 D1：turns 带 owner_worker+存活/租约字段，恢复 owner 感知。T1 整服务重启 naive 标记恰好安全，但滚动重启必须 owner 感知。初期简化 owner_pid+kill(pid,0)+start_time 防复用；生产用 3C。

**收敛**：2E=3E 同一动作（独立生成层+持久队列），50× 终态同时解②③。根因同源：生成任务寿命绑定 web 进程内存；外置任务寿命则两后果俱消，代价幂等+设施。

### 面向 2E/3E 移植的预留接口（evolvability seams，喂 G8/B1/D1/D2/D5/D8 与 Q1 模块图）

目标：把"生成任务寿命外置"移植做成局部实现替换而非重写。**光留接口不够**，需 3 条行为纪律 + 接口边界 + 配置接线。

**3 条行为纪律（现在必须遵守）**：
1. 执行器位置无关：executor=(turn_id,plan,deps) 纯执行单元，不依赖 request/FastAPI 上下文/进程内专属状态→可从 web 进程内任务原样搬到队列 worker。
2. TurnPlan/上下文可序列化：纯数据(pydantic/JSON)，不含活对象(连接/闭包/句柄)→队列 job payload 可序列化，兼喂 D8 回放与评估复现。
3. 写入幂等(turn_id 键)：助手消息按 turn_id upsert 非盲 INSERT；用户消息 UNIQUE(conversation_id,client_msg_id)→broker 至少一次红投不重复消息。

**接口边界（现在=接口+进程内实现，未来=换实现）**：
| Seam | 现在 | 未来 | 双重收益（现在就值） |
|---|---|---|---|
| TurnRunner/Scheduler | submit(turn_id,plan)->handle；InProcessRunner(asyncio.Task+注册表) | QueueRunner(broker) | handler 不直接 create_task，派发可换 |
| DeltaChannel | publish/subscribe(turn_id,from_offset)；InProcessChannel(asyncio.Queue) | RedisStream/PGNotify/DBPoll | executor 发增量/SSE 订阅走它，②完善换后端；offset 铺回放 |
| Repositories | Message/Turn/Audit/LearningState Repo；SqliteRepo | PgRepo | DB 迁移局部化；纯存储测试 fake repo(T1/T3) |
| TurnSlot/ConversationLock | acquire/release；部分唯一索引+轮询 | PG advisory lock | D3 机制 DB 无关，迁移不返工 |
| TurnRecovery | reclaim_orphans() owner 感知；启动扫描+pid/租约 | broker 红投+租约 reaper | 恢复可换；gunicorn 滚动重启本就必需 |
| ModelClient | stream chat+tool calls 接口；openai 兼容 | 换供应商/前置限流·缓存·队列 | D-model 可换；50× 瓶颈#1 限流缓存插此后 |
| 配置/DI 组合根 | config 选实现，FastAPI Depends 接线 | flip config 到 queue/redis/pg | 迁移=配置+新实现 |

**纪律与权衡（避免过度设计）**：只留接口+进程内实现+配置槽，**绝不现在写投机实现**（不引 broker/Redis/PG 依赖、不写队列 worker、不建通用插件框架）。大多数 seam 现在就回本（Repo→可测试、ModelClient→供应商切换、TurnSlot→D3 解耦、DeltaChannel→executor 本就要发增量、Runner→handler 本就要派发）；纯未来的仅配置槽 queue/redis/pg 模式——留槽不造实现。成本=少量间接/样板（turn_id 贯穿抵消）；收益=2E/3E 局部替换。
**最小集**：3 纪律 + Runner/DeltaChannel/Repo/TurnSlot/Recovery/ModelClient 六接口 + DI 配置槽；与 B4 策略扩展同源（接口+注册）。（用户已确认采纳，2026-11-21）
**映射**：接口即 Q1 架构图模块边界；G8 实现指引、B1 位置无关、C2/C3 幂等恢复、D8 可序列化快照、I1 演进落地。前移约束：D1 turns 带 owner+租约/写入幂等；D2 DeltaChannel offset；D5 TurnPlan 可序列化/executor 位置无关。

---

## D4 权威源分层

状态：已决策（2026-11-21）
影响需求：A3/B8/G2/G4/G5、C4、D8；上游 D0；下游 D1/D3/D7/D8

### 背景与约束
- Q1 Q2 要求四类数据各自权威源：①原始材料及解析/检索产物 ②对话消息与轮次状态 ③学习进度/答题记录 ④每轮上下文/策略版本/执行记录。
- C4：DB 为权威，进程内不持权威状态。完整历史 ≠ 模型上下文。Q2 预置材料片段、不做上传解析。
- seams 约束：Repo 接口（SQLite→PG）、上下文/plan 可序列化。

### 子决策 4.1 权威模型（已定 C + append-only 方向，scope 待确认）
| 候选 | 机制 | 利 | 弊/后果 |
|---|---|---|---|
| A 状态表权威 | messages/turns/learning_state 为当前态权威；audit 追加 | 读直接、简单、Q2 友好 | 历史变更需额外审计承载 |
| B 事件溯源 | 追加事件日志为权威，当前态=投影 | 可回放/审计强 | 投影/回放/一致性复杂，Q2 过度 |
| **C 混合（选定基础）** | 当前态用状态表；audit/答题记录追加只读；学习状态由事件更新 | 兼顾直读与可追溯 | 学习状态更新须与答题记录事务一致 |
- 用户追加约束：在 C 基础上**禁止 UPDATE/DELETE，只 append-only**（immutability 原则）。
- **真实张力**：与 D1 轮次状态机（status 是 UPDATE）、D3 running 槽（部分唯一索引依赖改 status）、部分回复刷盘（UPDATE content）冲突；no-delete 与擦除冲突（物理不删⇒擦除靠 crypto-shred+tombstone）。
- 调和：权威业务记录 append-only（turn_events/message_deltas/answer_events/audit/materials）；当前态=派生投影（最新事件/聚合，可重建非权威）；running-slot 锁=可变协调（非业务数据，豁免）；擦除=append tombstone+crypto-shred。
- **实质漂向 B（事件溯源）**：权威=append-only 日志，状态表降为派生投影；代价=投影/派生逻辑+更多行+复杂度↑。
- **scope 已定：乙（分层），2026-11-21**。 framing：这不是"为工期妥协"，而是**界定 append-only 的适用范围**——append-only 适用于天然不可变记录（audit/materials/answer_events）；messages/turns/learning_state 用可变状态表（C 原样，D1/D3 简单）；全量 append-only 作演进。
- 甲（Q2 全量 append-only/事件溯源）已在 4.1 因投影复杂度对 Q2 过度被否；再选甲等于把否掉的方案请回来，决策不一致→维持否。
- 乙后果：D1 轮次 status 可 UPDATE、D3 running 槽部分唯一索引按原设计成立、部分回复可 UPDATE 刷盘；擦除对可变状态表可物理删（Q2），材料/审计走 crypto-shred+tombstone。

### 子决策 4.2 四类数据权威与存放（推荐）
| 数据类 | 权威 | 派生/瞬态 | Q2 落地 | 设计演进 |
|---|---|---|---|---|
| 原始材料 | 原文文件（磁盘/对象存储） | — | 预置片段存 DB（material_snippets）为权威 | 对象存储 |
| 解析/检索产物 | 派生（可由原文重建） | 分块/嵌入/索引 | 无解析，预置片段 | chunk+向量索引流水线 |
| 消息+轮次状态 | DB（messages/turns） | 内存流缓冲瞬态（节流刷盘） | DB | PG |
| 学习进度/答题 | DB（learning_state/answer_records） | — | DB 预置+可选更新 | 事件投影 |
| 每轮上下文/策略/执行 | DB（turn 审计） | — | plan 引用+快照、planned/actual、model/tool calls | — |

### 子决策 4.3 上下文记录方式（已定 B：纯引用，2026-11-21）
| 候选 | 利 | 弊/后果 |
|---|---|---|
| A 全量渲染快照 | 精确可复现 | 体积大；**删除后仍保留被删内容→违反擦除** |
| **B 引用（选定）**：message_ids+snippet_ids+plan_version+窗口参数 | 紧凑；**删除后引用解析为空/tombstone，内容不可复现→符合擦除权** | 删除后该轮上下文不再可复现（期望行为） |
| C 混合（引用+hash+关键 inline） | 删除后关键内容存活 | **inline 保留用户明确要求删除的内容→违反擦除语义，弃** |
- 用户裁决：删除就应让内容指向为空，用户明确要求删除的内容不应可复现→选 B，弃 A/C。
- 机制：audit 只存引用 ID+metadata（策略版本/token/检查）；删除→源内容移除→引用解析为空/tombstone（优雅处理不报错）。
- 后果（完备）：删除后上下文不可复现（复现性让位擦除权）；评估用活数据不受影响；需优雅处理悬空引用。喂 G5/D1/D8。

### 子决策 4.4 Q2 预置材料存放（已定 C + 内容 hash insert-or-ignore + seed_manifest_hash，2026-11-21）
| 候选 | 利 | 弊 |
|---|---|---|
| A DB rows | SQL 工具可查、事务、Repo 一致 | 种子需脚本 |
| B JSON 文件 | 版本化固定用例简单 | 工具读文件不走 Repo、无 SQL 查询 |
| **C JSON 种子→初始化载入 DB（选定）** | 版本化 fixture（E1/E2 复现）+ DB 运行时查询 | 初始化载入步骤 |
- 用户细化：按**内容 hash 做 insert-or-ignore**（非 update，与 4.1 append-only 一致）→重复跑不插重复行（E6 幂等 seeding）。
- **seed_manifest_hash 记元数据表**：E2 控制变量凭据，证明两跑同种子。manifest=预置材料 HMAC ID 集合+用例定义+初始数据版本，规范化排序+canonical 序列化后哈希（确定性）；seed 用固定 HMAC key 保证 ID/hash 跨跑稳定；模型/参数/策略版本另按 run 记录。

### 子决策 4.5 材料存储：内容寻址 + 不可变桶 + 信封加密（已定 (a)，2026-11-21）
- 用户决策：内容寻址 hash 为 key + 不可变桶；调和取 **(a) 加密粉碎**，细化为：
  - **per-user KEK + per-object DEK（信封加密）**：DEK 加密对象，KEK 包裹 DEK，KEK 在 KMS。
  - **内容 ID 用 HMAC（per-user key）而非裸 SHA**：防跨用户指纹关联/存在性泄露；去重退化为用户内。
  - **删除 = 销毁 KEK + 消息侧 tombstone**（存储层密文不可读 + 应用层引用返回空，双层擦除）。
- CAS 价值：更新生成新 HMAC ID、旧对象不变→历史引用恒指向当时确切字节；用户内去重；防篡改。
- 不可变桶价值：WORM/object-lock 对象不被改写/删除→引用恒可解析。
- **必须写明的后果**：
  - KMS 在每次读关键路径（unwrap DEK+解密）→缓存 unwrapped DEK（短 TTL）。
  - 缓存 vs 擦除即时性：缓存 DEK 在 KEK 销毁后仍可解密至 TTL→**短 TTL + 擦除时主动 purge 该用户缓存 DEK**。
  - KMS 降级 **fail-closed**：未缓存读拒绝/工具返回 unavailable，轮次优雅降级；**不 fail-open**（过期密钥会解密已擦除内容）。
  - **KEK 粒度 vs G5 granular 删除**：销毁 KEK=整用户擦除；G5 单对话/话题删除=**销毁相关对象 DEK**（per-object 粒度）；KEK 仅整用户。
  - 共享材料：材料属话题可被多对话引用；删对话=tombstone 其消息/审计引用，不动共享话题材料；删话题才销毁话题材料 DEK。
  - HMAC key per-user，擦除时一并销毁；seed 用固定 key。
  - 不可逆 guardrail：销毁需授权+审计；误销毁=永久损失（宽限期 vs 即时擦除权衡）。
- Q2 简化：预置片段存 DB、ID=HMAC 内容 ID、append-only insert-or-ignore、可物理删；不引真实 KMS/对象存储；信封加密+不可变桶+加密粉碎是迁对象存储的生产设计。
- 检索：初期关键词查预置片段；演进向量索引。
- **已确认（2026-11-21）**：KEK 粒度调和（对话/话题删除用 per-object DEK 销毁、KEK 仅整用户）+ KMS 降级 fail-closed + 擦除缓存失效。用户补两前提：
  - **前提 A：wrapped DEK 存独立可删 key store，不内联进不可变对象元数据**（否则对象不可变连 DEK 锁死、备份删不净）。不可变桶只存密文。
    - critical 补充：整用户擦除（毁 KEK）备份天然无害；**granular 擦除（删 wrapped DEK 记录、KEK 仍活）若 key store 备份保留 wrapped DEK 则可恢复→不干净**。故 DEK 也做 **KMS 托管密钥**，granular 擦除=KMS 调度销毁 DEK，备份失效；KEK/DEK 统一 KMS 销毁机制。代价=per-object KMS 密钥数/成本/配额。（或要求 key store 备份可 purge，运维更难。）
  - **前提 B：擦除缓存失效全集群生效，用 per-user key_epoch 自增让旧缓存自然失效，不只清本进程**。
    - 机制：缓存条目带 (DEK, epoch_at_cache)；读时比对共享存储当前 epoch，不一致→失效→重取（已毁则失败）→fail-closed。epoch 在共享权威存储单调自增，全集群可见。状态式收敛优于 pub/sub 广播（广播会漏）。
    - 后果：擦除延迟由 epoch 检查新鲜度界定（建议读穿透/极短 TTL）；per-user epoch 偏粗（granular 擦除过度失效该用户全部缓存，安全但性能代价，可 per-conversation epoch 细化）；epoch 自增原子+持久，先增 epoch+毁密钥再确认擦除；读路径多一次共享 epoch 读（比 KMS 便宜）；50× 多节点同样适用。

### 推荐汇总
混合权威（状态表+追加审计）；DB 为对话/状态/审计权威；原文权威+派生可重建；上下文=纯引用（删除指向空，4.3=B）；材料=内容寻址 hash+不可变桶（4.5，调和待定）；Q2 预置=JSON 种子→DB；演进=对象存储+向量索引。

### 选定方案（已决策 2026-11-21）
- 4.1 权威模型：**C 混合 + append-only 分层（乙）**——audit/materials/answer_events append-only；messages/turns/learning_state 可变状态表；全量 append-only 作演进。界定适用范围，非工期妥协。
- 4.2 分层：DB 为对话/状态/审计权威；原文权威+派生可重建（采纳）。
- 4.3 上下文记录：**B 纯引用**（删除指向空/tombstone、内容不可复现）。
- 4.4 Q2 预置：**C JSON 种子→DB + 内容 hash insert-or-ignore + seed_manifest_hash**（E2 凭据）。
- 4.5 材料存储：**内容寻址 HMAC + 不可变桶 + (a) 信封加密粉碎**（per-user KEK/per-object DEK 均 KMS 托管；删除=KMS 销毁 KEK/DEK+消息 tombstone；wrapped DEK 独立可删 key store；per-user key_epoch 全集群缓存失效；fail-closed 降级）。

### 放弃方案及原因
- 4.1 甲（全量 append-only/事件溯源）：投影/派生复杂度对 Q2 过度→否；选乙是界定 append-only 适用范围，非妥协，维持对甲的否。
- 4.3 A 全量快照 / C 混合 inline：删除后仍保留用户明确要求删除的内容，违反擦除语义/隐私→弃。选 B 纯引用。
- 4.5 (b) governance：擦除依赖特权流程、治理复杂→弃；(c) 版本化+删除标记：版本保留与擦除平衡复杂、真擦除仍需特权 purge→弃。选 (a) 信封加密粉碎（不可变+擦除两全）。
- 4.5 裸 SHA 内容 ID：跨用户指纹关联/存在性泄露→弃，改 per-user HMAC。
- 4.5 DEK 内联不可变元数据：对象不可变锁死 DEK、备份删不净→弃，独立可删 key store + KMS 托管。
- 4.5 仅清本进程缓存：多 worker/节点擦除不即时→弃，per-user key_epoch 全集群收敛失效。

### 对下游的影响
- D1：状态表 schema（messages/turns/learning_state/answer_records/audit）+ owner/租约字段。
- D3：锁基底=DB 状态表。
- D7：上下文构建器输出引用+快照（4.3）。
- D8：审计=追加只读记录，评估直读。

---

## D1 轮次模型与状态机

状态：已决策（2026-11-21）
影响需求：A5/A6/A2/A3、C1/C3、T1/T3；上游 D4(乙)/D0；下游 D2/D3/D6/D8

### 背景与约束
- D4 乙：messages/turns/learning_state 可变状态表（status 可 UPDATE、部分可刷盘）；audit/materials/answer_events append-only。
- D0：SQLite WAL + gunicorn 多 worker；seams：owner/租约、写入幂等、plan 可序列化。
- ③ owner 感知恢复（gunicorn 滚动重启必需）；A5 轮次一等实体；A2 历史确定顺序+轮次状态。

### 子决策 1.1 实体模型（推荐）
- conversations(id, topic_id, user_id, strategy_name/version, created_at)
- turns(id, conversation_id, turn_seq, status, planned_strategy/version, actual_strategy/version, fallback_reason, owner_worker, lease_expires_at, client_msg_id, parent_turn_id, error_code, interrupt_reason, started_at, finished_at)
- messages(id, conversation_id, turn_id, role, content, msg_seq, is_partial, created_at)
- model_calls / tool_calls / turn_audit（append-only，D8 细化字段）
- learning_state（可变）/ answer_events（append-only）
- material_snippets(id=HMAC, topic_id, content)（append-only insert-or-ignore）
- metadata(seed_manifest_hash, schema_version)
- worker_liveness(worker_id, pid, start_time, last_heartbeat) + running-slot 协调（D3）

### 子决策 1.2 轮次状态机（已定 2026-11-21）
- Q2 实际流转：**running → completed / failed / interrupted**（三者皆终态）。
- schema CHECK 保留 **pending**，注释"预留给 2E 入队未领取"，**Q2 不写入 pending**（抢槽即 running）。
- **interrupted vs failed 是语义不是技术**：failed 带 error_code（模型报错、工具超时）；interrupted 带 reason（drain 超时、用户取消、进程回收）。UI 文案不同→分开。
- **只允许 running → 终态，禁止终态复活**。重试 = 新建 turn 带 parent_turn_id，不在原行改。

### 子决策 1.3 消息顺序与 seq（推荐，D3 细化）
- 每对话单调 turn_seq + msg_seq；抢槽后分配，用户消息与回复相邻→成对不交错（T3）。
- GET 按 msg_seq 排序，带 turn_id+status（A2）。

### 子决策 1.4 部分回复持久化（已定 A + 占位行，2026-11-21）
- 选 **A UPDATE-in-place**（乙可变状态表）。
- **关键：抢槽同一事务内就建 assistant 消息行（空内容）占住 msg_seq**。否则"生成完才插 assistant 行"→中断时库里只有用户消息无回复行→T3 成对有序在失败场景断。占位后中断该行仍在、内容停半截、seq 关系完好。
- 配套三件事：
  1. messages 加 **is_partial**（或 message_status）：生成中 true，最终写完 false。
  2. **上下文构建必须排除 partial 行**（否则下一轮把半截话当历史喂模型——最常见事故）。
  3. GET 可返回 partial 并打标；**审计只记最终版**，半成品不入 append-only 表。

### 子决策 1.5 owner/租约与恢复（已定 甲，2026-11-21）
- 选 **甲 lease/heartbeat**：turns.owner_worker + lease_expires_at；worker_liveness 心跳续租。
- 启动恢复：扫 running，**租约过期/owner 已死**→标 interrupted(reason=process_restart) 或 failed、保留部分（is_partial 行）、释放槽；owner 活着不动（滚动重启不误杀兄弟）。
- 乙 pid 简化弃（pid 复用风险、跨机不适用）。

### 子决策 1.6 幂等（推荐）
- UNIQUE(conversation_id, client_msg_id) 去重（C2）；assistant 按 turn_id upsert（seam 纪律 3）。

### 选定方案（已决策 2026-11-21）
- 1.1 实体模型：turns/messages/audit/learning_state/answer_events/material_snippets/metadata/worker_liveness（messages 加 is_partial；turns 加 parent_turn_id/error_code/interrupt_reason）。
- 1.2 状态机：running→completed/failed/interrupted；CHECK 预留 pending（2E，Q2 不写）；interrupted(reason) vs failed(error_code) 语义分开；禁终态复活，重试=新 turn+parent_turn_id。
- 1.3 seq：抢槽后分配，用户/回复相邻成对（T3）。
- 1.4 部分回复：A UPDATE-in-place + 抢槽同事务建 assistant 占位行；is_partial 标记；上下文排除 partial；GET 可返 partial 打标、审计只记最终。
- 1.5 owner：甲 lease/heartbeat，owner 感知恢复。
- 1.6 幂等：UNIQUE(conversation_id,client_msg_id)；assistant 按 turn_id upsert。

### 放弃方案及原因
- 1.4 B append deltas：乙已可变状态表，UPDATE 更简；部分回放非 Q2 必需→弃（append-only 审计另记最终）。
- 1.4 生成完才插 assistant 行：中断时缺回复行→T3 失败场景成对有序断→弃，改抢槽同事务占位。
- 1.5 乙 pid 简化：pid 复用风险、跨机不适用→弃，选 lease/heartbeat。
- 1.2 终态复活/原行重试：破坏状态机不可逆与审计→弃，重试=新 turn+parent_turn_id。
- 1.2 Q2 写入 pending：Q2 抢槽即 running，pending 仅 2E 入队语义→CHECK 预留但不写。

### 对下游的影响
- D2：interrupted 态语义、部分落库方式（1.4）。
- D3：running-slot 抢槽 + seq 分配（1.3）。
- D8：model_calls/tool_calls/audit 字段（1.1）。

---

## D2 断线语义

状态：已决策（2026-11-21）
影响需求：A1/A4、T2、G6；上游 D1；下游 D3（槽位归属与持有期）

### 背景与约束
- D1：assistant 占位行 + is_partial + UPDATE 刷盘；interrupted(reason=drain 超时/用户取消/进程回收)；禁终态复活。
- seams：TurnRunner（detached task）、DeltaChannel（publish/subscribe offset）。
- ②跨 worker 扇出受限（进程内队列）；③孤儿恢复（D1 lease）。

### 子决策 2.1 断连行为（已定 A，2026-11-21）
| 候选 | 机制 | 利 | 弊/后果 |
|---|---|---|---|
| **A 后台继续（推荐）** | 生成=detached task，SSE=订阅者；断连取消订阅不取消任务；跑完落库 completed | 贴合关页面重开+多端；T2 断言干净（completed+完整）；不浪费 token | 需任务注册表+drain+孤儿恢复（D1 已备） |
| B 断连即取消 | 断连中止生成，标 interrupted(reason=user_disconnect)，保留部分 | 实现略简、省后续 token | 多端一端断连杀另一端观看；浪费已生成；需区分断连 vs 显式取消 |
- **已定 A**：断连不取消生成；**实际取消需显式取消端点触发**（见 2.6）。与 D1 一致（interrupted 仅 drain/显式取消/回收）。

### 子决策 2.2 部分刷盘（D1 约束）
- 节流 UPDATE assistant 占位行 content，is_partial=true；间隔~500ms 或每 N chunk。
- 完成：content 最终 + is_partial=false + turn completed（同事务）。

### 子决策 2.3 重连视图（D1/②约束）
- GET 历史：running 返回 partial（is_partial 打标）；completed 返回完整。
- 可选 resume-stream 端点（P2，②跨 worker 用 DB 轮询）；Q2 重连=GET。

### 子决策 2.4 完成/失败信号（A1）
- SSE 终止事件：done（completed）/ error（failed+error_code）/ interrupted（+reason）。

### 子决策 2.5 interrupted 触发（D1 已定）
- drain 超时、用户显式取消、进程回收/重启恢复。**断连不触发（A）**。

### 子决策 2.6 显式取消端点（已定，2026-11-21）
- 端点：POST /conversations/{id}/turns/{turn_id}/cancel（仅 running 有效）。
- 机制（跨 worker，②）：取消请求可能落到非 owning worker→**DB 写 cancel_requested 标志**；owning worker 生成循环每 chunk 间检查标志→中止→标 interrupted(reason=user_cancel)、保留 partial、释放槽。DB 中介取消，跨 worker 成立。
- 后果：取消非即时（延迟=检查间隔）；与完成竞态→若已终态则 no-op 返回当前状态（禁终态复活 D1）；幂等；释放槽供下一轮。
- Q2：实现该端点以支撑 interrupted(user_cancel)；T2 测断连（A 下→completed），取消端点另测。

### 选定方案（已决策 2026-11-21）
- 2.1 A 后台继续：断连取消订阅不取消任务，跑完 completed。
- 2.2 节流 UPDATE 占位行（is_partial），完成同事务置 false+completed。
- 2.3 重连=GET（partial 打标/完整）；resume-stream P2。
- 2.4 SSE 终止事件 done/error/interrupted。
- 2.5 interrupted 触发=drain 超时/显式取消/进程回收；断连不触发。
- 2.6 显式取消端点→DB cancel_requested→owner 中止→interrupted(user_cancel)。

### 放弃方案及原因
- 2.1 B 断连即取消：多端一端断连杀另一端观看、浪费已生成、需区分断连 vs 显式取消→弃。选 A 后台继续 + 显式取消端点。

### 对下游的影响
- D3：running 槽由 detached task 持有整轮（非 HTTP 请求），断连不释放槽。

---

## 其余决策（按顺序展开，届时补充完整分析）
- **D3 并发串行化**（待决策）：整轮串行抢槽机制、幂等键、等待/超时。
- **D-model 模型 API**（待决策）：供应商/模型、工具协议、流式格式、token 计量。
- **D5 策略职责边界**（待决策）：plan(PlanInput)->TurnPlan 契约、回退阶段。
- **D6 注册表+版本化配置**（待决策）：name→version→factory、配置切换/回滚、planned vs actual。
- **D7 上下文构建器**（待决策）：窗口/摘要/检索、上下文快照落库。
- **D8 审计 schema=评估数据源**（待决策）：模型/工具调用记录字段、run_id、检查项汇总。
