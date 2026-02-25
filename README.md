# AliceScreep

用于下载 `https://www.alicesw.tw/` 与 `https://silvernoelle.com/` 的小说目录并导出为 **EPUB**，支持图形界面（GUI），并提供更现代的 Windows 风格界面。

## 图形界面（推荐）

直接启动：

```bash
python novel_downloader.py --gui
```

如果不传参数，也会默认打开 GUI：

```bash
python novel_downloader.py
```

你可以输入以下链接（都支持）：

- AliceSW 小说页：`https://www.alicesw.tw/novel/2735.html`
- AliceSW 章节目录页：`https://www.alicesw.tw/other/chapters/id/2735.html`
- SilverNoelle 分类目录页：`https://silvernoelle.com/category/.../`

对 AliceSW，程序会自动优先使用完整章节目录页 `/other/chapters/id/{id}.html`，避免抓到导航/分类等无关页面；对 SilverNoelle，会自动跟随“较旧文章 / Older Posts”分页抓取完整章节列表，并按发布时间从旧到新下载。

默认流程为：**下载数据 → 打开编辑界面 → 修改章节名/封面 → 再保存 EPUB**。

当输出路径使用默认值 `novel.epub` 时，程序会自动改用“小说标题.epub”保存，避免重复手动改文件名。

默认会在保存前执行“繁体转简体”（章节内容、章节名、书名、作者）。

## 命令行模式（可选）

```bash
python novel_downloader.py "https://www.alicesw.tw/novel/2735.html" -o novel_2735.epub --start 1 --end 20 --delay 0.5
```

## EPUB 说明

生成的 EPUB 包含：

- 自动清理章节标题里的站点后缀（如 `-愛麗絲書屋...`），保留纯净章节名
- 章节目录（TOC）
- 每章独立标题和正文
- 作品元数据（标题、作者、语言、时间）
- 自动尝试抓取封面图（抓取失败时会生成无封面 EPUB）

- SilverNoelle 章节下载会自动去除文末“共享此文章 / 分享到 X、Facebook、Telegram”等站点分享信息。

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


## 下载后编辑功能

GUI 下载完成后会弹出“编辑章节与封面”窗口，可进行：

- 修改书名、作者
- 逐章修改章节标题（选择章节后点“应用章节名”）
- 替换封面图片（JPG/PNG）
- 确认后再导出 EPUB


## 繁体转简体

- GUI 中可勾选“保存前繁体转简体”（默认开启）。
- CLI 默认开启，可用 `--no-simplified` 关闭。
- 建议安装 OpenCC 以获得完整转换效果：

```bash
pip install opencc-python-reimplemented
```

若未安装 OpenCC，程序会给出日志警告并保持原文。


## 项目结构（便于扩展多站点）

- `novel_downloader.py`：CLI/GUI 入口。
- `downloader/service.py`：下载主流程编排（统一逻辑）。
- `downloader/sites.py`：站点适配器（网站差异化逻辑），后续新增网站主要在此扩展。
- `downloader/epub.py`：EPUB 生成（公共逻辑）。
- `downloader/http.py` / `downloader/text.py` / `downloader/conversion.py`：网络、文本、繁转简等通用能力。

### 新增网站建议

1. 在 `downloader/sites.py` 新建一个 `SiteAdapter` 子类，实现 `build_chapter_index_url / discover_chapters / extract_meta / extract_content`。
2. 在 `get_site_adapter` 中按域名返回新适配器。
3. 其它流程（下载调度、章节过滤、EPUB 生成、GUI 编辑）无需重复实现。
