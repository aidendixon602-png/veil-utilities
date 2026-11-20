"""Presentation and behavior of the Veil info cards (no Discord calls)."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
from discord.ext import commands

from cogs.utility import Utility, _role_mentions
from core.help import Help, HelpView
from core.utils import Colors


def asset(url='https://cdn.example/avatar.png'):
    return SimpleNamespace(url=url)


def field(embed, name):
    return next(item.value for item in embed.fields if item.name == name)


def assert_fits(embed):
    assert len(embed) <= 6000
    assert len(embed.title or '') <= 256 and len(embed.description or '') <= 4096
    assert all(len(item.name) <= 256 and 0 < len(item.value) <= 1024 for item in embed.fields)


def user(**kwargs):
    return SimpleNamespace(id=42, display_avatar=asset(), created_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                           public_flags=SimpleNamespace(all=lambda: [SimpleNamespace(name='hypesquad_balance')]), **kwargs)


def test_user_profile_uses_real_dates_badges_and_banner_and_suppresses_mentions():
    async def run():
        profile = user()
        bot = SimpleNamespace(fetch_user=AsyncMock(return_value=SimpleNamespace(banner=asset('https://cdn.example/banner.png'))))
        ctx = SimpleNamespace(author=profile, send=AsyncMock())
        await Utility.userinfo.callback(Utility(bot), ctx)
        kwargs = ctx.send.await_args.kwargs
        embed = kwargs['embed']
        assert embed.title == 'User profile' and embed.colour.value == Colors.main
        assert '<@42>' in embed.description and 'ID 42' in embed.description
        assert field(embed, 'Account created').startswith('<t:')
        assert field(embed, 'Discord badges') == 'Hypesquad Balance'
        assert all(f.name != 'Joined server' for f in embed.fields)
        assert embed.image.url == 'https://cdn.example/banner.png'
        assert kwargs['allowed_mentions'].users is False and kwargs['allowed_mentions'].roles is False
        assert_fits(embed)
    asyncio.run(run())


def test_member_profile_role_overflow_cached_order_and_missing_join_date():
    async def run():
        joined = datetime(2022, 1, 1, tzinfo=timezone.utc)
        member = MagicMock(spec=discord.Member)
        member.id, member.created_at, member.joined_at = 42, joined - timedelta(days=10), joined
        member.display_avatar, member.premium_since = asset(), None
        member.public_flags = SimpleNamespace(all=lambda: [])
        member.roles = [SimpleNamespace(mention='@everyone')] + [SimpleNamespace(mention=f'<@&{i:018d}>') for i in range(100)]
        older = SimpleNamespace(joined_at=joined - timedelta(days=1))
        unknown = SimpleNamespace(joined_at=None)
        member.guild = SimpleNamespace(members=[member, unknown, older])
        bot = SimpleNamespace(fetch_user=AsyncMock(side_effect=aiohttp.ClientError()))
        ctx = SimpleNamespace(author=member, send=AsyncMock())
        await Utility.userinfo.callback(Utility(bot), ctx)
        embed = ctx.send.await_args.kwargs['embed']
        value = field(embed, 'Server roles · 100')
        assert '+56 more' in value or ' more' in value
        assert all(part.endswith('>') for part in value.split('  ·')[0].split())
        assert 'Cached join order #2' in embed.footer.text and 'Joined server' in [f.name for f in embed.fields]
        assert_fits(embed)
        member.joined_at = None
        member.roles = member.roles[:1]
        await Utility.userinfo.callback(Utility(bot), ctx)
        embed = ctx.send.await_args.kwargs['embed']
        assert field(embed, 'Joined server') == 'Unavailable'
        assert field(embed, 'Server roles · 0') == 'No server roles'
        assert 'Cached join order' not in embed.footer.text
    asyncio.run(run())


def test_role_mentions_never_splits_a_mention_and_respects_discord_field_limit():
    values = [f'<@&{i:019d}>' for i in range(1000)]
    result = _role_mentions(values)
    shown, suffix = result.split('  ·  ')
    assert len(result) <= 1024 and all(s in values for s in shown.split())
    assert suffix == f'+{len(values) - len(shown.split()):,} more'
    assert _role_mentions([]) == 'No server roles'


def test_bot_overview_reports_process_values_and_unavailable_latency(monkeypatch):
    import importlib
    monkeypatch.setattr(importlib.import_module('cogs.owner'), 'memory_mb', lambda: '100 MB (peak)')
    async def run():
        bot = SimpleNamespace(user=SimpleNamespace(name='Veil Utilities', display_avatar=asset()),
                              latency=0.1234, started_at=0, walk_commands=lambda: iter(range(12)))
        ctx = SimpleNamespace(send=AsyncMock())
        monkeypatch.setattr('cogs.utility.time.time', lambda: 60)
        await Utility.botinfo.callback(Utility(bot), ctx)
        kwargs = ctx.send.await_args.kwargs
        embed = kwargs['embed']
        assert embed.title == 'Bot overview' and embed.author.name == 'Veil Utilities'
        assert field(embed, 'Gateway latency') == '123 ms'
        assert field(embed, 'Registered commands') == '12'
        assert field(embed, 'Memory') == '100 MB (peak)' and field(embed, 'Uptime') == '1 minute'
        assert kwargs['allowed_mentions'].everyone is False
        assert_fits(embed)
        bot.latency = float('nan')
        await Utility.botinfo.callback(Utility(bot), ctx)
        assert field(ctx.send.await_args.kwargs['embed'], 'Gateway latency') == 'Unavailable'
    asyncio.run(run())


def test_help_home_brand_prefix_and_aliases_remain_intact():
    bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
    bot.add_command(Utility.userinfo.copy())
    bot.add_command(Utility.botinfo.copy())
    assert all(bot.get_command(alias) is bot.get_command('userinfo') for alias in ('ui', 'info', 'whois', 'user'))
    assert all(bot.get_command(alias) is bot.get_command('botinfo') for alias in ('bi', 'about', 'stats'))
    h = Help()
    h.context = SimpleNamespace(clean_prefix='!', author=SimpleNamespace(id=5), bot=bot, guild=None)
    view = HelpView(h, {'Utility': []})
    assert view.home.title is None and view.home.color.value == Colors.main
    assert view.home.description == 'Select a category below to view its commands.\n\n**prefix:** `!`'
    assert view.home.footer.text is None
    assert_fits(view.home)
    view.stop()
    asyncio.run(bot.close())
