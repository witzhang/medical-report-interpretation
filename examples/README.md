# examples —— 随包合成样例（纯虚构数据）

本目录是**演示与最小 eval** 用的合成数据，**不含任何真实个人数据**。
所有姓名/编号/数值均为虚构（受检者A、RH-2023-… 等）。

```
examples/
├── testdata/     3 份合成报告的 .txt（逐项真源）与 2 份 PDF（康泰 2024/2025）
│   └── make_reports.py   生成器（可重生成 PDF；见下"渲染说明"）
├── report/       合成 v6.3 基准报告 + 两图 + 两 spec（报告形态基准）
└── expected/     期望输出特征（scripts/eval_synth.py 的断言依据）
```

## 一分钟跑通

```bash
python3 engine/tools/preflight.py                                  # 环境预检
python3 scripts/eval_synth.py --out /tmp/eval_synth                # 引擎 + 期望特征 + 对账链
```

或手动：

```bash
python3 engine/run_narrative.py --src examples/testdata --out /tmp/demo
python3 engine/run_normalize.py --src examples/testdata --out /tmp/demo
python3 engine/run_pipeline.py  --src examples/testdata --out /tmp/demo
```

## 数据说明（刻意构造的"跨机构差异"）

- 两家机构：仁和健康体检中心（机构 A）、康泰综合医院（机构 B）
- 同一项目不同命名（谷丙转氨酶 / ALT / 丙氨酸氨基转移酶）、不同单位（mmol/L vs mg/dL）、
  不同参考区间；定性描述（TI-RADS vs C-TIRADS）；某年缺项
- **随包只含机构 B（康泰）的 PDF**：机构 A（仁和）的版式模板暂未随包
  （自动起草对其失败，见下），因此 demo 的年份为 **2024/2025**。
  仁和的 `.txt` 仍随包 —— 对账层（`--case synth`）读 .txt，不需要模板。

## 渲染说明（与工作区原版的差异）

`make_reports.py` 的渲染器按 `.txt` 的**显示列逐格定位**（等效"每格独立定位"的
真实机构 PDF），使 PDF 的列位可被按 x 定位的抽取层稳定解析。**报告文本一字未改**
（与同目录 `.txt` 互为真源）。重生成：

```bash
python3 examples/testdata/make_reports.py --only 2024 2025   # 只生成随包的两份
```

## 模板核对记录（SYN-002 · 康泰）

| 项 | 结果 |
|---|---|
| 来源 | `draft_template.py` 自动起草（2 份 PDF）→ 人工核对修正 |
| 结构 | 单栏；表头 `项目/结果/参考值/提示`；段标题为 `---- 段名 ----` 装饰线 |
| 列位 | 名称/结果/参考值/提示四列，按"最宽内容 × 2 + 2"反推安全列距（抽取层按最近列锚点归属） |
| 逐项核对 | 以 `.txt` 为真源逐项核对：**2024 = 22 项、2025 = 22 项**，项目名/数值/单位/参考区间四位一致 |
| 抽取基线 | `stats.items_per_report = [22, 22]`（漂移监控用；审计与抽取用同一套行过滤） |
| 已知边界 | ① 血压为 `138/88` 对值 → 化验通道不拆分（对账层按收缩压/舒张压建模）；② 该机构用美制单位（mg/dL），引擎按设计**只判等价、不换算** → 组合/分层项报"不可评估"，不把 mg/dL 当 mmol/L 算 |

## 机构 A（仁和）为什么没随包

自动起草对其**直接失败**（`没找到表头行`：该版式没有"检验报告单"式页标记，
段标题是 `===== 段名 =====` 装饰线）。按"模板必须先核对再使用"的纪律，
未经验证的模板不随包；它的 `.txt` 仍随包供对账链使用。
若要使其可跑：起草/手工修订模板 → 对 `.txt` 逐项核对 → 放入
`engine/catalog/templates/`（或你的数据根 `templates/`，见 README 的私有模板说明）。
