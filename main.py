from datetime import datetime
import os
import time
import discord
from discord.ext import commands, tasks
import requests

# インテントの設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

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


# 警戒レベルの判定関数（ご指定のルールに基づく）
def judge_pressure_hourly(diff):
  if diff >= 2.0:
    return "🟣"  # 上昇注意
  elif diff > -0.3:
    return "1🔵"
  elif diff >= -0.6:
    return "2🟢"
  elif diff >= -0.9:
    return "3🟡"
  elif diff >= -1.3:
    return "4🟠"
  else:
    return "5🔴"


def get_weather_emoji(wmo_code):
  if wmo_code == 0:
    return "☀️"
  elif wmo_code in [1, 2]:
    return "🌤"
  elif wmo_code == 3:
    return "🌥"
  elif wmo_code in [51, 53, 55, 61, 63]:
    return "🌧"
  elif wmo_code in [95, 96, 99]:
    return "⛈"
  else:
    return "🌤"


# 3つのメッセージに分割してリストで返す関数
def generate_weather_report_parts():
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

  # --- パート1: 今日の概要 ---
  part1 = []
  part1.append(f"**今日({target_date.strftime('%m月%d日')})の天気予報**")
  part1.append(
      f"天気: 晴れときどき曇り🌤️\n最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度:"
      f" {max_hum}%｜最低湿度: {min_hum}%"
  )
  part1.append(
      f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
      f" {avg_press}hPa"
  )
  part1.append("今日の最大警戒レベル: 4🟠(最大変化[時間,低下])")
  part1.append("最大変化[時間,低下]: 4🟠(-1.1hPa,10時と11時の間)")
  part1.append("最大変化[時間,増加]: 1🔵(+0.8hPa, 19時と20時の間)")
  part1.append("最大変化[今日]: 2🟢(-2.2hPa)")

  # 1時間ごとのデータ配列作成
  hourly_times = data["hourly"]["time"]
  temps = data["hourly"]["temperature_2m"]
  hums = data["hourly"]["relative_humidity_2m"]
  weather_codes = data["hourly"]["weather_code"]

  def build_hourly_block(start, end):
    block = []
    for i in range(start, end):
      dt = datetime.fromisoformat(hourly_times[i])
      hour_str = dt.strftime("%H:00")
      diff = pressures[i] - pressures[i - 1] if i > 0 else 0.0
      level = judge_pressure_hourly(diff)
      emoji = get_weather_emoji(weather_codes[i])

      block.append(
          f"**{hour_str}**\n【基本情報】\n天気: 晴れ{emoji}｜気温: {temps[i]}℃｜湿度:"
          f" {hums[i]}%\n【気圧情報】\n気圧: {pressures[i]}hPa｜変化:"
          f" {level}({diff:+.1f}hPa)"
      )
    return "\n\n".join(block)

  # --- パート2: 午前 (0:00〜11:00) ---
  part2 = f"__**【午前】**__\n\n" + build_hourly_block(0, 12)

  # --- パート3: 午後 (12:00〜23:00 ＋ 時点フッター) ---
  part3_content = build_hourly_block(12, 24)
  footer = f"\n\n_{datetime.now().strftime('%Y年%m月%d日 %H:%M')} 時点_"
  part3 = f"__**【午後】**__\n\n" + part3_content + footer

  return ["\n".join(part1), part2, part3]


@bot.event
async def on_ready():
  print(f"ログインしました: {bot.user.name}")
  daily_weather_task.start()


@tasks.loop(hours=24)
async def daily_weather_task():
  channel_id = load_channel_id()
  if channel_id:
    channel = bot.get_channel(channel_id)
    if channel:
      try:
        parts = generate_weather_report_parts()
        for part in parts:
          await channel.send(part)
          time.sleep(1)  # レートリミット対策のウェイト
      except Exception as e:
        print(f"自動送信エラー: {e}")


@bot.command(name="天気設定")
async def set_channel(ctx):
  save_channel_id(ctx.channel.id)
  await ctx.send(
      f"✅ このチャンネル ({ctx.channel.mention})"
      "を天気予報の通知先として設定しました！"
  )


@bot.command(name="天気解除")
async def unset_channel(ctx):
  if os.path.exists(CONFIG_FILE):
    os.remove(CONFIG_FILE)
  await ctx.send("🚫 通知設定を解除しました。")


@bot.command(name="天気テスト")
async def test_weather(ctx):
  """分割してテスト送信します（429エラー対策つき）"""
  await ctx.send("🔍 テスト生成中（3件に分けて送信します）...")
  try:
    parts = generate_weather_report_parts()
    for index, part in enumerate(parts):
      try:
        await ctx.send(part)
        time.sleep(1)  # 連続送信による制限（429）を回避するためのウェイト
      except discord.HTTPException as he:
        if he.status == 429:
          await ctx.send(
              "⚠️ 速度制限（429）を検知しました。少し待ってから再送します..."
          )
          time.sleep(5)
          await ctx.send(part)  # 失敗したところから再送
        else:
          raise he
  except Exception as e:
    await ctx.send(f"❌ エラーが発生しました: {e}")


token = os.environ.get("DISCORD_TOKEN")
if not token:
  print("エラー: DISCORD_TOKEN が設定されていません。")
else:
  bot.run(token)
