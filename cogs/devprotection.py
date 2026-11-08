"""Best-effort protection when someone attempts to ban a bot developer.

Actual Discord bans are attributed only from their exact audit-log event. A member-ban
fallback restores the developer without guessing who was responsible if audit access is missing.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import discord
from discord.ext import commands

from core import perms

log = logging.getLogger("veil.devprotection")
AUDIT_GRACE_SECONDS = 2
AUDIT_MAX_AGE = 120


class DeveloperProtection(commands.Cog):
    emoji = "🛡️"

    def __init__(self, bot):
        self.bot = bot
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._seen: dict[tuple[int, int], float] = {}
        self._audit_targets: dict[tuple[int, int], float] = {}

    async def _ban_actor(self, guild, actor_id):
        if not actor_id:
            return "Not attempted: the actor could not be verified."
        if actor_id in {guild.owner_id, getattr(self.bot.user, "id", None)} or perms.is_bot_developer(actor_id, self.bot) \
                or perms.is_whitelisted_person(actor_id):
            return "Not attempted: the actor is protected or cannot be banned by the bot."
        try:
            await guild.ban(discord.Object(id=actor_id), reason="Developer protection: attempted developer ban",
                            delete_message_seconds=0)
        except discord.HTTPException as error:
            return f"Failed (Discord {error.status}); check permissions and role hierarchy."
        except (OSError, asyncio.TimeoutError):
            return "Failed: could not reach Discord."
        return "Banned."

    @staticmethod
    async def _unban_developer(guild, developer_id):
        try:
            await guild.unban(discord.Object(id=developer_id), reason="Developer protection: reverse developer ban")
        except discord.NotFound as error:
            if error.code == 10026:  # Unknown Ban, not an ambiguous 404 for another resource
                return "Not banned or already unbanned.", False
            return f"Failed (Discord 404, code {error.code}); check manually.", False
        except discord.HTTPException as error:
            return f"Failed (Discord {error.status}); check permissions and unban manually.", False
        except (OSError, asyncio.TimeoutError):
            return "Failed: could not reach Discord; check manually.", False
        return "Unbanned; they must rejoin to return to the server.", True

    @staticmethod
    async def _invite(guild):
        # Do not create an invite to a private channel or distribute it to server logs.
        for channel in getattr(guild, "text_channels", []):
            try:
                if not channel.permissions_for(guild.default_role).view_channel:
                    continue
                if not channel.permissions_for(guild.me).create_instant_invite:
                    continue
                invite = await channel.create_invite(max_age=3600, max_uses=1, unique=True,
                                                     reason="Developer protection: one-use rejoin invite")
                return str(invite.url)
            except (discord.HTTPException, OSError, asyncio.TimeoutError, AttributeError):
                continue
        return None

    async def _log_channel(self, guild):
        antinuke = self.bot.get_cog("Antinuke")
        if antinuke:
            try:
                return await antinuke._log_channel(guild, await antinuke.cfg(guild.id))
            except Exception:
                log.exception("Could not find developer-protection log channel for guild %s", guild.id)
        return None

    async def handle(self, guild, developer_id: int, actor_id: int | None, *, source: str,
                     actual_ban: bool, fallback: bool = False):
        """Process one known attempt; always try unbanning even if actor punishment fails."""
        async with self._locks.setdefault((guild.id, developer_id), asyncio.Lock()):
            try:
                ban_result = await self._ban_actor(guild, actor_id) if actor_id else "Not attempted: actor unknown."
            except Exception:
                log.exception("Unexpected actor-ban error in guild %s", guild.id)
                ban_result = "Failed unexpectedly; check manually."
            try:
                unban_result, unbanned = await self._unban_developer(guild, developer_id)
            except Exception:
                log.exception("Unexpected developer-unban error in guild %s", guild.id)
                unban_result, unbanned = "Failed unexpectedly; unban manually.", False
            if fallback and unban_result == "Not banned or already unbanned.":
                return  # audit listener already handled it; don't send a second notice

            embed = discord.Embed(title="🛡️ Developer protection", color=discord.Color.red(),
                                  description="A ban targeting a bot developer was " +
                                  ("detected." if actual_ban else "blocked before it reached Discord."))
            embed.add_field(name="Server", value=f"{discord.utils.escape_markdown(guild.name)} (`{guild.id}`)", inline=False)
            embed.add_field(name="Developer", value=f"<@{developer_id}> (`{developer_id}`)", inline=True)
            embed.add_field(name="Actor", value=f"<@{actor_id}> (`{actor_id}`)" if actor_id else "Unknown", inline=True)
            embed.add_field(name="Actor ban", value=ban_result, inline=False)
            embed.add_field(name="Developer ban", value=unban_result, inline=False)
            embed.set_footer(text=f"Veil security • {source}")

            jump = None
            channel = await self._log_channel(guild)
            if channel:
                try:
                    message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    jump = message.jump_url
                except (discord.HTTPException, OSError, asyncio.TimeoutError):
                    log.warning("Could not post developer-protection log in guild %s", guild.id)
            invite = await self._invite(guild) if unbanned else None
            view = discord.ui.View()
            if jump:
                view.add_item(discord.ui.Button(label="View security alert", url=jump))
            if invite:
                view.add_item(discord.ui.Button(label="Rejoin server", url=invite))
                embed.add_field(name="Rejoin", value="One-use invite expires in one hour.", inline=False)
            elif unbanned:
                embed.add_field(name="Rejoin", value="No invite could be created; ask the server owner for one.", inline=False)
            try:
                developer = self.bot.get_user(developer_id) or await self.bot.fetch_user(developer_id)
                await developer.send(embed=embed, view=view if view.children else None,
                                     allowed_mentions=discord.AllowedMentions.none())
            except (discord.HTTPException, OSError, asyncio.TimeoutError):
                log.warning("Could not DM protected developer %s about guild %s", developer_id, guild.id)

            manager = getattr(self.bot, "incident_manager", None)
            if manager:
                try:
                    await manager.record(kind="developer_ban", severity="critical", guild_id=guild.id,
                                         actor_id=actor_id, summary="Ban targeting a bot developer",
                                         action=f"Actor: {ban_result} Developer: {unban_result}",
                                         details={"Developer ID": developer_id, "Source": source,
                                                  "Alert": jump or "Unavailable"}, notify=False)
                except Exception:
                    log.exception("Could not persist developer-protection incident in guild %s", guild.id)

    async def blocked_command(self, ctx) -> bool:
        """Recognize explicit developer targets before permission checks reject a ban command."""
        if not ctx.guild or not ctx.command or ctx.command.qualified_name not in {"ban", "softban"}:
            return False
        position, previous = ctx.view.index, ctx.view.previous
        try:
            ctx.view.skip_ws()
            word = ctx.view.get_quoted_word()
        except commands.CommandError:
            return False
        finally:
            # A non-developer target must still be parsed normally by the real command.
            ctx.view.index, ctx.view.previous = position, previous
        if not word:
            return False
        match = re.fullmatch(r"(?:<@!?(\d{15,20})>|(\d{15,20}))", word)
        if match:
            developer_id = int(match.group(1) or match.group(2))
        else:
            member = ctx.guild.get_member_named(word)
            developer_id = getattr(member, "id", None)
        if not developer_id or not perms.is_bot_developer(developer_id, self.bot):
            return False
        await self.handle(ctx.guild, developer_id, ctx.author.id, source="Veil command", actual_ban=False)
        await ctx.send("🛡️ Developer ban blocked. A security notice was attempted.",
                       allowed_mentions=discord.AllowedMentions.none())
        return True

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        if entry.action != discord.AuditLogAction.ban:
            return
        developer_id = getattr(getattr(entry, "target", None), "id", None)
        if not perms.is_bot_developer(developer_id, self.bot):
            return
        created = getattr(entry, "created_at", None)
        if created is None or not -10 <= (discord.utils.utcnow() - created).total_seconds() <= AUDIT_MAX_AGE:
            return  # stale/unverifiable events must not trigger an actor ban
        now = time.monotonic()
        self._seen = {key: t for key, t in self._seen.items() if now - t < 300}
        key = (entry.guild.id, entry.id)
        if key in self._seen:
            return
        self._seen[key] = now
        self._audit_targets = {key: t for key, t in self._audit_targets.items() if now - t < 300}
        self._audit_targets[(entry.guild.id, developer_id)] = now
        await self.handle(entry.guild, developer_id, getattr(entry, "user_id", None), source="Discord audit log",
                          actual_ban=True)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        if not perms.is_bot_developer(user.id, self.bot):
            return
        # The exact audit event is preferred for actor attribution. If it never arrives, restore without guessing.
        started = time.monotonic()
        await asyncio.sleep(AUDIT_GRACE_SECONDS)
        observed = self._audit_targets.get((guild.id, user.id), 0)
        await self.handle(guild, user.id, None, source="Member-ban fallback; actor unknown", actual_ban=True,
                          fallback=observed >= started - 3)


async def setup(bot):
    await bot.add_cog(DeveloperProtection(bot))
