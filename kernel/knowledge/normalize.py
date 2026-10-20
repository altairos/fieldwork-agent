"""版本化的本地检索投影；原文始终单独保存。"""
import hashlib
import re
import unicodedata

NORMALIZER_VERSION = "nfkc-casefold-cjk-bigram-v1"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_name(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def lexical_terms(text: str) -> tuple[str, ...]:
    """拉丁词与中文相邻双字；短专名另由显式别名覆盖，不伪称中文分词。"""
    terms: list[str] = []
    for part in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", normalize_name(text)):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            terms.extend(part[i:i + 2] for i in range(len(part) - 1))
            if len(part) == 1:
                terms.append(part)
        else:
            terms.append(part)
    return tuple(terms)
