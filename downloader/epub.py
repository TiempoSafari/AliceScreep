from __future__ import annotations

import html
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .models import ChapterContent, NovelMeta

RUBY_TOKEN_RE = re.compile(r"⟦RUBY:(.*?)\|(.*?)⟧")


def _render_text_with_ruby(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in RUBY_TOKEN_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            parts.append(html.escape(text[cursor:start]))
        base = html.escape(match.group(1))
        annotation = html.escape(match.group(2))
        parts.append(f"<ruby>{base}<rt>{annotation}</rt></ruby>")
        cursor = end
    if cursor < len(text):
        parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def to_xhtml_paragraphs(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    paragraphs = [line for line in lines if line]
    if not paragraphs:
        return "<p></p>"
    return "\n".join(f"<p>{_render_text_with_ruby(p)}</p>" for p in paragraphs)


def build_epub(
    output_file: Path,
    meta: NovelMeta,
    chapters: list[ChapterContent],
    cover_bytes: bytes | None,
    cover_media_type: str | None,
    cover_name: str | None,
) -> None:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_items: list[str] = []
    nav_points: list[str] = []
    nav_links: list[str] = []

    if cover_bytes and cover_media_type and cover_name:
        manifest_items.append(
            f'<item id="cover-image" href="images/{cover_name}" media-type="{cover_media_type}" properties="cover-image"/>'
        )
        manifest_items.append('<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append('<itemref idref="cover-page"/>')

    for idx, chapter in enumerate(chapters, start=1):
        manifest_items.append(f'<item id="chap{idx}" href="text/chapter{idx}.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="chap{idx}"/>')
        nav_points.append(
            f'''<navPoint id="navPoint-{idx}" playOrder="{idx}">
      <navLabel><text>{html.escape(chapter.title)}</text></navLabel>
      <content src="text/chapter{idx}.xhtml"/>
    </navPoint>'''
        )
        nav_links.append(f'<li><a href="text/chapter{idx}.xhtml">{html.escape(chapter.title)}</a></li>')

    opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(meta.title)}</dc:title>
    <dc:creator>{html.escape(meta.author)}</dc:creator>
    <dc:language>{meta.language}</dc:language>
    <dc:date>{now_iso}</dc:date>
    <meta property="dcterms:modified">{now_iso}</meta>
  </metadata>
  <manifest>
    {''.join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    {''.join(spine_items)}
  </spine>
</package>
'''

    toc_ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(meta.title)}</text></docTitle>
  <navMap>
    {''.join(nav_points)}
  </navMap>
</ncx>
'''

    nav_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-Hant">
<head><title>目录</title></head>
<body>
  <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
    <h1>{html.escape(meta.title)}</h1>
    <ol>
      {''.join(nav_links)}
    </ol>
  </nav>
</body>
</html>
'''

    with zipfile.ZipFile(output_file, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">
  <rootfiles>
    <rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/>
  </rootfiles>
</container>
""",
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)

        if cover_bytes and cover_media_type and cover_name:
            cover_page = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>封面</title></head>
<body>
  <div style="text-align:center; margin:0; padding:0;">
    <img src="images/{cover_name}" alt="cover" style="max-width:100%; height:auto;"/>
  </div>
</body>
</html>
'''
            zf.writestr("OEBPS/cover.xhtml", cover_page)
            zf.writestr(f"OEBPS/images/{cover_name}", cover_bytes)

        for idx, chapter in enumerate(chapters, start=1):
            chapter_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-Hant">
<head><title>{html.escape(chapter.title)}</title></head>
<body>
  <h1>{html.escape(chapter.title)}</h1>
  {to_xhtml_paragraphs(chapter.content)}
</body>
</html>
'''
            zf.writestr(f"OEBPS/text/chapter{idx}.xhtml", chapter_xhtml)
