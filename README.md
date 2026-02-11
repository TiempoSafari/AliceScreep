# AliceScreep

用于下载 `https://www.alicesw.tw/` 小说并导出为 TXT。

## 使用方法

```bash
python novel_downloader.py "https://www.alicesw.tw/novel/19861.html" -o novel_19861.txt
```

常用参数：

- `--start 10`：从第 10 章开始。
- `--end 30`：下载到第 30 章结束。
- `--delay 0.5`：每章等待 0.5 秒，降低请求频率。

示例：

```bash
python novel_downloader.py "https://www.alicesw.tw/novel/19861.html" --start 1 --end 20 -o part1.txt
```

> 注意：网站结构变更后，可能需要更新章节/正文提取规则。
