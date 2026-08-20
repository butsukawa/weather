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


def generate_weather_embeds():
  # Open-Meteo API（3日分のデータを取得するため daily の日数等を拡張）
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

  daily_times = data["daily"]["time"]  # 今日・明日・あさって等の配列

  embeds = []

  # --- 1. 概要 Embed (オレンジ色・今日、明日、あさっての3日分を1つにまとめる) ---
  embed1 = discord.Embed(title="3日間天気予報 概要", color=discord.Color.orange())

  overview_texts = []
  # 0:今日, 1:明日, 2:あさって の3日分を作成
  day_labels = ["今日", "明日", "あさって"]

  for day_idx in range(min(3, len(daily_times))):
    d_str = daily_times[day_idx]
    d_dt = datetime.strptime(d_str, "%Y-%m-%d")
    label = day_labels[day_idx] if day_idx < len(day_labels) else f"{day_idx}日後"

    max_temp = data["daily"]["temperature_2m_max"][day_idx]
    min_temp = data["daily"]["temperature_2m_min"][day_idx]
    total_precip = data["daily"].get("precipitation_sum", [0])[day_idx]
    total_snow = data["daily"].get("snowfall_sum", [0])[day_idx]

    # その日の24時間のインデックス範囲 (day_idx * 24 から 24時間)
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
        round(sum(day_pressures) / len(day_pressures), 1)
        if day_pressures
        else 0
    )

    # 情報（真冬日、冬日、夏日、真夏日、猛暑日、酷暑日、熱帯夜）
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

    # 気圧変化
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
            f"{judge_pressure_hourly(diff)}({diff:+.1f}hPa,"
            f" {t_prev}と{t_curr}の間)"
        )
      if diff > max_rise_val:
        max_rise_val = diff
        max_rise_str = (
            f"{judge_pressure_hourly(diff)}({diff:+.1f}hPa,"
            f" {t_prev}と{t_curr}の間)"
        )

    daily_diff = (
        round(day_pressures[-1] - day_pressures[0], 1) if day_pressures else 0.0
    )
    max_daily_str = f"{judge_pressure_daily(daily_diff)}({daily_diff:+.1f}hPa)"
    lowest_diff = min(day_diffs) if day_diffs else 0.0
    max_alert_level = judge_pressure_hourly(lowest_diff)

    # ご指定のシンプルで見やすい3行形式
    block_text = (
        f"**{label}({d_dt.strftime('%m月%d日')})の天気予報**\n"
        f"天気: 晴れときどき曇り {main_weather_emoji}\n"
        f"最高気温: {max_temp}℃｜最低気温: {min_temp}℃｜最高湿度: {max_hum}%｜最低湿度:"
        f" {min_hum}%\n"
        f"降水量: {total_precip}mm｜降雪量: {total_snow}cm｜情報: {info_str}\n"
        f"最大気圧: {max_press}hPa｜最低気圧: {min_press}hPa｜平均気圧:"
        f" {avg_press}hPa\n\n"
        f"今日の最大警戒レベル: {max_alert_level[0]}\n"  # 絵文字の数字部分や記号
        f"最大変化[時間,低下]: {max_drop_str}\n"
        f"最大変化[時間,増加]: {max_rise_str}\n"
        f"最大変化[今日]: {max_daily_str}"
    )
    overview_texts.append(block_text)

  embed1.description = "\n\n".join(overview_texts)
  embed1.set_footer(text=f"{now_jst.strftime('%Y年%m月%d日 %H:%M')} 時点")
  embeds.append(embed1)

  # --- 2. 時間ごとの天気（今日の分をコードブロック等を用いて綺麗に3列に並べる） ---
  # 画像のようにきれいに整列させるため、Markdownのコードブロックを活用します
  def build_clean_columns(start_hour, end_hour):
    hours_data = []
    for idx in range(start_hour, end_hour):
      t_str = f"{idx:02d}:00"
      emoji = get_weather_emoji(data["hourly"]["weather_code"][idx])
      temp = data["hourly"]["temperature_2m"][idx]
      hum = data["hourly"]["relative_humidity_2m"][idx]
      precip = data["hourly"]["precipitation"][idx]
      wind_spd = data["hourly"]["wind_speed_10m"][idx]
      wind_d = get_wind_direction(data["hourly"]["wind_direction_10m"][idx])

      hours_data.append({
          "time": t_str,
          "emoji": emoji,
          "temp": f"{temp}°C",
          "hum": f"{hum}%",
          "precip": f"{precip}mm",
          "wind": f"{wind_d} {wind_spd}m/s",
      })

    # 3つずつ並べたテーブル風レイアウトを作成
    lines = []
    for i in range(0, len(hours_data), 3):
      chunk = hours_data[i : i + 3]
      # 1行目: 時間と絵文字
      line_t = " | ".join(f"{c['time']} {c['emoji']}" for c in chunk)
      # 2行目: 気温・湿度
      line_th = " | ".join(f"気温:{c['temp']} 湿:{c['hum']}" for c in chunk)
      # 3行目: 降水量
      line_p = " | ".join(f"降水:{c['precip']}" for c in chunk)
      # 4行目: 風
      line_w = " | ".join(f"風:{c['wind']}" for c in chunk)

      lines.append(f"{line_t}\n{line_th}\n{line_p}\n{line_w}")
      lines.append("-" * 32)  ミア仕切り

    return "```\n" + "\n".join(lines) + "\n```"

  # 午前 Embed (緑)
  embed2 = discord.Embed(title="【午前】(00:00 - 11:00)", color=discord.Color.green())
  embed2.description = build_clean_columns(0, 12)
  embeds.append(embed2)

  # 午後 Embed (青)
  embed3 = discord.Embed(title="【午後】(12:00 - 23:00)", color=discord.Color.blue())
  embed3.description = build_clean_columns(12, 24)
  embeds.append(embed3)

  return embeds


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
  await ctx.send("🔍 テスト生成中（3日分概要＆3列レイアウト）...")
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
