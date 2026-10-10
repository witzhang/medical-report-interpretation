# engine —— 引擎简版说明（分发版）

> 开发日志与设计长文已移出分发包（留在作者工作区）；本文件只讲**怎么用**与**边界**。

## 模块地图

| 层 | 模块 | 职责 |
|---|---|---|
| L1 抽取 | `checkup/tables.py`（化验单）· `checkup/narrative.py`（叙述/标量） | 按**模板**（版面假设）从 PDF 文字层取数；列定位按 x 锚点 |
| L1 版面 | `checkup/layout.py` | 页面几何 → 阅读顺序的行/词 |
| L2 归一 | `checkup/normalize.py` + `catalog/analytes.json` | 原文项目名 → 规范指标（208+ 条词典 + 登记制映射规则）；单位**只判等价、不换算** |
| L3 校验 | `checkup/validate.py`（含漂移/Delta Check/载体判定）· `checkup/invariants.py` | 错误必须被拦下；整份等级取最严重项 |
| L4 判定 | `checkup/judge.py` | 位置轴（逐年 vs 当年区间）× 变化轴（RCV 两两比较，**全部两两对**） |
| L5 组合 | `checkup/compose.py` · `checkup/bands.py` · `checkup/lipid_table.py` | 复合指标/分层；单位与规范不等价的年份**拦下**（报"不可评估"） |
| L6 结论 | `checkup/conclude.py` | 按共同上游归并结论（信息/建议两档，绝不诊断） |
| L7 输出 | `checkup/render.py` | 三视图（临床数值版/患者版/审计版）；年份列**取自数据** |
| 入口 | `run_narrative.py` → `run_normalize.py` → `run_pipeline.py` | 三件套；版式不匹配 → 非零退出 |
| 工具 | `tools/preflight.py`（环境预检）· `tools/{health,mutate,triage_mutants,audit_*}.py`（开发/质量工具，**需数据根**） | |

## 命令

```bash
python3 engine/tools/preflight.py                                        # 环境预检
python3 engine/run_pipeline.py --src <报告目录> --out <产物目录>          # 全链（含三视图/CSV）
python3 engine/draft_template.py <样本.pdf> --org NEW-001 --verify       # 新机构模板起草（草案须人工核对）
python3 engine/build_catalog.py                                          # 项目名目录种子（需数据根）
python3 -m pytest engine/tests -q                                        # 测试（无数据根时依赖数据的文件 skip）
```

## 模板机制

- 模板 = 某机构版式的**版面假设**（页标记/表头列/栏数/段标题列/噪声模式），
  放在 `engine/catalog/templates/*.json`；**你的机构模板不必进包** ——
  放 `<数据根>/templates/` 或 `CHECKUP_TEMPLATES` 指定目录，运行时一起加载。
- 自动起草（`draft_template.py`）产出的是**草案**（`confirmed=False`）：
  必须对样本真源逐项核对后才可使用 —— 草案"抽到多少项"不等于"抽对"。
- 路由是**硬匹配**：机构专属锚点缺一不可 + 栏数必须一致；匹配不上 → 非零退出。

## 边界

- 仅支持带文字层 PDF（无 OCR）；扫描件会被如实报出"内容未被分析"。
- 单位不换算（设计如此）；换算需登记 + 出处（见 `reconcile/loaders.py` 示例）。
- `tools/` 里的开发工具依赖作者数据根与完整测试环境，普通使用者不需要它们；
  `tools/preflight.py` 是唯一给使用者用的。
