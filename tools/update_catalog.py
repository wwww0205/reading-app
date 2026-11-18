#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动维护 catalog.json（章节目录）

它做三件事：
1. 扫描 books/book_XX/ 里的章节文件：
   - 标准名 ch1.txt、ch2.txt ……
   - 网站下载下来的原名（如 有匪_第67章_备战-镇魂小说网_xxx.txt）→ 自动改名成 ch67.txt
2. 跟现有的 catalog.json 对比：缺的补上、多出来的（文件已删）去掉
3. 顺手检查 store.json 和每个 catalog.json 的 JSON 格式有没有写错

重要原则：已经写好的章节标题，一律保留不动，永远不会被你手动写的东西覆盖。
只有新加的文件会被自动补一条，标题先写成"第N章"，你想改就去 GitHub 网页上改那一条。

用法：
    python tools/update_catalog.py
出错时退出码为 1（GitHub Actions 会显示红叉并阻止提交）。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]     # 仓库根目录
BOOKS_DIR = ROOT / "books"
STORE = ROOT / "store.json"
CH_PATTERN = re.compile(r"^ch(\d+)\.txt$", re.IGNORECASE)
# 文件名里带"第67章""第六十七章"也能认出来
CN_CHAPTER = re.compile(r"第\s*([0-9]+|[零一二三四五六七八九十百]+)\s*章")
CN_NUM = "零一二三四五六七八九"
CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}
# 下载站塞进正文的广告行，读起来碍眼，顺手删掉
AD_LINES = ("镇魂小说网", "请记住本站", "本章未完", "手机阅读", "www.", "http://", "https://")


def parse_chapter_num(s: str):
    """'67' -> 67；'六十七' -> 67"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        left, _, right = s.partition("十")
        tens = CN_DIGIT.get(left, 1) if left else 1
        ones = CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(s) == 1:
        return CN_DIGIT.get(s)
    return None


# 兜底：文件名里没有"第N章"时，就从名字里找一个数字当章号
NUM_IN_NAME = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
YEAR_LIKE = set(range(1900, 2100))


def guess_num_from_name(stem: str):
    """有且只有一个非年份数字时才认，多个数字一律不认（防止把日期当章号）"""
    nums = [int(x) for x in NUM_IN_NAME.findall(stem)]
    nums = [n for n in nums if n > 0 and n not in YEAR_LIKE]
    if len(nums) == 1:
        return nums[0]
    return None


def read_text_any(path: Path) -> str:
    """网站下载的 txt 可能是 GBK，挨个编码试一遍"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def clean_ads(text: str) -> str:
    """去掉下载站塞进正文的广告行"""
    keep = [ln for ln in text.splitlines() if not any(a in ln for a in AD_LINES)]
    return "\n".join(keep).strip() + "\n"


def cn_number(n: int) -> str:
    """1 -> 一, 10 -> 十, 21 -> 二十一（只处理 1~99，够用）"""
    if n <= 0:
        return str(n)
    if n < 10:
        return CN_NUM[n]
    if n < 20:
        return "十" + (CN_NUM[n % 10] if n % 10 else "")
    tens, ones = divmod(n, 10)
    return CN_NUM[tens] + "十" + (CN_NUM[ones] if ones else "")


def guess_title(path: Path, number: int) -> str:
    """优先用正文第一行如果它长得像标题（第X章 xxx），否则用 第N章"""
    try:
        first_line = read_text_any(path).splitlines()[0].strip()
    except Exception:
        return f"第{cn_number(number)}章"
    if re.match(r"^第\s*[一二三四五六七八九十百0-9]+\s*章", first_line) and len(first_line) <= 40:
        return first_line
    return f"第{cn_number(number)}章"


errors = []
changed_books = []


# ---------- 1. 检查 store.json ----------
if not STORE.exists():
    errors.append("找不到 store.json（书城清单），请确认它在仓库最外层。")
else:
    try:
        store = json.loads(STORE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        store = None
        errors.append(
            f"store.json 格式写错了：第 {e.lineno} 行第 {e.colno} 列附近有问题。"
            f"常见原因：漏了英文逗号、多了一个逗号、用了中文逗号，或者引号是中文引号。"
        )
    if isinstance(store, list):
        for book in store:
            folder = book.get("folder")
            cover = book.get("cover")
            if folder and not (ROOT / folder).is_dir():
                errors.append(f"store.json 里这本书的 folder 指向的文件夹不存在：{folder}")
            if cover and cover.startswith(("http://", "https://")):
                pass  # 用外链图床，正常
            elif cover and not (ROOT / cover).exists():
                errors.append(f"store.json 里这本书的封面文件不存在：{cover}")
    elif store is not None:
        errors.append("store.json 最外层应该是 [ ] 方括号包着的列表，现在不是。")


# ---------- 2. 逐本书处理 catalog.json ----------
if not BOOKS_DIR.is_dir():
    errors.append("找不到 books 文件夹。")
    book_dirs = []
else:
    book_dirs = sorted([p for p in BOOKS_DIR.iterdir() if p.is_dir() and p.name.startswith("book")])

if not book_dirs:
    print("没有找到任何书（books/book_XX 文件夹），什么也不用做。")

for book_dir in book_dirs:
    cat_path = book_dir / "catalog.json"
    book_id = book_dir.name

    # 扫描真实存在的章节文件
    files = {}
    renamed = []
    replaced = []
    dup_names = []
    unmatched = []

    # 第一遍：标准名 ch12.txt
    for f in book_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".txt":
            m = CH_PATTERN.match(f.name)
            if m:
                files[int(m.group(1))] = f.name

    # 第二遍：网站下载的原名，自动改名成 chN.txt
    for f in sorted(book_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".txt":
            continue
        if CH_PATTERN.match(f.name):
            continue
        m = CN_CHAPTER.search(f.stem)
        if not m:
            continue
        num = parse_chapter_num(m.group(1))
        if not num or num <= 0:
            continue
        if num in files:
            # 这个章号已经有 chN.txt 了：内容一样就当重复文件删掉，不一样就用新的覆盖
            target = book_dir / f"ch{num}.txt"
            cleaned = clean_ads(read_text_any(f)) if f.stat().st_size else ""
            old = read_text_any(target).strip()
            if old == cleaned.strip():
                f.unlink()
                dup_names.append(f"{f.name}（和 ch{num}.txt 内容相同，重复文件已删）")
            else:
                target.write_text(cleaned, encoding="utf-8")
                f.unlink()
                replaced.append(f"{f.name}  →  已覆盖旧的 ch{num}.txt")
            continue
        target = book_dir / f"ch{num}.txt"
        text = read_text_any(f)
        cleaned = clean_ads(text) if text else ""
        try:
            target.write_text(cleaned, encoding="utf-8")
            f.unlink()
        except Exception as e:
            errors.append(f"{book_id}：改名 {f.name} 失败（{e}）")
            continue
        files[num] = target.name
        renamed.append(f"{f.name}  →  {target.name}")

    # 第三遍：兜底——名字里既没"第N章"也没 chN，就试着自己找数字
    for f in sorted(book_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".txt":
            continue
        if CH_PATTERN.match(f.name):
            continue
        if CN_CHAPTER.search(f.stem):
            continue
        stem = re.sub(r"[_\-\s]+$", "", f.stem)
        num = guess_num_from_name(stem)
        if num is None:
            unmatched.append(f.name)
            continue
        if num in files:
            target = book_dir / f"ch{num}.txt"
            cleaned = clean_ads(read_text_any(f)) if f.stat().st_size else ""
            if read_text_any(target).strip() == cleaned.strip():
                f.unlink()
                dup_names.append(f"{f.name}（和 ch{num}.txt 内容相同，重复文件已删）")
            else:
                target.write_text(cleaned, encoding="utf-8")
                f.unlink()
                replaced.append(f"{f.name}  →  已覆盖旧的 ch{num}.txt")
            continue
        target = book_dir / f"ch{num}.txt"
        try:
            target.write_text(clean_ads(read_text_any(f)), encoding="utf-8")
            f.unlink()
        except Exception as e:
            errors.append(f"{book_id}：改名 {f.name} 失败（{e}）")
            continue
        files[num] = target.name
        renamed.append(f"{f.name}  →  {target.name}（按文件名里的数字 {num} 判定）")

    if not files:
        print(f"[跳过] {book_id} 里没有 ch1.txt 这类章节文件")
        continue

    # 读现有的 catalog（ tolerate 不存在 ）
    existing = {}
    if cat_path.exists():
        try:
            data = json.loads(cat_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(
                f"{book_id}/catalog.json 格式写错了：第 {e.lineno} 行第 {e.colno} 列附近有问题。"
                f"常见原因：两个 {{}} 之间漏了英文逗号、最后一个 {{}} 后面多了逗号、引号是中文的。"
            )
            continue
        chapters = data.get("chapters") if isinstance(data, dict) else None
        if not isinstance(chapters, list):
            errors.append(f"{book_id}/catalog.json 里缺少 chapters 列表，或者格式不对。")
            continue
        for item in chapters:
            if isinstance(item, dict) and item.get("file"):
                existing[item["file"]] = item.get("title") or ""

    # 合并：保留已有标题，补上缺的，删掉文件已消失的
    new_chapters = []
    added = []
    removed = []
    for num in sorted(files):
        fname = files[num]
        if fname in existing:
            title = existing[fname] or guess_title(book_dir / fname, num)
        else:
            title = guess_title(book_dir / fname, num)
            added.append(f"{title} ({fname})")
        new_chapters.append({"title": title, "file": fname})
    present = set(files.values())
    for fname, title in existing.items():
        if fname not in present:
            removed.append(f"{title} ({fname})")

    new_data = {"book_id": book_id, "chapters": new_chapters}

    if cat_path.exists():
        try:
            old_data = json.loads(cat_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_data = None
    else:
        old_data = None

    def report_files():
        for line in renamed:
            print(f"    ✎ 改名  {line}")
        for line in replaced:
            print(f"    ↻ 覆盖  {line}")
        for line in dup_names:
            print(f"    ✓ 去重  {line}")
        for name in unmatched:
            print(f"    ? 认不出  {name}（名字里找不到章号，已跳过；手动改成 ch? 号.txt 就能收进来）")

    if old_data == new_data:
        print(f"[不变] {book_id}：共 {len(new_chapters)} 章，目录本来就是对的")
        report_files()
        continue

    cat_path.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    changed_books.append(book_id)
    print(f"[已更新] {book_id}/catalog.json：共 {len(new_chapters)} 章")
    report_files()
    for line in added:
        print(f"    + 补上  {line}")
    for line in removed:
        print(f"    - 移除  {line}")


# ---------- 3. 结论 ----------
print("\n" + "=" * 46)
if errors:
    print("发现以下问题，需要你手动修（这次没有提交任何改动）：")
    for e in errors:
        print("  ✗ " + e)
    print("=" * 46)
    sys.exit(1)

if changed_books:
    print("搞定，目录已更新：" + "、".join(changed_books))
else:
    print("检查通过，目录没有需要改动的地方。")
print("=" * 46)
