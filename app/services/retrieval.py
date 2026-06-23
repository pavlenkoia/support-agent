from pathlib import Path


class RetrievalService:
    def retrieve(self, query: str, knowledge_backend: str, knowledge_root: str) -> dict:
        if knowledge_backend != "filesystem":
            return {"kb_status": "not_found", "kb_snippets": []}

        kb_index = Path(knowledge_root) / "index.md"
        if kb_index.exists():
            return {
                "kb_status": "found",
                "kb_snippets": [{
                    "text": "External knowledge base index available",
                    "source_type": "kb_article",
                    "source_ref": str(kb_index),
                    "retrieval_notes": f"filesystem lookup for query: {query}",
                }],
            }

        return {"kb_status": "not_found", "kb_snippets": []}
