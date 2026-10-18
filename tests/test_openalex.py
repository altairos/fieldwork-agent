"""kernel/sources/openalex — OpenAlex 学术文献检索与结果归一化（mock _get，不联网、无 key）。

问题2 实证：生产会话里 search_keyword/search_ai（飞猪旅行类目）查不到学术文献，
内核无任何文献检索能力。OpenAlex 是免费开放学术图谱（2.5 亿+ 文献，CC0），
REST 无需 key 即可基础调用，补社科文献检索缺口。
"""
from fieldwork_agent.kernel.models import LiteratureItem, LiteraturePage
from fieldwork_agent.kernel.registry import build_tool_schemas, capabilities
from fieldwork_agent.kernel.sources.openalex import (
    OpenAlexClient,
    reconstruct_abstract,
    search_literature,
)

# 真实 OpenAlex /works 响应片段（字段名逐字对齐官方 schema）
_WORKS_PAYLOAD = {
    "meta": {"count": 25, "page": 1, "per_page": 10},
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "display_name": "The social life of things",
            "title": "The social life of things",
            "publication_year": 1986,
            "doi": "https://doi.org/10.1017/CBO9780511812682",
            "cited_by_count": 1234,
            "authorships": [
                {"author": {"display_name": "Arjun Appadurai"}},
                {"author": {"display_name": "Igor Kopytoff"}},
            ],
            "primary_location": {"source": {"display_name": "Cambridge University Press"}},
            "open_access": {"is_oa": False, "oa_url": None},
            "abstract_inverted_index": {"things": [0, 2], "have": [1], "social": [3]},
        },
    ],
}


# ---------------------------------------------------------------- 摘要重建


def test_reconstruct_abstract_from_inverted_index():
    """OpenAlex 摘要以 inverted index 存储（词→位置列表），按位置重排为纯文本。"""
    inv = {"things": [0, 2], "have": [1], "social": [3]}
    assert reconstruct_abstract(inv) == "things have things social"


def test_reconstruct_abstract_empty_or_bad_shape():
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""
    assert reconstruct_abstract("不是字典") == ""


# ---------------------------------------------------------------- 字段映射


def test_search_works_maps_fields(monkeypatch):
    calls = {}

    def fake_get(path, params):
        calls["path"], calls["params"] = path, params
        return _WORKS_PAYLOAD

    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", fake_get)
    env = client.search_works("commodity circulation", max_results=5)
    assert env.ok and isinstance(env.data, LiteraturePage)
    assert env.data.total == 25                      # meta.count 直出（不加工）
    item = env.data.items[0]
    assert isinstance(item, LiteratureItem)
    assert item.title == "The social life of things"
    assert item.authors == ["Arjun Appadurai", "Igor Kopytoff"]
    assert item.year == 1986
    assert item.doi == "10.1017/CBO9780511812682"    # 剥 https://doi.org/ 前缀
    assert item.venue == "Cambridge University Press"
    assert item.cited_by_count == 1234
    assert item.abstract == "things have things social"
    assert item.is_oa is False
    assert calls["path"] == "/works"
    assert calls["params"]["search"] == "commodity circulation"
    assert calls["params"]["per-page"] == 5


def test_search_works_year_and_oa_filters(monkeypatch):
    calls = {}

    def fake_get(path, params):
        calls["params"] = params
        return _WORKS_PAYLOAD

    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", fake_get)
    client.search_works("temple kitchen", year_from=2000, year_to=2020,
                        open_access_only=True)
    filt = calls["params"]["filter"]
    assert "publication_year:2000-2020" in filt
    assert "is_oa:true" in filt


def test_search_works_year_from_only_open_ended(monkeypatch):
    calls = {}
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda p, q: (calls.setdefault("params", q), _WORKS_PAYLOAD)[1])
    client.search_works("merit economy", year_from=2010)
    assert "publication_year:2010-" in calls["params"]["filter"]


def test_search_works_polite_pool_email(monkeypatch):
    """配置 email → 附 mailto 进 polite pool（OpenAlex 推荐的稳定调用方式）。"""
    calls = {}
    client = OpenAlexClient(email="researcher@example.org")
    monkeypatch.setattr(client, "_get", lambda p, q: (calls.setdefault("params", q), _WORKS_PAYLOAD)[1])
    client.search_works("annadanam")
    assert calls["params"]["mailto"] == "researcher@example.org"


def test_search_works_chinese_source_links(monkeypatch):
    """中文社科深链（spike 实证的可用入口）：ncpssd 平台入口 + PubScholar 带 query 深链。

    OpenAlex 以外文为主，中文期刊需这些平台补位；ncpssd 无 GET 检索深链故给主页入口，
    PubScholar 免登录且 /search?q= 可用，把 query URL 编码后拼进深链。"""
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda p, q: _WORKS_PAYLOAD)
    env = client.search_works("白族银器 传承")
    urls = " ".join(s.url for s in env.data.chinese_sources)
    assert "ncpssd" in urls                       # 国家哲社文献中心入口
    assert "pubscholar.cn/search?q=" in urls       # PubScholar 带 query 深链
    assert "%E7%99%BD" in urls                    # “白”已 URL 编码进深链
    assert all(s.label for s in env.data.chinese_sources)  # 每条有中文标签


# ---------------------------------------------------------------- 失败折包络


def test_search_works_no_result(monkeypatch):
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda p, q: {"meta": {"count": 0}, "results": []})
    env = client.search_works("一个完全不存在的主题字符串")
    assert env.ok is False and env.error.code == "openalex_no_result"
    assert env.error.hint


def test_search_works_timeout_folds_envelope(monkeypatch):
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda p, q: {"_local_error": "timeout: timed out"})
    env = client.search_works("temple")
    assert env.ok is False and env.error.code == "openalex_timeout"


def test_search_works_rate_limited_folds_envelope(monkeypatch):
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda p, q: {"_local_error": "HTTP 429"})
    env = client.search_works("temple")
    assert env.ok is False and env.error.code == "literature_rate_limited"


def test_search_works_generic_error_folds_envelope(monkeypatch):
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda p, q: {"_local_error": "urlopen error: boom"})
    env = client.search_works("temple")
    assert env.ok is False and env.error.code == "openalex_error"


# ---------------------------------------------------------------- 能力注册


def test_search_literature_registered():
    meta = capabilities()["search_literature"]
    assert meta.read_only is True and meta.open_world is True
    names = {t["function"]["name"] for t in build_tool_schemas()}
    assert "search_literature" in names


def test_search_literature_fn_self_constructs_client(monkeypatch):
    """能力 fn 无 client 注入时自建 OpenAlexClient（同 amap 自持模式），不联网走 mock。"""
    monkeypatch.setattr(OpenAlexClient, "_get", lambda self, p, q: _WORKS_PAYLOAD)
    env = search_literature("commodity circulation")
    assert env.ok and isinstance(env.data, LiteraturePage)
    assert env.data.items[0].title == "The social life of things"
