from app.services.insights_service import build_insights_dashboard, get_agent_context


def test_build_insights_dashboard_summarizes_records_and_sessions() -> None:
    records = [
        {
            "uuid": "11111111-1111-4111-8111-111111111111",
            "timestampIso": "2026-04-18T10:00:00.000Z",
            "promptStatus": "captured",
            "prompt": {"modelName": "gpt-4o-mini"},
            "insertion": {
                "extractedInsertedCodeBlock": "const token = process.env.API_KEY; exec(command);",
                "netAddedLines": 24,
            },
            "file": {"path": "src/auth/middleware.ts"},
            "metadata": {
                "correlationConfidence": 0.85,
                "agentContext": {
                    "toolName": "Cursor",
                    "provider": "OpenAI",
                    "sessionKind": "agentic",
                    "host": "api.openai.com",
                    "userAgent": "cursor-agent",
                    "modelName": "gpt-4o-mini",
                    "sessionSignature": "Cursor|OpenAI|gpt-4o-mini|agentic",
                    "detectedAtIso": "2026-04-18T10:00:00.000Z",
                },
            },
        },
        {
            "uuid": "22222222-2222-4222-8222-222222222222",
            "timestampIso": "2026-04-18T10:10:00.000Z",
            "promptStatus": "captured",
            "prompt": {"modelName": "gpt-4o-mini"},
            "insertion": {
                "extractedInsertedCodeBlock": "const token = process.env.API_KEY;",
                "netAddedLines": 8,
            },
            "file": {"path": "src/auth/middleware.ts"},
            "metadata": {
                "correlationConfidence": 0.88,
                "agentContext": {
                    "toolName": "Cursor",
                    "provider": "OpenAI",
                    "sessionKind": "agentic",
                    "host": "api.openai.com",
                    "userAgent": "cursor-agent",
                    "modelName": "gpt-4o-mini",
                    "sessionSignature": "Cursor|OpenAI|gpt-4o-mini|agentic",
                    "detectedAtIso": "2026-04-18T10:10:00.000Z",
                },
            },
        },
    ]

    dashboard = build_insights_dashboard(records)

    assert dashboard["summary"]["totalRecords"] == 2
    assert dashboard["summary"]["uniqueAgentSessions"] == 1
    assert dashboard["agentSessions"][0]["toolName"] == "Cursor"
    assert dashboard["summary"]["highRiskRecords"] >= 1


def test_build_insights_dashboard_groups_by_native_session_id() -> None:
    records = [
        {
            "uuid": "33333333-3333-4333-8333-333333333333",
            "timestampIso": "2026-04-18T11:00:00.000Z",
            "promptStatus": "captured",
            "prompt": {"modelName": "gpt-4o-mini"},
            "insertion": {
                "extractedInsertedCodeBlock": "const alpha = true;",
                "netAddedLines": 4,
            },
            "file": {"path": "src/example-a.ts"},
            "metadata": {
                "correlationConfidence": 0.91,
                "agentContext": {
                    "toolName": "Cursor",
                    "provider": "OpenAI",
                    "sessionId": "cursor-session-123",
                    "conversationId": "cursor-conversation-a",
                    "runId": "cursor-run-a",
                    "sessionKind": "agentic",
                    "host": "api.openai.com",
                    "userAgent": "cursor-agent",
                    "modelName": "gpt-4o-mini",
                    "sessionSignature": "Cursor|OpenAI|gpt-4o-mini|agentic|signature-a",
                    "detectedAtIso": "2026-04-18T11:00:00.000Z",
                },
            },
        },
        {
            "uuid": "44444444-4444-4444-8444-444444444444",
            "timestampIso": "2026-04-18T11:05:00.000Z",
            "promptStatus": "captured",
            "prompt": {"modelName": "gpt-4o-mini"},
            "insertion": {
                "extractedInsertedCodeBlock": "const beta = true;",
                "netAddedLines": 6,
            },
            "file": {"path": "src/example-b.ts"},
            "metadata": {
                "correlationConfidence": 0.92,
                "agentContext": {
                    "toolName": "Cursor",
                    "provider": "OpenAI",
                    "sessionId": "cursor-session-123",
                    "conversationId": "cursor-conversation-b",
                    "runId": "cursor-run-b",
                    "sessionKind": "agentic",
                    "host": "api.openai.com",
                    "userAgent": "cursor-agent",
                    "modelName": "gpt-4o-mini",
                    "sessionSignature": "Cursor|OpenAI|gpt-4o-mini|agentic|signature-b",
                    "detectedAtIso": "2026-04-18T11:05:00.000Z",
                },
            },
        },
    ]

    dashboard = build_insights_dashboard(records)

    assert dashboard["summary"]["uniqueAgentSessions"] == 1
    assert dashboard["agentSessions"][0]["sessionId"] == "cursor-session-123"


def test_backend_risk_rules_match_frontend_process_and_sql_patterns() -> None:
    records = [
        {
            "uuid": "55555555-5555-4555-8555-555555555555",
            "timestampIso": "2026-04-18T12:00:00.000Z",
            "promptStatus": "captured",
            "prompt": {"modelName": "gpt-4o-mini"},
            "insertion": {
                "extractedInsertedCodeBlock": (
                    "spawn('npm', ['test']);\n"
                    "exec(command);\n"
                    "UPDATE users SET role = 'admin';\n"
                    "DELETE FROM sessions;"
                ),
                "netAddedLines": 8,
            },
            "file": {"path": "src/admin.ts"},
            "metadata": {"correlationConfidence": 0.9},
        }
    ]

    dashboard = build_insights_dashboard(records)
    preview = dashboard["highRiskRecords"][0]

    assert dashboard["summary"]["highRiskRecords"] == 1
    assert preview["riskScore"] >= 38


def test_backend_legacy_agent_context_infers_operation_type() -> None:
    records = [
        {
            "uuid": "66666666-6666-4666-8666-666666666666",
            "timestampIso": "2026-04-18T13:00:00.000Z",
            "promptStatus": "captured",
            "prompt": {"modelName": "gpt-4o-mini"},
            "insertion": {
                "extractedInsertedCodeBlock": "const renamed = simplify(oldName);",
                "netAddedLines": 5,
            },
            "file": {"path": "src/refactor.ts"},
            "correlation": {
                "promptStatus": "captured",
                "captureStatus": "full",
                "targetHost": "api.openai.com",
                "requestHeaders": {"user-agent": "unknown-client"},
                "parameters": {},
                "fullPromptMessages": [{"role": "user", "content": "Refactor and simplify this module."}],
                "rawModelResponse": "const renamed = simplify(oldName);",
            },
            "metadata": {"correlationConfidence": 0.9},
        }
    ]

    agent_context = get_agent_context(records[0])

    assert agent_context is not None
    assert agent_context["operationType"] == "refactor"
