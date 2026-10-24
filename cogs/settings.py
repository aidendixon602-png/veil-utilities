"""Server settings: prefix, aliases, ignores, disabled commands, fake permissions, modlog."""
from __future__ import annotations

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, chunk_embeds, has_perms, is_server_owner

VALID_PERMS = sorted(p for p, _ in discord.Permissions.all())


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
        await ctx.neutral(f"Server prefix is `{s.get('prefix', self.bot.default_prefix)}`{extra}")

    @prefix.command(name="set")
    @has_perms(manage_guild=True)
    async def prefix_set(self, ctx: Context, prefix: str):
        """Change the server prefix"""
        if len(prefix) > 10:
            raise UserError("Prefix can be at most 10 characters")
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        s["prefix"] = prefix
        await self.bot.db.set_config(ctx.guild.id, "settings", s)
        await ctx.approve(f"Server prefix set to `{prefix}`")

    @commands.command()
    async def selfprefix(self, ctx: Context, prefix: str = None):
        """Set a personal prefix that works everywhere (`none` to remove)"""
        if prefix is None or prefix.lower() == "none":
            await self.bot.db.set_selfprefix(ctx.author.id, None)
            return await ctx.approve("Removed your self prefix")
        if len(prefix) > 10:
            raise UserError("Prefix can be at most 10 characters")
        await self.bot.db.set_selfprefix(ctx.author.id, prefix)
        await ctx.approve(f"Your self prefix is now `{prefix}`")

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
        if self.bot.get_command(shortcut):
            raise UserError(f"`{shortcut}` is already a command")
        if not self.bot.get_command(command.split()[0]):
            raise UserError(f"`{command.split()[0]}` isn't a command")
        a = await self.bot.db.get_config(ctx.guild.id, "aliases")
        if len(a) >= 100:
            raise UserError("You can have at most 100 aliases")
        a[shortcut] = command
        await self.bot.db.set_config(ctx.guild.id, "aliases", a)
        await ctx.approve(f"`{shortcut}` now runs `{command}`")

    @alias.command(name="remove", aliases=["delete", "del"])
    @has_perms(manage_guild=True)
    async def alias_remove(self, ctx: Context, shortcut: str):
        """Remove an alias"""
        a = await self.bot.db.get_config(ctx.guild.id, "aliases")
        if a.pop(shortcut.lower(), None) is None:
            raise UserError(f"No alias named `{shortcut}`")
        await self.bot.db.set_config(ctx.guild.id, "aliases", a)
        await ctx.approve(f"Removed alias `{shortcut}`")

    @alias.command(name="list")
    @has_perms(manage_guild=True)
    async def alias_list(self, ctx: Context):
        """List aliases"""
        a = await self.bot.db.get_config(ctx.guild.id, "aliases")
        await ctx.paginate(chunk_embeds("Aliases", [f"`{k}` → `{v}`" for k, v in a.items()]))

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
            msg = f"No longer ignoring {target.mention}"
        else:
            lst.append(target.id)
            msg = f"Now ignoring {target.mention}"
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
        await ctx.approve(f"`{cmd.qualified_name}` has been disabled.")

    @disablecommand.command(name="list")
    @has_perms(manage_guild=True)
    async def disable_list(self, ctx: Context):
        """List disabled commands"""
        s = await self.bot.db.get_config(ctx.guild.id, "settings")
        disabled = sorted(s.get("disabled", []))
        if not disabled:
            return await ctx.warn("No commands are disabled.")
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
        await ctx.approve(f"`{name}` has been re-enabled.")

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
        await ctx.approve(f"Mod logs will be sent to {channel.mention}" if channel else "Disabled mod logs")

    # ---------------------------------------------------------- fake permissions
    @commands.group(aliases=["fp", "fakeperms"], invoke_without_command=True)
    @is_server_owner()
    async def fakepermissions(self, ctx: Context):
        """Give roles permissions that only work through the bot — no dangerous Discord perms needed"""
        await ctx.send_help(ctx.command)

    @fakepermissions.command(name="grant", aliases=["add"])
    @is_server_owner()
    async def fp_grant(self, ctx: Context, role: discord.Role, *, permissions: str):
        """Grant fake permissions, e.g. `fp grant @Mods ban_members, kick_members`"""
        perms = [p.strip().lower().replace(" ", "_") for p in permissions.split(",")]
        bad = [p for p in perms if p not in VALID_PERMS]
        if bad:
            raise UserError(f"Invalid permission(s): {', '.join(bad)}")
        fp = await self.bot.db.get_config(ctx.guild.id, "fakeperms")
        lst = fp.setdefault(str(role.id), [])
        lst.extend(p for p in perms if p not in lst)
        await self.bot.db.set_config(ctx.guild.id, "fakeperms", fp)
        await ctx.approve(f"{role.mention} now has fake {', '.join(f'`{p}`' for p in perms)}")

    @fakepermissions.command(name="revoke", aliases=["remove"])
    @is_server_owner()
    async def fp_revoke(self, ctx: Context, role: discord.Role, *, permissions: str = "all"):
        """Revoke fake permissions from a role (`all` to clear)"""
        fp = await self.bot.db.get_config(ctx.guild.id, "fakeperms")
        if permissions == "all":
            fp.pop(str(role.id), None)
        else:
            perms = {p.strip().lower().replace(" ", "_") for p in permissions.split(",")}
            fp[str(role.id)] = [p for p in fp.get(str(role.id), []) if p not in perms]
        await self.bot.db.set_config(ctx.guild.id, "fakeperms", fp)
        await ctx.approve(f"Updated fake permissions for {role.mention}")

    @fakepermissions.command(name="list")
    @commands.guild_only()
    async def fp_list(self, ctx: Context):
        """List fake permissions"""
        fp = await self.bot.db.get_config(ctx.guild.id, "fakeperms")
        lines = [f"<@&{r}>: {', '.join(f'`{p}`' for p in ps)}" for r, ps in fp.items() if ps]
        await ctx.paginate(chunk_embeds("Fake permissions", lines))


async def setup(bot):
    await bot.add_cog(Settings(bot))
