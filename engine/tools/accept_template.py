#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板一键验收（P2-5）：起草 → 证据链验收 → 全绿才落盘。

    # ① 已有一份模板：直接验收
    python3 engine/tools/accept_template.py --pdfs <样本.pdf …> --template engine/catalog/templates/X.json

    # ② 新机构：起草 + 验收（几何起草失败时，若配了 VLM 会请它出道提案）
    python3 engine/tools/accept_template.py --pdfs <样本.pdf …> --org NEW-001 --name "机构名" [--vlm]

    # ③ 验收通过后落盘（复制到包内 templates/ 并写验收档案 <org>.accept.json）
    python3 engine/tools/accept_template.py --pdfs … --template … --install

设计（见 checkup/accept.py）：证据链分 ok / review / block 三级 ——
**全绿才自动落盘**；有问题就出"对照卡"（PDF 行原文 vs 引擎读数 vs 独立复核），
用户只需回答"这两个读数一样吗"，**不需要医学知识**。

退出码：0 = OK（可落盘）· 2 = REVIEW（需人工看对照卡）· 1 = BLOCK（打回）· 3 = 用法错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

from checkup import accept as ACCEPT  # noqa: E402
from checkup import intake  # noqa: E402
from checkup import vlm as VLM  # noqa: E402
from devpaths import guard_out  # noqa: E402  （产物落点闸门：禁止写进 skill 目录，B8）


from checkup.console import init_console  # noqa: E402
init_console()

def _load_or_draft(args, pdfs: list[str], client, vlm_work: str):
    """返回 ``(template, 起草说明, 起草备注)`` 或 ``(None, 失败原因, [])``。

    ``起草备注`` 是**分级后的草稿 notes 与待确认问题**（A9）：结构已定 /
    部分成功（待确认字段）/ 无法起草，直出到验收报告与档案。门槛只看
    ``confidence > 0`` —— 原来的 ``and d.template.lab.header_cells`` 恒真，
    等于没查；``confidence == 0`` 必须走失败分支（fail-loud）。
    """
    if args.template:
        from checkup.templates import Template  # noqa: PLC0415
        with open(args.template, encoding="utf-8") as f:
            t = Template.from_dict(json.load(f))
        note = f"来自文件 {args.template}（org_id={t.org_id}）"
        if args.org and args.org != t.org_id:
            note += (f"；注意：落盘用模板自带的 org_id={t.org_id}，"
                     f"--org={args.org} 只在自动起草时用")
        return t, note, [], []

    from checkup.autotemplate import draft_template  # noqa: PLC0415
    d = draft_template(pdfs, org_id=args.org, org_name=args.name or "")
    notes = "；".join(d.notes) if d.notes else "无"
    draft_notes = ([f"起草分级：{d.status_label}"
                    f"（置信度 {d.confidence:.2f}）"]
                   + [f"⚠ {n}" for n in d.notes]
                   + [f"？ 待确认：{q}" for q in d.open_questions])
    if d.confidence > 0:
        return (d.template, (f"几何自动起草（{d.status_label}，"
                             f"置信度 {d.confidence:.2f}；{notes}）"),
                draft_notes, d.decisions())
    # 几何起草失败 → 请 VLM 出道提案（仍须验收链通过）
    if client is not None and client.cfg.provider == "agent":
        # agent 模式：写"起草工作单"，等 agent 读图作答
        import fitz  # noqa: PLC0415
        pages = []
        for p in pdfs[:3]:
            doc = fitz.open(p)
            try:
                for i in range(min(2, doc.page_count)):
                    pages.append(doc[i].get_pixmap(dpi=150).tobytes("png"))
            finally:
                doc.close()
        tasks = [{"key": f"page_{i+1}", "png": png, "row_text": ""}
                 for i, png in enumerate(pages)]
        paths = VLM.write_work_order(vlm_work, tasks, kind="draft",
                                     extra_md="模板字段说明见提示词；"
                                              "把 JSON 写进同目录 proposal.json")
        print(f"⚠ 几何自动起草失败（{notes}）。已生成**起草工作单**：{paths['work_order']}")
        print("  请 agent 读页面图 → 把提案 JSON 写进 "
              f"{os.path.join(os.path.dirname(paths['work_order']), 'proposal.json')}"
              " → 重跑本命令")
        prop_path = os.path.join(os.path.dirname(paths["work_order"]), "proposal.json")
        if os.path.isfile(prop_path):
            with open(prop_path, encoding="utf-8") as f:
                prop = json.load(f)
            t = VLM.template_from_proposal(prop, pdfs[0], org_id=args.org,
                                           org_name=args.name or "")
            return (t, f"VLM 起草提案（agent 作答，{prop_path}）", draft_notes,
                    d.decisions())
        return None, "等 agent 完成起草工作单（见上）", draft_notes, d.decisions()
    if client is not None and client.available:
        pages = []
        import fitz  # noqa: PLC0415
        for p in pdfs[:3]:
            doc = fitz.open(p)
            try:
                for i in range(min(2, doc.page_count)):
                    pages.append(doc[i].get_pixmap(dpi=150).tobytes("png"))
            finally:
                doc.close()
        prop = client.propose_template(pages, args.org, args.name or "")
        if prop:
            t = VLM.template_from_proposal(prop, pdfs[0], org_id=args.org,
                                           org_name=args.name or "")
            return (t, (f"VLM 起草提案（模型 {client.cfg.model or 'replay'}，"
                        f"置信度 {prop.get('confidence')}）—— "
                        f"{prop.get('notes', '')}"), draft_notes, d.decisions())
    return (None, (f"几何自动起草失败（{notes}）；"
                   + ("VLM 未配置 → 用 --vlm 并配 CHECKUP_VLM_*，"
                      if not (client and client.available) else "VLM 提案也不可用，")
                   + "或手工写模板后 --template 验收"), draft_notes, d.decisions())


def _run_search(args, client):
    """求解器模式（A11）：枚举候选 → 逐款验收 → 全绿者里取品质最好的一款。

    返回 ``(template, 说明, 起草备注)``；没有全绿候选 → ``(None, 原因, 备注)``。
    求解记录（含试跑表与对照卡）落到 ``<out_dir>/求解_<org>.md``。
    """
    from checkup.autotemplate import search_template  # noqa: PLC0415
    out_dir = args.out or os.path.join(_product_root(), "accept", args.org)

    def _accept(t, pdfs):  # 与主流程同一套验收参数（VLM 通道也一起带上）
        return ACCEPT.accept(t, pdfs, vlm=client,
                             clip_dir=os.path.join(out_dir, "cards"),
                             vlm_required=False, degraded=False)

    res = search_template(args.pdfs, org_id=args.org, org_name=args.name or "",
                          accept_fn=_accept)
    print()
    print(res.render())
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"求解_{args.org}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(res.render() + "\n")
    print(f"\n求解记录：{path}")
    if res.template is None:
        print("✗ 求解器未得到全绿候选 —— 见上面的试跑记录；"
              "按对照卡/待确认问题修完再跑，或手工写模板 --template 验收")
        return None, f"求解器无全绿候选（{res.chosen}）", [], []
    notes = [f"求解分级：从 {len(res.trials)} 款候选里选出全绿 {len(res.green)} 款，"
             f"按品质分（签名词当值/名 = 假项）取「{res.chosen}」"]
    if res.card:
        notes.append("· 多个全绿候选品质并列 → 已出对照卡（见求解记录），"
                     "采用者为并列中的第一个；如需换款请人工指定")
    if res.draft is not None:
        notes += [f"⚠ {n}" for n in res.draft.notes]
        notes += [f"？ 待确认：{q}" for q in res.draft.open_questions]
    decisions = res.draft.decisions() if res.draft is not None else []
    decisions.append({"决定": "求解器选定的变体", "依据": res.render().splitlines()[0],
                      "不确定": bool(res.card)})
    return (res.template,
            f"求解器选定变体「{res.chosen}」（全绿 {len(res.green)}/{len(res.trials)}）",
            notes, decisions)


def main() -> int:
    ap = argparse.ArgumentParser(description="模板一键验收：起草 → 证据链 → 落盘")
    ap.add_argument("--pdfs", nargs="+", required=True, help="该机构样本 PDF（≥1，建议 2-3 份）")
    ap.add_argument("--template", default="", help="已起草的模板 JSON（缺省则自动起草）")
    ap.add_argument("--org", default="NEW-001", help="机构 ID")
    ap.add_argument("--name", default="", help="机构名称")
    ap.add_argument("--out", default="", help="验收报告输出目录（缺省 <产物根>/accept/<org>）")
    ap.add_argument("--vlm", action="store_true",
                    help="启用 VLM 通道（默认=**agent 本人**读图：先出工作单，"
                         "agent 作答后重跑；配了 CHECKUP_VLM_PROVIDER 则走外部端点）")
    ap.add_argument("--vlm-work", default="",
                    help="VLM 工作单目录（缺省与 --out 同处）")
    ap.add_argument("--allow-network", action="store_true",
                    help="允许把**页面截图**发给 VLM 端点（默认不发）")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="接受『疑似 OCR 叠层』的输入（数据质量将标降级）")
    ap.add_argument("--install", action="store_true",
                    help="验收 OK 时把模板复制进包内 templates/ 并写验收档案")
    ap.add_argument("--search", action="store_true",
                    help="**求解器模式**（A11）：把草稿的每个可争议决定展开成候选变体"
                         "（噪声锚点/分界口径/单位列/标志列/页标记/栏数），逐款跑验收链，"
                         "取全绿者；多个全绿出对照卡。与 --template 互斥")
    ap.add_argument("--force", action="store_true",
                    help="REVIEW 也落盘（会把对照卡一并记进验收档案）")
    args = ap.parse_args()
    if args.search and args.template:
        print("✗ --search 与 --template 互斥：求解器模式自己起草候选（用法错误）")
        return 3

    # ---- 一、输入预检（先判"能不能读"）
    verdicts = [intake.classify(p) for p in args.pdfs]
    blocked = [v for v in verdicts if not v.usable]
    degraded = [v for v in verdicts if v.usable and v.degraded]
    for v in verdicts:
        flag = "✓" if v.usable and not v.degraded else ("⚠ 降级" if v.usable else "✗")
        print(f"{flag} {os.path.basename(v.path)} —— {v.label}：{v.reason}")
    if blocked:
        print("\n✗ 有文件不可用（先按下面的提示处理，不要硬跑）：")
        for v in blocked:
            print(f"   · {os.path.basename(v.path)}：{v.next_step}")
        return 3
    if degraded and not args.allow_degraded:
        print("\n⚠ 有文件是『疑似 OCR 叠层』（数字可能来自图像识别）：")
        for v in degraded:
            print(f"   · {os.path.basename(v.path)}：{v.next_step}")
        print("   如确要继续：加 --allow-degraded（会按降级记录数据质量）")
        return 3
    if degraded:
        print("   （已按 --allow-degraded 继续：数据质量为『降级』，请如实标注）\n")

    # ---- 二、模板：读入 / 起草 / **求解**（--search）
    #
    # ⚠️ 落点统一走 ``guard_out``（B8）：验收报告/对照卡/VLM 工作单都属于**产物**，
    # 一律不许写进 skill 目录；缺省落在用户产物根，**不落当前目录**。
    out_dir = guard_out(args.out or os.path.join(_product_root(), "accept", args.org))
    vlm_work = guard_out(args.vlm_work or os.path.join(out_dir, "vlm"))
    client = None
    vlm_required = False
    if args.vlm:
        cfg = VLM.load_config(allow_network=args.allow_network)
        if cfg.provider == "agent":
            # agent 模式：答案来自 agent 读图后写下的 answers.json
            cfg.answers_path = os.path.join(vlm_work, "answers.json")
            vlm_required = True
        client = VLM.VlmClient(cfg)
        print(f"[VLM] {client.describe()}")
    if args.search:
        template, why, draft_notes, decisions = _run_search(args, client)
        if template is None:
            return 2
    else:
        template, why, draft_notes, decisions = _load_or_draft(
            args, args.pdfs, client, vlm_work)
    if template is None:
        print(f"\n✗ 没有可用模板：{why}")
        return 1
    print(f"\n模板：{why}")
    for line in draft_notes:
        print(f"  {line}")

    # ---- 三、验收链（agent 模式：先确认"第二读者"的答案是否就绪）
    os.makedirs(out_dir, exist_ok=True)
    if args.vlm and client is not None and client.cfg.provider == "agent":
        if not os.path.isfile(client.cfg.answers_path):
            import fitz  # noqa: PLC0415
            tasks = []
            max_rows = int(os.environ.get("CHECKUP_VLM_MAX_ROWS", "12"))
            for path in args.pdfs:
                doc = fitz.open(path)
                try:
                    results = ACCEPT.extract_lab_results(doc, "0000", template)
                except Exception:  # noqa: BLE001
                    results = []
                tasks += ACCEPT.vlm_row_tasks(doc, template, results,
                                              os.path.basename(path), max_rows)
                doc.close()
            paths = VLM.write_work_order(vlm_work, tasks, kind="read_rows")
            print()
            print("⚠ VLM 第二读者**尚未作答** —— 已生成工作单：")
            print(f"   · 工作单：{paths['work_order']}")
            print(f"   · 行图目录：{paths['clips']}（{len(tasks)} 张）")
            print(f"   · 把答案写进：{paths['answers']}（示例见 answers.json.example）")
            first = [t["key"] for t in tasks]
            print(f"   👉 **先只试读第一张**：clips/{first[0]}.png —— 确认你能看图；"
                  f"若读不了（如运行环境提示 model does not support image input）"
                  f"就**停下并告知用户**（切换支持视觉的模型或配 CHECKUP_VLM_* 端点），"
                  f"别把 {len(tasks)} 张都读一遍")
            print("   能读 → 由 **agent 本人**逐张作答后重跑本命令（会自动接着验收）")
            return 2
        print(f"[VLM] 已读到 agent 的作答：{client.cfg.answers_path}")
    clip_dir = os.path.join(out_dir, "cards")
    report = ACCEPT.accept(template, args.pdfs, vlm=client, clip_dir=clip_dir,
                           vlm_required=vlm_required,
                           degraded=bool(degraded),
                           draft_notes=draft_notes, decisions=decisions)
    md = os.path.join(out_dir, f"验收_{template.org_id}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(report.render() + "\n")
    with open(os.path.join(out_dir, f"验收_{template.org_id}.json"), "w",
              encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    print()
    print(report.render())
    print(f"\n验收报告：{md}")

    verdict = report.verdict
    if verdict == "block":
        print("✗ BLOCK —— 模板与这批 PDF 不匹配，不予落盘。请按对照卡/证据修模板或样本后重跑。")
        return 1
    if verdict == "review" and not args.force:
        print("⚠ REVIEW —— 有需人工确认的项（见对照卡）。确认无误后加 --force 落盘，"
              "或修完重跑。")
        return 2

    # ---- 四、落盘（含验收档案，供审计/复现）
    #
    # **两种分支都写验收档案**（B9）：原来只在 --install 时写档案，但提示语说
    # "验收档案仍会留在 out_dir/" —— 文案与行为不符。现在两条路都留档：
    # 档案是"这次验收是怎么过的"的记录，与模板进不进包无关。
    # 档案一律写用户产物目录、不写进包（丙案）：① 档案是验收记录，按边界本该
    # 写用户目录、skill 运行期只读；② 档案含样本文件名（用户报告名），进包再
    # 分享就会漏；③ 模板目录只留模板，任何枚举它的工具都不会被绊倒。
    tdir = os.path.join(ENGINE, "catalog", "templates")
    if args.install:
        os.makedirs(tdir, exist_ok=True)
        tpl_path = os.path.join(tdir, f"{template.org_id}.json")
        template.save(tpl_path)
        installed = True
    else:
        # 未安装时也把模板（草案/求解选定款）落在产物目录，供人查看与复核
        tpl_path = os.path.join(out_dir, f"{template.org_id}.json")
        template.save(tpl_path)
        installed = False
    archive_path = os.path.join(out_dir, f"{template.org_id}.accept.json")
    archive = {"org_id": template.org_id,
               "samples": [os.path.basename(p) for p in args.pdfs],
               "verdict": verdict,
               "installed": installed,
               "template_path": tpl_path,
               "template_sha1": _sha1(tpl_path),
               "vlm": report.vlm,
               "evidences": [e.to_dict() for e in report.evidences]}
    if report.draft_notes:
        archive["draft_notes"] = list(report.draft_notes)
    if report.decisions:
        archive["decisions"] = list(report.decisions)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    if installed:
        print(f"✓ 模板已落盘：{tpl_path}")
        print(f"✓ 验收档案（审计/复现用，**在用户产物目录**）：{archive_path}")
        print(f"  落点提示：模板在包内 templates/；验收报告与档案在 {out_dir}/"
              f"（用 --out / CHECKUP_OUT 可把后者固定到你的产物目录）")
    else:
        print(f"✓ 模板（未进包）：{tpl_path}")
        print(f"✓ 验收档案（审计/复现用）：{archive_path}")
        print(f"（未加 --install：模板未进包。加 --install 复制到 {tdir}/；"
              f"上面两份都在用户产物目录 {out_dir}/）")
    return 0


def _product_root() -> str:
    try:
        sys.path.insert(0, os.path.join(SKILL, "reconcile"))
        from paths import product_root  # noqa: PLC0415
        return product_root()
    except Exception:  # noqa: BLE001
        return os.getcwd()


def _sha1(path: str) -> str:
    import hashlib
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
