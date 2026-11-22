# 策略对比表 — run-20261122T062209-397d0c

- seed_manifest_hash: `32f95818fc66a70ea89f57c24cf1afb8b35b8dc0e4b77fbc96ad50552391b654`
- model: `qwen3.8-flash` params: `{"model": "qwen3.8-flash", "temperature": 0.7, "max_tokens": 2048, "top_p": 0.9}`
- checks_version: 1 cases_version: 1

| 指标 | explain-first | practice-first |
|---|---|---|
| 轮次数 | 12 | 12 |
| completed/failed/interrupted | 12/0/0 | 12/0/0 |
| 回退轮次 | 0 | 0 |
| 输入 token 总/均 | 49055 / 4087.9 | 40544 / 3378.7 |
| 输出 token 总/均 | 12476 / 1039.7 | 8924 / 743.7 |
| 平均耗时 ms | 17638.2 | 12629.6 |
| 工具调用总/均 | 22 / 1.83 | 17 / 1.42 |
| 工具分布 | {"ask_question": 6, "judge_answer": 4, "provide_explanation": 9, "query_material": 3} | {"ask_question": 9, "judge_answer": 4, "provide_explanation": 4} |

## 行为检查通过率

| 检查项 | explain-first | practice-first |
|---|---|---|
| ask_question_uses_bank_id | 12/12 (100%) | 12/12 (100%) |
| explain_first_explains_before_question | 12/12 (100%) | — |
| only_allowed_tools_called | 12/12 (100%) | 12/12 (100%) |
| practice_first_opens_with_question | — | 4/4 (100%) |
| wrong_answer_followed_by_explanation | 12/12 (100%) | 11/12 (92%) |

## 观察

跨 run 汇总、失败归因与差异分析见 [../REPORT.md](../REPORT.md)。本 run 结构性检查全过；wrong_answer_followed_by_explanation 失败明细见 REPORT.md 第 3 节。
