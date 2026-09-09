"""A labelled recall floor for memory retrieval.

Token saving on its own is a meaningless number: dropping every bullet saves
100%.  The claim only means something paired with what survived, so this is the
other half -- a hand-labelled set of queries with the bullets that must still be
there afterwards, and a floor that turns a ranking regression red.

The set is small and synthetic, and that is a real limitation: it is written by
the same person who wrote the ranker, so it measures "does the ranker still do
what it did" far better than it measures "is the ranker good".  It is a
regression fence, not a leaderboard.
"""

from __future__ import annotations

import pytest

from engine.context.budget import estimate_tokens
from engine.memory.retrieval import RetrievalConfig, chunk_document, retrieve_memory

# A document with enough distinct topics that a query has to actually
# discriminate.  Topics are deliberately overlapping in vocabulary -- "sqlite"
# appears in three different bullets across two sections -- because a corpus
# where every query has one lexically unique answer flatters any ranker.
MEMORY = """# Project Memory

## Active Work
- **当前任务**: 正在实现记忆的查询期检索层
- **进行中的重构**: shell 的渲染决策抽成不含 Ink 的模块

## Pending
- **待办-审批超时**: 审批超时后的 run 终态语义还没定
- **待办-保留策略**: trace 与 incident 的保留期未定义

## Verified Outcomes
- **sqlite 并发**: WAL 模式下开启 busy_timeout 后并发写不再报 database is locked
- **回填事务**: 按持锁时长而非行数切分回填事务，热路径不再是二次复杂度
- **token 估算**: CJK 宽字符按 1/3 token 估算会低估日韩输入三倍
- **哈希链降级**: 审计链在旧格式记录上会走降级校验路径
- **子 Agent 预算**: 批与单体双 token 预算能挡住失控的委派
- **CI 平台差异**: 同一套测试在两个 runner 上的结果取决于 git 配置

## Decisions
- **单 Agent**: 不做常驻的第二个 Agent，先把单链路正确性做完
- **不设向量库**: 检索索引落在既有 SQLite 上，不引入外部向量服务
- **审批不可绕过**: tool guard 是硬边界，skill 与插件都不能绕开
- **文档正本**: 每个主题只保留一份权威文档，其余归档
- **本地优先**: 除 LLM provider 外不产生任何出站流量

## Known Pitfalls
- **sqlite wal 坑**: WAL 的 -wal/-shm 副文件必须与主库一起搬，否则拒绝打开
- **ink CI 坑**: ink 检测到 CI 会关掉 clear-and-reprint，擦除序列不会出现
- **git 自动打包**: gc.auto 交给环境决定的话，commit 会在关键路径上突然打包
- **审批阻塞**: 没有应答方时写类工具会挂满整个审批超时
- **空 run 假通过**: 崩掉的 run 工具序列同样为空，会被当成"正确地什么都没做"
- **符号链接逃逸**: 托管路径的每一段都要拒绝符号链接，否则可以逃出数据根
"""

# query → the topic keys that must survive retrieval.
LABELLED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sqlite wal 的副文件要注意什么", ("sqlite wal 坑",)),
    ("并发写报 database is locked 怎么办", ("sqlite 并发",)),
    ("为什么 CI 上和本地结果不一样", ("CI 平台差异", "ink CI 坑", "git 自动打包")),
    ("审批超时之后 run 是什么状态", ("待办-审批超时",)),
    ("写类工具为什么会卡住", ("审批阻塞",)),
    ("我们为什么不引入向量数据库", ("不设向量库",)),
    ("要不要再加一个常驻 Agent", ("单 Agent",)),
    ("token 估算对中日韩有什么问题", ("token 估算",)),
    ("测试怎么会假通过", ("空 run 假通过",)),
    ("现在在做什么", ("当前任务",)),
    ("skill 能不能绕过权限检查", ("审批不可绕过",)),
    ("回填为什么慢", ("回填事务",)),
)

# Recall must not drop below this.  It is a floor to notice regressions, not a
# target: raising it by tuning against this very set would only overfit to it.
RECALL_FLOOR = 0.90

# Below this the narrowing is not paying for its own complexity.
SAVING_FLOOR = 0.30


def _kept_topics(query: str, config: RetrievalConfig | None = None) -> set[str]:
    result = retrieve_memory(MEMORY, query, config)
    return {chunk.topic for chunk in result.kept if chunk.topic}


# Queries the lexical ranker provably cannot serve, kept in the set rather than
# quietly relabelled.  Deleting a label because the ranker misses it is how a
# benchmark stops measuring anything.
#
# "为什么 CI 上和本地结果不一样" must reach the bullet about ``gc.auto``, whose
# text ("commit 会在关键路径上突然打包") shares **no token at all** with the
# query.  It is the canonical case for semantic retrieval and the concrete
# reason ``RetrievalConfig.embedder`` exists.  strict=True on purpose: if an
# embedder is configured and this starts passing, the run says so and the label
# should move out of this list.
KNOWN_LEXICAL_MISSES = {"为什么 CI 上和本地结果不一样"}


@pytest.mark.parametrize(
    "query, expected",
    [
        pytest.param(
            query, expected, id=query,
            marks=pytest.mark.xfail(
                reason="lexically disjoint from its answer; needs a semantic ranker",
                strict=True,
            ),
        )
        if query in KNOWN_LEXICAL_MISSES
        else pytest.param(query, expected, id=query)
        for query, expected in LABELLED
    ],
)
def test_each_labelled_query_keeps_its_answer(query: str, expected: tuple[str, ...]):
    """Per-query, so a failure names the query instead of a percentage."""
    kept = _kept_topics(query, RetrievalConfig(max_keep=10))
    missing = set(expected) - kept
    assert not missing, f"dropped {sorted(missing)} for {query!r}; kept {sorted(kept)}"


def test_recall_across_the_labelled_set_stays_above_the_floor():
    hits = 0
    total = 0
    for query, expected in LABELLED:
        kept = _kept_topics(query, RetrievalConfig(max_keep=10))
        for topic in expected:
            total += 1
            hits += int(topic in kept)
    recall = hits / total
    # The floor sits below 100% deliberately: it tolerates the known lexical
    # miss above while still catching a ranker that starts losing answers it
    # used to find.
    assert recall >= RECALL_FLOOR, f"recall fell to {recall:.0%} (floor {RECALL_FLOOR:.0%})"


def test_narrowing_still_pays_for_itself():
    """Recall is only half the claim; the other half is that it saved anything."""
    whole = estimate_tokens(MEMORY)
    savings = []
    for query, _ in LABELLED:
        result = retrieve_memory(MEMORY, query, RetrievalConfig(max_keep=10))
        savings.append((whole - estimate_tokens(result.text)) / whole)
    mean = sum(savings) / len(savings)
    assert mean >= SAVING_FLOOR, f"mean saving fell to {mean:.0%} (floor {SAVING_FLOOR:.0%})"


def test_the_labelled_topics_all_exist_in_the_document():
    """A typo in the fixture would make a query trivially unsatisfiable."""
    present = {chunk.topic for chunk in chunk_document(MEMORY) if chunk.topic}
    for query, expected in LABELLED:
        missing = set(expected) - present
        assert not missing, f"{query!r} labels topics that are not in the document: {missing}"
