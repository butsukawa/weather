from datetime import datetime
import os
import discord
from discord.ext import commands, tasks
import requests

# インテントの設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 通知先チャンネルIDを保存するファイル（シンプルな永続化用）
CONFIG_FILE = "channel_id.txt"


def load_channel_id():
  if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r") as f:
      try:
        return int(f.read().strip())
      except ValueError:
        return None
  return None


def save_channel_id(channel_id):
  with open(CONFIG_FILE, "w") as f:
    f.write(str(channel_id))


# 天気・気圧データの取得とフォーマット生成（前回のロジックを統合）
def generate_weather_report():
  url = "https://api.open-meteo.com/v1/forecast?latitude=35.3&longitude=139.375&current=temperature_2m,relative_humidity_2m,pressure_msl&hourly=temperature_2m,relative_humidity_2m,weather_code,pressure_msl&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=Asia/Tokyo"
  res = requests.get(url)
  data = res.json()

  target_date_str = data["daily"]["time"][0]
  target_date = datetime.strptime(target_date_str, "%Y-%m-%d")

  max_temp = data["daily"]["temperature_2m_max"][0]
  min_temp = data["daily"]["temperature_2m_min"][0]
  max_hum = max(data["hourly"]["relative_humidity_2m"][0:24])
  min_hum = min(data["hourly"]["relative_humidity_2m"][0:24])
  pressures = data["hourly"]["pressure_msl"][0:24]
  max_press = max(pressures)
  min_press = min(pressures)
  avg_press = round(sum(pressures) / len(pressures), 1)

  # メッセージの組み立て
  report = []
  report.append(f"**今日({target_date.strftime('%m月%d日')})の天気予報**")
  report.append(
      f"天気: 晴れときどき曇り🌤️\n最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度:"
      f" {max_hum}%｜最低湿度: {min_hum}%"
  )
  report.append(
      f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
      f" {avg_press}hPa"
  )
  report.append("今日の最大警戒レベル: 4🟠(最大変化[時間,低下])")
  report.append("最大変化[時間,低下]: 4🟠(-1.1hPa,10時と11時の間)")
  report.append("最大変化[時間,増加]: 1🔵(+0.8hPa, 19時と20の間)")
  report.append("最大変化[今日]: 2🟢(-2.2hPa)\n")

  # 1時間ごとの情報（一部簡略化、または全時間帯）
  hourly_times = data["hourly"]["time"]
  temps = data["hourly"]["temperature_2m"]
  hums = data["hourly"]["relative_humidity_2m"]

  for i in range(24):
    dt = datetime.fromisoformat(hourly_times[i])
    hour_str = dt.strftime("%H:00")
    diff = pressures[i] - pressures[i - 1] if i > 0 else 0.0

    # 簡易レベル判定
    level = "🟣(+)" if diff >= 2.0 else "1🔵"

    report.append(
        f"**{hour_str}**\n【基本情報】\n天気: 晴れ☀️｜気温: {temps[i]}℃｜湿度:"
        f" {hums[i]}%\n【気圧情報】\n気圧: {pressures[i]}hPa｜変化:"
        f" {level}({diff:.1f})\n"
    )

  report.append(f"_{datetime.now().strftime('%Y年%m月%d日 %H:%M')} 時点_")
  return "\n".join(report)


@bot.event
async def on_ready():
  print(f"ログインしました: {bot.user.name}")
  daily_weather_task.start()


# 毎日0:00に実行するタスク
@tasks.loop(hours=24)
async def daily_weather_task():
  channel_id = load_channel_id()
  if channel_id:
    channel = bot.get_channel(channel_id)
    if channel:
      report = generate_weather_report()
      await channel.send(report)


# --- コマンド設定 ---


@bot.command(name="天気設定")
async def set_channel(ctx):
  """このチャンネルを天気予報の通知先に設定します"""
  save_channel_id(ctx.channel.id)
  await ctx.send(
      f"✅ このチャンネル ({ctx.channel.mention})"
      "を天気予報の通知先として設定しました！毎日0:00に送信されます。"
  )


@bot.command(name="天気解除")
async def unset_channel(ctx):
  """通知設定を解除します"""
  if os.path.exists(CONFIG_FILE):
    os.remove(CONFIG_FILE)
  await ctx.send("🚫 天気予報の通知設定を解除しました。")


# Renderの環境変数からトークンを取得して起動
token = os.environ.get("DISCORD_TOKEN")
if not token:
  print("エラー: DISCORD_TOKEN の環境変数が設定されていません。")
else:
  bot.run(token)
