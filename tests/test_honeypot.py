"""`,honeypot`: a trap channel catches the account, and never one of yours."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

import cogs.honeypot as H
from cogs.honeypot import Honeypot, canonical_action
from core.utils import Colors


def guild(gid=10, owner_id=100, channels=()):
    chans = {c.id: c for c in channels}
    roles = []
    me = SimpleNamespace(id=2, guild_permissions=SimpleNamespace(manage_channels=True, ban_members=True,
                                                                kick_members=True, moderate_members=True,
                                                                manage_roles=True, manage_messages=True),
                         top_role=SimpleNamespace(position=1), roles=[])
    g = SimpleNamespace(id=gid, owner_id=owner_id, me=me, channels=list(channels), roles=roles,
                        get_channel=lambda cid: chans.get(cid), get_role=lambda rid: None,
                        icon=None, name="Server")
    for c in channels:
        c.guild = g
    return g


def text_channel(cid=30, name="honeypot"):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id, ch.name, ch.mention = cid, name, f"<#{cid}>"
    ch.send, ch.delete = AsyncMock(), AsyncMock()
    return ch


def member(uid=7, *, admin=False, bot=False, roles=()):
    m = SimpleNamespace(id=uid, name="spammer", mention=f"<@{uid}>", bot=bot, roles=list(roles),
                        guild_permissions=SimpleNamespace(administrator=admin),
                        ban=AsyncMock(), kick=AsyncMock(), timeout=AsyncMock(), edit=AsyncMock())
    return m


def make_cog(cfg=None, permlevels=None):
    store = {"honeypot": dict(cfg or {}), "permlevels": dict(permlevels or {})}

    async def get_config(gid, module, default=None):
        return store.setdefault(module, {})

    async def set_config(gid, module, data):
        store[module] = data

    bot = SimpleNamespace(user=SimpleNamespace(id=2), db=SimpleNamespace(get_config=AsyncMock(side_effect=get_config),
                                                                        set_config=AsyncMock(side_effect=set_config)))
    cog = Honeypot(bot)
    return cog, store


def message(channel, author, content="FREE NITRO https://scam.example"):
    return SimpleNamespace(guild=channel.guild, channel=channel, author=author, content=content,
                           attachments=[], delete=AsyncMock())


def test_action_names_are_forgiving_and_unknown_ones_are_refused():
    assert canonical_action("Softban") == canonical_action("soft-ban") == canonical_action("sb") == "softban"
    assert canonical_action("MUTE") == "timeout" and canonical_action("jail") == "quarantine"
    assert canonical_action("nothing") == "log"
    assert canonical_action("explode") is None and canonical_action(None) is None


def test_setup_registers_the_channel_and_posts_the_warning():
    async def go():
        ch = text_channel()
        g = guild(channels=[ch])
        cog, store = make_cog()
        ctx = SimpleNamespace(guild=g, author=SimpleNamespace(id=100), approve=AsyncMock(), clean_prefix=",",
                              send_help=AsyncMock())
        await cog.honeypot_add.callback(cog, ctx, ch, "ban")
        assert store["honeypot"][str(ch.id)]["action"] == "ban"
        assert "honeypot" in ch.send.await_args.args[0].lower()   # the pinned-style warning
    asyncio.run(go())


def test_a_spammer_is_softbanned_by_default_and_everything_is_recorded():
    async def go():
        ch = text_channel()
        g = guild(channels=[ch])
        ch.guild = g
        g.ban = AsyncMock()
        spammer = member()
        g.unban = AsyncMock()
        cog, store = make_cog({str(ch.id): {"action": "softban"}})
        # member.ban is what a softban uses; it also needs guild.unban
        spammer.ban = AsyncMock()
        msg = message(ch, spammer)
        await cog.on_message(msg)
        spammer.ban.assert_awaited_once()
        assert spammer.ban.await_args.kwargs["delete_message_seconds"] == H.PURGE
        g.unban.assert_awaited_once()                               # soft, not a permanent ban
        msg.delete.assert_awaited_once()                            # the scam message goes
        assert store["honeypot"][str(ch.id)]["action"] == "softban"
        card = ch.send.await_args.kwargs["embed"]
        assert "Honeypot triggered" in card.title and card.footer.text == "User ID: 7"
    asyncio.run(go())


def test_staff_are_never_punished():
    async def go():
        ch = text_channel()
        guild(channels=[ch], owner_id=100)
        cog, _ = make_cog({str(ch.id): {"action": "ban"}})
        for who in (member(100),                        # the server owner
                    member(5, admin=True),              # Discord Administrator
                    member(6, bot=True)):               # another bot, which posts by design
            await cog.on_message(message(ch, who))
            who.ban.assert_not_awaited()
        # a moderator with a Veil permission level: rank comes from the permission config
        mod = member(9)
        cog2, _ = make_cog({str(ch.id): {"action": "ban"}})
        mod.roles = [SimpleNamespace(id=55)]
        cog2.bot.db.get_config = AsyncMock(side_effect=lambda gid, module, default=None:
                                           {"55": ["moderator"]} if module == "permlevels" else {})
        await cog2.on_message(message(ch, mod))
        mod.ban.assert_not_awaited()
    asyncio.run(go())


def test_a_plain_member_with_no_level_is_caught_even_if_it_looks_risky():
    async def go():
        ch = text_channel()
        guild(channels=[ch])
        cog, _ = make_cog({str(ch.id): {"action": "kick"}})
        person = member(42)
        await cog.on_message(message(ch, person))
        person.kick.assert_awaited_once()
    asyncio.run(go())


def test_a_channel_that_is_not_a_honeypot_is_ignored():
    async def go():
        ch = text_channel(30, "general")
        cog, store = make_cog({})
        person = member()
        await cog.on_message(message(ch, person))
        person.ban.assert_not_awaited() and person.kick.assert_not_awaited()
        assert store["honeypot"] == {}
    asyncio.run(go())


def test_a_failed_punishment_is_still_logged_with_the_reason():
    async def go():
        ch = text_channel()
        guild(channels=[ch])
        cog, _ = make_cog({str(ch.id): {"action": "ban"}})
        person = member()

        async def refuse(*a, **k):
            raise discord.Forbidden(SimpleNamespace(status=403, reason=""), "missing permissions")
        person.ban = refuse
        await cog.on_message(message(ch, person))
        card = ch.send.await_args.kwargs["embed"]
        assert "failed" in card.description.lower() and "403" in card.description
        assert card.color.value == Colors.caution
    asyncio.run(go())


def test_removing_and_relisting_honeypots():
    async def go():
        a, b = text_channel(30, "honeypot"), text_channel(31, "vault")
        g = guild(channels=[a, b])
        cog, store = make_cog({str(a.id): {"action": "ban"}, str(b.id): {"action": "log"}})
        ctx = SimpleNamespace(guild=g, clean_prefix=",", approve=AsyncMock(), neutral=AsyncMock(), send=AsyncMock(),
                              author=SimpleNamespace(id=100))
        await cog.honeypot_remove.callback(cog, ctx, a)
        assert str(a.id) not in store["honeypot"] and str(b.id) in store["honeypot"]
        await cog.honeypot_list.callback(cog, ctx)
        description = ctx.send.await_args.kwargs["embed"].description
        assert "<#31>" in description and "log" in description
    asyncio.run(go())
