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
    assert total_cmds == 37


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
