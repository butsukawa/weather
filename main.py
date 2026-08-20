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


# --- Discordボット設定（スラッシュコマンド対応） ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

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


# 単日の概要テキストを生成する共通関数
def create_day_overview_text(data, day_idx, label_prefix=""):
  d_str = data["daily"]["time"][day_idx]
  d_dt = datetime.strptime(d_str, "%Y-%m-%d")
  label = (
      label_prefix
      if label_prefix
      else ["今日", "明日", "あさって", "3日後"][day_idx]
      if day_idx < 4
      else f"{day_idx}日後"
  )

  max_temp = data["daily"]["temperature_2m_max"][day_idx]
  min_temp = data["daily"]["temperature_2m_min"][day_idx]
  total_precip = data["daily"].get("precipitation_sum", [0])[day_idx]
  total_snow = data["daily"].get("snowfall_sum", [0])[day_idx]

  start_h = day_idx * 24
  end_h = start_h + 24
  day_hums = data["hourly"]["relative_humidity_2m"][start_h:end_h]
  day_pressures = data["hourly"]["pressure_msl"][start_h:end_h]
  day_codes = data["hourly"]["weather_code"][start_h:end_h]

  max_hum = max(day_hums) if day_hums else 0
  min_hum = min(day_hums) if day_hums else 0
  max_press = max(day_pressures) if day_pressures else 0
  min_press = min(day_pressures) if day_pressures else 0
  avg_press = (
      round(sum(day_pressures) / len(day_pressures), 1) if day_pressures else 0
  )

  info_tags = []
  if max_temp >= 35.0:
    info_tags.append("猛暑日")
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

  main_weather_emoji = get_weather_emoji(day_codes[0] if day_codes else 0)

  day_diffs = [
      0.0 if i == 0 else round(day_pressures[i] - day_pressures[i - 1], 1)
      for i in range(len(day_pressures))
  ]
  max_drop_val, max_drop_str = 0.0, "なし"
  max_rise_val, max_rise_str = 0.0, "なし"

  for i in range(1, len(day_diffs)):
    diff = day_diffs[i]
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

  daily_diff = (
      round(day_pressures[-1] - day_pressures[0], 1) if day_pressures else 0.0
  )
  max_daily_str = f"{judge_pressure_daily(daily_diff)}({daily_diff:+.1f}hPa)"
  lowest_diff = min(day_diffs) if day_diffs else 0.0
  max_alert_level = judge_pressure_hourly(lowest_diff)

  block_text = (
      f"**{label}({d_dt.strftime('%m月%d日')})の天気予報**\n"
      f"天気: 晴れときどき曇り {main_weather_emoji}\n"
      f"最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度: {max_hum}%｜最低湿度:"
      f" {min_hum}%\n"
      f"降水量: {total_precip}mm｜降雪量: {total_snow}cm｜情報: {info_str}\n"
      f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
      f" {avg_press}hPa\n\n"
      f"今日の最大警戒レベル: {max_alert_level[0]}\n"
      f"最大変化[時間,低下]: {max_drop_str}\n"
      f"最大変化[時間,増加]: {max_rise_str}\n"
      f"最大変化[今日]: {max_daily_str}"
  )
  return block_text


# --- 1. 4日分の天気予報生成（毎日6:00配信） ---
def generate_weather_forecast_embeds():
  url = (
      "https://api.open-meteo.com/v1/forecast?latitude=35.3&longitude=139.375&"
      "hourly=temperature_2m,relative_humidity_2m,weather_code,pressure_msl&"
      "daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum&"
      "timezone=Asia/Tokyo"
  )
  res = requests.get(url)
  data = res.json()

  jst = timezone(timedelta(hours=9))
  now_jst = datetime.now(jst)

  embeds = []
  embed1 = discord.Embed(title="4日間天気予報 概要", color=discord.Color.orange())

  overview_texts = []
  # 4日分 (0〜3)
  for day_idx in range(4):
    overview_texts.append(create_day_overview_text(data, day_idx))

  embed1.description = "\n\n".join(overview_texts)
  embed1.set_footer(text=f"{now_jst.strftime('%Y年%m月%d日 %H:%M')} 時点")
  embeds.append(embed1)
  return embeds


# --- 2. 今日の1時間予報生成（毎日0:00配信：今日の概要 ＋ 午前・午後詳細） ---
def generate_today_hourly_embeds():
  url = (
      "https://api.open-meteo.com/v1/forecast?latitude=35.3&longitude=139.375&"
      "hourly=temperature_2m,relative_humidity_2m,weather_code,pressure_msl,"
      "wind_speed_10m,wind_direction_10m,precipitation&"
      "daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum&"
      "timezone=Asia/Tokyo"
  )
  res = requests.get(url)
  data = res.json()

  jst = timezone(timedelta(hours=9))
  now_jst = datetime.now(jst)

  embeds = []

  # ① 今日の天気概要 Embed (オレンジ)
  embed_overview = discord.Embed(
      title="今日の天気予報 概要", color=discord.Color.orange()
  )
  embed_overview.description = create_day_overview_text(data, 0, label_prefix="今日")
  embeds.append(embed_overview)

  pressures = data["hourly"]["pressure_msl"][0:24]
  hourly_diffs = [
      0.0 if i == 0 else round(pressures[i] - pressures[i - 1], 1)
      for i in range(24)
  ]

  def create_hourly_embed(title, color, start_h, end_h):
    emb = discord.Embed(title=title, color=color)
    for idx in range(start_h, end_h):
      t_str = f"{idx:02d}:00"
      emoji = get_weather_emoji(data["hourly"]["weather_code"][idx])
      temp = data["hourly"]["temperature_2m"][idx]
      hum = data["hourly"]["relative_humidity_2m"][idx]
      precip = data["hourly"]["precipitation"][idx]
      wind_spd = data["hourly"]["wind_speed_10m"][idx]
      wind_d = get_wind_direction(data["hourly"]["wind_direction_10m"][idx])
      diff = hourly_diffs[idx]
      level = judge_pressure_hourly(diff)

      field_value = (
          f"天気: {emoji}\n"
          f"気温: {temp}°C\n"
          f"湿度: {hum}%\n"
          f"降水: {precip}mm\n"
          f"風向風速: {wind_d} {wind_spd}m/s\n"
          f"気圧変化: {level}({diff:+.1f}hPa)"
      )
      emb.add_field(name=t_str, value=field_value, inline=True)
    return emb

  # ② 午前 (0:00〜11:00) - 緑
  embed_am = create_hourly_embed(
      "【今日の1時間予報】午前 (00:00 - 11:00)", discord.Color.green(), 0, 12
  )
  embeds.append(embed_am)

  # ③ 午後 (12:00〜23:00) - 青
  embed_pm = create_hourly_embed(
      "【今日の1時間予報】午後 (12:00 - 23:00)", discord.Color.blue(), 12, 24
  )
  embed_pm.set_footer(text=f"{now_jst.strftime('%Y年%m月%d日 %H:%M')} 時点")
  embeds.append(embed_pm)

  return embeds


@bot.event
async def on_ready():
  print(f"ログインしました: {bot.user.name}")
  daily_weather_task.start()
  hourly_weather_task.start()
  try:
    await bot.tree.sync()
    print("スラッシュコマンドを同期しました。")
  except Exception as e:
    print(f"同期エラー: {e}")


# 毎日6:00に4日分の天気予報を配信
@tasks.loop(time=datetime.strptime("06:00", "%H:%M").time())
async def daily_weather_task():
  channel_id = load_channel_id()
  if channel_id:
    channel = bot.get_channel(channel_id)
    if channel:
      try:
        embeds = generate_weather_forecast_embeds()
        for emb in embeds:
          await channel.send(embed=emb)
          time.sleep(1)
      except Exception as e:
        print(f"自動送信エラー(6:00): {e}")


# 毎日0:00に今日の1時間予報を配信
@tasks.loop(time=datetime.strptime("00:00", "%H:%M").time())
async def hourly_weather_task():
  channel_id = load_channel_id()
  if channel_id:
    channel = bot.get_channel(channel_id)
    if channel:
      try:
        embeds = generate_today_hourly_embeds()
        for emb in embeds:
          await channel.send(embed=emb)
          time.sleep(1)
      except Exception as e:
        print(f"自動送信エラー(0:00): {e}")


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


# スラッシュコマンド: /test_yohou (4日分の天気予報)
@bot.tree.command(
    name="test_yohou", description="4日分の天気予報をテスト送信します"
)
async def slash_test_yohou(interaction: discord.Interaction):
  await interaction.response.send_message(
      "🔍 4日分の天気予報を生成中...", ephemeral=True
  )
  try:
    embeds = generate_weather_forecast_embeds()
    for emb in embeds:
      await interaction.channel.send(embed=emb)
      time.sleep(1)
  except Exception as e:
    await interaction.followup.send(
        f"❌ エラーが発生しました: {e}", ephemeral=True
    )


# スラッシュコマンド: /test_today (今日の1時間予報 ＋ 概要)
@bot.tree.command(
    name="test_today", description="今日の1時間予報をテスト送信します"
)
async def slash_test_today(interaction: discord.Interaction):
  await interaction.response.send_message(
      "🔍 今日の1時間予報を生成中...", ephemeral=True
  )
  try:
    embeds = generate_today_hourly_embeds()
    for emb in embeds:
      await interaction.channel.send(embed=emb)
      time.sleep(1)
  except Exception as e:
    await interaction.followup.send(
        f"❌ エラーが発生しました: {e}", ephemeral=True
    )


if __name__ == "__main__":
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()

  token = os.environ.get("DISCORD_TOKEN")
  if not token:
    print("エラー: DISCORD_TOKEN が設定されていません。")
  else:
    bot.run(token)
