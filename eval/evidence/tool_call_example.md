# 真实工具调用运行示例（B7）

- 捕获时间：2026-11-22T06:05:08+00:00
- 模型：`qwen3.8-flash`，参数：`{"model": "qwen3.8-flash", "temperature": 0.7, "max_tokens": 2048, "top_p": 0.9}`
- 端点：`https://maas.qianwenaiapi.com/compatible-mode/v1`
- 策略：explain-first@1（planned=explain-first@1，fallback=None）
- 用户消息：我想学习韦达定理。请先用 query_material 查询材料中关于韦达定理的内容，再讲解并出一道题。

## 执行轨迹（call_seq 跨模型/工具统一单调）

| call_seq | 类型 | 名称/模型 | 关键信息 |
|---|---|---|---|
| 1 | model | qwen3.8-flash | input=1193 output=76 tokens, 1458ms |
| 2 | tool | query_material | args={"query": "韦达定理 两根之和 两根之积"} allowed=True |
| 3 | model | qwen3.8-flash | input=1387 output=49 tokens, 885ms |
| 4 | tool | provide_explanation | args={"knowledge_point": "韦达定理（根与系数的关系）"} allowed=True |
| 5 | model | qwen3.8-flash | input=1465 output=429 tokens, 6069ms |
| 6 | tool | ask_question | args={"question_id": "q-vieta-01", "question_text": "方程 x²−5x+6=0 的两根之和与两根之积分别是多少？（提示：直接用韦达定理，不解方程）"} allowed=True |
| 7 | model | qwen3.8-flash | input=1942 output=46 tokens, 1251ms |

模型发起工具调用 → 执行器回填工具结果 → 模型继续生成，直至最终回复（见 trace 中 model/tool 交错）。
工具取回的材料片段 refs 已累积入 turn_context（审计覆盖模型实际所见）。

## 最终回复

**韦达定理（根与系数的关系）**

根据材料：若 x₁、x₂ 是一元二次方程 ax²+bx+c=0（a≠0）的两个根，则

- 两根之和：x₁ + x₂ = −b/a
- 两根之积：x₁ · x₂ = c/a

**怎么理解**
- 这两个式子是从求根公式 x = (−b ± √Δ)/(2a) 推出来的：两根相加时 ±√Δ 互相抵消，剩下 −b/a；两根相乘时用平方差，得到 c/a。
- 好处是**不用真的把根解出来**，就能知道和与积；反过来，已知一根也能求另一根，还能"由和与积构造方程"：x² − (和)x + (积) = 0。

**用 x² − 5x + 6 = 0 演示**
这里 a = 1，b = −5，c = 6。
- x₁ + x₂ = −b/a = −(−5)/1 = **5**
- x₁ · x₂ = c/a = 6/1 = **6**
- 验证：解出根是 2 和 3，2+3=5，2×3=6 ✅

⚠️ 常见错误：忘记 b 自带的负号，把 −b/a 直接写成 b/a；使用时一定要先把方程化成一般式，再取 a、b、c。

---

来练一道：

请写出你的答案（两根之和 = ?，两根之积 = ?），我来帮你判定。

完整审计（plan_doc、model_calls、tool_calls、turn_context、SSE 事件）见 `tool_call_example.json`。
复跑：`python scripts/capture_tool_example.py`。
