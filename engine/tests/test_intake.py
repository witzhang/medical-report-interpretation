#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输入预检（intake）回归：**"不支持"必须带原因与出路**。

    python3 tests/test_intake.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup import intake  # noqa: E402

TMP = tempfile.mkdtemp(prefix="_tmp_intake_")   # 系统临时区（不落进包）


def _setup() -> None:
    os.makedirs(TMP, exist_ok=True)
    # 真文字层（字数过 120 阈值）
    d = fitz.open()
    p = d.new_page()
    p.insert_text((50, 60), "某某医院 健康体检报告单 报告编号: X-1 姓名: 受检者", fontsize=9)
    for i in range(12):
        p.insert_text((50, 80 + i * 12), f"项目{i} 结果{i} 参考值{i} 提示{i} 补充说明文字", fontsize=9)
    d.save(os.path.join(TMP, "text.pdf"))
    d.close()
    # 扫描件：只有图
    d = fitz.open()
    p = d.new_page()
    p.insert_image(fitz.Rect(0, 0, 500, 700),
                   pixmap=fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400)))
    d.save(os.path.join(TMP, "scan.pdf"))
    d.close()
    # 伪文字层：图 + 不可见文字（OCR 叠层典型）
    d = fitz.open()
    p = d.new_page()
    p.insert_image(fitz.Rect(0, 0, 400, 300),
                   pixmap=fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 300)))
    p.insert_text((50, 60), "ALT 61 U/L 0-40 HGB 149 g/L 130-175", fontsize=12,
                  render_mode=3)
    d.save(os.path.join(TMP, "pseudo.pdf"))
    d.close()
    # 加密
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 72), "检验报告单 项目 结果 参考值")
    d.save(os.path.join(TMP, "enc.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256,
           user_pw="x")
    d.close()
    # OFD / docx / xlsx / csv / 图片 / 伴随文件
    for name, member in (("a.ofd", "OFD.xml"), ("b.docx", "word/document.xml"),
                         ("c.xlsx", "xl/workbook.xml")):
        z = zipfile.ZipFile(os.path.join(TMP, name), "w")
        z.writestr(member, "<x/>")
        z.close()
    open(os.path.join(TMP, "d.csv"), "w", encoding="utf-8").write("a,b\n1,2\n")
    open(os.path.join(TMP, "e.jpg"), "wb").write(b"\xff\xd8\xff")
    open(os.path.join(TMP, "note.txt"), "w", encoding="utf-8").write("合成真源，伴随文件")
    open(os.path.join(TMP, "make.py"), "w", encoding="utf-8").write("print(1)")


def _v(name: str) -> intake.FileVerdict:
    return intake.classify(os.path.join(TMP, name))


def test_text_layer_pdf_is_usable():
    v = _v("text.pdf")
    assert v.kind == "pdf_text" and v.usable and not v.degraded


def test_scanned_pdf_rejected_with_next_step():
    v = _v("scan.pdf")
    assert v.kind == "pdf_scan" and not v.usable
    assert "电子版" in v.next_step and "OCR" in v.next_step


def test_pseudo_text_layer_is_degraded_not_silent():
    """有文字层但来自 OCR：**必须标出来**（最危险的一类静默错）。"""
    v = _v("pseudo.pdf")
    assert v.kind == "pdf_pseudo_text"
    assert v.usable and v.degraded
    assert "OCR" in v.reason and "--allow-degraded" in v.next_step


def test_encrypted_pdf_rejected():
    v = _v("enc.pdf")
    assert v.kind == "pdf_encrypted" and not v.usable


def test_office_formats_rejected_with_paths():
    for name, kind in (("a.ofd", "ofd"), ("b.docx", "docx"), ("c.xlsx", "xlsx"),
                       ("d.csv", "csv"), ("e.jpg", "image")):
        v = _v(name)
        assert v.kind == kind and not v.usable, name
        assert v.next_step, f"{name} 必须给下一步（不能只报不支持）"


def test_companion_files_are_ignored_not_blocked():
    """数据真源/脚本等伴随文件不算"不可用"（否则正常目录会被判成有问题）。"""
    for name in ("note.txt", "make.py"):
        v = _v(name)
        assert v.ignored and v.usable, name


def test_summarize_excludes_ignored():
    s = intake.summarize(intake.scan_dir(TMP))
    assert s["ignored"] == 2, s          # note.txt + make.py
    assert s["blocked"] == 7, s          # scan+enc+ofd+docx+xlsx+csv+jpg
    assert s["degraded"] == 1 and s["usable"] == 1


def test_every_blocked_kind_has_actionable_next_step():
    """红线：任何"不可用"都必须给出用户可执行的下一步。"""
    for v in intake.scan_dir(TMP):
        if not v.usable:
            assert len(v.next_step) >= 8, f"{v.kind} 的 next_step 太短/缺失"


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed, skipped = [], 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except BaseException as e:  # noqa: BLE001
            if type(e).__name__ in ("Skipped", "DataMissing"):
                skipped += 1
                print(f"  · {name} 跳过：{e}")
                continue
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    done = len(tests) - skipped
    print(f"\n{done - len(failed)}/{done} 通过" + (f"（跳过 {skipped}）" if skipped else ""))
    return 1 if failed else 0


#: pytest 入口
setup_module = _setup


def _main_with_setup() -> int:
    _setup()
    return _main()


if __name__ == "__main__":
    sys.exit(_main_with_setup())
