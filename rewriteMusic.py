import asyncio
import functools
import itertools
import math
import random

import discord
import youtube_dl
from async_timeout import timeout
from discord.ext import commands

from datetime import datetime, timedelta
import aiohttp
import os
from urllib.request import urlopen, Request
import requests
from requests import get
import re

youtube_dl.utils.bug_reports_message = lambda: ''

bad_words = []

async def change_status(string):
    game = discord.Game(str(string))
    await bot.change_presence(activity=game)

def download_img(url, name, path):
    req = Request(url, headers={"User-Agent":"Mozila 5.0"})
    raw_img = urlopen(req).read()
    f = open(os.path.join(path, name), "wb")
    f.write(raw_img)

async def nekobot(imgtype:str):
    async with aiohttp.ClientSession() as cs:
        async with cs.get("https://nekobot.xyz/api/image?type=%s" % imgtype) as res:
            res = await res.json()
    return res.get("message")

class VoiceError(Exception):
    pass

class YTDLError(Exception):
    pass

class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration')))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return '**{0.title}** by **{0.uploader}**'.format(self)

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('`{}`와 관련된 것을 찾지 못했습니다.'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('`{}`와 관련된 것을 찾지 못했습니다.'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('`{}`을(를) 작업하지 못했습니다.'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('`{}`와 관련된 것을 불러오는데 실패했습니다.'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append('{} 일'.format(days))
        if hours > 0:
            duration.append('{} 시간'.format(hours))
        if minutes > 0:
            duration.append('{} 분'.format(minutes))
        if seconds > 0:
            duration.append('{} 초'.format(seconds))

        return ', '.join(duration)

class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self):
        time_stamp = str(datetime.now()).split('.', 1)[0]
        embed = (discord.Embed(title='현재 재생중',
                               description='```css\n{0.source.title}\n```'.format(self),
                               color=discord.Color.blurple())
                 .add_field(name='길이', value=self.source.duration)
                 .add_field(name='요청자', value=self.requester.mention)
                 .add_field(name='업로더', value='[{0.source.uploader}]({0.source.uploader_url})'.format(self))
                 .add_field(name='URL', value='[Click]({0.source.url})'.format(self))
                 .set_thumbnail(url=self.source.thumbnail)
                 .set_footer(text="{0}에 의해 {1}에 요청됨".format(self.requester.display_name, time_stamp), icon_url=self.requester.avatar_url))

        return embed

class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]

class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()

            if not self.loop:
                # Try to get the next song within 3 minutes.
                # If no song will be added to the queue in time,
                # the player will disconnect due to performance
                # reasons.
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return

            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            await self.current.source.channel.send(embed=self.current.create_embed())

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage('이 명령어는 DM에서는 사용하지 못합니다.')

        return True

    async def cog_before_invoke(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send('오류 발생: {}'.format(str(error)))

    @commands.command(name='join', invoke_without_subcommand=True)
    async def _join(self, ctx: commands.Context):
        """Joins a voice channel."""

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='summon')
    @commands.has_permissions(manage_guild=True)
    async def _summon(self, ctx: commands.Context, *, channel: discord.VoiceChannel = None):
        """Summons the bot to a voice channel.
        If no channel was specified, it joins your channel.
        """

        if not channel and not ctx.author.voice:
            raise VoiceError('음성 채널에 입장해 있지 않거나 잘못된 채널을 입력하셨습니다.')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', aliases=['disconnect'])
    @commands.has_permissions(manage_guild=True)
    async def _leave(self, ctx: commands.Context):
        """Clears the queue and leaves the voice channel."""

        if not ctx.voice_state.voice:
            return await ctx.send('아무 음성 채널에도 입장해 있지 않습니다.')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    @commands.command(name='volume')
    async def _volume(self, ctx: commands.Context, *, volume: int):
        """Sets the volume of the player."""

        if not ctx.voice_state.is_playing:
            return await ctx.send('현재 재생중인 음악이 없습니다.')
        else:
            if volume > 100 or volume < 0:
                return await ctx.send('소리는 0에서 100사이만 가능합니다.')
            else:
                ctx.voice_state.volume = volume / 100
                await ctx.send('소리를 {}%로 조정했습니다.'.format(volume))

    @commands.command(name='now', aliases=['current', 'playing'])
    async def _now(self, ctx: commands.Context):
        """Displays the currently playing song."""

        await ctx.send(embed=ctx.voice_state.current.create_embed())

    @commands.command(name='pause')
    @commands.has_permissions(manage_guild=True)
    async def _pause(self, ctx: commands.Context):
        """Pauses the currently playing song."""

        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            await ctx.message.add_reaction('⏯')

    @commands.command(name='resume')
    @commands.has_permissions(manage_guild=True)
    async def _resume(self, ctx: commands.Context):
        """Resumes a currently paused song."""

        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            await ctx.message.add_reaction('⏯')

    @commands.command(name='stop')
    @commands.has_permissions(manage_guild=True)
    async def _stop(self, ctx: commands.Context):
        """Stops playing song and clears the queue."""

        ctx.voice_state.songs.clear()

        if ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            await ctx.message.add_reaction('⏹')

    @commands.command(name='skip')
    async def _skip(self, ctx: commands.Context):
        """Vote to skip a song. The requester can automatically skip.
        3 skip votes are needed for the song to be skipped.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('현재 아무 노래도 재생하고 있지 않습니다.')

        voter = ctx.message.author
        if voter == ctx.voice_state.current.requester:
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 3:
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()
            else:
                await ctx.send('건너뛰기 투표를 하셨습니다. 현재인원[**{}/3**]'.format(total_votes))

        else:
            await ctx.send('이미 건너뛰기 투표를 하셨습니다.')

    @commands.command(name='queue')
    async def _queue(self, ctx: commands.Context, *, page: int = 1):
        """Shows the player's queue.
        You can optionally specify the page to show. Each page contains 10 elements.
        """

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('큐가 비어있습니다.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} 개의 노래:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                 .set_footer(text='페이지 {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)

    @commands.command(name='shuffle')
    async def _shuffle(self, ctx: commands.Context):
        """Shuffles the queue."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('큐가 비어있습니다.')

        ctx.voice_state.songs.shuffle()
        await ctx.message.add_reaction('✅')

    @commands.command(name='remove')
    async def _remove(self, ctx: commands.Context, index: int):
        """Removes a song from the queue at a given index."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('큐가 비어있습니다.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.message.add_reaction('✅')

    @commands.command(name='loop')
    async def _loop(self, ctx: commands.Context):
        """Loops the currently playing song.
        Invoke this command again to unloop the song.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('현재 재생중인 노래가 없습니다.')

        # Inverse boolean value to loop and unloop.
        ctx.voice_state.loop = not ctx.voice_state.loop
        if ctx.voice_state.loop:
            await ctx.message.add_reaction('✅')
        else:
            await ctx.message.add_reaction('❌')

    @commands.command(name='play')
    async def _play(self, ctx: commands.Context, *, search: str):
        """Plays a song.
        If there are songs in the queue, this will be queued until the
        other songs finished playing.
        This command automatically searches from various sites if no URL is provided.
        A list of these sites can be found here: https://rg3.github.io/youtube-dl/supportedsites.html
        """

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        async with ctx.typing():
            try:
                source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send('요청을 처리하는동안 오류가 발생했습니다: {}'.format(str(e)))
            else:
                song = Song(source)

                await ctx.voice_state.songs.put(song)
                await ctx.send('{}이(가) 큐에 추가됨.'.format(str(source)))
                await ctx.message.add_reaction('▶')

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('아무 음성 채널에도 입장해 있지 않습니다.')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('이미 입장해 있습니다.')

bot = commands.Bot(command_prefix="!!")
bot.add_cog(Music(bot))

@bot.event
async def on_ready():
    print('Logged in as:\n{0.user.name}\n{0.user.id}'.format(bot))

@bot.event
async def on_message(message):
    if message.content.upper() in bad_words:
        embed = discord.Embed(title=str(message.author.display_name+"님이 금지단어를 사용했습니다."), color = discord.Colour.red(), description = str(message.content+", "+message.channel.mention))
        member1 = discord.utils.get(message.guild.members, name="大切な") or discord.utils.get(message.guild.members, name="하늘씌")
        member2 = discord.utils.get(message.guild.members, name="刹那")
        dmchannel = await member1.create_dm()
        if member2:
            dmchanne2 = await member2.create_dm()
            await dmchanne2.send(embed=embed)
        else:
            pass
        await dmchannel.send(embed=embed)
    else:
        pass

    if message.content.startswith('!clear'):
        amount = message.content.split(" ")[1]
        try:
            await message.channel.purge(limit=amount)
        except:
            pass

        
    await bot.process_commands(message)

@bot.command()
async def change_presence(ctx, string):
    await change_status(string)
    await ctx.send("현재 상태를 '{0}'로 변경했습니다!".format(string))

@bot.command()
async def userinfo(ctx, member:discord.Member):
    time_stamp = str(ctx.message.created_at+timedelta(hours=9)).split('.', 1)[0]
    joined = str(member.joined_at+timedelta(hours=9)).split('.', 1)[0]
    created = str(member.created_at+timedelta(hours=9)).split('.', 1)[0]
    user_status = str()
    playing = str()
    if str(member.status) == "online":
        user_status = "온라인"
    if str(member.status) == "offline":
        user_status = "오프라인"
    if str(member.status) == "idle":
        user_status = "자리비움"
    if str(member.status) == "dnd" or str(member.status) == "do_not_disturb":
        user_status = "다른 용무 중"
    roles = [role for role in member.roles]
    if member.activities:
        playing = ", ".join([activity.name for activity in member.activities])
    else:
        playing = "\u200b"
    try:
        embed = discord.Embed(colour=member.color)
        embed.set_author(name="{0}의 정보".format(member.name))
        embed.set_thumbnail(url=member.avatar_url)
        embed.set_footer(text="{0}에 의해 {1}에 요청됨".format(ctx.author.name, time_stamp), icon_url=ctx.author.avatar_url)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="고유번호", value="#"+member.discriminator)
        embed.add_field(name="별명", value=member.display_name)
        embed.add_field(name="아이디 생성 날짜", value=created)
        embed.add_field(name="서버에 가입 날짜", value=joined)
        embed.add_field(name="현재 상태", value=user_status)
        embed.add_field(name="역할", value=', '.join([role.mention for role in roles]))
        embed.add_field(name="가장 높은 역할", value=member.top_role.mention)
        embed.add_field(name="봇 여부", value=str(member.bot))
        embed.add_field(name="gif 프사 여부", value=str(member.is_avatar_animated()))
        embed.add_field(name="폰 접속 여부", value=str(member.is_on_mobile()))
        embed.add_field(name="현재 하는 중", value=playing)

        await ctx.channel.send(embed=embed)
    except IndexError:
        await ctx.channel.send("해당 유저를 찾을 수 없습니다!")
    except Exception as e:
        embed = discord.Embed(colour=discord.Color.red(), title="오류 발생!", description=e)
        await ctx.channel.send(embed=embed)
    except:
        await ctx.channel.send("알 수 없는 오류 발생!")
    finally:
        pass

@bot.command()
async def serverinfo(ctx):
    afkc = str()
    if not ctx.guild.afk_channel:
        afkc = "없음"
    else:
        afkc = "#"+str(ctx.guild.afk_channel)
    level = str()
    if ctx.guild.verification_level == discord.VerificationLevel.none:
        level = "보안 없음"
    if ctx.guild.verification_level == discord.VerificationLevel.low:
        level = "이메일 인증 요구"
    if ctx.guild.verification_level == discord.VerificationLevel.medium:
        level = "이메일 인증과 디스코드 가입 5분 이상"
    if ctx.guild.verification_level == discord.VerificationLevel.high:
        level = "이메일 인증과 디스코드 가입 5분 이상, 서버에 가입한지 10분 이상"
    if ctx.guild.verification_level == discord.VerificationLevel.extreme:
        level = "디스코드 계정에 휴대폰 인증 요구"
    banner = str()
    if ctx.guild.banner_url:
        banner = ctx.guild.banner_url
    else:
        banner = "없음"
    roles = [role for role in ctx.guild.roles]
    created = str(ctx.guild.created_at+timedelta(hours=9)).split('.', 1)[0]
    time_stamp = str(ctx.message.created_at+timedelta(hours=9)).split('.', 1)[0]
    try:
        embed = discord.Embed(colour=0x7289DA)
        embed.set_author(name="{0}의 정보".format(ctx.guild.name))
        embed.set_thumbnail(url=ctx.guild.icon_url)
        embed.set_footer(text="{0}에 의해 {1}에 요청됨".format(ctx.author.name, time_stamp), icon_url=ctx.author.avatar_url)
        embed.add_field(name="이름", value=ctx.guild.name)
        embed.add_field(name="ID", value=ctx.guild.id)
        embed.add_field(name="설명", value=ctx.guild.description)
        embed.add_field(name="서버 위치", value=str(ctx.guild.region))
        embed.add_field(name="잠수 시간", value=str(ctx.guild.afk_timeout)+" 초")
        embed.add_field(name="잠수 채널", value=afkc)
        embed.add_field(name="주인장", value=ctx.guild.owner.mention)
        embed.add_field(name="최대 멤버 수", value=ctx.guild.max_members)
        embed.add_field(name="초대장", value=ctx.guild.splash)
        embed.add_field(name="보안 수준", value=level)
        embed.add_field(name="서버 프리미엄 티어", value=ctx.guild.premium_tier)
        embed.add_field(name="서버 부스트된 횟수", value=str(ctx.guild.premium_subscription_count)+" 회")
        embed.add_field(name="음성채팅방 개수", value=str(len(ctx.guild.voice_channels))+" 개")
        embed.add_field(name="텍스트채팅방 개수", value=str(len(ctx.guild.text_channels))+" 개")
        embed.add_field(name="카테고리 개수", value=str(len(ctx.guild.categories))+" 개")
        embed.add_field(name="서버의 최대 이모지 개수", value=str(ctx.guild.emoji_limit)+" 개")
        embed.add_field(name="최대 비트레이트", value=str((ctx.guild.bitrate_limit)/1000)+" kbps")
        embed.add_field(name="서버의 최대 공유 가능한 파일크기", value=str((ctx.guild.filesize_limit)/1048576)+" MB")
        embed.add_field(name="접속된 멤버 수", value=str(ctx.guild.member_count)+" 명")
        embed.add_field(name="서버에 부스트한 멤버 수", value=str(len(ctx.guild.premium_subscribers))+" 명")
        embed.add_field(name="역할", value=", ".join([role.mention for role in roles]))
        embed.add_field(name="배너 주소", value=banner)
        embed.add_field(name="서버 생성된 날짜", value=created)

        await ctx.channel.send(embed=embed)
    except AttributeError as e:
        await ctx.channel.send("알 수 없는 오류 발생! - {0}".format(e))
    except Exception as e:
        await ctx.channel.send("알 수 없는 오류 발생! - {0}".format(e))
    except:
        await ctx.channel.send("알 수 없는 오류 발생!")
    
@bot.command()
async def thighs_num(ctx, number=1, kwargs='-null'):
    i = 0
    if kwargs == "-save":
        directory = "N:/ffmpeg/images/"
        async with aiohttp.ClientSession() as cs:
            while i < number:
                async with cs.get("https://nekobot.xyz/api/v2/image/thighs") as res:
                    res = await res.json()
                    link = res["message"]
                    image_name = link.replace('https://cdn.nekobot.xyz/thighs/', '')
                    print("Name:"+image_name)
                    print("Path:"+directory)
                    print("URL:"+link)
                    if number > 100:
                        counter = number
                        download_img(link, image_name, directory)
                        await ctx.channel.send("("+res["message"]+")"+" 저장됨!")
                        counter = number-1
                        if counter == 0:
                            continue
                    if number <= 100:
                        download_img(link, image_name, directory)
                        await ctx.channel.send("("+res["message"]+")"+" 저장됨!")
                    i += 1
    elif kwargs != "-save":
        async with aiohttp.ClientSession() as cs:
            while i < number:
                async with cs.get("https://nekobot.xyz/api/v2/image/thighs") as res:
                    res = await res.json()
                    i += 1
                    await ctx.channel.send(res["message"])

@bot.command()
async def pgif(ctx):
    em = discord.Embed(color=0xDEADBF)
    em.set_image(url=await nekobot("pgif"))
    await ctx.channel.send(embed=em)

@bot.command()
async def create_text_channel(ctx, name, nsfw):
    guild = ctx.guild
    overwrites = {
    guild.default_role: discord.PermissionOverwrite(read_messages=False),
    guild.me: discord.PermissionOverwrite(read_messages=True)
    }
    channel = await ctx.guild.create_text_channel(name, overwrites=overwrites, reason=None, nsfw=nsfw)
    print(channel.id)

@bot.command()
async def modify_channel(ctx, channel: discord.TextChannel):
    await channel.set_permissions(ctx.author, read_messages=True)

@bot.command()
async def gtn(ctx):
    em = discord.Embed(color=0xDEADBF)
    params = {'type': 'nsfw-gtn', 'nsfw': 'true'}
    res = requests.get("https://rra.ram.moe/i/r", params=params)
    resj = res.json()
    print(res.text)
    x = resj["path"]
    em.set_image(url="https://rra.ram.moe"+x)
    await ctx.channel.send(embed=em)

@bot.command()
async def create_role(ctx, name):
    role = await ctx.guild.create_role(name=name, permissions=discord.Permissions(permissions=8))
    await ctx.author.add_roles(role)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount=2):
    await ctx.channel.purge(limit=amount)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def add_words(ctx, *args):
    global bad_words
    words = load_file("words.txt")
    for word in args:
        if word in bad_words:
            await ctx.channel.send("이미 "+word+"는 금지단어에 있습니다.")
        else:
            words.append(word.upper())
            await ctx.channel.send(", ".join(args)+"를 금지단어에 추가했습니다.")
    bad_words = words
    save_file("words.txt", bad_words)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def delete_words(ctx, *args):
    global bad_words
    words = load_file("words.txt")
    for word in args:
        try:
            words.remove(word)
            await ctx.channel.send("단어 "+word+"를 삭제했습니다.")
        except ValueError:
            await ctx.channel.send("단어 "+word+"를 삭제하는데 실패했습니다. 관리자에게 문의해 주세요.")
    bad_words = words
    save_file("words.txt", bad_words)

@bot.command(aliases=['show_words'])
@commands.has_permissions(manage_guild=True)
async def reload_words(ctx):
    global bad_words
    words = load_file("words.txt")
    bad_words = words
    embed = discord.Embed(title="단어 목록을 갱신했습니다. 현재 단어:", color = discord.Colour.dark_purple())
    i = 0
    while i < len(bad_words):
        embed.add_field(name=str(i+1)+"번째", value=bad_words[i])
        i = i+1
    await ctx.channel.send(embed=embed)

def load_file(fullname):
    with open('./'+fullname, mode='rt', encoding='utf-8') as f:
        words1 = f.readline()
        words = words1.upper().split(':')
    return words

def save_file(fullname, words):
    with open("./"+fullname, mode='wt', encoding='utf-8') as f:
        f.write(':'.join(words))


ACCESS_TOKEN = os.environ["BOT_TOKEN"]
bot.run(ACCESS_TOKEN)