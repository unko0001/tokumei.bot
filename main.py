import discord
from discord.ext import commands
from collections import defaultdict
from discord import app_commands
import json
import asyncio
import os
import re
import aiohttp
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# JSONファイルから禁止ワードリストを読み込む
forbidden_words_json_path = os.getenv("FORBIDDEN_WORDS_JSON_PATH")
with open(forbidden_words_json_path, "r", encoding="utf-8") as json_file:
    data = json.load(json_file)
    forbidden_words = data["forbidden_words"]

# ユーザーIDごとにメッセージやメンション数を追跡するための辞書
sent_messages = defaultdict(dict)
user_mention_count = defaultdict(int)
message_count = defaultdict(int)

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
bot.remove_command("help")

forum_creation_cooldown = set()

async def remove_from_cooldown(user_id):
    await asyncio.sleep(600)  # 10分後にクールダウンから削除
    forum_creation_cooldown.remove(user_id)

bot_forum_creation_count = 0

async def increase_bot_forum_count():
    global bot_forum_creation_count
    bot_forum_creation_count += 1
    await asyncio.sleep(120)  # 2分後にカウントを減らす
    bot_forum_creation_count -= 1

bot_forum_creation_count = 0

webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

if not webhook_url:
    raise ValueError("WEBHOOK_URL is not set in the environment variables.")

bot_message_count = 0  # ボットの送信数を追跡
bot_edit_count = 0     # ボットの編集数を追跡

# メッセージ内のロールメンションをチェックする関数
def contains_role_mentions(message):
    return bool(re.search(r"<@&\d+>", message))

# メッセージ内の絵文字数をカウントする関数
def count_emojis(message):
    emoji_pattern = re.compile(
        r'[\U0001F600-\U0001F64F]|'    # 普通の顔文字
        r'[\U0001F300-\U0001F5FF]|'    # 図形・記号
        r'[\U0001F680-\U0001F6FF]|'    # トランスポート・地図記号
        r'[\U0001F1E0-\U0001F1FF]|'    # 国旗
        r'[\U00002500-\U00002BEF]|'    # CJK（中国・日本・韓国）
        r'[\U00002702-\U000027B0]|'    # カーセンサー
        r'[\U0001f926-\U0001f937]|'    # 人々やパーツ
        r'[\U00010000-\U0010ffff]|'    # その他のエモーティコン
        r'\u2640-\u2642|'              # 男女
        r'\u2600-\u2B55|'              # 太陽や月、星など
        r'\u200d|'                     # ゼロ幅結合子
        r'\u23cf|'                     # カーソル
        r'\u23e9|'                     # 戻る
        r'\u231a|'                     # 時計
        r'\ufe0f|'                     # テキスト結合子
        r'\u3030'                      # 波ダッシュ
        , flags=re.UNICODE)
    return len(emoji_pattern.findall(message))

@bot.tree.command(name="a", description="匿名でメッセージを送信します。")
@app_commands.describe(message="匿名で送信するメッセージ")
async def anonymous_message(interaction: discord.Interaction, message: str, reply_url: str = None):
    global bot_message_count

    if message_count[message] >= 5:
        await interaction.response.send_message("同じメッセージを連続で送信することはできません。1分後に再度お試しください。", ephemeral=True)
        return

    if contains_role_mentions(message):
        await interaction.response.send_message("ロールメンションを含むため、送信できません。", ephemeral=True)
        return
    
    if len(message.split("\n")) >= 8:
        await interaction.response.send_message("改行が多すぎます。8改行未満にしてください。", ephemeral=True)
        return
    
    if len(message) >= 599:
        await interaction.response.send_message("文字が多すぎます。599文字未満にしてください。", ephemeral=True)
        return
    
    if len(message) >= 150:
        bot_message_count += 1
        if bot_message_count > 3:
            await interaction.response.send_message("150文字以上のメッセージの送信は一時的に制限されています。1分後に再度試してください。", ephemeral=True)
            bot_message_count -= 1
            return
    
    if count_emojis(message) >= 10:
        await interaction.response.send_message("絵文字が多すぎます。10個未満にしてください。", ephemeral=True)
        return

    for word in forbidden_words:
        if word in message:
            if word in ["@everyone", "@here"]:
                await interaction.response.send_message("全体メンションを含むため、送信できません。", ephemeral=True)
            elif word in ["discord.gg/", "discord.com/invite/", "discordapp.com/invite/"]:
                await interaction.response.send_message("招待リンクを含むため、送信できません。", ephemeral=True)
            else:
                await interaction.response.send_message("禁止ワードを含むため、送信できません。", ephemeral=True)
            return

    mentioned_users = set(re.findall(r"<@!?(\d+)>", message))
    unique_mention_count = len(mentioned_users)

    if unique_mention_count > 1:
        await interaction.response.send_message("メンションが多すぎます。メンションは1メッセージに1回です。", ephemeral=True)
        return

    user_mention_count[interaction.user.id] += unique_mention_count

    if unique_mention_count == 1:
        bot_message_count += 1
        if bot_message_count > 3:
            await interaction.response.send_message("メンションの送信は一時的に制限されています。1分後に再度試してください。", ephemeral=True)
            bot_message_count -= 1
            return
    
    try:
        if reply_url:
            if not reply_url.startswith("https://discord.com/channels/"):
                raise ValueError("無効なメッセージリンクです")

            _, guild_id, reply_channel_id, message_id = reply_url.split("/")[-4:]
            reply_channel_id = int(reply_channel_id)
            if reply_channel_id != interaction.channel.id:
                await interaction.response.send_message("返信はそのチャンネルで送信してください。", ephemeral=True)
                return

            reply_channel = bot.get_channel(reply_channel_id)
            reply_message = await reply_channel.fetch_message(int(message_id))
            sent_message = await reply_message.reply(message)
            await interaction.response.send_message("匿名でメッセージに返信しました。", ephemeral=True)
        else:
            sent_message = await interaction.channel.send(message)
            sent_messages[interaction.user.id][sent_message.id] = message

            await interaction.response.send_message("匿名でメッセージを送信しました。", ephemeral=True)

        message_count[message] += 1

    except ValueError as ve:
        print(ve)
        await interaction.response.send_message("無効なメッセージリンクです。正しい形式のリンクを指定してください。", ephemeral=True)
    except Exception as e:
        print(e)
        await interaction.response.send_message("指定されたメッセージには返信できませんでした。", ephemeral=True)
    finally:
        if len(message) >= 199 or unique_mention_count == 1:
            bot_message_count += 1

        if message_count[message] == 5:
            await asyncio.sleep(60)
            message_count[message] = 0

        await asyncio.sleep(60)
        bot_message_count -= 1

@bot.tree.command(name="e", description="自分が送信したメッセージを編集または削除します。")
@app_commands.describe(message_url="編集または削除するメッセージのリンク", edit_message="編集後のメッセージ（省略可、編集する場合のみ必要）")
async def edit_or_delete_message(interaction: discord.Interaction, message_url: str, edit_message: str = None):
    global bot_edit_count

    try:
        if not message_url.startswith("https://discord.com/channels/"):
            raise ValueError("無効なメッセージリンクです")

        channel_id, message_id = map(int, message_url.split("/")[-2:])
        channel = bot.get_channel(channel_id)
        message = await channel.fetch_message(message_id)

        if interaction.user.id not in sent_messages or message_id not in sent_messages[interaction.user.id]:
            await interaction.response.send_message("指定されたメッセージを編集または削除する権限がありません。", ephemeral=True)
            return

        if edit_message:
            if message.content == edit_message:
                await interaction.response.send_message("新しいメッセージは元のメッセージと同じです。", ephemeral=True)
                return

            if message.content == sent_messages[interaction.user.id][message_id]:
                bot_edit_count += 1
                if bot_edit_count > 3:
                    await interaction.response.send_message("連続編集の回数制限に達しました。1分後に再度お試しください。", ephemeral=True)
                    bot_edit_count -= 1
                    return

            await message.edit(content=edit_message)
            await interaction.response.send_message("メッセージを編集しました。", ephemeral=True)
            sent_messages[interaction.user.id][message_id] = edit_message

            await asyncio.sleep(60)
            bot_edit_count -= 1

        else:
            await message.delete()
            await interaction.response.send_message("メッセージを削除しました。", ephemeral=True)
            del sent_messages[interaction.user.id][message_id]

    except ValueError as ve:
        print(ve)
        await interaction.response.send_message("無効なメッセージリンクです。正しい形式のリンクを指定してください。", ephemeral=True)
    except Exception as e:
        print(e)
        await interaction.response.send_message("メッセージの編集または削除に失敗しました。", ephemeral=True)

class ForumCreationModal(discord.ui.Modal):
    def __init__(self, channel):
        super().__init__(title="新しい投稿")
        self.channel = channel
        self.add_item(discord.ui.TextInput(label="題名", custom_id="title", max_length=200, placeholder="タイトル"))
        self.add_item(discord.ui.TextInput(label="内容", custom_id="message", style=discord.TextStyle.long, max_length=3000, placeholder="メッセージを入力..."))
        self.add_item(discord.ui.TextInput(label="タグ", custom_id="tags", required=False, placeholder="タグ（カンマ区切り）"))

    async def on_submit(self, interaction: discord.Interaction):
        title = self.children[0].value
        message = self.children[1].value
        tags = self.children[2].value if self.children[2].value else ""

        # ユーザーメンションのチェック
        mentioned_users = set(re.findall(r"<@!?(\d+)>", message))
        unique_mention_count = len(mentioned_users)
        if unique_mention_count > 1:
            await interaction.response.send_message("メンションが多すぎます。メンションは1メッセージに1回です。", ephemeral=True)
            return

        # 1ユーザーが作成できるフォーラムチャンネルを10分に1個までに制限する
        if interaction.user.id in forum_creation_cooldown:
            await interaction.response.send_message("フォーラムを作成するためのクールダウン中です。10分後に再度試してください。", ephemeral=True)
            return
        else:
            forum_creation_cooldown.add(interaction.user.id)
            asyncio.create_task(remove_from_cooldown(interaction.user.id))



        # BOTが作成できるフォーラムチャンネルを2分に2個までに制限する
        if bot_forum_creation_count >= 2:
            await interaction.response.send_message("BOTが作成できるフォーラムチャンネルの上限に達しました。しばらくしてから再度お試しください。", ephemeral=True)
            return
        else:
            asyncio.create_task(increase_bot_forum_count())

        # title:とmessage:に禁止ワードが含まれている場合はブロックする
        for word in forbidden_words:
            if word in title or word in message:
                await interaction.response.send_message("禁止ワードを含むため、フォーラムを作成できません。", ephemeral=True)
                return

        # message:に絵文字10個以上が含まれている場合はブロックする
        if count_emojis(message) >= 10:
            await interaction.response.send_message("絵文字が多すぎます。10個未満にしてください。", ephemeral=True)
            return

        # 利用可能なタグを取得
        available_tags = {t.name: t for t in self.channel.available_tags}
        
        # 指定されたタグを分割してリストに変換
        applied_tags = []
        if tags:
            tag_list = tags.split(",")
            # 指定されたタグがすべて利用可能か確認
            for tag in tag_list:
                tag = tag.strip()
                if tag in available_tags:
                    applied_tags.append(available_tags[tag])
                else:
                    await interaction.response.send_message(f"指定されたタグ「{tag}」が見つかりません。", ephemeral=True)
                    return

        # スレッドを作成してタグを適用
        post = await self.channel.create_thread(name=title, content=message, applied_tags=applied_tags)
        
        await interaction.response.send_message(f"フォーラム「{title}」を作成し、タグ「{tags}」を適用して内容を投稿しました。", ephemeral=True)

@bot.tree.command(name="f", description="匿名でフォーラムに新しい投稿を作成します")
@app_commands.describe(channel="匿名で投稿するチャンネル")
async def forum_command(interaction: discord.Interaction, channel: discord.ForumChannel):
    if not channel.permissions_for(interaction.user).send_messages:
        await interaction.response.send_message("このチャンネルに投稿する権限がありません。", ephemeral=True)
        return
    await interaction.response.send_modal(ForumCreationModal(channel))

bot.run(BOT_TOKEN)
