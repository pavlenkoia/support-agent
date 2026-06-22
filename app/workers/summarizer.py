def summarize_case(messages: list[str]) -> str:
    return " | ".join(messages[:3])
