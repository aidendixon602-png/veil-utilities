from unittest.mock import AsyncMock, MagicMock

from cogs.integrations import Integrations
from cogs.fun import Fun


def test_define_formatting():
    bot = MagicMock()
    cog = Integrations(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.clean_prefix = ","

    sample_api_data = [
        {
            "word": "serendipity",
            "phonetic": "/ˌsɛrənˈdɪpɪti/",
            "meanings": [
                {
                    "partOfSpeech": "noun",
                    "definitions": [
                        {
                            "definition": "The faculty of making happy and unexpected discoveries by accident.",
                            "example": "A fortunate stroke of serendipity."
                        }
                    ],
                    "synonyms": ["chance", "fluke", "fortune"]
                }
            ]
        }
    ]
    cog.get_json = AsyncMock(return_value=sample_api_data)

    import asyncio
    asyncio.run(cog.define.callback(cog, ctx, word="serendipity"))
    ctx.send.assert_called_once()
    embed = ctx.send.call_args[1]["embed"]
    assert "Serendipity" in embed.title
    assert "Noun" in embed.fields[0].name
    assert "happy and unexpected discoveries" in embed.fields[0].value


def test_currency_convert():
    bot = MagicMock()
    cog = Integrations(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()

    sample_data = {
        "amount": 100.0,
        "base": "USD",
        "date": "2026-10-25",
        "rates": {"EUR": 89.5}
    }
    cog.get_json = AsyncMock(return_value=sample_data)

    import asyncio
    asyncio.run(cog.convert.callback(cog, ctx, amount=100.0, from_curr="USD", to_curr="EUR"))
    ctx.send.assert_called_once()
    embed = ctx.send.call_args[1]["embed"]
    assert "USD ➔ EUR" in embed.title
    assert "89.50 EUR" in embed.fields[1].value


def test_ipinfo_lookup():
    bot = MagicMock()
    cog = Integrations(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()

    sample_data = {
        "status": "success",
        "query": "1.1.1.1",
        "country": "Australia",
        "countryCode": "AU",
        "regionName": "Queensland",
        "city": "South Brisbane",
        "zip": "4101",
        "lat": -27.4766,
        "lon": 153.0166,
        "timezone": "Australia/Brisbane",
        "isp": "Cloudflare, Inc",
        "org": "APNIC and Cloudflare DNS Resolver Project",
        "as": "AS13335 Cloudflare, Inc."
    }
    cog.get_json = AsyncMock(return_value=sample_data)

    import asyncio
    asyncio.run(cog.ipinfo.callback(cog, ctx, query="1.1.1.1"))
    ctx.send.assert_called_once()
    embed = ctx.send.call_args[1]["embed"]
    assert "1.1.1.1" in embed.title
    assert "Cloudflare" in embed.fields[1].value


def test_qr_code_generation():
    bot = MagicMock()
    cog = Integrations(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()

    import asyncio
    asyncio.run(cog.qr.callback(cog, ctx, text_or_url="https://discord.gg/veil"))
    ctx.send.assert_called_once()
    embed = ctx.send.call_args[1]["embed"]
    assert embed.image.url.startswith("https://api.qrserver.com/v1/create-qr-code/")
    assert "https://discord.gg/veil" in embed.description


def test_pypi_lookup():
    bot = MagicMock()
    cog = Integrations(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()

    sample_data = {
        "info": {
            "name": "discord.py",
            "version": "2.7.1",
            "summary": "A Python wrapper for the Discord API",
            "author": "Rapptz",
            "license": "MIT",
            "requires_python": ">=3.8"
        }
    }
    cog.get_json = AsyncMock(return_value=sample_data)

    import asyncio
    asyncio.run(cog.pypi.callback(cog, ctx, package="discord.py"))
    ctx.send.assert_called_once()
    embed = ctx.send.call_args[1]["embed"]
    assert "discord.py v2.7.1" in embed.title
    assert "pip install discord.py" in embed.fields[0].value


def test_npm_lookup():
    bot = MagicMock()
    cog = Integrations(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()

    sample_data = {
        "name": "express",
        "dist-tags": {"latest": "4.19.2"},
        "description": "Fast, unopinionated, minimalist web framework",
        "author": {"name": "TJ Holowaychuk"},
        "license": "MIT"
    }
    cog.get_json = AsyncMock(return_value=sample_data)

    import asyncio
    asyncio.run(cog.npm.callback(cog, ctx, package="express"))
    ctx.send.assert_called_once()
    embed = ctx.send.call_args[1]["embed"]
    assert "express v4.19.2" in embed.title
    assert "npm install express" in embed.fields[0].value


def test_fun_apis():
    bot = MagicMock()
    cog = Fun(bot)
    ctx = MagicMock()
    ctx.send = AsyncMock()

    # Test advice
    session_mock = MagicMock()
    resp_mock = AsyncMock()
    resp_mock.status = 200
    resp_mock.json = AsyncMock(return_value={"slip": {"advice": "Keep moving forward."}})
    session_mock.get.return_value.__aenter__.return_value = resp_mock
    bot.session = session_mock

    import asyncio
    asyncio.run(cog.advice.callback(cog, ctx))
    ctx.send.assert_called()
    embed = ctx.send.call_args[1]["embed"]
    assert "Keep moving forward" in embed.description
