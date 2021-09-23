import discord
import typing
import emoji
from DuckBot.helpers import paginator
from DuckBot.helpers.time_inputs import ShortTime
from discord.ext import commands
from DuckBot.__main__ import DuckBot, CustomContext
def setup(bot):
    bot.add_cog(Test(bot))


class Test(commands.Cog):
    """
    🧪 Test commands. 💀 These may not work, or not be what you think they will.
    Remember that these commands are all a work in progress, and they may or may not ever be released
    """
    def __init__(self, bot):
        self.bot: DuckBot = bot

    @commands.command()
    async def test(self, ctx: CustomContext):
        await ctx.send(ctx.tick(await self.bot.is_owner(ctx.guild.owner), text="Is bot owner"))
