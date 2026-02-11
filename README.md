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

你可以输入以下两种链接（都支持）：

- 小说页：`https://www.alicesw.tw/novel/2735.html`
- 章节目录页：`https://www.alicesw.tw/other/chapters/id/2735.html`

程序会自动优先使用完整章节目录页 `/other/chapters/id/{id}.html`，避免抓到导航/分类等无关页面。

## 命令行模式（可选）

```bash
python novel_downloader.py "https://www.alicesw.tw/novel/2735.html" -o novel_2735.txt --start 1 --end 20 --delay 0.5
```

## 日志说明

下载日志会显示：

- 实际使用的章节目录页 URL
- 章节解析统计（候选链接、有效章节、各类过滤数量）
- 每章下载进度与最终写入章节数

> 注意：网站结构变更后，可能需要更新章节/正文提取规则。
