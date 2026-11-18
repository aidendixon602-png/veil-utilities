"""Server/user info attribution, aliases and graceful API-failure behavior."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from discord.ext import commands

from cogs.utility import Utility
from core.bot import Veil
from core.help import Help


def test_serverinfo_and_si_share_attribution_and_actual_owner():
    async def go():
        b = commands.Bot(command_prefix=',', intents=discord.Intents.none())
        b.add_command(Utility.serverinfo.copy())
        command = b.get_command('serverinfo')
        assert b.get_command('si') is command
        help_cmd = Help()
        help_cmd.context = SimpleNamespace(clean_prefix='!', bot=b, author=SimpleNamespace(id=5), guild=None)
        for lookup in ('serverinfo', 'si'):
            card = help_cmd.command_embed(b.get_command(lookup))
            assert 'Property of Midtown Genre Owner: simisear' in card.description
            assert '843909457616044092' in card.description and '!serverinfo' in card.description
        guild = SimpleNamespace(name='Example', description='A community', icon=None, banner=None, owner_id=42,
                                created_at=datetime.now(timezone.utc), member_count=None, members=[],
                                text_channels=[], voice_channels=[], categories=[], roles=[], emojis=[],
                                emoji_limit=50, premium_subscription_count=0, premium_tier=0,
                                verification_level='none', vanity_url_code=None, id=123)
        ctx = SimpleNamespace(guild=guild, send=AsyncMock())
        await Utility.serverinfo.callback(Utility.__new__(Utility), ctx)
        embed = ctx.send.await_args.kwargs['embed']
        assert embed.description == 'A community'
        assert 'Property of Midtown Genre Owner: simisear' not in embed.description
        assert '843909457616044092' not in embed.description
        assert next(f.value for f in embed.fields if f.name == 'Owner') == '<@42>'
        assert 'Unavailable' in next(f.value for f in embed.fields if f.name == 'Members')
        assert ctx.send.await_args.kwargs['allowed_mentions'].everyone is False
        await b.close()
    asyncio.run(go())


def test_userinfo_uses_cached_profile_if_discord_lookup_fails():
    async def go():
        bot = SimpleNamespace(fetch_user=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), 'no access')))
        cog = Utility(bot)
        user = SimpleNamespace(id=5, color=None, display_avatar=SimpleNamespace(url='https://cdn.example/a.png'),
                               created_at=datetime.now(timezone.utc), public_flags=SimpleNamespace(all=lambda: []))
        ctx = SimpleNamespace(author=user, send=AsyncMock())
        await Utility.userinfo.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        assert ctx.send.await_args.kwargs['embed'].image.url is None
    asyncio.run(go())


def test_existing_prefixes_and_mentions_remain_available():
    async def go():
        db = SimpleNamespace(get_config=AsyncMock(return_value={'prefix': '!'}), get_selfprefix=AsyncMock(return_value='?'))
        bot = SimpleNamespace(db=db, default_prefix=',', user=SimpleNamespace(id=123))
        message = SimpleNamespace(guild=SimpleNamespace(id=5), author=SimpleNamespace(id=10))
        prefixes = await Veil._get_prefix(bot, bot, message)
        assert prefixes == ['<@123> ', '<@!123> ', '?', '!']
        message.guild = None
        db.get_selfprefix.return_value = None
        assert await Veil._get_prefix(bot, bot, message) == ['<@123> ', '<@!123> ', ',']
    asyncio.run(go())
