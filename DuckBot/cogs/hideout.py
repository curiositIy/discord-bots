import functools
import io
import json
import os
import re
import urllib
import zlib
from inspect import Parameter

import discord
import typing
import yarl
from discord.ext import commands

import DuckBot.__main__
from DuckBot import errors
from DuckBot.__main__ import DuckBot
from DuckBot.cogs.management import get_webhook
from DuckBot.helpers import constants
from DuckBot.helpers.context import CustomContext


def hideout_only():
    def predicate(ctx: CustomContext):
        if ctx.guild and ctx.guild.id == 774561547930304536:
            return True
        raise errors.NoHideout

    return commands.check(predicate)


url_regex = re.compile(r"^http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|)+$")


def setup(bot):
    bot.add_cog(Hideout(bot))


def finder(text, collection, *, key=None, lazy=True):
    suggestions = []
    text = str(text)
    pat = '.*?'.join(map(re.escape, text))
    regex = re.compile(pat, flags=re.IGNORECASE)
    for item in collection:
        to_search = key(item) if key else item
        r = regex.search(to_search)
        if r:
            suggestions.append((len(r.group()), r.start(), item))

    def sort_key(tup):
        if key:
            return tup[0], tup[1], key(tup[2])
        return tup

    if lazy:
        return (z for _, _, z in sorted(suggestions, key=sort_key))
    else:
        return [z for _, _, z in sorted(suggestions, key=sort_key)]


class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFSIZE = 16 * 1024

    def __init__(self, buffer):
        self.stream = io.BytesIO(buffer)

    def readline(self):
        return self.stream.readline().decode('utf-8')

    def skipline(self):
        self.stream.readline()

    def read_compressed_chunks(self):
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self):
        buf = b''
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode('utf-8')
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')


def whitelist():
    async def predicate(ctx: CustomContext):
        if not ctx.command.qualified_name.startswith('inviter'):
            return True
        if await ctx.bot.db.fetchval('SELECT uid FROM inv_whitelist WHERE uid = $1', ctx.author.id):
            return True
        else:
            await ctx.send('You are not whitelisted to run inviter commands!')
            raise errors.NoHideout

    return commands.check(predicate)


class Hideout(commands.Cog, name='DuckBot Hideout'):
    """
    🧪 Test commands. 💀 These may not work, or not be what you think they will.
    Remember that these commands are all a work in progress, and they may or may not ever be released
    """

    def __init__(self, bot):
        self.bot: DuckBot = bot

    async def build_rtfm_lookup_table(self, page_types):
        cache = {}
        for key, page in page_types.items():
            sub = cache[key] = {}
            async with self.bot.session.get(page + '/objects.inv') as resp:
                if resp.status != 200:
                    channel = self.bot.get_channel(880181130408636456)
                    await channel.send(f'```py\nCould not create RTFM lookup table for {page}\n```')
                    continue

                stream = SphinxObjectFileReader(await resp.read())
                cache[key] = self.parse_object_inv(stream, page)

        self._rtfm_cache = cache

    def parse_object_inv(self, stream, url):
        # key: URL
        # n.b.: key doesn't have `discord` or `discord.ext.commands` namespaces
        result = {}

        # first line is version info
        inv_version = stream.readline().rstrip()

        if inv_version != '# Sphinx inventory version 2':
            raise RuntimeError('Invalid objects.inv file version.')

        # next line is "# Project: <name>"
        # then after that is "# Version: <version>"
        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]

        # next line says if it's a zlib header
        line = stream.readline()
        if 'zlib' not in line:
            raise RuntimeError('Invalid objects.inv file, not z-lib compatible.')

        # This code mostly comes from the Sphinx repository.
        entry_regex = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in result:
                # From the Sphinx Repository:
                # due to a bug in 1.1 and below,
                # two inventory entries are created
                # for Python modules, and the first
                # one is correct
                continue

            # Most documentation pages have a label
            if directive == 'std:doc':
                subdirective = 'label'

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            prefix = f'{subdirective}:' if domain == 'std' else ''

            if projname == 'discord.py':
                key = key.replace('discord.ext.commands.', '').replace('discord.', '')

            result[f'{prefix}{key}'] = os.path.join(url, location)

        return result

    async def do_rtfm(self, ctx, key, obj):
        page_types = {
            'latest': 'https://discordpy.readthedocs.io/en/latest',
            'latest-jp': 'https://discordpy.readthedocs.io/ja/latest',
            'python': 'https://docs.python.org/3',
            'python-jp': 'https://docs.python.org/ja/3',
            'master': 'https://discordpy.readthedocs.io/en/master',
            'edpy': 'https://enhanced-dpy.readthedocs.io/en/latest',
            'chai': 'https://chaidiscordpy.readthedocs.io/en/latest',
            'bing': 'https://asyncbing.readthedocs.io/en/latest',
            'pycord': 'https://pycord.readthedocs.io/en/master'
        }
        embed_titles = {
            'latest': 'Documentation for `discord.py v1.7.3`',
            'latest-jp': 'Documentation for `discord.py v1.7.3` in Japanese',
            'python': 'Documentation for `python`',
            'python-jp': 'Documentation for `python` in Japanese',
            'master': 'Documentation for `discord.py v2.0.0a`',
            'edpy': 'Documentation for `enhanced-dpy`',
            'chai': 'Documentation for `chaidiscord.py`',
            'bing': 'Documentation for `asyncbing`',
            'pycord': 'Documentation for `pycord`'
        }

        if obj is None:
            await ctx.send(page_types[key])
            return

        if not hasattr(self, '_rtfm_cache'):
            await ctx.trigger_typing()
            await self.build_rtfm_lookup_table(page_types)

        obj = re.sub(r'^(?:discord\.(?:ext\.)?)?(?:commands\.)?(.+)', r'\1', obj)

        if key.startswith('latest'):
            # point the abc.Messageable types properly:
            q = obj.lower()
            for name in dir(discord.abc.Messageable):
                if name[0] == '_':
                    continue
                if q == name:
                    obj = f'abc.Messageable.{name}'
                    break

        cache = list(self._rtfm_cache[key].items())

        matches = finder(obj, cache, key=lambda t: t[0], lazy=False)[:8]

        e = discord.Embed(colour=discord.Colour.blurple(), title=embed_titles.get(key, 'Documentation'))
        if len(matches) == 0:
            return await ctx.send('Could not find anything. Sorry.')

        e.description = '\n'.join(f'[`{key}`]({url})' for key, url in matches)
        await ctx.send(embed=e)

    @commands.group(aliases=['rtfd', 'rtdm'], invoke_without_command=True)
    async def rtfm(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity.
        Events, objects, and functions are all supported through
        a cruddy fuzzy algorithm.
        """
        await self.do_rtfm(ctx, 'master', obj)

    @rtfm.command(name='jp')
    async def rtfm_jp(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity (Japanese)."""
        await self.do_rtfm(ctx, 'latest-jp', obj)

    @rtfm.command(name='python', aliases=['py'])
    async def rtfm_python(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity."""
        await self.do_rtfm(ctx, 'python', obj)

    @rtfm.command(name='py-jp', aliases=['py-ja'])
    async def rtfm_python_jp(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a Python entity (Japanese)."""
        await self.do_rtfm(ctx, 'python-jp', obj)

    @rtfm.command(name='master', aliases=['2.0'])
    async def rtfm_master(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity (master branch)"""
        await self.do_rtfm(ctx, 'master', obj)

    @rtfm.command(name='latest', aliases=['1.7'])
    async def rtfm_master(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a discord.py entity (master branch)"""
        await self.do_rtfm(ctx, 'latest', obj)

    @rtfm.command(name='enhanced-dpy', aliases=['edpy'])
    async def rtfm_edpy(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a enhanced-discord.py entity"""
        await self.do_rtfm(ctx, 'edpy', obj)

    @rtfm.command(name='asyncbing', aliases=['bing'])
    async def rtfm_asyncbing(self, ctx, *, obj: str = None):
        """Gives you a documentation link for an asyncbing entity """
        await self.do_rtfm(ctx, 'bing', obj)

    @rtfm.command(name='chaidiscordpy', aliases=['chaidpy', 'cdpy'])
    async def rtfm_chai(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a chaidiscord.py entity"""
        await self.do_rtfm(ctx, 'chai', obj)

    @rtfm.command(name='pycord')
    async def rtfm_pycord(self, ctx, *, obj: str = None):
        """Gives you a documentation link for a pycord entity"""
        await self.do_rtfm(ctx, 'pycord', obj)

    @commands.command()
    @hideout_only()
    async def addbot(self, ctx: CustomContext, bot: discord.User, *, reason: commands.clean_content):
        bot_queue = self.bot.get_channel(870784166705393714)
        if not bot.bot:
            raise commands.BadArgument('That dos not seem to be a bot...')
        if bot in ctx.guild.members:
            raise commands.BadArgument('That bot is already on this server...')
        confirm = await ctx.confirm(
            f'Does your bot comply with {ctx.guild.rules_channel.mention if ctx.guild.rules_channel else "<channel deleted?>"}?'
            f'\n If so, press one of these:', return_message=True)
        if confirm[0]:
            await confirm[1].edit(content='✅ Done, you will be @pinged when the bot is added!', view=None)
            embed = discord.Embed(description=reason)
            embed.set_author(icon_url=bot.display_avatar.url, name=str(bot), url=discord.utils.oauth_url(bot.id))
            embed.set_footer(text=f"Requested by {ctx.author} ({ctx.author.id})")
            await bot_queue.send(embed=embed)
        else:
            await confirm[1].edit(content='Aborting...', view=None)

    @commands.command(name='decode-qr-code', aliases=['qr-decode', 'decode-qr', 'qr'])
    @commands.cooldown(2, 30, commands.BucketType.user)
    async def decode_qr_code(self, ctx: CustomContext, *, qr_code: typing.Optional[
        typing.Union[discord.Member,
                     discord.User,
                     discord.PartialEmoji,
                     discord.Guild,
                     discord.Invite, str]
    ]):
        """
        Attempts to decode a QR code
        Can decode from the following:
          - A direct URL to an image
          - An Emoji
          - A user's profile picture
          - A server icon:
            - from an ID/name (if the bot is in that server)
            - from an invite URL (if the bot is not in the server)
          - Others? Will attempt to decode if a link is passed.
        """
        if qr_code is None:
            if ctx.message.attachments:
                qr_code = ctx.message.attachments[0]
            elif ctx.message.stickers:
                qr_code = ctx.message.stickers[0].url
            elif ctx.message.reference:
                if ctx.message.reference.resolved.attachments:
                    qr_code = ctx.message.reference.resolved.attachments[0]
                elif ctx.message.reference.resolved.embeds:
                    if ctx.message.reference.resolved.embeds[0].thumbnail:
                        qr_code = ctx.message.reference.resolved.embeds[0].thumbnail.proxy_url
                    elif ctx.message.reference.resolved.embeds[0].image:
                        qr_code = ctx.message.reference.resolved.embeds[0].image.proxy_url
        if not qr_code:
            raise commands.MissingRequiredArgument(Parameter(name='qr_code', kind=Parameter.POSITIONAL_ONLY))

        async with ctx.typing():
            link = getattr(qr_code, 'avatar', None) \
                   or getattr(qr_code, 'icon', None) \
                   or getattr(qr_code, 'guild', None) \
                   or qr_code
            link = getattr(getattr(link, 'icon', link), 'url', link)
            if url_regex.match(link):
                url = urllib.parse.quote(link, safe='')
                async with self.bot.session.get(
                        yarl.URL(f"http://api.qrserver.com/v1/read-qr-code/?fileurl={url}", encoded=True)) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data[0]['symbol'][0]['data'] is None:
                            raise commands.BadArgument(data[0]['symbol'][0]['error'])
                        embed = discord.Embed(title='I found the following data:',
                                              description=data[0]['symbol'][0]['data'])
                        embed.set_thumbnail(url=link)
                        await ctx.send(embed=embed)
                    else:
                        raise commands.BadArgument(f'API failed with status {r.status}')
            else:
                raise commands.BadArgument('No URL was found')

    @commands.command(name='impersonate', aliases=['webhook-send', 'wh-send', 'say-as'])
    @commands.bot_has_permissions(manage_webhooks=True)
    async def send_as_others(self, ctx: CustomContext, member: discord.Member, *, message):
        """ Sends a message as another person. """
        wh = await get_webhook(ctx.channel)
        await wh.send(message, avatar_url=member.display_avatar.url, username=member.display_name)
        await ctx.message.delete(delay=0)

    @commands.command(name='raw-message', aliases=['rmsg', 'raw'])
    @commands.cooldown(rate=1, per=40, type=commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.user)
    async def raw_message(self, ctx: CustomContext, message: typing.Optional[discord.Message]):
        async with ctx.typing():
            message: discord.Message = getattr(ctx.message.reference, 'resolved', message)
            if not message:
                raise commands.BadArgument('You must specify a message, or quote (reply to) one.')
            try:
                data = await self.bot.http.get_message(message.channel.id, message.id)
            except discord.HTTPException:
                raise commands.BadArgument('There was an error retrieving that message.')
            pretty_data = json.dumps(data, indent=4)
            if len(pretty_data) > 1990:
                gist = await self.bot.create_gist(filename='raw_message.json',
                                                  description='Raw Message created by DuckBot', content=pretty_data)
                to_send = f"**Output too long:**\n<{gist}>"
            else:
                to_send = f"```json\n{pretty_data}\n```"
            return await ctx.send(to_send, reference=ctx.message)

    @commands.command()
    @commands.is_owner()
    async def promotional(self, ctx: CustomContext, channel: discord.TextChannel):
        wh = await get_webhook(channel)
        embed = discord.Embed(title='Minecon Live 2021 is coming soon!',
                              url='https://www.minecraft.net/live',
                              colour=0x53A334,
                              description='Minecraft Live 2021 will be streamed <t:1634397600>:'
                                          f'\n{constants.YOUTUBE_LOGO} **[Minecraft Live 2021](https://www.youtube.com/watch?v=w6zLprHHZOk)**'
                                          f'\n{constants.YOUTUBE_LOGO} **[[AUDIO DESCRIPTION] Minecraft Live 2021](https://www.youtube.com/watch?v=vQnfKoikihE)**'
                                          f'\n{constants.YOUTUBE_LOGO} **[[AMERICAN SIGN LANGUAGE] Minecraft Live 2021](https://www.youtube.com/watch?v=nGKwHKSBtWA)**')
        embed.set_thumbnail(
            url='https://cdn.discordapp.com/attachments/879251951714467840/898050057947992104/minecraft_live1.png')
        await wh.send(username='Minecraft',
                      avatar_url='https://yt3.ggpht.com/VjFl0g2OJs6f08q0hVoiij3-CibesgwfV8RNZ-dbu7s3I-LvVTXrAu4J32MI_NlvE8v9EdYoWao=s88-c-k-c0x00ffffff-no-rj',
                      embed=embed)
        await ctx.message.add_reaction("💌")

    @commands.command(name='check-user')
    async def check_user(self, ctx: CustomContext, member: typing.Union[discord.Member, discord.User]):
        if isinstance(member, discord.Member):
            return await ctx.send(f"✅ **|** **{discord.utils.escape_markdown(str(member))}** is in this server!")
        await ctx.send(f"❌ **|** **{discord.utils.escape_markdown(str(member))}** is not in this server!")

    @commands.command()
    async def credits(self, ctx: CustomContext):
        embed = discord.Embed(description="**You copied something from my bad source code?**"
                                          "\nYou **don't need to credit me** in a command, don't"
                                          "\nworry. Although if you wish to do so, you can"
                                          "\nadd credits in the source code. ([Mozilla Public"
                                          "\nLicense](https://github.com/LeoCx1000/discord-bots/blob"
                                          "/master/LICENSE) btw one of the terms is to use the same for"
                                          "\nyour project). Just add a comment or something in"
                                          "\nthere saying where the code came from."
                                          "\nThe latter also being optional, of course."
                                          "\n(I don't know the legalities of the Mozilla Public "
                                          "\nLicense so this is not legal advice in any way.)"
                                          "\n"
                                          "\n**As for why I don't have a proper credits command?**"
                                          "\nI don't see the need to. I don't expect others to"
                                          "\ngive me credits. It's all up to them, and well,"
                                          "\nunder that same reasoning I don't add any credits"
                                          "\nhere. It's also because I (the developer of DuckBot)"
                                          "\nI'm an idiot, and I can't remember every person who"
                                          "\nhelped me, so as to not offend anyone I'd rather"
                                          "\njust not add a credits command."
                                          "\nOf course if you want to get credit because you"
                                          "\nhelped me, or because I took a snippet off your"
                                          "\ncode, let me know and I will gladly add a note"
                                          "\nin said command giving proper credits to your"
                                          "\nrepository 😊 Just that I can't remember anyone.",
                              title='Why no credits?')
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.VoiceChannel):
            return
        query = 'SELECT * FROM inviter WHERE guild_id = $1'
        if query := await self.bot.db.fetchrow(query, channel.guild.id):
            if channel.category_id == query['category']:
                if send_to := self.bot.get_channel(query['text_channel']):
                    invite = await channel.create_invite(max_age=3600 * 24)
                    message = await send_to.send(invite.url)
                    await self.bot.db.execute('INSERT INTO voice_channels(channel_id, message_id) '
                                              'VALUES ($1, $2)', channel.id, message.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.VoiceChannel):
            return
        query = 'SELECT * FROM inviter WHERE guild_id = $1'
        if query := await self.bot.db.fetchrow(query, channel.guild.id):
            if channel.category_id == query['category']:
                if delete_from := self.bot.get_channel(query['text_channel']):
                    query = 'SELECT message_id FROM voice_channels WHERE channel_id = $1'
                    if msg_id := await self.bot.db.fetchval(query, channel.id):
                        message = delete_from.get_partial_message(msg_id)
                        try:
                            await message.delete()
                        except discord.HTTPException:
                            pass

    @commands.group(invoke_without_command=True)
    @whitelist()
    async def inviter(self, ctx):
        """You probably are not whitelisted to see this! """
        pass

    @commands.guild_only()
    @whitelist()
    @inviter.command(name='set')
    async def set_inviter(self, ctx: CustomContext, category: discord.CategoryChannel,
                          text_channel: discord.TextChannel):
        await self.bot.db.execute("INSERT INTO inviter(guild_id, category, text_channel) VALUES ($1, $2, $3) "
                                  "ON CONFLICT (guild_id) DO UPDATE SET "
                                  "category = $2, "
                                  "text_channel = $3;", ctx.guild.id, category.id, text_channel.id)
        await ctx.message.add_reaction('✅')

    @commands.guild_only()
    @whitelist()
    @inviter.command(name='unset')
    async def unset_inviter(self, ctx: CustomContext):
        await self.bot.db.execute("DELETE FROM inviter WHERE guild_id = $1;", ctx.guild.id)
        await ctx.message.add_reaction('✅')

    @commands.is_owner()
    @inviter.group(name='w')
    async def inviter_whitelist(self, ctx):
        """To allow only some people to run the command."""
        pass

    @commands.is_owner()
    @inviter_whitelist.command(name='a')
    async def whitelist_add(self, ctx: CustomContext, user: discord.User):
        await self.bot.db.execute('INSERT INTO inv_whitelist(uid) VALUES ($1) '
                                  'ON CONFLICT (uid) DO NOTHING', user.id)
        await ctx.message.add_reaction('✅')

    @commands.is_owner()
    @inviter_whitelist.command(name='r')
    async def whitelist_rem(self, ctx: CustomContext, user: discord.User):
        await self.bot.db.execute('DELETE FROM inv_whitelist WHERE uid = $1', user.id)
        await ctx.message.add_reaction('✅')
