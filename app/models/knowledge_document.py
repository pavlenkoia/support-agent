from dataclasses import dataclass


@dataclass(slots=True)
class KnowledgeDocument:
    path: str
    title: str
