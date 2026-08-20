from datetime import datetime, timezone, timedelta
import os
import threading
import time
from flask import Flask
import discord
from discord.ext import commands, tasks
import requests

# --- Flaskサーバー設定（Renderのポート対策） ---
app = Flask(__name__)


@app.route("/")
def home():
  return "Your service is live! Discord Bot is running."


def run_flask():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)


# --- Discordボット設定 ---
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


# ご指定のWMOコード等に基づく完全な天気絵文字判定
def get_weather_emoji(wmo_code, rain=0, show_rain=False):
  # 0: 快晴
  if wmo_code == 0:
    return "☀️"
  # 1, 2: 晴れ / 晴れ時々曇り等
  elif wmo_code in [1, 2]:
    return "🌤"
  # 3: 曇り
  elif wmo_code == 3:
    return "🌥"
  # 45, 48: 霧
  elif wmo_code in [45, 48]:
    return "🌫"
  # 51, 53, 55, 56, 57: 霧雨など
  elif wmo_code in [51, 53, 55, 56, 57]:
    return "🌧"
  # 61, 63, 65, 66, 67: 雨・大雨
  elif wmo_code in [61, 63, 65, 66, 67]:
    if wmo_code == 65:  # 激しい雨
      return "☔"
    return "🌧"
  # 71, 73, 75, 77: 雪・みぞれ
  elif wmo_code in [71, 73, 75, 77]:
    if wmo_code == 77:
      return "🌨"  # みぞれ
    return "❄"  # 雪
  # 80, 81, 82: にわか雨（晴れときどき雨、雨など）
  elif wmo_code in [80, 81, 82]:
    return "🌦"
  # 85, 86: 雪の陣
  elif wmo_code in [85, 86]:
    return "❄"
  # 95, 96, 99: 雷雨・雷雲
  elif wmo_code in [95, 96, 99]:
    return "⛈"
  else:
    return "🌤"


# 1時間ごとの気圧変化レベル判定
def judge_pressure_hourly(diff):
  if diff >= 2.0:
    return "🟣"  # 上昇注意 (+2含み〜)
  elif diff > -0.3:
    return "1🔵"  # +2含まない〜-0.3
  elif diff >= -0.6:
    return "2🟢"  # -0.3含まない〜-0.6
  elif diff >= -0.9:
    return "3🟡"  # -0.6含まない〜-0.9
  elif diff >= -1.3:
    return "4🟠"  # -0.9含まない〜-1.3
  else:
    return "5🔴"  # -1.3含まない〜-∞


# 日ごとの気圧変化レベル判定
def judge_pressure_daily(diff):
  if diff >= 7.0:
    return "🟣"
  elif diff > -2.0:
    return "1🔵"
  elif diff >= -5.0:
    return "2🟢"
  elif diff >= -8.0:
    return "3🟡"
  elif diff >= -12.0:
    return "4🟠"
  else:
    return "5🔴"


def generate_weather_embeds():
  url = "https://api.open-meteo.com/v1/forecast?latitude=35.3&longitude=139.375&current=temperature_2m,relative_humidity_2m,pressure_msl&hourly=temperature_2m,relative_humidity_2m,weather_code,pressure_msl&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=Asia/Tokyo"
  res = requests.get(url)
  data = res.json()

  jst = timezone(timedelta(hours=9))
  now_jst = datetime.now(jst)

  target_date_str = data["daily"]["time"][0]
  target_date = datetime.strptime(target_date_str, "%Y-%m-%d")

  max_temp = data["daily"]["temperature_2m_max"][0]
  min_temp = data["daily"]["temperature_2m_min"][0]
  hourly_temps = data["hourly"]["temperature_2m"][0:24]
  hourly_hums = data["hourly"]["relative_humidity_2m"][0:24]
  pressures = data["hourly"]["pressure_msl"][0:24]
  weather_codes = data["hourly"]["weather_code"][0:24]

  max_hum = max(hourly_hums)
  min_hum = min(hourly_hums)
  max_press = max(pressures)
  min_press = min(pressures)
  avg_press = round(sum(pressures) / len(pressures), 1)

  # 変化量の計算と最大値の抽出
  hourly_diffs = []
  for i in range(24):
    if i == 0:
      hourly_diffs.append(0.0)
    else:
      hourly_diffs.append(round(pressures[i] - pressures[i - 1], 1))

  # 時間ごとの低下・増加の最大値を探す
  max_drop_val = 0.0
  max_drop_str = "なし"
  max_rise_val = 0.0
  max_rise_str = "なし"

  for i in range(1, 24):
    diff = hourly_diffs[i]
    t_prev = f"{i-1:02d}:00"
    t_curr = f"{i:02d}:00"
    if diff < max_drop_val:
      max_drop_val = diff
      level = judge_pressure_hourly(diff)
      max_drop_str = f"{level}({diff:+.1f}hPa, {t_prev}と{t_curr}の間)"

    if diff > max_rise_val:
      max_rise_val = diff
      level = judge_pressure_hourly(diff)
      max_rise_str = f"{level}({diff:+.1f}hPa, {t_prev}と{t_curr}の間)"

  # 今日全体の変化（例として当日の最高気圧と最低気圧の差、または初めと終わりの差など）
  daily_diff = round(pressures[-1] - pressures[0], 1)
  daily_level = judge_pressure_daily(daily_diff)
  max_daily_str = f"{daily_level}({daily_diff:+.1f}hPa)"

  # 今日の最大警戒レベル（低下・増加の中で最も深刻なレベルを抽出）
  # レベルの数値（1〜5、🟣など）を判定
  max_alert_name = "1🔵"
  # 簡易的に一番低い値（マイナスが大きい）を警戒レベルとする
  lowest_diff = min(hourly_diffs)
  max_alert_level = judge_pressure_hourly(lowest_diff)

  # --- 1. 概要 Embed (オレンジ) ---
  embed1 = discord.Embed(
      title=f"今日({target_date.strftime('%m月%d日')})の天気予報",
      color=discord.Color.orange(),
  )
  main_weather_emoji = get_weather_emoji(data["daily"]["weather_code"][0])
  overview_desc = (
      f"天気: 晴れときどき曇り {main_weather_emoji}\n"
      f"最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度: {max_hum}%｜最低湿度:"
      f" {min_hum}%\n"
      f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
      f" {avg_press}hPa\n"
      f"今日の最大警戒レベル: {max_alert_level}(最大変化[時間,低下])\n"
      f"最大変化[時間,低下]: {max_drop_str}\n"
      f"最大変化[時間,増加]: {max_rise_str}\n"
      f"最大変化[今日]: {max_daily_str}"
  )
  embed1.description = overview_desc

  # --- 2. 午前 Embed (緑：00:00〜11:00) ---
  embed2 = discord.Embed(title="【午前】", color=discord.Color.green())
  am_text = []
  for i in range(0, 12):
    t_str = f"{i:02d}:00"
    emoji = get_weather_emoji(weather_codes[i])
    diff = hourly_diffs[i]
    level = judge_pressure_hourly(diff)
    block = (
        f"**{t_str}**\n【基本情報】\n天気: 晴れ {emoji}｜気温:"
        f" {hourly_temps[i]}℃｜湿度: {hourly_hums[i]}%\n【気圧情報】\n気圧:"
        f" {pressures[i]}hPa｜変化: {level}({diff:+.1f}hPa)"
    )
    am_text.append(block)
  embed2.description = "\n\n".join(am_text)

  # --- 3. 午後 Embed (青：12:00〜23:00) ---
  embed3 = discord.Embed(title="【午後】", color=discord.Color.blue())
  pm_text = []
  for i in range(12, 24):
    t_str = f"{i:02d}:00"
    emoji = get_weather_emoji(weather_codes[i])
    diff = hourly_diffs[i]
    level = judge_pressure_hourly(diff)
    block = (
        f"**{t_str}**\n【基本情報】\n天気: 晴れ {emoji}｜気温:"
        f" {hourly_temps[i]}℃｜湿度: {hourly_hums[i]}%\n【気圧情報】\n気圧:"
        f" {pressures[i]}hPa｜変化: {level}({diff:+.1f}hPa)"
    )
    pm_text.append(block)

  footer_str = f"\n\n_{now_jst.strftime('%Y年%m月%d日 %H:%M')} 時点_"
  embed3.description = "\n\n".join(pm_text) + footer_str

  return [embed1, embed2, embed3]


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
        embeds = generate_weather_embeds()
        for emb in embeds:
          await channel.send(embed=emb)
          time.sleep(1)
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
  await ctx.send(
      "🔍 テスト生成中（Embed形式で3件に分けて送信します）..."
  )
  try:
    embeds = generate_weather_embeds()
    for emb in embeds:
      try:
        await ctx.send(embed=emb)
        time.sleep(1)
      except discord.HTTPException as he:
        if he.status == 429:
          await ctx.send(
              "⚠️ 速度制限（429）を検知しました。少し待ってから再送します..."
          )
          time.sleep(5)
          await ctx.send(embed=emb)
        else:
          raise he
  except Exception as e:
    await ctx.send(f"❌ エラーが発生しました: {e}")


if __name__ == "__main__":
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()

  token = os.environ.get("DISCORD_TOKEN")
  if not token:
    print("エラー: DISCORD_TOKEN が設定されていません。")
  else:
    bot.run(token)
