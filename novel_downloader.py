#!/usr/bin/env python3
"""多站点小说下载器：CLI + GUI 入口（PyQt5）。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from pathlib import Path

from downloader import DownloadPayload, download_novel_payload, normalize_chapter_title, run_download, save_payload_to_epub
from downloader.models import ChapterContent, NovelMeta
from downloader.text import safe_filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 AliceSW/SilverNoelle/ESJZone 小说并导出成 EPUB")
    parser.add_argument("index_url", nargs="?", help="小说链接，例如 https://www.alicesw.tw/novel/2735.html / https://silvernoelle.com/category/.../ / https://www.esjzone.cc/detail/1768217077.html")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT_FILE), help="输出 EPUB 文件路径（默认 output/novel.epub，自动可按书名命名）")
    parser.add_argument("--delay", type=float, default=0.2, help="每章下载间隔秒数，默认 0.2")
    parser.add_argument("--start", type=int, default=1, help="起始章节（从1开始）")
    parser.add_argument("--end", type=int, default=0, help="结束章节（0 表示到最后）")
    parser.add_argument("--no-simplified", action="store_true", help="关闭繁体转简体（默认开启）")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    return parser.parse_args()


DEFAULT_OUTPUT_DIR = Path.cwd() / "output"
DEFAULT_OUTPUT_FILE = DEFAULT_OUTPUT_DIR / "novel.epub"
DEFAULT_STASH_DIR = DEFAULT_OUTPUT_DIR / "stash"
DEFAULT_CONFIG_DIR = Path.cwd() / "config"


def _ensure_output_dirs() -> None:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_STASH_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def save_payload_stash(path: Path, payload: DownloadPayload) -> None:
    data = {
        "version": 1,
        "meta": {
            "title": payload.meta.title,
            "author": payload.meta.author,
            "language": payload.meta.language,
        },
        "chapters": [
            {"title": c.title, "content": c.content, "source_url": c.source_url}
            for c in payload.chapters
        ],
        "cover": {
            "bytes_b64": base64.b64encode(payload.cover_bytes).decode("ascii") if payload.cover_bytes else None,
            "type": payload.cover_type,
            "name": payload.cover_name,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_payload_stash(path: Path) -> DownloadPayload:
    data = json.loads(path.read_text(encoding="utf-8"))
    meta_data = data.get("meta", {})
    meta = NovelMeta(
        title=meta_data.get("title", "未命名小说"),
        author=meta_data.get("author", "未知作者"),
        language=meta_data.get("language", "zh-Hant"),
    )
    chapters = [
        ChapterContent(
            title=item.get("title", "未知章节"),
            content=item.get("content", ""),
            source_url=item.get("source_url", ""),
        )
        for item in data.get("chapters", [])
    ]
    cover_data = data.get("cover", {})
    cover_b64 = cover_data.get("bytes_b64")
    cover_bytes = base64.b64decode(cover_b64) if cover_b64 else None
    return DownloadPayload(
        meta=meta,
        chapters=chapters,
        cover_bytes=cover_bytes,
        cover_type=cover_data.get("type"),
        cover_name=cover_data.get("name"),
    )



SITE_CONFIG_FILE = DEFAULT_CONFIG_DIR / "site_configs.json"
DEFAULT_SITE_CONFIGS = {
    "AliceSW": {
        "base_url": "https://www.alicesw.tw/novel/2735.html",
        "username": "",
        "password": "",
        "use_login": False,
    },
    "SilverNoelle": {
        "base_url": "https://silvernoelle.com/category/.../",
        "username": "",
        "password": "",
        "use_login": False,
    },
    "ESJZone": {
        "base_url": "https://www.esjzone.cc/detail/1768217077.html",
        "username": "",
        "password": "",
        "use_login": False,
    },
}


def load_site_configs() -> dict:
    _ensure_output_dirs()
    if not SITE_CONFIG_FILE.exists():
        return json.loads(json.dumps(DEFAULT_SITE_CONFIGS))
    try:
        loaded = json.loads(SITE_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEFAULT_SITE_CONFIGS))

    merged = json.loads(json.dumps(DEFAULT_SITE_CONFIGS))
    for name, cfg in loaded.items():
        if not isinstance(cfg, dict):
            continue
        if name not in merged:
            merged[name] = {
                "base_url": "",
                "username": "",
                "password": "",
                "use_login": False,
            }
        merged[name].update({
            "base_url": str(cfg.get("base_url", merged[name].get("base_url", ""))),
            "username": str(cfg.get("username", "")),
            "password": str(cfg.get("password", "")),
            "use_login": bool(cfg.get("use_login", False)),
        })
    return merged


def save_site_configs(configs: dict) -> None:
    _ensure_output_dirs()
    SITE_CONFIG_FILE.write_text(json.dumps(configs, ensure_ascii=False, indent=2), encoding="utf-8")

def launch_gui() -> int:
    try:
        import PyQt5
        from PyQt5.QtCore import QLibraryInfo, Qt, QThread, pyqtSignal
        from PyQt5.QtGui import QColor, QFont, QPixmap
        from PyQt5.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QButtonGroup,
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QFileDialog,
            QFormLayout,
            QFrame,
            QGraphicsDropShadowEffect,
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
            QScrollArea,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:
        print(f"GUI 启动失败：{exc}")
        print("请安装 PyQt5：pip install PyQt5")
        return 1


    def _configure_qt_runtime() -> None:
        """尽量自动修复 Qt platform plugin 初始化失败问题。"""
        plugin_candidates: list[Path] = []
        try:
            qt_plugins = Path(QLibraryInfo.location(QLibraryInfo.PluginsPath))
            plugin_candidates.append(qt_plugins / "platforms")
        except Exception:
            pass

        pyqt_root = Path(PyQt5.__file__).resolve().parent
        plugin_candidates.extend(
            [
                pyqt_root / "Qt5" / "plugins" / "platforms",
                pyqt_root / "Qt" / "plugins" / "platforms",
                pyqt_root / "plugins" / "platforms",
            ]
        )

        for candidate in plugin_candidates:
            if candidate.exists():
                os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(candidate))
                break

        # Windows 常见问题：找不到 Qt 依赖 DLL，给 PATH 补充几个候选目录
        if os.name == "nt":
            dll_dirs: list[Path] = []
            for parent in [pyqt_root / "Qt5", pyqt_root / "Qt", pyqt_root]:
                dll_dirs.extend([parent / "bin", parent])
            old_path = os.environ.get("PATH", "")
            prefix = os.pathsep.join(str(d) for d in dll_dirs if d.exists())
            if prefix:
                os.environ["PATH"] = prefix + (os.pathsep + old_path if old_path else "")
            os.environ.setdefault("QT_QPA_PLATFORM", "windows")

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
        progress = pyqtSignal(int, int)

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
                    progress_callback=lambda cur, total: self.progress.emit(cur, total),
                )
                self.done.emit(payload)
            except Exception as exc:
                self.failed.emit(str(exc))

    class Card(QFrame):
        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("card")
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(28)
            shadow.setOffset(0, 8)
            shadow.setColor(QColor(19, 33, 68, 28))
            self.setGraphicsEffect(shadow)


    class SiteConfigDialog(QDialog):
        def __init__(self, site_name: str, cfg: dict, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle(f"{site_name} 站点配置")
            self.resize(460, 280)

            root = QVBoxLayout(self)
            root.setContentsMargins(14, 14, 14, 14)
            root.setSpacing(10)

            hint = QLabel("可配置入口链接与登录凭证，便于网站地址变化或需要登录后再抓取。")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{S.SUB};")
            root.addWidget(hint)

            form = QFormLayout()
            form.setSpacing(8)
            self.base_url_edit = QLineEdit(str(cfg.get("base_url", "")))
            self.use_login_chk = QCheckBox("该站点需要登录")
            self.use_login_chk.setChecked(bool(cfg.get("use_login", False)))
            self.username_edit = QLineEdit(str(cfg.get("username", "")))
            self.password_edit = QLineEdit(str(cfg.get("password", "")))
            self.password_edit.setEchoMode(QLineEdit.Password)

            form.addRow("入口链接", self.base_url_edit)
            form.addRow("登录选项", self.use_login_chk)
            form.addRow("用户名", self.username_edit)
            form.addRow("密码", self.password_edit)
            root.addLayout(form)

            self.use_login_chk.toggled.connect(self._toggle_login_fields)
            self._toggle_login_fields(self.use_login_chk.isChecked())

            buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            root.addWidget(buttons)

        def _toggle_login_fields(self, enabled: bool) -> None:
            self.username_edit.setEnabled(enabled)
            self.password_edit.setEnabled(enabled)

        def get_config(self) -> dict:
            return {
                "base_url": self.base_url_edit.text().strip(),
                "use_login": self.use_login_chk.isChecked(),
                "username": self.username_edit.text().strip(),
                "password": self.password_edit.text(),
            }

    class ChapterEditor(QDialog):
        def __init__(self, payload: DownloadPayload, output_path: str, output_edit: QLineEdit, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.payload = payload
            self.output_path = output_path
            self.output_edit = output_edit
            self.stash_requested = False
            self.last_stash_path: Path | None = None
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
            self.cover_preview = QLabel()
            self.cover_preview.setFixedSize(120, 160)
            self.cover_preview.setAlignment(Qt.AlignCenter)
            self.cover_preview.setStyleSheet("background:#f3f6fb; border:1px solid #d5dfef; border-radius:8px;")
            cover_btn = QPushButton("更换封面")
            cover_btn.clicked.connect(self.replace_cover)

            info_l.addWidget(QLabel("书名"), 0, 0)
            info_l.addWidget(self.title_edit, 0, 1, 1, 4)
            info_l.addWidget(QLabel("作者"), 1, 0)
            info_l.addWidget(self.author_edit, 1, 1)
            info_l.addWidget(QLabel("封面"), 1, 2)
            info_l.addWidget(self.cover_label, 1, 3)
            info_l.addWidget(cover_btn, 1, 4)
            info_l.addWidget(self.cover_preview, 0, 5, 2, 1)
            info_l.setColumnStretch(1, 1)
            info_l.setColumnStretch(3, 1)
            root.addWidget(info_card)
            self._update_cover_preview()

            body = QHBoxLayout()
            body.setSpacing(10)
            root.addLayout(body, 1)

            list_card = Card()
            ll = QVBoxLayout(list_card)
            ll.setContentsMargins(12, 12, 12, 12)
            ll.addWidget(QLabel("章节列表（可拖拽排序）"))
            self.chapter_list = QListWidget()
            self.chapter_list.setAlternatingRowColors(False)
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
            stash = QPushButton("暂存")
            stash.clicked.connect(self.stash_and_accept)
            save = QPushButton("保存修改并导出")
            save.clicked.connect(self.save_and_accept)
            save.setObjectName("primary")
            actions.addWidget(cancel)
            actions.addWidget(stash)
            actions.addWidget(save)
            root.addLayout(actions)

            self.refresh_chapter_list()


        def _update_cover_preview(self) -> None:
            if not self.payload.cover_bytes:
                self.cover_preview.setText("无封面")
                self.cover_preview.setPixmap(QPixmap())
                return
            pix = QPixmap()
            if not pix.loadFromData(self.payload.cover_bytes):
                self.cover_preview.setText("封面加载失败")
                return
            shown = pix.scaled(
                self.cover_preview.width() - 8,
                self.cover_preview.height() - 8,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.cover_preview.setPixmap(shown)

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
            self._update_cover_preview()

        def stash_and_accept(self) -> None:
            self._sync_order_from_list()
            self.payload.meta.title = self.title_edit.text().strip() or self.payload.meta.title
            self.payload.meta.author = self.author_edit.text().strip() or self.payload.meta.author

            default_stash = DEFAULT_STASH_DIR / f"{safe_filename(self.payload.meta.title, suffix='')}.novelstash.json"
            stash_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存暂存文件",
                str(default_stash),
                "Novel Stash (*.novelstash.json);;JSON Files (*.json)",
            )
            if not stash_path:
                return
            save_payload_stash(Path(stash_path), self.payload)
            self.stash_requested = True
            self.last_stash_path = Path(stash_path)
            self.accept()

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
            self.site_configs = load_site_configs()
            self.selected_site_name = next(iter(self.site_configs.keys()), "")
            self.site_card_buttons: dict[str, QPushButton] = {}
            self._build_ui()

        def _build_ui(self) -> None:
            _ensure_output_dirs()
            self.setWindowTitle("AliceScreep 小说下载器")
            self.resize(1100, 840)
            self.setMinimumSize(980, 760)
            self.setStyleSheet(
                f"""
                QMainWindow, QWidget {{
                    background:{S.BG};
                    color:{S.TEXT};
                    font-family:'Segoe UI','Microsoft YaHei','PingFang SC';
                    font-size:10pt;
                }}
                QLabel {{
                    background: transparent;
                    color:{S.TEXT};
                }}
                QFrame#card {{
                    background:{S.CARD};
                    border:1px solid #d8e2f0;
                    border-radius:16px;
                }}
                QLineEdit {{
                    background:#ffffff;
                    border:1px solid #d7e0ee;
                    border-radius:10px;
                    padding:9px 10px;
                    selection-background-color:#cfe1ff;
                }}
                QLineEdit:focus {{
                    border:1px solid #76a2ff;
                    background:#fdfefe;
                }}
                QPushButton {{
                    background:#f8fafc;
                    border:1px solid #d9e1ec;
                    border-radius:10px;
                    padding:8px 12px;
                    font-weight:500;
                }}
                QPushButton:hover {{
                    background:#eef3fb;
                    border-color:#c4d6f4;
                }}
                QPushButton:pressed {{
                    background:#e2ebfa;
                }}
                QPushButton#siteCard:hover {{
                    background:#edf4ff;
                    border-color:#b6caee;
                }}
                QPushButton#siteCard:pressed {{
                    background:#e4edfc;
                }}
                QPushButton#primary {{
                    background:{S.PRIMARY};
                    color:white;
                    border:none;
                    font-weight:600;
                    padding:9px 14px;
                }}
                QPushButton#primary:hover {{
                    background:{S.PRIMARY_H};
                }}
                QPushButton#primary:pressed {{
                    background:#275ed0;
                }}
                QTextEdit#log, QListWidget {{
                    border:1px solid #1f2d49;
                    border-radius:10px;
                }}
                QTextEdit#log {{
                    background:{S.LOG_BG};
                    color:{S.LOG_TXT};
                    padding:12px;
                    font-family:'Cascadia Code','Consolas';
                }}
                QListWidget {{
                    background:#f8fbff;
                    border:1px solid #d9e3f3;
                    color:{S.TEXT};
                    padding:6px;
                }}
                QListWidget::item {{
                    border-radius:8px;
                    padding:7px 8px;
                    margin:2px 0;
                }}
                QListWidget::item:selected {{
                    background:#dce9ff;
                    color:#0e2244;
                }}
                QScrollBar:vertical {{
                    background:transparent;
                    width:10px;
                    margin:4px 0 4px 0;
                }}
                QScrollBar::handle:vertical {{
                    background:#b4c4df;
                    min-height:24px;
                    border-radius:5px;
                }}
                QScrollBar::handle:vertical:hover {{
                    background:#98add0;
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                    height:0;
                }}
                QScrollBar:horizontal {{
                    background:transparent;
                    height:10px;
                    margin:0 4px 0 4px;
                }}
                QScrollBar::handle:horizontal {{
                    background:#b4c4df;
                    min-width:24px;
                    border-radius:5px;
                }}
                QScrollBar::handle:horizontal:hover {{
                    background:#98add0;
                }}
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                    width:0;
                }}
                QProgressBar {{
                    border:1px solid #d9e2f1;
                    border-radius:7px;
                    background:#ecf1f8;
                    text-align:center;
                    height:14px;
                }}
                QProgressBar::chunk {{
                    border-radius:6px;
                    background:{S.PRIMARY};
                }}
                """
            )

            root = QWidget()
            self.setCentralWidget(root)
            shell = QHBoxLayout(root)
            shell.setContentsMargins(18, 18, 18, 18)
            shell.setSpacing(10)

            sidebar = Card()
            sidebar.setFixedWidth(340)
            side_l = QVBoxLayout(sidebar)
            side_l.setContentsMargins(12, 12, 12, 12)
            side_l.setSpacing(8)
            side_l.addWidget(QLabel("网站配置"))
            side_hint = QLabel("每个网站使用独立配置卡片。点击卡片可弹窗编辑入口链接与登录信息。")
            side_hint.setWordWrap(True)
            side_hint.setStyleSheet(f"color:{S.SUB};")
            side_l.addWidget(side_hint)

            side_scroll = QScrollArea()
            side_scroll.setWidgetResizable(True)
            side_scroll.setFrameShape(QFrame.NoFrame)
            side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            side_wrap = QWidget()
            self.site_cards_layout = QVBoxLayout(side_wrap)
            self.site_cards_layout.setContentsMargins(0, 4, 0, 4)
            self.site_cards_layout.setSpacing(8)

            for site_name in self.site_configs.keys():
                btn = QPushButton()
                btn.setObjectName("siteCard")
                btn.setCursor(Qt.PointingHandCursor)
                cfg = self.site_configs.get(site_name, {})
                base = str(cfg.get("base_url", "")).strip() or "(未设置入口链接)"
                btn.setText(f"{site_name}\n{base}")
                btn.setToolTip("点击编辑站点配置")
                btn.setMinimumHeight(74)
                btn.setStyleSheet("text-align:left; padding:10px 12px;")
                btn.clicked.connect(lambda _=False, name=site_name: self.open_site_config_dialog(name))
                self.site_card_buttons[site_name] = btn
                self.site_cards_layout.addWidget(btn)

            self.site_cards_layout.addStretch(1)
            side_scroll.setWidget(side_wrap)
            side_l.addWidget(side_scroll, 1)

            shell.addWidget(sidebar)
            self.refresh_site_card_styles()

            content = QWidget()
            shell.addWidget(content, 1)
            outer = QVBoxLayout(content)
            outer.setContentsMargins(0, 0, 0, 0)
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
            self.output_edit = QLineEdit(str(DEFAULT_OUTPUT_FILE))
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

            tips_title = QLabel("操作提示")
            tips_title.setStyleSheet("font-weight:600;")
            tv.addWidget(tips_title)
            for msg in [
                "• 填写链接后点击“开始下载并编辑”",
                "• 下载结束后会进入编辑窗口",
                "• 编辑窗口支持封面替换与章节拖拽",
                "• 支持正则批量改章节名",
            ]:
                lbl = QLabel(msg)
                lbl.setWordWrap(True)
                tv.addWidget(lbl)

            site_title = QLabel("支持网站")
            site_title.setStyleSheet("font-weight:600; margin-top:8px;")
            tv.addWidget(site_title)
            for site_name, sample in [
                ("AliceSW", "https://www.alicesw.tw/novel/2735.html"),
                ("SilverNoelle", "https://silvernoelle.com/category/.../"),
                ("ESJZone", "https://www.esjzone.cc/detail/1768217077.html"),
            ]:
                row = QLabel(f"• {site_name}\n  {sample}")
                row.setWordWrap(True)
                row.setStyleSheet(f"color:{S.SUB};")
                tv.addWidget(row)

            tv.addStretch(1)
            tip_card.setFixedWidth(320)
            mid.addWidget(tip_card)

            self.progress = QProgressBar()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("0%")
            self.progress.hide()
            outer.addWidget(self.progress)

            bottom = QHBoxLayout()
            self.status = QLabel("就绪")
            self.status.setStyleSheet(f"color:{S.SUB};")
            bottom.addWidget(self.status)
            bottom.addStretch(1)

            quit_btn = QPushButton("退出")
            quit_btn.clicked.connect(self.close)
            load_stash_btn = QPushButton("读取暂存并编辑")
            load_stash_btn.clicked.connect(self.load_stash_and_edit)
            start_btn = QPushButton("开始下载并编辑")
            start_btn.setObjectName("primary")
            start_btn.clicked.connect(self.start_download)
            self.start_btn = start_btn
            bottom.addWidget(quit_btn)
            bottom.addWidget(load_stash_btn)
            bottom.addWidget(start_btn)
            outer.addLayout(bottom)

        def refresh_site_card_styles(self) -> None:
            for name, btn in self.site_card_buttons.items():
                active = name == self.selected_site_name
                if active:
                    btn.setStyleSheet(
                        "text-align:left; padding:10px 12px;"
                        "background:#dfeaff; border:1px solid #8bb4ff; border-radius:12px;"
                        "font-weight:600;"
                    )
                else:
                    btn.setStyleSheet(
                        "text-align:left; padding:10px 12px;"
                        "background:#f8fbff; border:1px solid #d3def0; border-radius:12px;"
                    )

        def open_site_config_dialog(self, site_name: str) -> None:
            self.selected_site_name = site_name
            self.refresh_site_card_styles()
            cfg = self.site_configs.get(site_name, {})
            dialog = SiteConfigDialog(site_name, cfg, self)
            if dialog.exec_() != QDialog.Accepted:
                return

            self.site_configs[site_name] = dialog.get_config()
            save_site_configs(self.site_configs)
            updated_base = self.site_configs[site_name].get("base_url", "")
            self.site_card_buttons[site_name].setText(f"{site_name}\n{updated_base or '(未设置入口链接)'}")
            QMessageBox.information(self, "配置已保存", f"{site_name} 配置已保存。")

        def current_site_default_url(self) -> str:
            if not self.selected_site_name:
                return ""
            cfg = self.site_configs.get(self.selected_site_name, {})
            return str(cfg.get("base_url", "")).strip()

        def browse_output(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "保存 EPUB", str(DEFAULT_OUTPUT_FILE), "EPUB Files (*.epub)")
            if path:
                self.output_edit.setText(path)

        def append_log(self, msg: str) -> None:
            self.log.append(msg)

        def set_running(self, running: bool) -> None:
            self.start_btn.setDisabled(running)
            self.progress.setVisible(running)
            if running:
                self.progress.setValue(0)
                self.progress.setFormat("0%")
            self.status.setText("下载中..." if running else "就绪")

        def update_progress(self, current: int, total: int) -> None:
            if total <= 0:
                return
            percent = int(current * 100 / total)
            self.progress.setValue(percent)
            self.progress.setFormat(f"{percent}% ({current}/{total})")
            self.status.setText(f"下载中... {current}/{total}")

        def start_download(self) -> None:
            input_url = self.url_edit.text().strip()
            if not input_url:
                input_url = self.current_site_default_url()
                if input_url:
                    self.url_edit.setText(input_url)

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
            self.worker.progress.connect(self.update_progress)
            self.worker.done.connect(lambda payload: self.on_done(payload, output_path))
            self.worker.start()

        def open_editor_and_handle(self, payload: DownloadPayload, output_path: str) -> None:
            dlg = ChapterEditor(payload, self.output_edit.text(), self.output_edit, self)
            if not dlg.exec_():
                self.append_log("❌ 已取消保存")
                return
            if dlg.stash_requested:
                stash_msg = f"💾 已暂存: {dlg.last_stash_path}" if dlg.last_stash_path else "💾 已暂存"
                self.append_log(stash_msg)
                self.status.setText("已暂存")
                QMessageBox.information(self, "暂存完成", stash_msg)
                return
            save_payload_to_epub(payload, Path(self.output_edit.text()), logger=self.append_log)
            self.status.setText("导出完成")
            QMessageBox.information(self, "完成", "下载完成，已编辑并生成 EPUB。")

        def load_stash_and_edit(self) -> None:
            _ensure_output_dirs()
            path, _ = QFileDialog.getOpenFileName(
                self,
                "读取暂存文件",
                str(DEFAULT_STASH_DIR),
                "Novel Stash (*.novelstash.json);;JSON Files (*.json)",
            )
            if not path:
                return
            try:
                payload = load_payload_stash(Path(path))
            except Exception as exc:
                QMessageBox.critical(self, "读取失败", f"读取暂存失败: {exc}")
                return
            if self.output_edit.text().strip().endswith("novel.epub"):
                self.output_edit.setText(str(DEFAULT_OUTPUT_DIR / safe_filename(payload.meta.title)))
            self.open_editor_and_handle(payload, self.output_edit.text().strip())

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

            self.open_editor_and_handle(payload, output_path)

    _configure_qt_runtime()

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication([])
    app.setFont(QFont("Segoe UI", 10))
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
