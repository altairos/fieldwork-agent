"""Markdown/TXT 段落切分；字符预算是软上限，不冒充模型token计数。"""
import re

from fieldwork_agent.kernel.knowledge.models import KnowledgeChunk, KnowledgeDocument, TextLocator
from fieldwork_agent.kernel.knowledge.normalize import content_hash

CHUNKER_VERSION = "paragraph-char-v1"


def split_document(doc: KnowledgeDocument, *, max_chars: int = 1200) -> tuple[KnowledgeChunk, ...]:
    if max_chars < 1:
        raise ValueError("max_chars必须为正数")
    if doc.reading_level == "metadata_only" or not doc.content.strip():
        return ()
    spans: list[tuple[int, int, tuple[str, ...]]] = []
    headings: list[tuple[int, str]] = []
    for match in re.finditer(r"\S.*?(?=\n[ \t]*\n|\n#{1,6}[ \t]+|\Z)", doc.content, re.DOTALL):
        start, end = match.start(), match.end()
        while end > start and doc.content[end - 1].isspace():
            end -= 1
        heading = re.match(r"(#{1,6})[ \t]+([^\n]+)", doc.content[start:end])
        if heading:
            level = len(heading[1])
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, heading[2].strip()))
        section = tuple(title for _, title in headings)
        if spans and not heading and spans[-1][2] == section and end - spans[-1][0] <= max_chars:
            spans[-1] = (spans[-1][0], end, section)
        else:
            spans.append((start, end, section))
    result = []
    for ordinal, (start, end, section) in enumerate(spans, start=1):
        text = doc.content[start:end]
        digest = content_hash(text)
        result.append(KnowledgeChunk(
            scope=doc.scope, project_id=doc.project_id, doc_id=doc.doc_id, doc_version=doc.version,
            chunk_id=f"{doc.doc_id}:v{doc.version}:{ordinal}:{digest[:12]}", text=text, content_hash=digest,
            locator=TextLocator(char_start=start, char_end=end,
                                line_start=doc.content[:start].count("\n") + 1,
                                line_end=doc.content[:end].count("\n") + 1),
            section_path=section, over_char_budget=len(text) > max_chars))
    return tuple(result)
