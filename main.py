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


# 風向の度数を方角（16方位など簡易）に変換
def get_wind_direction(deg):
  if deg is None:
    return "不明"
  dirs = [
      "北",
      "北北東",
      "北東",
      "東北東",
      "東",
      "東南東",
      "南東",
      "南南東",
      "南",
      "南南西",
      "南西",
      "西南西",
      "西",
      "西北西",
      "北西",
      "北北西",
  ]
  idx = int((deg + 11.25) / 22.5) % 16
  return dirs[idx]


# 天気絵文字判定
def get_weather_emoji(wmo_code):
  if wmo_code == 0:
    return "☀️"
  elif wmo_code in [1, 2]:
    return "🌤"
  elif wmo_code == 3:
    return "🌥"
  elif wmo_code in [45, 48]:
    return "🌫"
  elif wmo_code in [51, 53, 55, 56, 57]:
    return "🌧"
  elif wmo_code in [61, 63, 65, 66, 67]:
    return "☔" if wmo_code == 65 else "🌧"
  elif wmo_code in [71, 73, 75, 77]:
    return "🌨" if wmo_code == 77 else "❄"
  elif wmo_code in [80, 81, 82]:
    return "🌦"
  elif wmo_code in [85, 86]:
    return "❄"
  elif wmo_code in [95, 96, 99]:
    return "⛈"
  else:
    return "🌤"


# 気圧変化レベル判定
def judge_pressure_hourly(diff):
  if diff >= 2.0:
    return "🟣"
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
  # Open-Meteo API（風速・風向・雲量・降水量・降雪量を追加取得）
  url = (
      "https://api.open-meteo.com/v1/forecast?latitude=35.3&longitude=139.375&"
      "current=temperature_2m,relative_humidity_2m,pressure_msl&"
      "hourly=temperature_2m,relative_humidity_2m,weather_code,pressure_msl,"
      "wind_speed_10m,wind_direction_10m,cloud_cover,precipitation,snowfall&"
      "daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum&"
      "timezone=Asia/Tokyo"
  )
  res = requests.get(url)
  data = res.json()

  jst = timezone(timedelta(hours=9))
  now_jst = datetime.now(jst)

  target_date_str = data["daily"]["time"][0]
  target_date = datetime.strptime(target_date_str, "%Y-%m-%d")

  max_temp = data["daily"]["temperature_2m_max"][0]
  min_temp = data["daily"]["temperature_2m_min"][0]
  total_precip = data["daily"].get("precipitation_sum", [0])[0]
  total_snow = data["daily"].get("snowfall_sum", [0])[0]

  hourly_temps = data["hourly"]["temperature_2m"][0:24]
  hourly_hums = data["hourly"]["relative_humidity_2m"][0:24]
  pressures = data["hourly"]["pressure_msl"][0:24]
  weather_codes = data["hourly"]["weather_code"][0:24]
  wind_speeds = data["hourly"]["wind_speed_10m"][0:24]
  wind_dirs = data["hourly"]["wind_direction_10m"][0:24]
  cloud_covers = data["hourly"]["cloud_cover"][0:24]
  precips = data["hourly"]["precipitation"][0:24]

  max_hum = max(hourly_hums)
  min_hum = min(hourly_hums)
  max_press = max(pressures)
  min_press = min(pressures)
  avg_press = round(sum(pressures) / len(pressures), 1)

  # 情報（真冬日、冬日、夏日、真夏日、猛暑日、酷暑日、熱帯夜）の判定
  info_tags = []
  if max_temp >= 35.0:
    info_tags.append("猛暑日")  # または酷暑日
  elif max_temp >= 30.0:
    info_tags.append("真夏日")
  elif max_temp >= 25.0:
    info_tags.append("夏日")

  if min_temp < 0.0:
    if max_temp < 0.0:
      info_tags.append("真冬日")
    else:
      info_tags.append("冬日")

  if min_temp >= 25.0:
    info_tags.append("熱帯夜")

  info_str = ", ".join(info_tags) if info_tags else "特になし"

  # 気圧変化量計算
  hourly_diffs = [
      0.0 if i == 0 else round(pressures[i] - pressures[i - 1], 1)
      for i in range(24)
  ]

  max_drop_val, max_drop_str = 0.0, "なし"
  max_rise_val, max_rise_str = 0.0, "なし"

  for i in range(1, 24):
    diff = hourly_diffs[i]
    t_prev = f"{i-1:02d}:00"
    t_curr = f"{i:02d}:00"
    if diff < max_drop_val:
      max_drop_val = diff
      max_drop_str = (
          f"{judge_pressure_hourly(diff)}({diff:+.1f}hPa, {t_prev}と{t_curr}の間)"
      )
    if diff > max_rise_val:
      max_rise_val = diff
      max_rise_str = (
          f"{judge_pressure_hourly(diff)}({diff:+.1f}hPa, {t_prev}と{t_curr}の間)"
      )

  daily_diff = round(pressures[-1] - pressures[0], 1)
  max_daily_str = f"{judge_pressure_daily(daily_diff)}({daily_diff:+.1f}hPa)"
  lowest_diff = min(hourly_diffs)
  max_alert_level = judge_pressure_hourly(lowest_diff)

  main_weather_emoji = get_weather_emoji(data["daily"]["weather_code"][0])

  # --- 1. 概要 Embed (オレンジ) ---
  embed1 = discord.Embed(
      title=f"今日({target_date.strftime('%m月%d日')})の天気予報",
      color=discord.Color.orange(),
  )
  overview_desc = (
      f"天気: 晴れときどき曇り {main_weather_emoji}\n"
      f"最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度: {max_hum}%｜最低湿度:"
      f" {min_hum}%\n"
      f"降水量: {total_precip}mm｜降雪量: {total_snow}cm｜情報: {info_str}\n"
      f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
      f" {avg_press}hPa\n\n"
      f"今日の最大警戒レベル: {max_alert_level}\n"
      f"最大変化[時間,低下]: {max_drop_str}\n"
      f"最大変化[時間,増加]: {max_rise_str}\n"
      f"最大変化[今日]: {max_daily_str}"
  )
  embed1.description = overview_desc
  # 概要のフッターに時点を追加
  embed1.set_footer(text=f"{now_jst.strftime('%Y年%m月%d日 %H:%M')} 時点")

  # --- 共通のブロック生成関数（3列風レイアウト / 横並び） ---
  def build_grid_blocks(start_hour, end_hour):
    blocks = []
    # 3つずつグループ化して横並び風にする
    for i in range(start_hour, end_hour, 3):
      line_parts = []
      for j in range(3):
        idx = i + j
        if idx >= end_hour:
          break
        t_str = f"{idx:02d}:00"
        emoji = get_weather_emoji(weather_codes[idx])
        diff = hourly_diffs[idx]
        level = judge_pressure_hourly(diff)
        w_dir = get_wind_direction(wind_dirs[idx])

        # 1時間ごとのコンパクトなレイアウト
        cell = (
            f"**{t_str}** {emoji}\n"
            f"気温:{hourly_temps[idx]}℃ 湿:{hourly_hums[idx]}%\n"
            f"風:{wind_speeds[idx]}m {w_dir}\n"
            f"雲:{cloud_covers[idx]}% 降:{precips[idx]}mm\n"
            f"気圧:{pressures[idx]}hPa ({level}{diff:+.1f})"
        )
        line_parts.append(cell)
      # 3つをスペースや空白行で並べる
      blocks.append(" | ".join(line_parts))
    return "\n\n".join(blocks)

  # --- 2. 午前 Embed (緑：00:00〜11:00) ---
  embed2 = discord.Embed(title="【午前】", color=discord.Color.green())
  embed2.description = build_grid_blocks(0, 12)

  # --- 3. 午後 Embed (青：12:00〜23:00) ---
  embed3 = discord.Embed(title="【午後】", color=discord.Color.blue())
  embed3.description = build_grid_blocks(12, 24)

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
  await ctx.send("🔍 テスト生成中...")
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
