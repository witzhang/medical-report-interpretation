# 全流程细则（六阶段）

> **一句话**：脚本算事实，agent 写话。数值、比较、判定、缺口 → 脚本产出；
> 含义、建议、人话 → agent 写。**跨界即错**（方案 §5.1）。

工作目录约定（**运行期**）：

```
<技能目录>/            只读：代码、规则、契约、参考
<用户工作目录>/        可写：一切产物（默认 <数据根>/体检解读_out，可用 CHECKUP_OUT 覆盖）
   ├── out/            run_narrative → run_normalize → run_pipeline 的产物
   ├── artifacts/<名>/ 四张底稿（coverage/states/advice/facts）
   ├── <报告>.md       报告正文（交付格式 = Markdown）
   ├── <同名>_merge_diagram.svg · <同名>_causal_graph.svg
   └── 对账_<名>.md    对账记录
```

---

## 阶段 0 · 摄入（脚本）

1. 确认报告目录：**只放 PDF**（1～5+ 份）。**文件名需含 4 位年份**
   （年份据此区分；体检日期与机构一律从内页读）。
2. 跑引擎三件套（**同一个 `--out`**）：

```bash
python3 "$SKILL/engine/run_narrative.py" --src <报告目录> --out <产物>   # 抽取层（四通道）
python3 "$SKILL/engine/run_normalize.py"  --src <报告目录> --out <产物>   # 归一
python3 "$SKILL/engine/run_pipeline.py"   --src <报告目录> --out <产物>
```

（以绝对路径从任意目录调用即可；`cd` 进 skill 目录也可以，但产物必须用
`--out` 显式指向用户目录。）

3. **看退出码与首屏**：
   - `run_pipeline` **非零退出** → 版式未识别（机构/版式未注册）→ **停下来**：
     报告不再生成；按 `draft_template.py` 起草该机构模板并人工确认，或用 `--org` 指定已注册模板。
     ⛔ 禁止拿别家版式硬套、⛔ 禁止把"没读到数据"写成"没发现问题"。
   - `validation.json` 里 `level=fatal` → 同理停止；`warn` → 继续，但**报告必须写明**。
4. 报出事实（**脚本口径，不许 agent 手工数**）：有几年、哪家机构、各年项目数、
   未匹配项（`UNMATCHED` 不猜不丢）、缺口初判（附录一最终以 `coverage.json` 为准）。

**禁令**：⛔ 不静默跳过任何文件（扫描件/无文字层要如实报"内容未被分析"）；
⛔ 不用文件名当日期；⛔ 不改 `raw` 层原文。

## 阶段 1 · 问诊（agent，≤5 问、1 轮）

1. **时机**：解析完成之后、写报告之前（⛔ 不许一上来就问）。
2. **问什么 —— 两个来源**（缺一不可）：
   - ① **规则 `input_fields`**："因缺输入而降级/不可评估的规则"反推；
   - ② **产物推断的高影响待问项**：先跑
     `python3 scripts/ask_queue.py --engine-out <产物> --check`，它会列出——
     **机构建议过但数据里未见执行的检查**（"拖了两年"类问题的来源）· **能解除现有黄/红级的测量**
     （家庭血压、包年数、是否换过检测方法/是否重测）· 数据质量疑点；
   - 排序 = **能降级最高级项者优先**；合并两来源后取前 N 个（**≤5 问、1 轮**）。
   - **年龄、性别必须问到位**（判读失效的输入）；
   - 吸烟/饮酒/既往诊断/用药/家族史——只问**会改变结论**的；
   - 危险分层类结论缺家族史时：按"无"代入 + 敏感性说明（规则库口径）。
3. **问完即用**：结果只进表头（`问诊补充` + `前提假设声明`）与本次判读，
   ⛔ **不落盘**、不进任何文件、不进日志（无状态设计）。
4. 问不到的（用户也不记得）→ 写进**受影响的那张卡片**（"此项若为 X 则结论为 Y"），
   ⛔ 不许甩到报告末尾单独成章。
5. 用药史入 `profile`（临时文件，`--profile` 或 `CHECKUP_PROFILE`）；
   ④ 节必须带服药安全提示句（见 report-form）。

## 阶段 2 · 复算（脚本，产出四张底稿）

```bash
python3 "$SKILL/reconcile/gen_artifacts.py" --engine-out <产物> --case-name <名> \
  --profile <个体信息.json> --out <产物>/artifacts/<名>
```

（`--profile` 可换 `CHECKUP_PROFILE`；字段规范见 `contracts/profile.schema.json`。）

产出 `coverage.json`（缺口/覆盖率）· `states.json`（跨年状态/影像台账）·
`advice.json`（机构建议逐条带页码）· `facts.json`（数值事实表/派生/数据质量）。

**禁令**：⛔ 报告里的事实性判断只能来自这四张底稿；⛔ agent 不许看 CSV 手工数
"缺/有/涨了/降了"；⛔ 派生量（eGFR/China-PAR/危险分层/代谢综合征）只认底稿。

## 阶段 3 · 判读（agent 读底稿 + 规则库）

1. 需要多年对比（**N≥2 个体检日期**）→ **先读 `trend-rules.md` 再继续**。
2. 判读用 `rules/`（中国口径为唯一进报告依据；每条规则带 `provenance`）；
   机构原文（"当时怎么说"）只在 `advice.json`。
3. 分档（🔴/🟡/🟢/⚪）**映射到脚本**（`states.json` 状态分类 + 卡片级别）；
   ⛔ `undetermined`/`not_assessable`/`not_applicable` 不许折叠进绿色；
   ⛔ 报告的"红" ≠ 官方 A/B 类危急值（A/B 类**另立**，命中时中断常规流程、置顶直报）。
4. 鉴别分析与关联成簇由 agent 做（脚本做不了）：多个异常归成一个上游（"六项是一件事"）。

## 阶段 4 · 报告（agent 写作）

1. 形态：**以 `report-form-v6.4.md` 为准**（两章 8 节 + 两附录 + 免责声明）；
   卡片字段以 `card-fields.md` 为准（12 字段、复查五要素）。
2. 两图：改 `*_spec.json` 后跑 `figures/merge_map_svg.py` 与 `figures/causal_svg.py`
   （⛔ agent 不碰坐标/排版；节点 ≤20、边 ≤24）。
3. 语气：知心懂医学的"大姐姐"（第二人称、短句、有立场、先给有据可依的安心）；
   ⛔ 诊断/药名/剂量；⛔ 禁用话术（`注意饮食`/`适当运动`/`建议进一步检查`/`不太正常`/
   `建议复查`不给条件）；⛔ 不把"不知道"说成"没问题"。

## 阶段 5 · 对账（脚本，**定稿门槛**）

```bash
python3 "$SKILL/reconcile/reconcile.py" --artifacts <产物>/artifacts/<名> --engine-out <产物> \
  --case-name <名> --report <报告.md> --specs <merge_spec.json> <causal_spec.json> \
  --out <产物>/对账_<名>.md
python3 "$SKILL/scripts/validate_report.py" <报告.md>   # 结构回归（必填章节/字段/话术）
```

**报告定稿 = 下面全部通过**（硬规矩，不通过不许交付）：

```bash
python3 reconcile/reconcile.py --artifacts <底稿> --report <报告.md> --specs <两 spec> --out <对账.md>
python3 scripts/validate_report.py <报告.md> --specs <两 spec>     # 结构回归 + 两图稀疏度
python3 scripts/check_gaps.py <报告.md> --artifacts <底稿>          # 缺口四类对账
python3 scripts/trace_number.py --report <报告.md> --artifacts <底稿> \
    --engine-out <产物> --pdf-dir <报告目录> --sample 5             # 抽样回读（回原 PDF 页）
python3 scripts/ask_queue.py --engine-out <产物> --check            # 问诊必问项齐备
python3 scripts/repeatability_check.py [--src <报告目录>]           # 双跑级别一致（可复现性）
```
指不回底稿的数字：补进底稿，或登记 `reconcile/constants.json`（先登记再引用）。
⚠️ 对账要**连两张图的 spec 一起对**（图里的文字也是报告的一部分）。
