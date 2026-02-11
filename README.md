# AliceScreep

用于下载 `https://www.alicesw.tw/` 小说并导出为 TXT，支持图形界面（GUI）。

## 图形界面（推荐）

直接启动：

```bash
python novel_downloader.py --gui
```

如果不传参数，也会默认打开 GUI：

```bash
python novel_downloader.py
```

界面中可填写：

- 小说目录页链接（如 `https://www.alicesw.tw/novel/19861.html`）
- 输出 TXT 路径（可点击“浏览”）
- 起始章节、结束章节（`0` 表示下载到最后）
- 每章请求间隔秒数

## 命令行模式（可选）

```bash
python novel_downloader.py "https://www.alicesw.tw/novel/19861.html" -o novel_19861.txt --start 1 --end 20 --delay 0.5
```

> 注意：网站结构变更后，可能需要更新章节/正文提取规则。
