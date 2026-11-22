# 评估报告：讲解优先 vs 练习优先（两次完整运行）

## 1. 运行配置与状态隔离

- 模型：`qwen3.8-flash`，参数 `temperature=0.7, max_tokens=2048, top_p=0.9`（单一配置源 `agent.config`，两臂两跑完全一致）。
- 端点：`https://maas.qianwenaiapi.com/compatible-mode/v1`（OpenAI 兼容）。
- 种子：`seeds/seed.json`（seed_version=1），`seed_manifest_hash=32f95818fc66a70ea89f57c24cf1afb8b35b8dc0e4b77fbc96ad50552391b654`（两跑相同，证明同种子；manifest 覆盖材料 HMAC ID 集合+题库+初始数据+用例定义）。
- 用例：`eval/cases.json`（cases_version=1，4 组×3 轮，含答错/答对/概念追问）。
- 检查：`eval/checks.json`（checks_version=1，5 项自动检查，断言结构化 tool_calls）。
- 策略臂：explain-first@1（用户 u-eval-a）/ practice-first@1（用户 u-eval-b），预置 learning_state 完全相同。
- **隔离**：每 run 独立 DB 文件 + 独立 uvicorn 服务实例；run 内两臂独立用户、独立会话。两跑之间、两臂之间无共享可变状态。
- 逐轮记录直读审计表（turns/model_calls/tool_calls/turn_context），非旁路日志。

| Run | run_id | 轮次 | 状态 | 归档 |
|---|---|---|---|---|
| 1 | run-20261122T062209-397d0c | 24 | 24 completed | `runs/run-20261122T062209-397d0c/` |
| 2 | run-20261122T062825-af0b25 | 24 | 24 completed | `runs/run-20261122T062825-af0b25/` |

每个 run 目录含：`records.jsonl`（逐轮记录）、`check_results.json`（88 条检查）、`summary.json`（汇总）、`comparison.md`（该 run 对比表）、`run.db`（完整审计库，gitignore）。

## 2. 跨 run 策略对比（两跑合计，每臂 24 轮）

| 指标 | explain-first | practice-first |
|---|---|---|
| 输入 token 总/均 | 101580 / 4232.5 | 75907 / 3162.8 |
| 输出 token 总/均 | 24477 / 1019.9 | 15870 / 661.3 |
| 平均耗时 ms | 17156.0 | 11323.9 |
| 工具调用总/均 | 41 / 1.71 | 32 / 1.33 |
| provide_explanation | 18 | 6 |
| ask_question | 9 | 18 |
| judge_answer | 7 | 8 |
| query_material | 7 | 0 |
| 回退轮次 | 0 | 0 |

单 run 明细见各 run 目录 `comparison.md`。

## 3. 行为检查结果

| 检查项 | explain-first run1/run2 | practice-first run1/run2 |
|---|---|---|
| only_allowed_tools_called | 12/12 · 12/12 | 12/12 · 12/12 |
| ask_question_uses_bank_id | 12/12 · 12/12 | 12/12 · 12/12 |
| explain_first_explains_before_question | 12/12 · 12/12 | — |
| practice_first_opens_with_question | — | 4/4 · 4/4 |
| wrong_answer_followed_by_explanation | 12/12 · 11/12 | 11/12 · 11/12 |

结构性检查（工具授权、题库 ID、讲解先于出题、首轮出题）两跑两臂 **100% 通过**。`wrong_answer_followed_by_explanation` 合计 45/48（93.8%），3 条失败：

| run | 臂 | 用例/轮 | detail | 归因 |
|---|---|---|---|---|
| 1 | practice-first | case-3/t2 | wrong_seqs=[2] explain_seqs=[] | 用户仅确认"和应该是 5"（半答案），答案键精确匹配判错；模型以正文纠正未调 provide_explanation |
| 2 | explain-first | case-1/t1 | wrong_seqs=[2] explain_seqs=[] | 判错"49"后模型以正文讲解，未调行为工具 |
| 2 | practice-first | case-2/t1 | wrong_seqs=[2] explain_seqs=[] | 用户答"根是 2 和 3"被判错（措辞/题目错位），模型正文处理未调工具 |

注：无答错判定的轮次该检查为空真（detail 注明 vacuous），通过率按全部适用轮次计。

## 4. 观察到的差异

1. **资源开销**：explain-first 输入 token 比 practice-first 高约 34%、输出高约 54%、平均耗时高约 52%。讲解优先每轮先输出完整讲解再出题，上下文与生成都更重；练习优先以短问题驱动，开销更低。
2. **工具行为签名稳定**：practice-first 每用例首轮必出题（8/8），ask_question 总量约为 explain-first 的 2 倍；explain-first 的 provide_explanation 稳定在每跑 9 次，且同轮内恒先于出题。策略差异确实落在 plan 输出（指令+工具门控）上，公共执行器未变。
3. **材料查询分布**：explain-first 主动 query_material 7 次（讲解取材），practice-first 0 次（本轮用例下练习驱动无需主动查材料，预选片段已够用）。
4. **跑间非确定性**：同配置两跑的 token 总量、工具组合、检查通过数均不同（如 explain-first ask_question 6 vs 3；失败 1 vs 2 条），符合 temperature=0.7 的预期；E6 不要求结果一致，记录完整可追溯即达标。
5. **答错→讲解链路是唯一脆弱点**：失败均源于"模型用正文讲解而非行为工具"或"答案键精确匹配把语义正确的半答案/异措辞判错"。结构性约束（allowed_tools 门控）从未被突破。

## 5. 已知限制与演进

- **行为工具依赖模型遵循指令**（D8.4 已接受）：正文讲解不产生 tool_call，检查会记失败；这是结构化可检性与模型自由度的权衡。演进：LLM-as-judge 补充质量类检查（I3/P2）。
- **答案键精确匹配**（P1=A）：确定、可测，但对半答案/异措辞脆弱（case-3/t2 实例）。演进：更宽的 acceptable 键、按知识点拆分小问、或语义判定工具。
- 单模型单参数组、4 用例小样本：差异方向可信，幅度不宜过度外推。
- 空真检查计入通过率：detail 全程注明，可按需过滤重算。

## 6. 复跑

```bash
python eval/harness.py                 # 一次完整运行（新 run_id，独立 DB/服务）
python eval/harness.py --case case-1   # 小子集
python -m pytest tests -m real_api -k t5   # 重复性自动化验证（两跑）
```
