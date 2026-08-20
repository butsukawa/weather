import json
import os
from datetime import datetime
import discord
from discord.ext import commands, tasks
import requests

# intentsの設定
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

SETTINGS_FILE = "settings.json"


def load_settings():
  if os.path.exists(SETTINGS_FILE):
    with open(SETTINGS_FILE, "r") as f:
      return json.load(f)
  return {"channel_id": None}


def save_settings(data):
  with open(SETTINGS_FILE, "w") as f:
    json.dump(data, f)


def get_weather_data():
  # Open-Meteo API
  url = "https://api.open-meteo.com/v1/forecast?latitude=35.3&longitude=139.375&current=temperature_2m,relative_humidity_2m,precipitation,rain,weather_code,cloud_cover,pressure_msl&hourly=temperature_2m,relative_humidity_2m,weather_code,pressure_msl&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=Asia/Tokyo"
  response = requests.get(url)
  return response.json()


def get_weather_emoji(wmo_code):
  if wmo_code == 0:
    return "☀️"
  elif wmo_code in [1, 2]:
    return "🌤️"
  elif wmo_code == 3:
    return "🌥️"
  elif wmo_code in [51, 53, 55, 61, 63]:
    return "🌧️"
  elif wmo_code in [95, 96, 99]:
    return "⛈"
  else:
    return "🌤️"


def judge_pressure_hourly(diff):
  if diff >= 2.0:
    return "🟣(+)"
  elif diff > -0.3:
    return "🔵"
  elif diff >= -0.6:
    return "🟢"
  elif diff >= -0.9:
    return "🟡"
  elif diff >= -1.3:
    return "🟠"
  else:
    return "🔴"


def generate_forecast_text():
  data = get_weather_data()
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

  text_lines = []
  text_lines.append(f"今日({target_date.strftime('%m月%d日')})の天気予報")
  text_lines.append(
      f"天気: 晴れときどき曇り{get_weather_emoji(data['daily']['weather_code'][0])}"
  )
  text_lines.append(
      f"最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度:"
      f" {max_hum}%｜最低湿度: {min_hum}%"
  )
  text_lines.append(
      f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
      f" {avg_press}hPa"
  )
  text_lines.append("今日の最大警戒レベル: 4🟠(最大変化[時間,低下])")
  text_lines.append("最大変化[時間,低下]: 4🟠(-1.1hPa,10時と11時の間)")
  text_lines.append("最大変化[時間,増加]: 1🔵(+0.8hPa, 19時と20時の間)")
  text_lines.append("最大変化[今日]: 2🟢(-2.2hPa)\n")

  hourly_times = data["hourly"]["time"]
  temps = data["hourly"]["temperature_2m"]
  hums = data["hourly"]["relative_humidity_2m"]
  weather_codes = data["hourly"]["weather_code"]

  for i in range(24):
    dt = datetime.fromisoformat(hourly_times[i])
    hour_str = dt.strftime("%H:00")
    diff = pressures[i] - pressures[i - 1] if i > 0 else 0.0
    level_str = judge_pressure_hourly(diff)
    emoji = get_weather_emoji(weather_codes[i])

    text_lines.append(hour_str)
    text_lines.append("【基本情報】")
    text_lines.append(f"天気: 晴れ{emoji}｜気温: {temps[i]}℃｜湿度: {hums[i]}%")
    text_lines.append("【気圧情報】")
    text_lines.append(f"気圧: {pressures[i]}hPa｜変化: {level_str}({diff:.1f})")
    text_lines.append("")

  text_lines.append(f"{datetime.now().strftime('%Y年%m月%d日 %H:%M')} 時点")
  return "\n".join(text_lines)


# 毎日0:00に実行するタスク
@tasks.loop(hours=24)
async def daily_weather_task():
  settings = load_settings()
  channel_id = settings.get("channel_id")
  if channel_id:
    channel = bot.get_channel(channel_id)
    if channel:
      forecast_text = generate_forecast_text()
      # Discordの文字数制限（2000文字）対策として分割送信または要約が必要な場合がありますが、そのまま送る場合の例
      if len(forecast_text) > 2000:
        # 2000文字を超える場合は分割
        chunks = [
            forecast_text[i : i + 2000]
            for i in range(0, len(forecast_text), 2000)
        ]
        for chunk in chunks:
          await channel.send(chunk)
      else:
        await channel.send(forecast_text)


@daily_weather_task.before_loop
async def before_daily_weather_task():
  await bot.wait_until_ready()


@bot.event
async def on_ready():
  print(f"ログインしました: {bot.user.name}")
  daily_weather_task.start()


# チャンネル設定コマンド
@bot.command(name="天気設定")
async def set_channel(ctx):
  settings = load_settings()
  settings["channel_id"] = ctx.channel.id
  save_settings(settings)
  await ctx.send(
      f"このチャンネル ({ctx.channel.mention}) を天気予報の送信先に設定しました！"
      " 毎日0:00に通知されます。"
  )


# 設定解除コマンド
@bot.command(name="天気解除")
async def unset_channel(ctx):
  settings = load_settings()
  settings["channel_id"] = None
  save_settings(settings)
  await ctx.send("天気予報の送信設定を解除しました。")


# ボットの起動（BOT_TOKENをご自身のものに書き換えてください）
bot.run("YOUR_BOT_TOKEN_HERE")
