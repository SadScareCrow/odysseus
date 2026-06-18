from src.tool_execution import _parse_qualified_mcp_args


def test_steam_mcp_placeholder_id_is_omitted_for_env_default():
    args, error = _parse_qualified_mcp_args(
        "mcp__91d755cf__steam_library",
        '{"operation":"owned","steamid":"STEAM_ID"}',
    )

    assert error is None
    assert args == {"operation": "owned"}


def test_steam_mcp_real_id_is_preserved():
    args, error = _parse_qualified_mcp_args(
        "mcp__91d755cf__steam_library",
        '{"operation":"owned","steamid":"76561198000000000"}',
    )

    assert error is None
    assert args == {"operation": "owned", "steamid": "76561198000000000"}
