#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动维护 catalog.json（章节目录）

它做三件事：
1. 扫描 books/book_XX/ 里所有的 ch1.txt、ch2.txt、ch3.txt……
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

CN_NUM = "零一二三四五六七八九"


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
        first_line = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except Exception:
        return f"第{cn_number(number)}章"
    if re.match(r"^第[一二三四五六七八九十百0-9]+章", first_line) and len(first_line) <= 40:
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
    for f in book_dir.iterdir():
        m = CH_PATTERN.match(f.name)
        if m and f.is_file():
            files[int(m.group(1))] = f.name

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

    if old_data == new_data:
        print(f"[不变] {book_id}：共 {len(new_chapters)} 章，目录本来就是对的")
        continue

    cat_path.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    changed_books.append(book_id)
    print(f"[已更新] {book_id}/catalog.json：共 {len(new_chapters)} 章")
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
