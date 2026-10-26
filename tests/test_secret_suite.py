from unittest.mock import AsyncMock, MagicMock, patch

from core.help import SECRET_CATEGORIES, SecretHelpView


def test_secret_categories_coverage():
    assert "security" in SECRET_CATEGORIES
    assert "licensing" in SECRET_CATEGORIES
    assert "runtime" in SECRET_CATEGORIES
    assert "diagnostics" in SECRET_CATEGORIES
    assert "tools" in SECRET_CATEGORIES

    total_cmds = sum(len(c["commands"]) for c in SECRET_CATEGORIES.values())
    assert total_cmds == 40
    assert any(c[0] == "shell" for c in SECRET_CATEGORIES["tools"]["commands"])
    assert any(c[0] == "cogs" for c in SECRET_CATEGORIES["diagnostics"]["commands"])
    assert any(c[0] == "traceback" for c in SECRET_CATEGORIES["diagnostics"]["commands"])


def test_secret_help_view_overview_embed():
    ctx = MagicMock()
    ctx.clean_prefix = ","
    ctx.bot.guilds = [MagicMock()]
    ctx.bot.authorized = {12345}
    ctx.bot.latency = 0.025
    ctx.bot.started_at = 1000

    view = SecretHelpView(ctx, initial_category="all")
    embed = view.get_embed()

    assert "🔒 Secret Commands — Developer Suite" in embed.title
    assert "Status" in embed.description
    assert len(embed.fields) == 5
    field_names = [f.name for f in embed.fields]
    assert any("Global Security" in name for name in field_names)
    assert any("Power Tools" in name for name in field_names)


def test_secret_help_view_category_detail():
    ctx = MagicMock()
    ctx.clean_prefix = ","
    ctx.bot.guilds = [MagicMock()]
    ctx.bot.authorized = {12345}
    ctx.bot.latency = 0.025
    ctx.bot.started_at = 1000

    view = SecretHelpView(ctx, initial_category="tools")
    embed = view.get_embed()

    assert "Power Tools" in embed.title
    assert ",eval" in embed.description
    assert ",sql" in embed.description
    assert ",sudo" in embed.description
    assert ",shell" in embed.description


def test_developer_embed_fields():
    import asyncio
    from core.help import Help, DeveloperView
    ctx = MagicMock()
    ctx.clean_prefix = ","
    ctx.guild = MagicMock(id=123, name="Veil HQ")
    ctx.author.id = 999
    bot = MagicMock()
    bot.user.name = "Veil Utilities"
    bot.user.display_avatar.url = "https://example.com/avatar.png"
    bot.guilds = [MagicMock(member_count=50, channels=[MagicMock(), MagicMock()])]
    bot.authorized = {123}
    bot.latency = 0.050
    bot.started_at = 1000
    bot.shard_count = 1
    bot.latencies = [(0, 0.050)]
    bot.walk_commands = MagicMock(return_value=[MagicMock(), MagicMock()])
    bot.cogs = {"Utility": MagicMock()}
    bot.extensions = {"cogs.utility": MagicMock()}
    bot.hardbans = set()
    bot.blacklist = set()
    bot.maintenance = None
    bot._error_seq = 0
    bot.db.fetchone = AsyncMock(return_value=1)
    bot.db.path = ":memory:"
    ctx.bot = bot

    help_cmd = Help()
    help_cmd.context = ctx

    embed = asyncio.run(help_cmd.developer_embed())
    assert embed.title == "Developer"
    assert len(embed.fields) >= 12
    field_names = [f.name for f in embed.fields]
    assert "Servers" in field_names
    assert "Users" in field_names
    assert "Channels" in field_names
    assert "Commands" in field_names
    assert "Uptime" in field_names
    assert "Latency" in field_names
    assert "Memory (RAM)" in field_names
    assert "Library" in field_names
    assert "Python" in field_names
    assert "Database" in field_names
    assert "System State" in field_names
    assert "Host Platform" in field_names

    # Test DeveloperView has 5 action buttons
    view = DeveloperView(help_cmd, author_id=999)
    assert len(view.children) == 5
    button_labels = [b.label for b in view.children]
    assert "Back" in button_labels
    assert "Refresh" in button_labels
    assert "Diagnostics" in button_labels
    assert "Logs" in button_labels
    assert "Secret Suite" in button_labels


def test_tokens_and_secrets_embed():
    from core.help import SecretHelpView
    ctx = MagicMock()
    ctx.clean_prefix = ","
    bot = MagicMock()
    bot.user.name = "Veil Utilities"
    bot.user.display_avatar.url = "https://example.com/avatar.png"
    bot.latency = 0.040
    bot.shard_count = 1
    bot.started_at = 1000
    ctx.bot = bot

    mock_settings = MagicMock()
    mock_settings.discord_token = "MTAxMjM0NTY3ODkw.abcdef.1234"
    mock_settings.database_url = None
    mock_settings.db_path = "veil.db"
    mock_settings.ai_api_key = "sk-ant-api03-abcdefg"
    mock_settings.ai_model = "gpt-oss-120b"
    mock_settings.lavalink_uri = "localhost:2333"
    mock_settings.lavalink_password = "youshallnotpass"
    mock_settings.spotify_client_id = "spot_client_id_123"
    mock_settings.spotify_client_secret = "spot_secret_456"
    mock_settings.lastfm_api_key = "lastfm_key_789"
    mock_settings.twitch_client_id = None
    mock_settings.twitch_client_secret = None
    mock_settings.ocr_space_api_key = None
    mock_settings.auth_required = True
    mock_settings.antinuke_dev_alerts = True

    with patch("core.config.settings", mock_settings):
        view = SecretHelpView(ctx, initial_category="tokens")
        embed = view.get_embed()

        assert "Secret Suite — Tokens & Runtime Credentials" in embed.title
        field_names = [f.name for f in embed.fields]
        assert any("Discord Bot Token" in n for n in field_names)
        assert any("Database" in n for n in field_names)
        assert any("AI Providers" in n for n in field_names)
        assert any("Audio & Lavalink" in n for n in field_names)
        assert any("Integrations & External APIs" in n for n in field_names)
        assert any("Security & Zero-Trust" in n for n in field_names)

        # Check that secrets are masked with bullets (never exposed in plaintext)
        for field in embed.fields:
            if "Discord Bot Token" in field.name:
                assert "●●●●" in field.value


def test_developer_diagnostics_and_logs_modes():
    import asyncio
    from types import SimpleNamespace
    from core.help import Help, DeveloperView
    ctx = MagicMock()
    ctx.clean_prefix = ","
    ctx.author.id = 999
    bot = MagicMock()
    bot.user.name = "Veil Utilities"
    bot.user.display_avatar.url = "https://example.com/avatar.png"
    bot.latency = 0.040
    bot.db.path = ":memory:"
    bot.db.fetchone = AsyncMock(return_value=1)
    bot.guilds = []
    bot.cogs = {}
    bot.walk_commands = MagicMock(return_value=[])
    bot.extensions = {}
    ctx.bot = bot

    help_cmd = Help()
    help_cmd.context = ctx

    view = DeveloperView(help_cmd, author_id=999)
    it = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), is_done=MagicMock(return_value=True)),
        edit_original_response=AsyncMock(),
        user=SimpleNamespace(id=999),
    )

    # 1. Switch to diagnostics mode
    diag_btn = next(b for b in view.children if b.label == "Diagnostics")
    asyncio.run(diag_btn.callback(it))
    assert view.mode == "diagnostics"
    it.edit_original_response.assert_awaited()
    diag_embed = it.edit_original_response.call_args[1]["embed"]
    assert "System Diagnostics & Hardware" in diag_embed.title

    # 2. Switch to logs mode
    it.edit_original_response.reset_mock()
    logs_btn = next(b for b in view.children if b.label == "Logs")
    asyncio.run(logs_btn.callback(it))
    assert view.mode == "logs"
    it.edit_original_response.assert_awaited()
    logs_embed = it.edit_original_response.call_args[1]["embed"]
    assert "Console Log Stream" in logs_embed.title

