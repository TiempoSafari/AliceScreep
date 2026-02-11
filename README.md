# AliceScreep

用于下载 `https://www.alicesw.tw/` 小说并导出为 **EPUB**，支持图形界面（GUI），并提供更现代的 Windows 风格界面。

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
python novel_downloader.py "https://www.alicesw.tw/novel/2735.html" -o novel_2735.epub --start 1 --end 20 --delay 0.5
```

## EPUB 说明

生成的 EPUB 包含：

- 章节目录（TOC）
- 每章独立标题和正文
- 作品元数据（标题、作者、语言、时间）
- 自动尝试抓取封面图（抓取失败时会生成无封面 EPUB）

## 日志说明

下载日志会显示：

- 实际使用的章节目录页 URL
- 章节解析统计（候选链接、有效章节、各类过滤数量）
- 每章下载进度
- ✅ 下载成功标记
- ❌ 失败/警告标记（GUI 中会显示为红色）
- 网络失败自动重试日志（默认重试 2 次）

## 常见问题

### `[警告] 章节下载失败，已跳过: <urlopen error [Errno 2] No such file or directory>`

常见原因：

- 章节链接里有隐藏空格、中文或未转义字符，`urllib` 可能把它当成本地路径而不是 HTTP 链接。
- 目录页偶发返回了不完整链接（比如缺少 `http/https`）。

最新版脚本已增加链接规范化与校验：

- 自动把相对链接补全为完整 `https://...` 形式
- 自动清理并编码 URL 中的特殊字符
- 非法链接会直接跳过并在日志里输出具体章节 URL 和错误原因

如果仍偶发失败，可把 `--delay` 调大一些（如 `0.8~1.5`），减少站点风控触发概率。


## GUI 界面风格

- 使用 `ttk` 主题组件，优先启用 `vista/xpnative`（可用时），更接近 Windows 新风格。
- 增加顶部标题区、卡片式参数区、进度条和深色日志面板。
- 日志颜色：成功绿色，失败/警告红色。
