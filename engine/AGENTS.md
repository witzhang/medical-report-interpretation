# AGENTS.md —— 给其他 agent 的入口

体检报告分析引擎。**从多机构版式 PDF 到综合结论**，全链路确定性计算，
不用大模型做取数与判读。

```bash
cd <skill>/engine

# ① 抽取层：叙述 / 体格标量 / 机构主检 → report_*.json 等
python3 run_narrative.py --src <报告目录> --out <产物目录>

# ② 归一（审计与排查用）
python3 run_normalize.py --src <报告目录> --out <产物目录>

# ③ 端到端分析：抽取 → 归一 → 校验 → 判定 → 组合 → 结论 → 三视图
python3 run_pipeline.py --src <报告目录> --out <产物目录>
```

跑完看 `<产物>/view_clinical.md`（门诊数值版）、`view_patient.md`（患者版）、
`view_audit.md`（审计版）。

> **产物一律写用户工作目录**（默认取数据根，`CHECKUP_OUT` 可覆盖）；
> ⛔ 不许写进 skill 目录——脚本对"写到 skill 里"直接报错。
> `run_pipeline.py --out X` 只产出**分析结果**那一部分（见第三节）；
> 抽取层与归一层的产物由上面①②补全（报告核对层要读全套）。
> **版式匹配不上 → 非零退出、中止后续所有层**（P0-7）：宁可停下报错，
> 也不交一份读起来像"没发现问题"的报告。

---

## 一、这个引擎能做什么

| 层 | 做什么 | 代码 |
|---|---|---|
| L1 抽取 | 化验表 / 体格标量 / 叙述（彩超·CT·心电图·骨密度）/ 机构主检 | `layout tables narrative` |
| L2 归一 | 项目名与单位归一到固定 id；**单位只做等价判定，绝不静默换算** | `normalize` |
| L3 校验 | 内部自洽、覆盖率、单位冲突、跳变、生理合理性、抽取审计、载体判定 | `validate` |
| L4 判定 | **位置轴**（是否越线）与**变化轴**（RCV）分开，都不塌缩成布尔 | `judge` `ranges` |
| L5 组合 | eGFR / FIB-4 / AIP / 非 HDL-C / 代谢综合征×3 / CKD 分期 / 血脂危险分层 / China-PAR / 余生风险 | `compose` `lipid_table` |
| L6 结论 | 按共同上游归并成结论，每条带证据指针与把握度 | `conclude` |
| L7 输出 | 三视图 + 措辞分层 + A 类红旗 | `render` `redflags` |

## 二、硬约束（**别绕过**）

1. **原始值永不改写。** 归一、修复、换算都写在新字段里，`value_text` 保留原样。
2. **每条结论都必须带证据指针**（`evidence`）—— 指回具体报告页与原文。
3. **五值枚举，不用布尔**：`normal / abnormal / undetermined / not_assessable / not_applicable`。
   - `not_applicable`（人群不适用）**优先于** `not_assessable`（缺输入）
4. **缺失 ≠ 阴性。** 某年没做某项检查 ⇒ 那条结论写「未做」，不写「正常」。
5. **单位只判等价，不换算。** 判不了可比性 ⇒ **拒绝出趋势**。
6. **不编造。** 没有 CVi 的项目就不给 RCV；没核实的指南阈值显式标 `verified=False`。
7. **A 类红旗不得软化**（《健康体检重要异常结果管理专家共识》A 类）。

## 三、输出（`--out` 目录）

### 给人看的

| 文件 | 用途 |
|---|---|
| `view_clinical.md` | **门诊数值版**：只列异常项、超噪声变化、关键复合指标 |
| `view_patient.md` | 患者版，措辞分层，不含诊断 |
| `view_audit.md` | 审计版：路由、校验、每个派生量的 inputs |

### 给程序用的

**`run_pipeline.py --out X` 产出的（分析结果）**

| 文件 | 内容 |
|---|---|
| `judgements.csv` | 每个项目的逐年值、CVi、RCV 阈值、位置轴、变化轴、稳健性 |
| `derived.csv` | 每条组合规则逐年的值、分层、`inputs`（**JSON 串**）、missing、notes |
| `conclusions.csv` | 结论：id、分类、共同上游、正文、不确定性、引用规则、措辞类型、建议 |
| `validation.json` | L3 各检查的等级与明细 |
| `view_clinical.md` `view_patient.md` `view_audit.md` | 三个视图 |

**`run_narrative.py` 产出的（抽取层，固定写 `out/`）**

| 文件 | 内容 |
|---|---|
| `report_<年>.json` | 该年的 `document` + `observations` + `narratives` + `institution_findings` |
| `observations_all.csv` | 全部观测（含化验与体格）|
| `narrative_all.csv` | 彩超/CT/心电图/骨密度的叙述条目 |
| `institution_findings.csv` | 机构主检报告自报的异常项 |

**`run_normalize.py` 产出的**

| 文件 | 内容 |
|---|---|
| `normalized_observations.csv` | 归一后的全部观测（审计与排查用）|

字段含义见 [`docs/数据契约.md`](docs/数据契约.md)。

## 四、命令行

```bash
python3 run_pipeline.py --help

--src DIR       报告目录（含 PDF）。**文件名不参与解析** ——
                体检日期与机构一律从内页读
--out DIR       产物目录（默认 out/）
--org ID        强制机构模板，跳过自动匹配（多机构场景用）
--profile FILE  个体信息：吸烟/糖尿病/用药史/家族史/地区
--quiet         只写文件
```

`--org` 指定了不存在的模板会**报错**，不会静默退回默认模板
（那会让整份报告按错的版式解析）。

## 五、换一家机构

版式匹配不上时**整份报告跳过**（P0-7：0 观测 → FATAL、非零退出），
绝不拿默认模板硬套（**引擎不内置任何默认版式**）。三条路：

```bash
# ① 自动起草模板（丢一份 PDF 进去）
python3 draft_template.py <样本.pdf> --org NEW-001 --verify

# ② 补项目名映射（遇到未映射的项目名时）
python3 draft_catalog.py --org <机构id> <样本.pdf...>

# ③ 冷启动三步见 README「冷启动」一节
```

模板是纯 JSON，在 `catalog/templates/<机构id>.json`。加一个文件 = 支持一家机构。

## 六、自检

```bash
python3 tools/health.py          # 一条命令看全项目健康度
python3 tools/health.py --full   # 另加变异测试自检
```

| 检查 | 失败的判据 |
|---|---|
| 全部测试 | 任何一项不通过（数据根挂载时 **536 项**；未挂载时数据依赖项 skip、其余约 109 项仍须全过——以 `python3 -m pytest engine/tests` 收集数为准）|
| 变异工具自检 | 等价变异没存活，或致命变异没被杀死 |
| 空转断言审计 | 存在零断言 / 恒真断言的测试 |
| 接线审计 | 存在声明了却没人引用的函数/类 |
| 开放项对账 | 扫到没登记，或登记了扫不到 |
| 仓库完整性 | 变异体计数与基线不符 |

⚠ `health.py` 会**先清 `__pycache__`**。这不是卫生问题：CPython 按
「mtime + 大小」判字节码失效，而很多变异**不改变文件大小**，
同一秒内写入会读到陈旧字节码，给出**看起来合理的错答案**。

## 七、边界（**别当成已完成**）

- **v1 没有图像通道。** 有图无文字层的扫描页会被 `check_carrier` 报出来
  （"内容未被分析"），而不是静默跳过
- **只在本机一种机构版式上验证过。** 多机构能力是**机制**上的
  （模板注册表 + 自动匹配 + 自动起草），不是**数据**上的
- **8 条开放项全部卡在外部输入**：
  ```bash
  python3 open_items.py     # 逐条列出，含 blocked_on_*
  ```
  含 4 条未核实的指南阈值（标了 `verified=False`）、China-PAR 系数原文、
  多机构样本、扫描件样本
- **本地模型（Laya）只做了选型与适配层**，未接入流水线
- **参考区间的年龄/性别分层表是空的**（机制在，数据缺）

## 八、改代码之前

- 跑 `python3 tools/health.py` 建立基线
- 引擎代码 6.4k 行，测试 8.0k 行 —— **测试比引擎多是有意的**：
  这是医疗相关计算，判错了后果比慢一点严重
- `checkup/` 里每个模块顶部都有 docstring 说明它在哪一层、依据哪份文献
- 结论里的每个数值都应该能追到 `derived.csv` 的 `inputs` 或原始 PDF 页

## 九、目录

```
checkup-engine/
├── README.md              设计叙事 + 逐条依据（2000 行，面向人）
├── docs/数据契约.md        字段级说明
├── devpaths.py            外部数据根解析（P0-1：代码与数据解绑）
├── run_pipeline.py        主入口
├── checkup/               引擎（24 个模块，6.4k 行）
├── catalog/
│   ├── analytes.json      208 个项目定义
│   ├── cvi.json           44 项的个体内变异（含来源）
│   └── templates/         机构版式模板（一家一个 JSON）
├── tests/                 536 项（测试比引擎多是有意的；未挂载数据时 skip 不 fail；以 pytest 收集数为准）
└── tools/                 健康检查与审计工具
```
