# QUICKSTART —— 5 分钟跑通（含接自己的报告）

> 本 skill：把体检报告 PDF（1 份或多年多份）变成一份"每个数字都能追到页码"的
> 解读与行动清单。**默认全程在本机跑，不联网。**
>
> `SKILL=<本 skill 目录>`（按你的安装位置替换，例：`~/.agents/skills/medical-report-interpretation`）

## 0. 装依赖（唯一第三方：PyMuPDF）

```bash
python3 -m pip install -r "$SKILL/engine/requirements.txt"
```

- ⚠️ PyMuPDF 采用 **AGPL-3.0 / 商业双授权**：个人本地使用按 AGPL-3.0；
  对外分发/商用前请自行确认许可条件。
- 需要 Python ≥ 3.9；**仅支持带文字层的电子版 PDF**（扫描件/翻拍件会被如实
  报出"内容未被分析"，不做 OCR）。

## 1. 环境预检

```bash
python3 "$SKILL/engine/tools/preflight.py"
```

逐项检查 Python 版本 / PyMuPDF / 模板目录 / 输出目录可写 / skill 只读守卫；
失败时给出可执行的下一步命令。

## 2. 合成 demo（不需要任何自有数据）

```bash
python3 "$SKILL/scripts/eval_synth.py" --out /tmp/eval_synth
```

它会：跑引擎三件套（`examples/testdata` 的合成报告）→ 断言期望输出特征
（年份 2024/2025、项数 22/22、映射率、无空列、无 2026）→ 跑对账链（`--case synth`）
→ 全绿退出 0。

手动版（三件套）：

```bash
python3 "$SKILL/engine/run_narrative.py" --src "$SKILL/examples/testdata" --out /tmp/demo
python3 "$SKILL/engine/run_normalize.py"  --src "$SKILL/examples/testdata" --out /tmp/demo
python3 "$SKILL/engine/run_pipeline.py"   --src "$SKILL/examples/testdata" --out /tmp/demo
```

产物：`report_*.json` · `judgements.csv`（逐年值/位置轴/变化轴）· `derived.csv`（复合指标）·
`conclusions.csv` · `view_{clinical,patient,audit}.md`（三视图）。

## 3. 接入你自己的报告

> 建议先把你的报告目录设为**数据根**（引擎据此找报告与私有模板）：
> `touch <你的数据目录>/.checkup-root`（或 `export CHECKUP_FIXTURES=<你的数据目录>`）。

```bash
# ① 先预检：每份文件的类型/能不能读/不能读的原因与下一步
#    （扫描件/OCR 叠层/加密/OFD/Word/Excel 都会被点名，并给出出路）
python3 "$SKILL/scripts/scan_archive.py" <你的报告目录>

# ② 直接跑（同一个 --out）；版式匹配不上会**非零退出**，这是特性不是 bug
python3 "$SKILL/engine/run_narrative.py" --src <你的报告目录> --out $OUT
python3 "$SKILL/engine/run_normalize.py"  --src <你的报告目录> --out $OUT
python3 "$SKILL/engine/run_pipeline.py"   --src <你的报告目录> --out $OUT
```

> 个体信息（吸烟/用药/家族史）用 `--profile` 或 `CHECKUP_PROFILE` 传入，
> 字段规范见 `contracts/profile.schema.json`；问诊产生的数值（如包年数）
> 可登记进数据根 `constants.user.json`，对账即可指回。
> 报告引用数值请**保持底稿单位**（底稿 cm 就写 cm，不换算成 mm）——
> 换算后的数字会被对账列为「需人工确认」。

**版式不匹配（退出码 2）怎么办**：新机构接入 = **三步，一条命令起步** ——

```bash
# ① 诊断 + 求解：页型清单 + 候选结构 + 模板草案 + ≤10 条待确认问题（每条带"无人应答时的默认代入"）
python3 "$SKILL/engine/tools/onboard_scan.py" <样本1.pdf> <样本2.pdf> \
    --org NEW-001 --name "机构名" --search
# ② 验收 + 落盘：求解器枚举候选变体（噪声锚点/分界/单位列/标志列/页标记/栏数）逐款跑验收链，
#    全绿者出线并落盘；多个全绿出"对照卡"交你定夺；无全绿 → 不落盘（退出码 2）
python3 "$SKILL/engine/tools/accept_template.py" \
    --pdfs <样本1.pdf> <样本2.pdf> --search --org NEW-001 --install
# ③ 项目名词表：≥0.8 批量通过 + 抽样复核（随机 10% 高置信 + 全部低置信）
python3 "$SKILL/engine/draft_catalog.py" <样本1.pdf> <样本2.pdf> --org NEW-001
```

验收链 = 逐行回读 + 双实现交叉 + 语义形状 + 旗标交叉 + 项数基线：
**全绿才落盘**；有问题给"对照卡 + 可执行修法建议"（只回答"这两个读数一样吗"，
不需要医学知识）。**建议给 2–3 份同年样本**：只给 1 份时机构专属锚点会留空
（"出现在 ≥2 份样本"校验不过），补一份重跑即自动补上。

> 之后**同类机构**（同厂商版式族）会走"版式类库"：按相似度提名最像的模板
> （低于硬阈值 → 强制全量初始化），命中则只改差量；新报告**首见自动验收**用
> `engine/tools/first_sight.py <新报告.pdf> --package "套餐名" --write-baseline`
> （绿 → 归档并写分套餐基线；红 → 报警）。

想再加一道"第二读者"（**对干净文字层 PDF 默认不需要**；对"疑似 OCR 叠层"等
降级输入强烈建议）：**默认由 agent 本人读图作答**，两步——

```bash
python3 "$SKILL/engine/tools/accept_template.py" --pdfs … --org NEW-001 --vlm
# ① 生成工作单（work_order.md + clips/*.png）；**先只试读第一张**验证能看图
# ② 能读 → agent 逐张作答写 answers.json → ③ 重跑同一条命令 → 自动比对
# 模型不支持图像输入时，agent 会明确提示你（换视觉模型 / 配外部端点），不会静默跳过
```

外部端点（备选，默认不联网）：

```bash
export CHECKUP_VLM_PROVIDER=openai CHECKUP_VLM_BASE=http://127.0.0.1:11434/v1 \
       CHECKUP_VLM_MODEL=<模型名>
python3 "$SKILL/engine/tools/accept_template.py" --pdfs … --org NEW-001 --vlm --allow-network
```

> 你的机构模板**不必进分发包**：放到数据根 `<数据根>/templates/`，或用
> `CHECKUP_TEMPLATES` 指定目录，运行时会被一起加载（README「权限与环境变量」）。

## 4. 四张底稿 → 报告 → 两图 → 对账

```bash
python3 "$SKILL/reconcile/gen_artifacts.py" --engine-out $OUT --case-name <名> --out $OUT/artifacts/<名>
python3 "$SKILL/figures/merge_map_svg.py" <名>_merge_spec.json      # 主图（归并信息图）
python3 "$SKILL/figures/causal_svg.py"    <名>_causal_spec.json     # 附图（因果归并图）
python3 "$SKILL/reconcile/reconcile.py" --artifacts $OUT/artifacts/<名> --engine-out $OUT \
  --case-name <名> --report <报告>.md --specs <两张 spec>.json --out $OUT/对账_<名>.md
python3 "$SKILL/scripts/validate_report.py" <报告>.md --specs <两张 spec>.json   # 结构回归
python3 "$SKILL/scripts/trace_number.py" --report <报告>.md --artifacts $OUT/artifacts/<名> \
  --engine-out $OUT --pdf-dir <报告目录> --sample 5 --out $OUT/抽样回读_<名>.md
```

**定稿门槛**：对账全绿 + 未追踪数字逐条有交代（尽量为 0）+ 结构回归通过 + 问诊必问项齐备；
不通过不许交付。报告形态以 `references/report-form-v6.4.md` 与
`examples/report/` 的合成实例为准。

**动手写报告之前**先跑一次写作前置自检（规则从对账链**自动提取**，不复制口径）：

```bash
python3 "$SKILL/engine/tools/write_checklist.py" --report <报告>.md \
  --specs <名>_merge_spec.json <名>_causal_spec.json --out $OUT/写作前置自检.md
```

**交付时附一张抽检单**（≤5 条：机器决定 + 依据 + 对照原文页 —— 你只看这几条就行）：

```bash
python3 "$SKILL/engine/tools/spot_check.py" --engine-out $OUT --out $OUT/抽检单.md
```

> 这三件与"交付四件套"的关系：报告/两图/对账是**必读**；抽检单与写作前置自检是
> **可选读**的审计件 —— 不看也不影响正确性（正确性由模板验收 E1–E6 + 对账全绿保证）。

## 5. 卸载

本 skill 是纯目录制品：删掉 `$SKILL` 即可。运行期产物全部写在
`--out`/`CHECKUP_OUT` 指向的**用户工作目录**（脚本拒绝写进 skill 目录），
不会在系统里留服务、后台进程或注册表项。

## 常见问题

| 症状 | 处置 |
|---|---|
| `✗ 报告目录不存在` | 用 `--src` 指定报告目录（含 PDF）；或设 `CHECKUP_SRC` |
| 退出码 2：未能识别版式 | 该机构模板未注册 → 走**新机构接入三步**（`onboard_scan.py --search` → `accept_template.py --search --install` → `draft_catalog.py`），见上文 §3 |
| `import fitz` 失败 | `python3 -m pip install -r "$SKILL/engine/requirements.txt"` |
| 扫描件/翻拍件 | v1 无 OCR，如实报"内容未被分析"；请提供带文字层的电子版 |
| 接入新机构后**所有命令都报"模板加载失败"** | 模板目录里有非模板 json → 按报错点名的文件移出即可；验收档案（`*.accept.json`）会被自动跳过，不用管 |
| 问"某国标准"（日本/USPSTF…） | 知识库（约 317MB）**不随包**：设 `CHECKUP_LIB` 指向你自备的库；不可达时如实答"资料不在本机"（结构与来源见 `references/library.md`） |
| 单位"不可比"提示 | 引擎**只判等价、不换算**（设计如此）；换算是登记制、需出处（对账层 `reconcile/loaders.py` 里有示例） |
| 想让 agent 全程代劳 | 把本文件与 `SKILL.md` 交给 agent；`SKILL.md` 写明**开工前两步**（`preflight.py` + 读 `references/workflow.md`）与各阶段入口/门槛 |
| 前一次跑得好，这次项数变了 | 先看是不是**套餐不同**：模板可按套餐记基线（`first_sight.py --package … --write-baseline`），不一致时 E6 会点名"偏离哪个套餐的基线" |
| 有哪些工具可以用 | `engine/tools/`：`preflight` 预检 · `onboard_scan` 入库诊断/求解 · `accept_template` 模板验收 · `first_sight` 首见验收 · `write_checklist` 写作前自检 · `spot_check` 抽检单 · `draft_template` 离线起草（维护用） |
