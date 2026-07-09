from app.services.orchestrator import OrchestratorService


def test_summarize_llm_trace_counts_failovers() -> None:
    service = OrchestratorService.__new__(OrchestratorService)

    summary = service._summarize_llm_trace(
        [
            {
                "role": "kb_agent",
                "duration_ms": 120.5,
                "used_failover": True,
                "failover_count": 1,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            {
                "role": "direct_llm",
                "duration_ms": 80.0,
                "used_failover": False,
                "failover_count": 0,
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        ]
    )

    assert summary["call_count"] == 2
    assert summary["failover_count"] == 1
    assert summary["failover_calls"] == 1
    assert summary["by_role"]["kb_agent"]["failover_count"] == 1
    assert summary["by_role"]["kb_agent"]["failover_calls"] == 1
    assert summary["by_role"]["direct_llm"]["failover_count"] == 0
    assert summary["by_role"]["direct_llm"]["failover_calls"] == 0
