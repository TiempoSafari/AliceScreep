#!/usr/bin/env python3
"""多站点小说下载器：CLI + GUI 入口。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from threading import Thread

from downloader import DownloadPayload, download_novel_payload, normalize_chapter_title, run_download, save_payload_to_epub
from downloader.text import safe_filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 AliceSW/SilverNoelle 小说并导出成 EPUB")
    parser.add_argument("index_url", nargs="?", help="小说链接，例如 https://www.alicesw.tw/novel/2735.html 或 https://silvernoelle.com/category/.../")
    parser.add_argument("-o", "--output", default="novel.epub", help="输出 EPUB 文件路径（默认自动使用书名命名）")
    parser.add_argument("--delay", type=float, default=0.2, help="每章下载间隔秒数，默认 0.2")
    parser.add_argument("--start", type=int, default=1, help="起始章节（从1开始）")
    parser.add_argument("--end", type=int, default=0, help="结束章节（0 表示到最后）")
    parser.add_argument("--no-simplified", action="store_true", help="关闭繁体转简体（默认开启）")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    return parser.parse_args()


def launch_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        print(f"GUI 启动失败：{exc}")
        return 1

    root = tk.Tk()
    root.title("AliceScreep 小说下载器")
    root.geometry("1080x820")
    root.minsize(980, 760)
    root.configure(bg="#e9eef6")

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure("App.TFrame", background="#e9eef6")
    style.configure("Surface.TFrame", background="#ffffff")
    style.configure("Card.TLabelframe", background="#ffffff", relief="solid", borderwidth=1)
    style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#1b2940", font=("Segoe UI", 10, "bold"))
    style.configure("Head.TLabel", background="#e9eef6", foreground="#0f1b2d", font=("Segoe UI", 21, "bold"))
    style.configure("SubHead.TLabel", background="#e9eef6", foreground="#667085", font=("Segoe UI", 10))
    style.configure("TLabel", font=("Segoe UI", 10))
    style.configure("TEntry", padding=7)
    style.configure("TButton", padding=(12, 8), font=("Segoe UI", 10))
    style.configure("Primary.TButton", padding=(14, 9), font=("Segoe UI", 10, "bold"), background="#2f6fed", foreground="#ffffff")
    style.map("Primary.TButton", background=[("active", "#3e7cff")])

    app = ttk.Frame(root, style="App.TFrame", padding=18)
    app.pack(fill="both", expand=True)

    header = ttk.Frame(app, style="App.TFrame")
    header.pack(fill="x", pady=(0, 10))
    ttk.Label(header, text="小说下载与编辑工作台", style="Head.TLabel").pack(anchor="w")
    ttk.Label(header, text="扁平化卡片布局 · 支持下载、编辑封面、拖拽排序、正则批量改名", style="SubHead.TLabel").pack(anchor="w", pady=(3, 0))

    config_card = ttk.LabelFrame(app, text="下载参数", style="Card.TLabelframe", padding=14)
    config_card.pack(fill="x")

    url_var = tk.StringVar()
    output_var = tk.StringVar(value=str(Path.cwd() / "novel.epub"))
    start_var = tk.StringVar(value="1")
    end_var = tk.StringVar(value="0")
    delay_var = tk.StringVar(value="0.2")
    simplified_var = tk.IntVar(value=1)
    status_var = tk.StringVar(value="就绪")

    ttk.Label(config_card, text="小说链接").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(config_card, textvariable=url_var).grid(row=0, column=1, columnspan=5, sticky="we", pady=4)

    ttk.Label(config_card, text="输出文件").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(config_card, textvariable=output_var).grid(row=1, column=1, columnspan=4, sticky="we", pady=4)

    def browse_output() -> None:
        path = filedialog.asksaveasfilename(title="保存 EPUB", defaultextension=".epub", filetypes=[("EPUB", "*.epub")])
        if path:
            output_var.set(path)

    ttk.Button(config_card, text="浏览", command=browse_output).grid(row=1, column=5, padx=(8, 0), sticky="we")

    ttk.Label(config_card, text="起始章节").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
    ttk.Entry(config_card, textvariable=start_var, width=8).grid(row=2, column=1, sticky="w", pady=(4, 0))
    ttk.Label(config_card, text="结束章节").grid(row=2, column=2, sticky="e", padx=(14, 8), pady=(4, 0))
    ttk.Entry(config_card, textvariable=end_var, width=8).grid(row=2, column=3, sticky="w", pady=(4, 0))
    ttk.Label(config_card, text="下载间隔(秒)").grid(row=2, column=4, sticky="e", padx=(14, 8), pady=(4, 0))
    ttk.Entry(config_card, textvariable=delay_var, width=8).grid(row=2, column=5, sticky="w", pady=(4, 0))

    ttk.Checkbutton(config_card, text="保存前繁体转简体", variable=simplified_var).grid(row=3, column=1, columnspan=3, sticky="w", pady=(8, 0))

    for col, weight in enumerate((0, 4, 0, 0, 0, 0)):
        config_card.columnconfigure(col, weight=weight)

    center = ttk.Frame(app, style="App.TFrame")
    center.pack(fill="both", expand=True, pady=(10, 8))

    log_card = ttk.LabelFrame(center, text="下载日志", style="Card.TLabelframe", padding=10)
    log_card.pack(side="left", fill="both", expand=True, padx=(0, 8))
    log_box = ScrolledText(log_card, height=24, bg="#0b1220", fg="#cfe7ff", insertbackground="#ffffff", relief="flat", padx=12, pady=12, font=("Consolas", 10))
    log_box.pack(fill="both", expand=True)

    tips_card = ttk.LabelFrame(center, text="操作提示", style="Card.TLabelframe", padding=10)
    tips_card.pack(side="left", fill="y")
    tips = [
        "1) 填写链接后点击开始下载",
        "2) 下载完成会自动弹出编辑器",
        "3) 可拖拽章节调整顺序",
        "4) 可用正则批量改章节名",
        "5) 支持替换封面并导出 EPUB",
    ]
    for item in tips:
        ttk.Label(tips_card, text=f"• {item}", wraplength=240, justify="left").pack(anchor="w", pady=2)

    progress = ttk.Progressbar(app, mode="indeterminate")
    progress.pack(fill="x")

    bottom = ttk.Frame(app, style="App.TFrame")
    bottom.pack(fill="x", pady=(8, 0))
    ttk.Label(bottom, textvariable=status_var, style="SubHead.TLabel").pack(side="left")

    downloading = {"active": False}

    def log(msg: str) -> None:
        def _append() -> None:
            log_box.insert("end", msg + "\n")
            log_box.see("end")
        root.after(0, _append)

    def set_running(active: bool) -> None:
        downloading["active"] = active
        status_var.set("下载中..." if active else "就绪")
        if active:
            progress.start(9)
        else:
            progress.stop()

    def open_editor(payload: DownloadPayload, output_path: str) -> bool:
        editor = tk.Toplevel(root)
        editor.title("编辑章节与封面")
        editor.geometry("1060x760")
        editor.transient(root)
        editor.grab_set()
        editor.configure(bg="#e9eef6")

        ef = ttk.Frame(editor, style="App.TFrame", padding=14)
        ef.pack(fill="both", expand=True)

        top = ttk.LabelFrame(ef, text="书籍信息", padding=10, style="Card.TLabelframe")
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text="书名").grid(row=0, column=0, sticky="w")
        title_var = tk.StringVar(value=payload.meta.title)
        ttk.Entry(top, textvariable=title_var).grid(row=0, column=1, columnspan=3, sticky="we", pady=4)

        ttk.Label(top, text="作者").grid(row=1, column=0, sticky="w")
        author_var = tk.StringVar(value=payload.meta.author)
        ttk.Entry(top, textvariable=author_var, width=30).grid(row=1, column=1, sticky="w", pady=4)

        cover_var = tk.StringVar(value=payload.cover_name or "(当前无封面)")
        ttk.Label(top, text="封面").grid(row=1, column=2, sticky="e")
        ttk.Label(top, textvariable=cover_var).grid(row=1, column=3, sticky="w", padx=(6, 0))

        def replace_cover() -> None:
            path = filedialog.askopenfilename(
                title="选择封面图片",
                filetypes=[("Image", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
            )
            if not path:
                return
            try:
                raw = Path(path).read_bytes()
            except Exception as exc:
                messagebox.showerror("错误", f"读取封面失败: {exc}", parent=editor)
                return
            suffix = Path(path).suffix.lower()
            if suffix == ".png":
                payload.cover_type = "image/png"
                payload.cover_name = "cover.png"
            else:
                payload.cover_type = "image/jpeg"
                payload.cover_name = "cover.jpg"
            payload.cover_bytes = raw
            cover_var.set(Path(path).name)

        ttk.Button(top, text="更换封面", command=replace_cover).grid(row=1, column=4, padx=(10, 0))

        body = ttk.Frame(ef, style="App.TFrame")
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="章节列表（拖拽排序）", padding=10, style="Card.TLabelframe")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        chapter_list = tk.Listbox(left, height=24, activestyle="none", selectmode="browse", bg="#f8fbff", relief="flat")
        chapter_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=chapter_list.yview)
        scrollbar.pack(side="right", fill="y")
        chapter_list.configure(yscrollcommand=scrollbar.set)

        def refresh_list(select_idx: int | None = None) -> None:
            chapter_list.delete(0, "end")
            for i, ch in enumerate(payload.chapters, start=1):
                chapter_list.insert("end", f"{i:03d}. {ch.title}")
            if payload.chapters:
                idx = 0 if select_idx is None else max(0, min(select_idx, len(payload.chapters) - 1))
                chapter_list.selection_set(idx)
                chapter_list.activate(idx)
                chapter_list.see(idx)

        refresh_list(0)

        right = ttk.LabelFrame(body, text="编辑工具", padding=10, style="Card.TLabelframe")
        right.pack(side="left", fill="y")

        chapter_title_var = tk.StringVar()
        ttk.Label(right, text="单章标题").pack(anchor="w")
        ttk.Entry(right, textvariable=chapter_title_var, width=34).pack(fill="x", pady=(2, 6))

        def on_select(_event=None) -> None:
            sel = chapter_list.curselection()
            if sel:
                chapter_title_var.set(payload.chapters[sel[0]].title)

        def apply_title() -> None:
            sel = chapter_list.curselection()
            if not sel:
                return
            idx = sel[0]
            payload.chapters[idx].title = normalize_chapter_title(chapter_title_var.get())
            refresh_list(idx)

        chapter_list.bind("<<ListboxSelect>>", on_select)
        ttk.Button(right, text="应用到当前章节", command=apply_title).pack(fill="x")

        ttk.Separator(right).pack(fill="x", pady=10)
        ttk.Label(right, text="批量正则替换").pack(anchor="w")
        regex_var = tk.StringVar()
        repl_var = tk.StringVar()
        ttk.Entry(right, textvariable=regex_var, width=34).pack(fill="x", pady=(2, 4))
        ttk.Entry(right, textvariable=repl_var, width=34).pack(fill="x", pady=(0, 6))
        ttk.Label(right, text="上框: 正则；下框: 替换文本（支持\1）", style="SubHead.TLabel").pack(anchor="w")

        scope_var = tk.StringVar(value="all")
        ttk.Radiobutton(right, text="作用于全部章节", value="all", variable=scope_var).pack(anchor="w")
        ttk.Radiobutton(right, text="仅作用于当前选中", value="selected", variable=scope_var).pack(anchor="w")

        def apply_batch() -> None:
            pattern = regex_var.get().strip()
            repl = repl_var.get()
            if not pattern:
                messagebox.showerror("参数错误", "请填写正则表达式", parent=editor)
                return
            try:
                rgx = re.compile(pattern)
            except re.error as exc:
                messagebox.showerror("正则错误", str(exc), parent=editor)
                return

            if scope_var.get() == "selected":
                sel = chapter_list.curselection()
                if not sel:
                    messagebox.showerror("提示", "请先选择章节", parent=editor)
                    return
                targets = [sel[0]]
            else:
                targets = list(range(len(payload.chapters)))

            changed = 0
            for idx in targets:
                old = payload.chapters[idx].title
                new = normalize_chapter_title(rgx.sub(repl, old))
                if new != old:
                    payload.chapters[idx].title = new
                    changed += 1
            refresh_list(targets[0] if targets else 0)
            messagebox.showinfo("完成", f"已更新 {changed} 个章节标题", parent=editor)

        ttk.Button(right, text="执行批量替换", command=apply_batch).pack(fill="x", pady=(6, 0))

        drag_state = {"from": None}

        def on_drag_start(event) -> None:
            drag_state["from"] = chapter_list.nearest(event.y)

        def on_drag_motion(event) -> None:
            src = drag_state.get("from")
            dst = chapter_list.nearest(event.y)
            if src is None or dst == src or dst < 0 or dst >= len(payload.chapters):
                return
            payload.chapters.insert(dst, payload.chapters.pop(src))
            drag_state["from"] = dst
            refresh_list(dst)

        chapter_list.bind("<Button-1>", on_drag_start)
        chapter_list.bind("<B1-Motion>", on_drag_motion)

        saved = {"ok": False}

        def save_and_close() -> None:
            payload.meta.title = title_var.get().strip() or payload.meta.title
            payload.meta.author = author_var.get().strip() or payload.meta.author
            if output_path.endswith("novel.epub"):
                output_var.set(str(Path(output_path).with_name(safe_filename(payload.meta.title))))
            saved["ok"] = True
            editor.destroy()

        actions = ttk.Frame(ef, style="App.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="保存修改并导出", style="Primary.TButton", command=save_and_close).pack(side="right")
        ttk.Button(actions, text="取消", command=editor.destroy).pack(side="right", padx=(0, 8))

        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)
        editor.wait_window()
        return saved["ok"]

    def start_download() -> None:
        if downloading["active"]:
            messagebox.showinfo("提示", "下载正在进行中，请稍候。")
            return
        input_url = url_var.get().strip()
        output_path = output_var.get().strip()
        if not input_url or not output_path:
            messagebox.showerror("参数错误", "请填写小说链接与输出文件路径。")
            return

        try:
            start = int(start_var.get().strip())
            end = int(end_var.get().strip())
            delay = float(delay_var.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "起始/结束章节必须是整数，间隔必须是数字。")
            return

        set_running(True)
        log_box.delete("1.0", "end")
        log("✨ 开始下载 EPUB 数据...（完成后自动打开编辑器）")

        def worker() -> None:
            payload = download_novel_payload(input_url, start, end, delay, logger=log, to_simplified=bool(simplified_var.get()))

            def done() -> None:
                set_running(False)
                if payload is None:
                    messagebox.showerror("失败", "下载失败，请检查日志。")
                    return
                if output_path.endswith("novel.epub"):
                    output_var.set(str(Path(output_path).with_name(safe_filename(payload.meta.title))))
                if not open_editor(payload, output_var.get().strip()):
                    log("❌ 已取消保存")
                    return
                save_payload_to_epub(payload, Path(output_var.get().strip()), logger=log)
                status_var.set("导出完成")
                messagebox.showinfo("完成", "下载完成，已编辑并生成 EPUB。")

            root.after(0, done)

        Thread(target=worker, daemon=True).start()

    ttk.Button(bottom, text="开始下载并编辑", style="Primary.TButton", command=start_download).pack(side="right")
    ttk.Button(bottom, text="退出", command=root.destroy).pack(side="right", padx=(0, 8))

    root.mainloop()
    return 0


def main() -> int:
    args = parse_args()
    if args.gui or not args.index_url:
        return launch_gui()
    return run_download(args.index_url, Path(args.output), args.start, args.end, args.delay, to_simplified=not args.no_simplified)


if __name__ == "__main__":
    raise SystemExit(main())
