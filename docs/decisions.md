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
| 2 | **D4 权威源分层** | D0 | D1 表设计、D3 锁基底、D8 审计存储、删除逻辑 | 讨论中 |
| 3 | **D1 轮次模型与状态机** | D4, D0 | D2 状态/部分态、D3 seq 与 running 槽、D6/D8 审计字段 | 待决策 |
| 4 | **D2 断线语义** | D1 | D3 槽位归属与持有期、D1 部分落库/interrupted、执行器任务模型 | 待决策 |
| 5 | **D3 并发串行化** | D0, D1, D2 | 消息有序/不丢不重、重试幂等机制 | 待决策 |
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

## 其余决策（按顺序展开，届时补充完整分析）

- **D4 权威源分层**（待决策）：DB 权威范围、材料原文/派生分层、Q2 预置片段存放。
- **D1 轮次模型与状态机**（待决策）：turns/messages/执行记录表、状态机、seq 分配、重启恢复。
- **D2 断线语义**（待决策）：后台继续 vs 取消；部分回复落库；重连视图。
- **D3 并发串行化**（待决策）：整轮串行抢槽机制、幂等键、等待/超时。
- **D-model 模型 API**（待决策）：供应商/模型、工具协议、流式格式、token 计量。
- **D5 策略职责边界**（待决策）：plan(PlanInput)->TurnPlan 契约、回退阶段。
- **D6 注册表+版本化配置**（待决策）：name→version→factory、配置切换/回滚、planned vs actual。
- **D7 上下文构建器**（待决策）：窗口/摘要/检索、上下文快照落库。
- **D8 审计 schema=评估数据源**（待决策）：模型/工具调用记录字段、run_id、检查项汇总。
