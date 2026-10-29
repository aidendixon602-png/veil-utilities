"""Server settings: prefix, aliases, ignores, disabled commands, fake permissions, modlog."""
from __future__ import annotations

import re

import discord
from discord.ext import commands

from core.utils import (DANGEROUS_PERMS, Colors, Context, OwnedView, UserError, chunk_embeds, has_perms,
                        human_join, is_server_owner)

VALID_PERMS = sorted(p for p, _ in discord.Permissions.all())
# fake perms that would be a problem on @everyone (moderation powers for every member)
EVERYONE_BLOCKED = {*DANGEROUS_PERMS, "moderate_members", "manage_messages", "manage_nicknames", "move_members",
                    "mute_members", "deafen_members", "manage_threads", "manage_events"}


# the permissions offered in the `,fp add @role` picker (Discord allows 25 options): the ones bot commands check
PICKER_PERMS = ["ban_members", "kick_members", "moderate_members", "manage_messages", "manage_nicknames",
                "manage_roles", "manage_channels", "manage_guild", "manage_webhooks", "manage_expressions",
                "manage_events", "manage_threads", "mention_everyone", "view_audit_log", "move_members",
                "mute_members", "deafen_members", "administrator"]


def perm_label(perm: str) -> str:
    return perm.replace("_", " ")


def parse_perms(text: str) -> tuple[list[str], list[str]]:
    """`ban members, kick_members manage roles` -> (["ban_members", "kick_members", "manage_roles"], unknown words).
    Commas, spaces and underscores all work; the longest known permission name wins at each step."""
    words = [w for w in re.split(r"[\s,]+", text.strip().lower()) if w]
    found, unknown, i = [], [], 0
    while i < len(words):
        for size in (4, 3, 2, 1):
            joined = "_".join(words[i:i + size]).replace("-", "_")
            if joined in VALID_PERMS:
                if joined not in found:
                    found.append(joined)
                i += size
                break
        else:
            unknown.append(words[i])
            i += 1
    return found, unknown


class FakePermPicker(OwnedView):
    """`,fp add @role` with no permissions: pick them from a list."""

    def __init__(self, cog: "Settings", ctx: Context, role: discord.Role):
        super().__init__(ctx.author.id, timeout=120, allow_developers=False)
        self.cog, self.ctx, self.role = cog, ctx, role
        options = [discord.SelectOption(label=perm_label(p), value=p) for p in PICKER_PERMS]
        select = discord.ui.Select(placeholder="Choose permissions to give…", options=options,
                                   min_values=1, max_values=len(options))
        select.callback = self.picked
        self.add_item(select)

    async def picked(self, it: discord.Interaction):
        perms = list(self.children[0].values)
        try:
            text = await self.cog.grant(self.ctx.guild, self.role, perms)
        except UserError as e:
            return await it.response.send_message(f"⚠️ {e}", ephemeral=True)
        self.stop()
        await it.response.edit_message(embed=discord.Embed(color=Colors.approve,
                                                           description=self.ctx._line("✅", text)), view=None)


class Settings(commands.Cog):
    """Configure how the bot behaves in your server"""
    emoji = "⚙️"

    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------- prefix
    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def prefix(self, ctx: Context):
        """View the server prefix"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        own = await self.bot.db.get_selfprefix(ctx.author.id)
        extra = f" • your self prefix is `{own}`" if own else ""
        await ctx.neutral(f"the server prefix is `{s.get('prefix', self.bot.default_prefix)}`{extra}")

    @prefix.command(name="set")
    @has_perms(manage_guild=True)
    async def prefix_set(self, ctx: Context, prefix: str):
        """Change the server prefix"""
        if len(prefix) > 10:
            raise UserError("Prefix can be at most 10 characters")
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        s["prefix"] = prefix
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(f"set the server prefix to `{prefix}`")

    @commands.command()
    async def selfprefix(self, ctx: Context, prefix: str = None):
        """Set a personal prefix that works everywhere (`none` to remove)"""
        if prefix is None or prefix.lower() == "none":
            await self.bot.db.set_selfprefix(ctx.author.id, None)
            return await ctx.approve("removed your self prefix")
        if len(prefix) > 10:
            raise UserError("Prefix can be at most 10 characters")
        await self.bot.db.set_selfprefix(ctx.author.id, prefix)
        await ctx.approve(f"set your self prefix to `{prefix}`")

    # ---------------------------------------------------------- aliases
    @commands.group(invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def alias(self, ctx: Context):
        """Create shortcuts for commands"""
        await ctx.send_help(ctx.command)

    @alias.command(name="add")
    @has_perms(manage_guild=True)
    async def alias_add(self, ctx: Context, shortcut: str, *, command: str):
        """Add an alias, e.g. `alias add b ban` or `alias add hi say hello`"""
        shortcut = shortcut.lower()
        command = command.strip()
        if not re.fullmatch(r"[\w\-!?.]{1,32}", shortcut):
            raise UserError("Shortcuts must be one word (letters, numbers, `-_!?.`), up to 32 characters")
        if len(command) > 500:
            raise UserError("That command is too long (500 characters max)")
        if self.bot.get_command(shortcut):
            raise UserError(f"`{shortcut}` is already a command")
        # resolve as deep as possible so `alias add w warn list` is checked down to the subcommand
        words = command.split()
        target = self.bot.get_command(words[0])
        if target is None:
            raise UserError(f"`{words[0]}` isn't a command")
        for word in words[1:]:
            if not isinstance(target, commands.Group):
                break
            sub = target.get_command(word)
            if sub is None:
                break
            target = sub
        if target.qualified_name.split()[0] == "alias":
            raise UserError("An alias can't point at the alias command")
        if target.hidden or getattr(target.cog, "qualified_name", "") in ("Owner", "DevTools"):
            raise UserError("You can't alias developer commands")
        a = await self.bot.db.get_config(ctx.guild.id, "aliases")
        if len(a) >= 100:
            raise UserError("You can have at most 100 aliases")
        a[shortcut] = command
        await self.bot.db.set_config(ctx.guild.id, "aliases", a)
        await ctx.approve(f"added alias `{shortcut}` → `{command}`")

    @alias.command(name="remove", aliases=["delete", "del"])
    @has_perms(manage_guild=True)
    async def alias_remove(self, ctx: Context, shortcut: str):
        """Remove an alias"""
        a = await self.bot.db.get_config(ctx.guild.id, "aliases")
        if a.pop(shortcut.lower(), None) is None:
            raise UserError(f"No alias named `{shortcut}`")
        await self.bot.db.set_config(ctx.guild.id, "aliases", a)
        await ctx.approve(f"removed alias `{shortcut}`")

    @alias.command(name="list")
    @has_perms(manage_guild=True)
    async def alias_list(self, ctx: Context):
        """List aliases"""
        a = await self.bot.db.get_config(ctx.guild.id, "aliases")
        if not a:
            return await ctx.warn("no aliases are set")
        await ctx.paginate(chunk_embeds(f"Aliases ({len(a)})", [f"`{k}` → `{v}`" for k, v in a.items()]))

    # ---------------------------------------------------------- ignore
    @commands.group(invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def ignore(self, ctx: Context, target: discord.TextChannel | discord.Member):
        """Toggle whether the bot ignores commands from a channel or member"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        key = "ignored_channels" if isinstance(target, discord.TextChannel) else "ignored_users"
        lst = s.setdefault(key, [])
        if target.id in lst:
            lst.remove(target.id)
            msg = f"no longer ignoring {target.mention}"
        else:
            lst.append(target.id)
            msg = f"now ignoring {target.mention}"
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(msg)

    @ignore.command(name="list")
    @commands.guild_only()
    async def ignore_list(self, ctx: Context):
        """List ignored channels and users"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        lines = [f"<#{c}>" for c in s.get("ignored_channels", [])] + [f"<@{u}>" for u in s.get("ignored_users", [])]
        await ctx.paginate(chunk_embeds("Ignored", lines))

    # ---------------------------------------------------------- disable
    @commands.group(aliases=["disablecmd", "dcmd"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def disablecommand(self, ctx: Context, *, command: str):
        """Disable a command for non-admins (`dcmd list` to see what's disabled)"""
        cmd = self.bot.get_command(command)
        if not cmd or cmd.cog_name in ("Settings", "Owner", "Authorization"):
            raise UserError("That command can't be disabled")
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        s.setdefault("disabled", [])
        if cmd.qualified_name in s["disabled"]:
            raise UserError(f"`{cmd.qualified_name}` is already disabled")
        s["disabled"].append(cmd.qualified_name)
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(f"disabled `{cmd.qualified_name}`")

    @disablecommand.command(name="list")
    @has_perms(manage_guild=True)
    async def disable_list(self, ctx: Context):
        """List disabled commands"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        disabled = sorted(s.get("disabled", []))
        if not disabled:
            return await ctx.warn("no commands are disabled")
        embed = discord.Embed(title="Disabled Commands", color=Colors.main,
                              description="\n".join(f"`{c}`" for c in disabled))
        embed.set_footer(text=f"{len(disabled)} disabled")
        await ctx.send(embed=embed)

    @commands.command(aliases=["enablecmd", "ecmd"])
    @has_perms(manage_guild=True)
    async def enablecommand(self, ctx: Context, *, command: str):
        """Re-enable a disabled command"""
        cmd = self.bot.get_command(command)
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        name = cmd.qualified_name if cmd else command
        if name not in s.get("disabled", []):
            raise UserError(f"`{name}` isn't disabled")
        s["disabled"].remove(name)
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(f"enabled `{name}`")

    # ---------------------------------------------------------- staff-only / bot channels
    @commands.command(aliases=["botlock", "lockbot"])
    @is_server_owner()
    @commands.guild_only()
    async def staffonly(self, ctx: Context, state: bool | None = None):
        """Toggle staff-only mode so normal members cannot run bot commands"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        current = bool(s.get("staff_only"))
        new_state = (not current) if state is None else state
        s["staff_only"] = new_state
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        note = ("\n-# only staff and members with a permission rank can run commands" if new_state
                else "\n-# everyone can run public commands")
        await ctx.approve(f"turned staff-only mode **{'on' if new_state else 'off'}**{note}")

    @commands.group(aliases=["cmdchannels", "botchannel"], invoke_without_command=True)
    @has_perms(manage_guild=True)
    async def botchannels(self, ctx: Context, channel: discord.TextChannel = None):
        """Restrict normal commands to specific channels (`botchannels <#channel>` to toggle)"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        channels = s.setdefault("bot_channels", [])
        if channel is None:
            if not channels:
                return await ctx.neutral("commands are allowed in every channel")
            lines = [f"<#{c}>" for c in channels]
            return await ctx.paginate(chunk_embeds("Allowed Bot Channels", lines))
        if channel.id in channels:
            channels.remove(channel.id)
            msg = f"removed {channel.mention} from the allowed bot channels"
        else:
            channels.append(channel.id)
            msg = f"added {channel.mention} to the allowed bot channels"
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(msg)

    # ---------------------------------------------------------- modlog
    @commands.command()
    @has_perms(manage_guild=True)
    async def modlog(self, ctx: Context, channel: discord.TextChannel = None):
        """Set (or clear) the channel where moderation cases are posted"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        if channel:
            s["modlog"] = channel.id
        else:
            s.pop("modlog", None)
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(f"mod logs will go to {channel.mention}" if channel else "turned off mod logs")

    # ---------------------------------------------------------- fake permissions (greed / bleed style)
    async def grant(self, guild: discord.Guild, role: discord.Role, perms: list[str]) -> str:
        """Give a role fake permissions; returns the success line. Shared by `fp add` and its picker."""
        dangerous = [p for p in perms if p in EVERYONE_BLOCKED]
        if role.is_default() and dangerous:
            raise UserError(f"You can't give @everyone {', '.join(f'`{perm_label(p)}`' for p in dangerous)}: that "
                            "would let every member use those commands. Make a staff role instead")
        fp = await self.bot.db.get_config(guild.id, "fakeperms")
        lst = fp.setdefault(str(role.id), [])
        lst.extend(p for p in perms if p not in lst)
        await self.bot.db.set_config(guild.id, "fakeperms", fp)
        note = ("\n-# ⚠️ `administrator` lets this role use every bot command, including antinuke-sensitive ones"
                if "administrator" in perms else "")
        return (f"gave {role.mention} {human_join([f'`{perm_label(p)}`' for p in perms])} "
                f"through the bot{note}")

    @commands.group(aliases=["fp", "fakeperms", "fakeperm"], invoke_without_command=True)
    @is_server_owner()
    async def fakepermissions(self, ctx: Context):
        """Give roles permissions that only work through the bot, with no dangerous Discord permissions needed"""
        await ctx.send_help(ctx.command)

    @fakepermissions.command(name="add", aliases=["grant", "give"], usage="<role> [permissions]")
    @is_server_owner()
    async def fp_add(self, ctx: Context, role: discord.Role, *, permissions: str = None):
        """Give a role permissions for the bot: `fp add @Mods ban members, kick members` (leave them out to pick)"""
        if not permissions:
            view = FakePermPicker(self, ctx, role)
            return await ctx.neutral(f"pick the permissions to give {role.mention} through the bot", view=view)
        perms, unknown = parse_perms(permissions)
        if unknown:
            raise UserError(f"`{' '.join(unknown)}` isn't a permission. Try names like `ban members` or `manage roles`")
        if not perms:
            raise UserError("Name at least one permission, like `ban members`")
        await ctx.approve(await self.grant(ctx.guild, role, perms))

    @fakepermissions.command(name="remove", aliases=["revoke", "take", "rm"], usage="<role> [permissions|all]")
    @is_server_owner()
    async def fp_remove(self, ctx: Context, role: discord.Role, *, permissions: str = "all"):
        """Take fake permissions back from a role (`all`, or leave them out, to clear it)"""
        fp = await self.bot.db.get_config(ctx.guild.id, "fakeperms")
        had = fp.get(str(role.id), [])
        if not had:
            raise UserError(f"{role.mention} has no fake permissions")
        if permissions.strip().lower() == "all":
            fp.pop(str(role.id), None)
            await self.bot.db.set_config(ctx.guild.id, "fakeperms", fp)
            return await ctx.approve(f"removed every fake permission from {role.mention}")
        perms, unknown = parse_perms(permissions)
        if unknown:
            raise UserError(f"`{' '.join(unknown)}` isn't a permission")
        left = [p for p in had if p not in perms]
        if left:
            fp[str(role.id)] = left
        else:
            fp.pop(str(role.id), None)
        await self.bot.db.set_config(ctx.guild.id, "fakeperms", fp)
        await ctx.approve(f"took {human_join([f'`{perm_label(p)}`' for p in perms])} from {role.mention}")

    @fakepermissions.command(name="list", aliases=["show", "view"])
    @commands.guild_only()
    async def fp_list(self, ctx: Context):
        """See which roles have fake permissions"""
        fp = await self.bot.db.get_config(ctx.guild.id, "fakeperms")
        lines = [f"<@&{r}>: {', '.join(f'`{perm_label(p)}`' for p in ps)}" for r, ps in fp.items() if ps]
        if not lines:
            return await ctx.warn(f"no roles have fake permissions yet, give some with `{ctx.clean_prefix}fp add`")
        await ctx.paginate(chunk_embeds("Fake permissions", lines, numbered=False))

    @fakepermissions.command(name="reset", aliases=["clear"])
    @is_server_owner()
    async def fp_reset(self, ctx: Context):
        """Remove every fake permission in the server"""
        fp = await self.bot.db.get_config(ctx.guild.id, "fakeperms")
        if not any(fp.values()):
            return await ctx.warn("no roles have fake permissions")
        await self.bot.db.set_config(ctx.guild.id, "fakeperms", {})
        await ctx.approve(f"removed the fake permissions from **{sum(1 for v in fp.values() if v)}** role(s)")


async def setup(bot):
    await bot.add_cog(Settings(bot))
