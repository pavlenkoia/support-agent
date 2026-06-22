from pathlib import Path


class RetrievalService:
    def retrieve(self, query: str, knowledge_backend: str, knowledge_root: str) -> list[dict]:
        if knowledge_backend != "filesystem":
            return []

        kb_index = Path(knowledge_root) / "index.md"
        if kb_index.exists():
            return [{
                "source": str(kb_index),
                "snippet": "External knowledge base index available",
                "query": query,
                "backend": knowledge_backend,
            }]
        return []
