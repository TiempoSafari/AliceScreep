#!/usr/bin/env python3
"""多站点小说下载器：CLI + GUI 入口（PyQt5）。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

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
        from PyQt5.QtCore import Qt, QThread, pyqtSignal
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QButtonGroup,
            QDialog,
            QFileDialog,
            QFrame,
            QGridLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QProgressBar,
            QRadioButton,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:
        print(f"GUI 启动失败：{exc}")
        print("请安装 PyQt5：pip install PyQt5")
        return 1

    class S:
        BG = "#edf2f8"
        CARD = "#ffffff"
        PRIMARY = "#2f6fed"
        PRIMARY_H = "#3e7cff"
        TEXT = "#1b2940"
        SUB = "#667085"
        LOG_BG = "#0b1220"
        LOG_TXT = "#cfe7ff"

    class DownloadWorker(QThread):
        log = pyqtSignal(str)
        done = pyqtSignal(object)
        failed = pyqtSignal(str)

        def __init__(self, url: str, start: int, end: int, delay: float, to_simplified: bool) -> None:
            super().__init__()
            self.url = url
            self.start_idx = start
            self.end_idx = end
            self.delay = delay
            self.to_simplified = to_simplified

        def run(self) -> None:
            try:
                payload = download_novel_payload(
                    self.url,
                    self.start_idx,
                    self.end_idx,
                    self.delay,
                    logger=lambda m: self.log.emit(m),
                    to_simplified=self.to_simplified,
                )
                self.done.emit(payload)
            except Exception as exc:
                self.failed.emit(str(exc))

    class Card(QFrame):
        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("card")

    class ChapterEditor(QDialog):
        def __init__(self, payload: DownloadPayload, output_path: str, output_edit: QLineEdit, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.payload = payload
            self.output_path = output_path
            self.output_edit = output_edit
            self.setWindowTitle("编辑章节与封面")
            self.resize(1060, 760)

            root = QVBoxLayout(self)
            root.setContentsMargins(14, 14, 14, 14)
            root.setSpacing(10)

            info_card = Card()
            info_l = QGridLayout(info_card)
            info_l.setContentsMargins(12, 12, 12, 12)

            self.title_edit = QLineEdit(payload.meta.title)
            self.author_edit = QLineEdit(payload.meta.author)
            self.cover_label = QLabel(payload.cover_name or "(当前无封面)")
            cover_btn = QPushButton("更换封面")
            cover_btn.clicked.connect(self.replace_cover)

            info_l.addWidget(QLabel("书名"), 0, 0)
            info_l.addWidget(self.title_edit, 0, 1, 1, 4)
            info_l.addWidget(QLabel("作者"), 1, 0)
            info_l.addWidget(self.author_edit, 1, 1)
            info_l.addWidget(QLabel("封面"), 1, 2)
            info_l.addWidget(self.cover_label, 1, 3)
            info_l.addWidget(cover_btn, 1, 4)
            info_l.setColumnStretch(1, 1)
            info_l.setColumnStretch(3, 1)
            root.addWidget(info_card)

            body = QHBoxLayout()
            body.setSpacing(10)
            root.addLayout(body, 1)

            list_card = Card()
            ll = QVBoxLayout(list_card)
            ll.setContentsMargins(12, 12, 12, 12)
            ll.addWidget(QLabel("章节列表（可拖拽排序）"))
            self.chapter_list = QListWidget()
            self.chapter_list.setDragDropMode(QAbstractItemView.InternalMove)
            self.chapter_list.setDefaultDropAction(Qt.MoveAction)
            self.chapter_list.currentRowChanged.connect(self.on_select)
            ll.addWidget(self.chapter_list, 1)
            body.addWidget(list_card, 1)

            tool_card = Card()
            tl = QVBoxLayout(tool_card)
            tl.setContentsMargins(12, 12, 12, 12)
            tl.setSpacing(8)
            tl.addWidget(QLabel("编辑工具"))
            tl.addWidget(QLabel("单章标题"))
            self.single_title = QLineEdit()
            tl.addWidget(self.single_title)
            btn_single = QPushButton("应用到当前章节")
            btn_single.clicked.connect(self.apply_single)
            tl.addWidget(btn_single)

            tl.addWidget(QLabel("批量正则替换"))
            self.regex_edit = QLineEdit()
            self.regex_edit.setPlaceholderText("正则表达式")
            self.repl_edit = QLineEdit()
            self.repl_edit.setPlaceholderText("替换文本（支持\\1）")
            tl.addWidget(self.regex_edit)
            tl.addWidget(self.repl_edit)
            tip = QLabel("上框: 正则；下框: 替换文本（支持\\1）")
            tip.setStyleSheet(f"color:{S.SUB};")
            tl.addWidget(tip)

            self.scope_all = QRadioButton("作用于全部章节")
            self.scope_sel = QRadioButton("仅作用于当前选中")
            self.scope_all.setChecked(True)
            grp = QButtonGroup(self)
            grp.addButton(self.scope_all)
            grp.addButton(self.scope_sel)
            tl.addWidget(self.scope_all)
            tl.addWidget(self.scope_sel)

            btn_batch = QPushButton("执行批量替换")
            btn_batch.clicked.connect(self.apply_batch)
            tl.addWidget(btn_batch)
            tl.addStretch(1)
            body.addWidget(tool_card)
            tool_card.setFixedWidth(320)

            actions = QHBoxLayout()
            actions.addStretch(1)
            cancel = QPushButton("取消")
            cancel.clicked.connect(self.reject)
            save = QPushButton("保存修改并导出")
            save.clicked.connect(self.save_and_accept)
            save.setObjectName("primary")
            actions.addWidget(cancel)
            actions.addWidget(save)
            root.addLayout(actions)

            self.refresh_chapter_list()

        def refresh_chapter_list(self, select: int = 0) -> None:
            self.chapter_list.clear()
            for i, ch in enumerate(self.payload.chapters, 1):
                item = QListWidgetItem(f"{i:03d}. {ch.title}")
                item.setData(Qt.UserRole, ch)
                self.chapter_list.addItem(item)
            if self.chapter_list.count() > 0:
                self.chapter_list.setCurrentRow(max(0, min(select, self.chapter_list.count() - 1)))

        def on_select(self, row: int) -> None:
            if row >= 0:
                ch = self.chapter_list.item(row).data(Qt.UserRole)
                self.single_title.setText(ch.title)

        def _sync_order_from_list(self) -> None:
            self.payload.chapters = [self.chapter_list.item(i).data(Qt.UserRole) for i in range(self.chapter_list.count())]

        def apply_single(self) -> None:
            row = self.chapter_list.currentRow()
            if row < 0:
                QMessageBox.warning(self, "提示", "请先选择章节")
                return
            ch = self.chapter_list.item(row).data(Qt.UserRole)
            ch.title = normalize_chapter_title(self.single_title.text())
            self._sync_order_from_list()
            self.refresh_chapter_list(row)

        def apply_batch(self) -> None:
            pattern = self.regex_edit.text().strip()
            repl = self.repl_edit.text()
            if not pattern:
                QMessageBox.critical(self, "参数错误", "请填写正则表达式")
                return
            try:
                rgx = re.compile(pattern)
            except re.error as exc:
                QMessageBox.critical(self, "正则错误", str(exc))
                return

            self._sync_order_from_list()
            if self.scope_sel.isChecked():
                row = self.chapter_list.currentRow()
                if row < 0:
                    QMessageBox.warning(self, "提示", "请先选择章节")
                    return
                targets = [row]
            else:
                targets = list(range(len(self.payload.chapters)))

            changed = 0
            for idx in targets:
                old = self.payload.chapters[idx].title
                new = normalize_chapter_title(rgx.sub(repl, old))
                if old != new:
                    self.payload.chapters[idx].title = new
                    changed += 1
            self.refresh_chapter_list(targets[0] if targets else 0)
            QMessageBox.information(self, "完成", f"已更新 {changed} 个章节标题")

        def replace_cover(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "选择封面图片", "", "Image Files (*.jpg *.jpeg *.png)")
            if not path:
                return
            try:
                raw = Path(path).read_bytes()
            except Exception as exc:
                QMessageBox.critical(self, "错误", f"读取封面失败: {exc}")
                return
            suffix = Path(path).suffix.lower()
            if suffix == ".png":
                self.payload.cover_type = "image/png"
                self.payload.cover_name = "cover.png"
            else:
                self.payload.cover_type = "image/jpeg"
                self.payload.cover_name = "cover.jpg"
            self.payload.cover_bytes = raw
            self.cover_label.setText(Path(path).name)

        def save_and_accept(self) -> None:
            self._sync_order_from_list()
            self.payload.meta.title = self.title_edit.text().strip() or self.payload.meta.title
            self.payload.meta.author = self.author_edit.text().strip() or self.payload.meta.author
            if self.output_path.endswith("novel.epub"):
                self.output_edit.setText(str(Path(self.output_path).with_name(safe_filename(self.payload.meta.title))))
            self.accept()

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.worker: DownloadWorker | None = None
            self._build_ui()

        def _build_ui(self) -> None:
            self.setWindowTitle("AliceScreep 小说下载器")
            self.resize(1100, 840)
            self.setMinimumSize(980, 760)
            self.setStyleSheet(
                f"""
                QMainWindow, QWidget {{ background:{S.BG}; color:{S.TEXT}; font-family:'Microsoft YaHei'; font-size:10pt; }}
                QFrame#card {{ background:{S.CARD}; border:1px solid #dde4ef; border-radius:14px; }}
                QLineEdit {{ background:white; border:1px solid #d9e1ec; border-radius:8px; padding:8px; }}
                QPushButton {{ background:#f8fafc; border:1px solid #d9e1ec; border-radius:8px; padding:8px 12px; }}
                QPushButton:hover {{ background:#eef3fb; }}
                QPushButton#primary {{ background:{S.PRIMARY}; color:white; border:none; font-weight:600; }}
                QPushButton#primary:hover {{ background:{S.PRIMARY_H}; }}
                QTextEdit#log {{ background:{S.LOG_BG}; color:{S.LOG_TXT}; border:none; border-radius:10px; padding:12px; font-family:Consolas; }}
                """
            )

            root = QWidget()
            self.setCentralWidget(root)
            outer = QVBoxLayout(root)
            outer.setContentsMargins(18, 18, 18, 18)
            outer.setSpacing(10)

            title = QLabel("小说下载与编辑工作台")
            title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
            subtitle = QLabel("PyQt5 扁平化圆角卡片风格 · 支持拖拽排序 / 正则批量改名 / 封面替换")
            subtitle.setStyleSheet(f"color:{S.SUB};")
            outer.addWidget(title)
            outer.addWidget(subtitle)

            card = Card()
            g = QGridLayout(card)
            g.setContentsMargins(14, 14, 14, 14)
            g.setHorizontalSpacing(8)
            g.setVerticalSpacing(8)
            self.url_edit = QLineEdit()
            self.output_edit = QLineEdit(str(Path.cwd() / "novel.epub"))
            self.start_edit = QLineEdit("1")
            self.end_edit = QLineEdit("0")
            self.delay_edit = QLineEdit("0.2")

            browse = QPushButton("浏览")
            browse.clicked.connect(self.browse_output)

            self.simplified = QRadioButton("保存前繁体转简体")
            self.simplified.setChecked(True)

            g.addWidget(QLabel("小说链接"), 0, 0)
            g.addWidget(self.url_edit, 0, 1, 1, 5)
            g.addWidget(QLabel("输出文件"), 1, 0)
            g.addWidget(self.output_edit, 1, 1, 1, 4)
            g.addWidget(browse, 1, 5)
            g.addWidget(QLabel("起始章节"), 2, 0)
            g.addWidget(self.start_edit, 2, 1)
            g.addWidget(QLabel("结束章节"), 2, 2)
            g.addWidget(self.end_edit, 2, 3)
            g.addWidget(QLabel("下载间隔(秒)"), 2, 4)
            g.addWidget(self.delay_edit, 2, 5)
            g.addWidget(self.simplified, 3, 1, 1, 3)
            g.setColumnStretch(1, 1)
            outer.addWidget(card)

            mid = QHBoxLayout()
            mid.setSpacing(10)
            outer.addLayout(mid, 1)

            log_card = Card()
            lv = QVBoxLayout(log_card)
            lv.setContentsMargins(12, 12, 12, 12)
            lv.addWidget(QLabel("下载日志"))
            self.log = QTextEdit()
            self.log.setObjectName("log")
            self.log.setReadOnly(True)
            lv.addWidget(self.log, 1)
            mid.addWidget(log_card, 1)

            tip_card = Card()
            tv = QVBoxLayout(tip_card)
            tv.setContentsMargins(12, 12, 12, 12)
            tv.addWidget(QLabel("操作提示"))
            for msg in [
                "• 填写链接后点击“开始下载并编辑”",
                "• 下载结束后会进入编辑窗口",
                "• 编辑窗口支持封面替换与章节拖拽",
                "• 支持正则批量改章节名",
            ]:
                lbl = QLabel(msg)
                lbl.setWordWrap(True)
                tv.addWidget(lbl)
            tv.addStretch(1)
            tip_card.setFixedWidth(300)
            mid.addWidget(tip_card)

            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.hide()
            outer.addWidget(self.progress)

            bottom = QHBoxLayout()
            self.status = QLabel("就绪")
            self.status.setStyleSheet(f"color:{S.SUB};")
            bottom.addWidget(self.status)
            bottom.addStretch(1)

            quit_btn = QPushButton("退出")
            quit_btn.clicked.connect(self.close)
            start_btn = QPushButton("开始下载并编辑")
            start_btn.setObjectName("primary")
            start_btn.clicked.connect(self.start_download)
            self.start_btn = start_btn
            bottom.addWidget(quit_btn)
            bottom.addWidget(start_btn)
            outer.addLayout(bottom)

        def browse_output(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "保存 EPUB", str(Path.cwd() / "novel.epub"), "EPUB Files (*.epub)")
            if path:
                self.output_edit.setText(path)

        def append_log(self, msg: str) -> None:
            self.log.append(msg)

        def set_running(self, running: bool) -> None:
            self.start_btn.setDisabled(running)
            self.progress.setVisible(running)
            self.status.setText("下载中..." if running else "就绪")

        def start_download(self) -> None:
            input_url = self.url_edit.text().strip()
            output_path = self.output_edit.text().strip()
            if not input_url or not output_path:
                QMessageBox.critical(self, "参数错误", "请填写小说链接与输出文件路径。")
                return
            try:
                start = int(self.start_edit.text().strip())
                end = int(self.end_edit.text().strip())
                delay = float(self.delay_edit.text().strip())
            except ValueError:
                QMessageBox.critical(self, "参数错误", "起始/结束章节必须是整数，间隔必须是数字。")
                return

            self.log.clear()
            self.append_log("✨ 开始下载 EPUB 数据...（完成后自动打开编辑器）")
            self.set_running(True)

            self.worker = DownloadWorker(input_url, start, end, delay, self.simplified.isChecked())
            self.worker.log.connect(self.append_log)
            self.worker.failed.connect(self.on_failed)
            self.worker.done.connect(lambda payload: self.on_done(payload, output_path))
            self.worker.start()

        def on_failed(self, err: str) -> None:
            self.set_running(False)
            self.append_log(f"❌ 下载异常: {err}")
            QMessageBox.critical(self, "失败", "下载失败，请检查日志。")

        def on_done(self, payload: DownloadPayload | None, output_path: str) -> None:
            self.set_running(False)
            if payload is None:
                QMessageBox.critical(self, "失败", "下载失败，请检查日志。")
                return
            if output_path.endswith("novel.epub"):
                self.output_edit.setText(str(Path(output_path).with_name(safe_filename(payload.meta.title))))

            dlg = ChapterEditor(payload, self.output_edit.text(), self.output_edit, self)
            if dlg.exec_():
                save_payload_to_epub(payload, Path(self.output_edit.text()), logger=self.append_log)
                self.status.setText("导出完成")
                QMessageBox.information(self, "完成", "下载完成，已编辑并生成 EPUB。")
            else:
                self.append_log("❌ 已取消保存")

    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei", 10))
    w = MainWindow()
    w.show()
    return app.exec_()


def main() -> int:
    args = parse_args()
    if args.gui or not args.index_url:
        return launch_gui()
    return run_download(args.index_url, Path(args.output), args.start, args.end, args.delay, to_simplified=not args.no_simplified)


if __name__ == "__main__":
    raise SystemExit(main())
