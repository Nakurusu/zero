# 策略对比表 — run-20261122T062825-af0b25

- seed_manifest_hash: `32f95818fc66a70ea89f57c24cf1afb8b35b8dc0e4b77fbc96ad50552391b654`
- model: `qwen3.8-flash` params: `{"model": "qwen3.8-flash", "temperature": 0.7, "max_tokens": 2048, "top_p": 0.9}`
- checks_version: 1 cases_version: 1

| 指标 | explain-first | practice-first |
|---|---|---|
| 轮次数 | 12 | 12 |
| completed/failed/interrupted | 12/0/0 | 12/0/0 |
| 回退轮次 | 0 | 0 |
| 输入 token 总/均 | 52525 / 4377.1 | 35363 / 2946.9 |
| 输出 token 总/均 | 12001 / 1000.1 | 6946 / 578.8 |
| 平均耗时 ms | 16673.8 | 10018.2 |
| 工具调用总/均 | 19 / 1.58 | 15 / 1.25 |
| 工具分布 | {"ask_question": 3, "judge_answer": 3, "provide_explanation": 9, "query_material": 4} | {"ask_question": 9, "judge_answer": 4, "provide_explanation": 2} |

## 行为检查通过率

| 检查项 | explain-first | practice-first |
|---|---|---|
| ask_question_uses_bank_id | 12/12 (100%) | 12/12 (100%) |
| explain_first_explains_before_question | 12/12 (100%) | — |
| only_allowed_tools_called | 12/12 (100%) | 12/12 (100%) |
| practice_first_opens_with_question | — | 4/4 (100%) |
| wrong_answer_followed_by_explanation | 11/12 (92%) | 11/12 (92%) |

## 观察

跨 run 汇总、失败归因与差异分析见 [../REPORT.md](../REPORT.md)。本 run 结构性检查全过；wrong_answer_followed_by_explanation 失败明细见 REPORT.md 第 3 节。
