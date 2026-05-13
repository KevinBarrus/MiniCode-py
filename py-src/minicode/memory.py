from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from minicode.config import MINI_CODE_MEMORY_PATH


# ---------------------------------------------------------------------------
# Tokenization helpers
# ---------------------------------------------------------------------------

# Tokenize text into lowercase words, individual CJK chars, and CJK bigrams
_WORD_RE = re.compile(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]')
_CJK_BIGRAM_RE = re.compile(r'[\u4e00-\u9fff]{2}')
_TAG_RE = re.compile(r'`([^`]+)`')


class MemoryScope(Enum):
    """Memory scope — determines lifetime and update cadence."""

    PROJECT = "project"       # Project-level, manually curated
    SESSION = "session"       # Auto-collected during current session
    WORKSPACE = "workspace"   # Cross-session, auto-learned


@dataclass
class MemoryEntry:
    """A single memory entry with metadata for relevance scoring."""

    id: str
    scope: MemoryScope
    category: str
    content: str
    tags: list[str] = field(default_factory=list)
    usage_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Cached tokenization to avoid recomputing during search
    _cached_tokens: list[str] | None = None

    def get_tokens(self) -> list[str]:
        """Return cached tokens, computing on first call."""
        if self._cached_tokens is None:
            self._cached_tokens = _tokenize(
                f"{self.content} {self.category} {' '.join(self.tags)}"
            )
        return self._cached_tokens

    def bump_usage(self) -> None:
        """Increment usage counter and update timestamp."""
        self.usage_count += 1
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict for JSON storage."""
        return {
            "id": self.id,
            "scope": self.scope.value,
            "category": self.category,
            "content": self.content,
            "tags": self.tags,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        """Deserialize from plain dict."""
        return cls(
            id=data["id"],
            scope=MemoryScope(data["scope"]),
            category=data["category"],
            content=data["content"],
            tags=data.get("tags", []),
            usage_count=data.get("usage_count", 0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class MemoryFile:
    """A scope-specific memory file with in-memory index."""

    scope: MemoryScope
    entries: list[MemoryEntry] = field(default_factory=list)
    dirty: bool = False

    def add(self, entry: MemoryEntry) -> None:
        """Add a new entry, replacing existing entry with same content."""
        # Check for duplicate content (same content in same category)
        for i, existing in enumerate(self.entries):
            if (existing.content == entry.content
                    and existing.category == entry.category):
                self.entries[i] = entry
                self.dirty = True
                return
        self.entries.append(entry)
        self.dirty = True

    def search(self, query: str) -> list[MemoryEntry]:
        """Search entries by keyword with BM25 relevance scoring.

        Combines BM25 semantic relevance with usage frequency for
        better result ranking than simple substring matching.
        Query terms are expanded using code terminology dictionary.
        Exact tag matches receive highest priority scores.
        """
        if not self.entries:
            return []

        query_tokens = _tokenize(query)
        query_tokens = _expand_query_terms(query_tokens)
        if not query_tokens:
            return []

        query_lower = query.lower()
        query_terms = query_lower.split()

        entry_tokens = [entry.get_tokens() for entry in self.entries]

        idf = _compute_idf(entry_tokens)
        avgdl = _compute_avgdl(entry_tokens)
        now = time.time()

        scored: list[tuple[float, MemoryEntry]] = []
        for i, entry in enumerate(self.entries):
            bm25 = _bm25_score(query_tokens, entry_tokens[i], idf, avgdl)

            substring_score = 0.0
            content_lower = entry.content.lower()
            if query_lower in content_lower:
                substring_score = 2.0
            elif any(q in content_lower for q in query_terms):
                substring_score = 1.0

            tag_score = 0.0
            entry_tags = entry.tags
            entry_category_lower = entry.category.lower()
            exact_tag_match = any(
                tag.lower() == query_lower for tag in entry_tags
            )
            partial_tag_match = any(
                query_lower in tag.lower() for tag in entry_tags
            )
            if exact_tag_match:
                tag_score = 5.0
            elif partial_tag_match:
                tag_score = 1.5
            if query_lower in entry_category_lower:
                tag_score += 1.0

            match_score = bm25 + substring_score + tag_score
            if match_score <= 0:
                continue

            usage_bonus = math.log1p(entry.usage_count) * 0.3

            age_hours = (now - entry.updated_at) / 3600
            recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5

            total_score = match_score + usage_bonus + recency_bonus
            scored.append((total_score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored]

    def format_as_markdown(self) -> str:
        """Format all entries as a markdown document."""
        lines = [f"# {self.scope.value.title()} Memory\n"]
        categories: dict[str, list[MemoryEntry]] = {}
        for entry in self.entries:
            categories.setdefault(entry.category, []).append(entry)

        for category in sorted(categories):
            lines.append(f"## {category}\n")
            for entry in categories[category]:
                tags_str = f" `{'` `'.join(entry.tags)}`" if entry.tags else ""
                lines.append(f"- {entry.content}{tags_str}")
            lines.append("")

        return "\n".join(lines)


class MemoryManager:
    """Manages all memory scopes with persistence and relevance scoring."""

    def __init__(self) -> None:
        self.memories: dict[MemoryScope, MemoryFile] = {
            scope: MemoryFile(scope=scope) for scope in MemoryScope
        }
        self._search_cache: dict[tuple[str, MemoryScope | None], list[MemoryEntry]] = {}
        self._search_cache_ttl = 300  # 5 minutes
        self._search_cache_timestamp: dict[tuple[str, MemoryScope | None], float] = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load all memory files from disk."""
        for scope in MemoryScope:
            path = _get_memory_path(scope)
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    self._parse_memory_md(content, scope)
                except Exception:
                    pass  # Corrupted file, start fresh

    def save(self) -> None:
        """Save all dirty memory files to disk."""
        for scope, mem_file in self.memories.items():
            if mem_file.dirty:
                path = _get_memory_path(scope)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(mem_file.format_as_markdown(), encoding="utf-8")
                mem_file.dirty = False

    def _parse_memory_md(self, content: str, scope: MemoryScope) -> None:
        """Parse MEMORY.md file into entries."""
        lines = content.split("\n")
        current_category = "general"
        entry_counter = 0

        for line in lines:
            line = line.strip()

            # Skip headers and metadata
            if line.startswith("#") or line.startswith("*") or not line:
                if line.startswith("## "):
                    current_category = line[3:].strip().lower()
                continue

            # Parse list items
            if line.startswith("- "):
                entry_content = line[2:]

                # Extract tags
                tags = []
                if "`" in entry_content:
                    tag_matches = _TAG_RE.findall(entry_content)
                    for tag_match in tag_matches:
                        tags.extend(tag_match.split())
                    entry_content = _TAG_RE.sub("", entry_content).strip()

                entry_counter += 1
                entry = MemoryEntry(
                    id=f"{scope.value}-{entry_counter}",
                    scope=scope,
                    category=current_category,
                    content=entry_content,
                    tags=tags,
                )
                self.memories[scope].entries.append(entry)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def add(
        self,
        content: str,
        scope: MemoryScope = MemoryScope.SESSION,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        """Add a new memory entry.

        Auto-classifies content if category is not provided.
        """
        if category is None:
            category, auto_tags = _auto_classify_content(content)
            if tags:
                tags = list(set(tags + auto_tags))
            else:
                tags = auto_tags

        entry_id = f"{scope.value}-{len(self.memories[scope].entries) + 1}"
        entry = MemoryEntry(
            id=entry_id,
            scope=scope,
            category=category,
            content=content,
            tags=tags or [],
        )
        self.memories[scope].add(entry)
        return entry

    def get(self, entry_id: str) -> MemoryEntry | None:
        """Get a memory entry by ID."""
        for scope in MemoryScope:
            for entry in self.memories[scope].entries:
                if entry.id == entry_id:
                    return entry
        return None

    def update(self, entry_id: str, **kwargs: Any) -> MemoryEntry | None:
        """Update a memory entry by ID."""
        entry = self.get(entry_id)
        if entry is None:
            return None

        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
                if key in ("content", "category", "tags"):
                    entry._cached_tokens = None  # Invalidate token cache

        entry.updated_at = time.time()
        self.memories[entry.scope].dirty = True
        return entry

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry by ID."""
        for scope in MemoryScope:
            for i, entry in enumerate(self.memories[scope].entries):
                if entry.id == entry_id:
                    self.memories[scope].entries.pop(i)
                    self.memories[scope].dirty = True
                    return True
        return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        limit: int = 20,
        min_relevance: float = 0.0,
    ) -> list[MemoryEntry]:
        """Search memories across scopes with relevance scoring.

        Uses cached results when the same query is repeated within
        the cache TTL window.
        """
        cached = self._get_cached_search(query, scope)
        if cached is not None:
            return cached[:limit]

        results = []

        scopes_to_search = [scope] if scope else list(MemoryScope)

        for s in scopes_to_search:
            results.extend(self.memories[s].search(query))

        # Apply minimum relevance threshold
        # (entries are already scored by MemoryFile.search)
        if min_relevance > 0 and results:
            query_tokens = _tokenize(query)
            scores = [self._score_entry(e, query_tokens) for e in results]
            max_score = max(scores) if scores else 0
            if max_score > 0:
                results = [
                    e for e, s in zip(results, scores)
                    if s / max_score >= min_relevance
                ]

        # Results are already ranked by MemoryFile.search()
        # Deduplicate by content (keep highest-scored)
        seen_content: set[str] = set()
        deduped = []
        for entry in results:
            content_key = entry.content[:100].strip().lower()
            if content_key not in seen_content:
                seen_content.add(content_key)
                deduped.append(entry)

        self._cache_search_result(query, scope, deduped)
        return deduped[:limit]

    def _score_entry(self, entry: MemoryEntry, query_tokens: list[str]) -> float:
        """Compute relevance score for a memory entry."""
        if not query_tokens:
            return 0.0

        query_tokens_expanded = _expand_query_terms(query_tokens)
        entry_tokens = _tokenize(
            f"{entry.content} {entry.category} {' '.join(entry.tags)}"
        )
        idf = _compute_idf([entry_tokens])
        avgdl = len(entry_tokens)
        bm25 = _bm25_score(query_tokens_expanded, entry_tokens, idf, avgdl)

        query_lower = " ".join(query_tokens).lower()
        content_lower = entry.content.lower()
        substring_score = 0.0
        if query_lower in content_lower:
            substring_score = 2.0
        elif any(q in content_lower for q in query_tokens):
            substring_score = 1.0

        tag_score = 0.0
        entry_tags = entry.tags
        entry_category_lower = entry.category.lower()
        exact_tag_match = any(tag.lower() == query_lower for tag in entry_tags)
        partial_tag_match = any(query_lower in tag.lower() for tag in entry_tags)
        if exact_tag_match:
            tag_score = 5.0
        elif partial_tag_match:
            tag_score = 1.5
        if query_lower in entry_category_lower:
            tag_score += 1.0

        usage_bonus = math.log1p(entry.usage_count) * 0.3

        age_hours = (time.time() - entry.updated_at) / 3600
        recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5

        return bm25 + substring_score + tag_score + usage_bonus + recency_bonus
    
    def get_relevant_context(
        self,
        max_entries: int = 20,
        max_tokens: int = 8000,
    ) -> str:
        """Get relevant context for the current session.

        Combines project, workspace, and session memories into a
        formatted context string suitable for inclusion in prompts.
        """
        context_parts = []

        # Project memory (highest priority)
        project_memories = self.memories[MemoryScope.PROJECT].entries
        if project_memories:
            context_parts.append("## Project Knowledge")
            for entry in project_memories[:max_entries // 3]:
                context_parts.append(f"- [{entry.category}] {entry.content}")

        # Workspace memory (medium priority)
        workspace_memories = self.memories[MemoryScope.WORKSPACE].entries
        if workspace_memories:
            context_parts.append("\n## Workspace Patterns")
            for entry in workspace_memories[:max_entries // 3]:
                context_parts.append(f"- [{entry.category}] {entry.content}")

        # Session memory (recent context)
        session_memories = self.memories[MemoryScope.SESSION].entries
        if session_memories:
            context_parts.append("\n## Session Context")
            for entry in session_memories[:max_entries // 3]:
                context_parts.append(f"- [{entry.category}] {entry.content}")

        context = "\n".join(context_parts)

        # Rough token estimation (4 chars per token)
        if len(context) > max_tokens * 4:
            context = context[:max_tokens * 4] + "\n... (truncated)"

        return context

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _get_cached_search(
        self, query: str, scope: MemoryScope | None
    ) -> list[MemoryEntry] | None:
        """Return cached search results if still valid."""
        key = (query, scope)
        timestamp = self._search_cache_timestamp.get(key)
        if timestamp and time.time() - timestamp < self._search_cache_ttl:
            return copy.deepcopy(self._search_cache.get(key))
        return None

    def _cache_search_result(
        self,
        query: str,
        scope: MemoryScope | None,
        results: list[MemoryEntry],
    ) -> None:
        """Cache search results with timestamp."""
        key = (query, scope)
        self._search_cache[key] = copy.deepcopy(results)
        self._search_cache_timestamp[key] = time.time()

    def clear_cache(self) -> None:
        """Clear the search cache."""
        self._search_cache.clear()
        self._search_cache_timestamp.clear()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_CATEGORY_TO_TAGS: dict[str, list[str]] = {
    "architecture": ["design-pattern"],
    "code-pattern": ["function"],
    "testing": ["test"],
    "configuration": ["config"],
    "workflow": ["git"],
    "security": ["security"],
    "performance": ["optimization"],
    "convention": ["style"],
}

_CLASSIFICATION_RULES: list[tuple[str, list[str], list[str]]] = [
    ("architecture", ["design", "pattern", "structure", "module", "layer", "架构", "设计模式", "结构"], ["design-pattern"]),
    ("code-pattern", ["function", "method", "class", "interface", "api", "函数", "方法", "类"], ["function"]),
    ("testing", ["test", "spec", "assert", "mock", "测试", "断言", "用例"], ["test"]),
    ("configuration", ["config", "setting", "env", "variable", "配置", "环境变量", "设置"], ["config"]),
    ("workflow", ["git", "commit", "branch", "merge", "pr", "工作流", "分支", "合并"], ["git"]),
    ("security", ["security", "auth", "permission", "encrypt", "安全", "认证", "权限"], ["security"]),
    ("performance", ["performance", "optimize", "cache", "speed", "性能", "优化", "缓存"], ["optimization"]),
    ("convention", ["convention", "style", "format", "lint", "规范", "格式", "风格"], ["style"]),
]


def _auto_classify_content(content: str) -> tuple[str, list[str]]:
    """Analyze content and return (category, tags) using keyword heuristics.

    Supports both English and Chinese keywords. Returns "general" category
    with empty tags if no classification rules match.

    Args:
        content: Text content to classify

    Returns:
        Tuple of (category, tags) - e.g., ("architecture", ["design-pattern"])
    """
    content_lower = content.lower()
    category_scores: dict[str, int] = {}
    matched_tags: list[str] = []

    for category, keywords in _CLASSIFICATION_RULES:
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            category_scores[category] = score
            matched_tags.extend(_CATEGORY_TO_TAGS.get(category, []))

    if not category_scores:
        return "general", []

    best_category = max(category_scores, key=category_scores.get)
    return best_category, matched_tags


# ---------------------------------------------------------------------------
# Tokenization helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words, individual CJK chars, and CJK bigrams.

    This tokenizer handles:
    - English words and numbers (lowercased)
    - Individual CJK characters
    - CJK bigrams (pairs of consecutive CJK characters)
    - Mixed content (e.g., "hello世界" -> ["hello", "世", "界", "世界"])

    Args:
        text: Raw text to tokenize

    Returns:
        List of tokens for indexing and search
    """
    text = text.lower()
    tokens = []

    # Extract English words and numbers, and individual CJK characters
    for match in _WORD_RE.finditer(text):
        token = match.group()
        tokens.append(token)

    # Extract CJK bigrams (pairs of consecutive CJK characters)
    for match in _CJK_BIGRAM_RE.finditer(text):
        tokens.append(match.group())

    return tokens


# ---------------------------------------------------------------------------
# BM25 scoring
# ---------------------------------------------------------------------------


def _compute_idf(doc_tokens: list[list[str]]) -> dict[str, float]:
    """Compute IDF (Inverse Document Frequency) for each token.

    IDF measures how rare a term is across all documents.
    Rare terms get higher weights, common terms get lower weights.

    Formula: log((N - n + 0.5) / (n + 0.5) + 1)
    where N = total documents, n = documents containing the term
    """
    N = len(doc_tokens)
    if N == 0:
        return {}

    token_doc_count: dict[str, int] = {}
    for tokens in doc_tokens:
        seen = set(tokens)
        for token in seen:
            token_doc_count[token] = token_doc_count.get(token, 0) + 1

    idf = {}
    for token, n in token_doc_count.items():
        idf[token] = math.log((N - n + 0.5) / (n + 0.5) + 1)

    return idf


def _compute_avgdl(doc_tokens: list[list[str]]) -> float:
    """Compute average document length.

    Used in BM25 scoring to normalize term frequency by document length.
    """
    if not doc_tokens:
        return 0.0
    return sum(len(tokens) for tokens in doc_tokens) / len(doc_tokens)


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Compute BM25 relevance score for a document.

    BM25 is a probabilistic ranking function that considers:
    - Term frequency (how often query terms appear in the document)
    - Inverse document frequency (how rare the term is across all documents)
    - Document length normalization (shorter documents get a boost)

    Formula for each term:
    score = IDF * (f * (k1 + 1)) / (f + k1 * (1 - b + b * |D| / avgdl))
    where f = term frequency, |D| = document length

    Args:
        query_tokens: Tokenized query terms
        doc_tokens: Tokenized document content
        idf: IDF dictionary for all tokens
        avgdl: Average document length
        k1: Term frequency saturation parameter (default: 1.5)
        b: Length normalization parameter (default: 0.75)

    Returns:
        BM25 relevance score (higher = more relevant)
    """
    if not query_tokens or not doc_tokens or avgdl == 0:
        return 0.0

    # Count term frequencies in document
    tf: dict[str, int] = {}
    for token in doc_tokens:
        tf[token] = tf.get(token, 0) + 1

    # Document length
    D = len(doc_tokens)

    score = 0.0
    for token in query_tokens:
        if token not in idf:
            continue

        f = tf.get(token, 0)
        if f == 0:
            continue

        # BM25 formula
        numerator = f * (k1 + 1)
        denominator = f + k1 * (1 - b + b * D / avgdl)
        score += idf[token] * numerator / denominator

    return score


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------

# Code terminology synonyms for query expansion
_CODE_SYNONYMS: dict[str, list[str]] = {
    "function": ["func", "method", "def", "fn"],
    "class": ["type", "struct", "object"],
    "variable": ["var", "let", "const", "param", "arg"],
    "import": ["include", "require", "using", "from"],
    "error": ["exception", "raise", "throw", "catch", "panic"],
    "test": ["spec", "assert", "mock", "unittest"],
    "config": ["setting", "env", "configuration", "option"],
    "build": ["compile", "make", "cmake", "gradle", "webpack"],
    "deploy": ["release", "publish", "ship", "cd"],
    "git": ["commit", "branch", "merge", "pr", "repository"],
    "api": ["endpoint", "route", "handler", "controller"],
    "database": ["db", "sql", "query", "table", "schema"],
    "async": ["await", "promise", "future", "callback"],
    "cache": ["memoize", "buffer", "store", "redis"],
    "log": ["logger", "debug", "trace", "audit"],
    "auth": ["login", "token", "jwt", "oauth", "permission"],
    "validate": ["check", "verify", "assert", "sanitize"],
    "serialize": ["json", "xml", "yaml", "parse", "encode"],
    "performance": ["optimize", "speed", "latency", "benchmark"],
    "security": ["encrypt", "hash", "sanitize", "xss", "csrf"],
}


def _expand_query_terms(query_tokens: list[str]) -> list[str]:
    """Expand query terms with code terminology synonyms.

    This improves search recall by matching related terms.
    For example, searching for "function" also matches "method" and "def".

    Args:
        query_tokens: Original tokenized query terms

    Returns:
        Expanded list of query tokens including synonyms
    """
    expanded = set(query_tokens)

    for token in query_tokens:
        # Check if token is a synonym key
        if token in _CODE_SYNONYMS:
            expanded.update(_CODE_SYNONYMS[token])
        # Check if token is a synonym value
        for key, synonyms in _CODE_SYNONYMS.items():
            if token in synonyms:
                expanded.add(key)
                expanded.update(synonyms)

    return list(expanded)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _get_memory_path(scope: MemoryScope) -> Path:
    """Get the file path for a memory scope."""
    base = Path(MINI_CODE_MEMORY_PATH)
    return base / f"MEMORY-{scope.value.upper()}.md"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_manager: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    """Get the global memory manager instance (lazy init + load)."""
    global _manager
    if _manager is None:
        _manager = MemoryManager()
        _manager.load()
    return _manager


def reset_memory_manager() -> None:
    """Reset the global memory manager (useful for testing)."""
    global _manager
    _manager = None
