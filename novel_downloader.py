#!/usr/bin/env python3
"""多站点小说下载器：CLI + GUI 入口。"""

from __future__ import annotations

import argparse
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
    root.geometry("920x700")

    container = ttk.Frame(root, padding=16)
    container.pack(fill="both", expand=True)

    card = ttk.LabelFrame(container, text="下载参数", padding=12)
    card.pack(fill="x")

    url_var = tk.StringVar()
    output_var = tk.StringVar(value=str(Path.cwd() / "novel.epub"))
    start_var = tk.StringVar(value="1")
    end_var = tk.StringVar(value="0")
    delay_var = tk.StringVar(value="0.2")
    simplified_var = tk.IntVar(value=1)

    ttk.Label(card, text="小说链接").grid(row=0, column=0, sticky="w")
    ttk.Entry(card, textvariable=url_var, width=72).grid(row=0, column=1, columnspan=3, sticky="we", pady=4)

    ttk.Label(card, text="输出文件").grid(row=1, column=0, sticky="w")
    ttk.Entry(card, textvariable=output_var, width=68).grid(row=1, column=1, columnspan=2, sticky="we", pady=4)

    def browse_output() -> None:
        path = filedialog.asksaveasfilename(title="保存 EPUB", defaultextension=".epub", filetypes=[("EPUB", "*.epub")])
        if path:
            output_var.set(path)

    ttk.Button(card, text="浏览", command=browse_output).grid(row=1, column=3, padx=(6, 0))

    ttk.Label(card, text="起始章节").grid(row=2, column=0, sticky="w")
    ttk.Entry(card, textvariable=start_var, width=8).grid(row=2, column=1, sticky="w")
    ttk.Label(card, text="结束章节").grid(row=2, column=2, sticky="e")
    ttk.Entry(card, textvariable=end_var, width=8).grid(row=2, column=3, sticky="w")

    ttk.Label(card, text="下载间隔(秒)").grid(row=3, column=0, sticky="w")
    ttk.Entry(card, textvariable=delay_var, width=8).grid(row=3, column=1, sticky="w")
    ttk.Checkbutton(card, text="保存前繁体转简体", variable=simplified_var).grid(row=3, column=2, columnspan=2, sticky="w")

    log_box = ScrolledText(container, height=24)
    log_box.pack(fill="both", expand=True, pady=(10, 8))

    progress = ttk.Progressbar(container, mode="indeterminate")
    progress.pack(fill="x")

    footer = ttk.Frame(container)
    footer.pack(fill="x", pady=(8, 0))

    downloading = {"active": False}

    def log(msg: str) -> None:
        def _append() -> None:
            log_box.insert("end", msg + "\n")
            log_box.see("end")
        root.after(0, _append)

    def set_running(active: bool) -> None:
        downloading["active"] = active
        progress.start(10) if active else progress.stop()

    def open_editor(payload: DownloadPayload, output_path: str) -> bool:
        editor = tk.Toplevel(root)
        editor.title("编辑章节与封面")
        editor.geometry("860x620")
        editor.transient(root)
        editor.grab_set()

        frame = ttk.Frame(editor, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="书名").grid(row=0, column=0, sticky="w")
        title_var = tk.StringVar(value=payload.meta.title)
        ttk.Entry(frame, textvariable=title_var, width=72).grid(row=0, column=1, columnspan=3, sticky="we", pady=4)

        ttk.Label(frame, text="作者").grid(row=1, column=0, sticky="w")
        author_var = tk.StringVar(value=payload.meta.author)
        ttk.Entry(frame, textvariable=author_var, width=30).grid(row=1, column=1, sticky="w", pady=4)

        chapter_list = tk.Listbox(frame, height=20)
        chapter_list.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(8, 6))
        for i, ch in enumerate(payload.chapters, start=1):
            chapter_list.insert("end", f"{i:03d}. {ch.title}")

        edit_frame = ttk.Frame(frame)
        edit_frame.grid(row=3, column=0, columnspan=4, sticky="we")
        chapter_title_var = tk.StringVar()
        ttk.Label(edit_frame, text="章节名").pack(side="left")
        ttk.Entry(edit_frame, textvariable=chapter_title_var).pack(side="left", fill="x", expand=True, padx=8)

        def on_select(_event=None) -> None:
            sel = chapter_list.curselection()
            if sel:
                chapter_title_var.set(payload.chapters[sel[0]].title)

        def apply_title() -> None:
            sel = chapter_list.curselection()
            if not sel:
                return
            idx = sel[0]
            new_title = normalize_chapter_title(chapter_title_var.get())
            payload.chapters[idx].title = new_title
            chapter_list.delete(idx)
            chapter_list.insert(idx, f"{idx+1:03d}. {new_title}")

        chapter_list.bind("<<ListboxSelect>>", on_select)
        ttk.Button(edit_frame, text="应用章节名", command=apply_title).pack(side="left")

        saved = {"ok": False}

        def save_and_close() -> None:
            payload.meta.title = title_var.get().strip() or payload.meta.title
            payload.meta.author = author_var.get().strip() or payload.meta.author
            if output_path.endswith("novel.epub"):
                suggested = Path(output_path).with_name(safe_filename(payload.meta.title))
                output_var.set(str(suggested))
            saved["ok"] = True
            editor.destroy()

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=4, sticky="we", pady=(8, 0))
        ttk.Button(actions, text="保存修改并导出", command=save_and_close).pack(side="right")
        ttk.Button(actions, text="取消", command=editor.destroy).pack(side="right", padx=(0, 8))

        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        frame.rowconfigure(2, weight=1)
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
                    return
                save_payload_to_epub(payload, Path(output_var.get().strip()), logger=log)
                messagebox.showinfo("完成", "下载完成，已编辑并生成 EPUB。")

            root.after(0, done)

        Thread(target=worker, daemon=True).start()

    ttk.Button(footer, text="开始下载并编辑", command=start_download).pack(side="left")
    ttk.Button(footer, text="退出", command=root.destroy).pack(side="right")

    root.mainloop()
    return 0


def main() -> int:
    args = parse_args()
    if args.gui or not args.index_url:
        return launch_gui()
    return run_download(args.index_url, Path(args.output), args.start, args.end, args.delay, to_simplified=not args.no_simplified)


if __name__ == "__main__":
    raise SystemExit(main())
