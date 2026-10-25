import pytest
from unittest.mock import AsyncMock, MagicMock

import discord
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
