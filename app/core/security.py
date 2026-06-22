def redact_for_logs(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-2:]
