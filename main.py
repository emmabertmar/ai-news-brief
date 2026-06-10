"""
Daily AI news brief.
Pipeline: fetch headlines -> write a script -> turn it into a voice -> make cards.
"""

import os
import re
import html
from datetime import datetime, timezone, date
import feedparser
from zoneinfo import ZoneInfo

os.makedirs("output", exist_ok=True)

# When running on a schedule, only proceed at 07:00 Stockholm time.
# (GitHub fires two crons to cover summer/winter; this lets one through.)
if os.environ.get("GITHUB_ACTIONS") == "true":
    hour = datetime.now(ZoneInfo("Europe/Stockholm")).hour
    if hour != 7:
        print(f"Stockholm time is {hour}:00, not 07:00 — skipping.")
        raise SystemExit(0)
    

# ========================================
# Fetch fresh AI headlines from RSS feeds
# ========================================
FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+when:1d&hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.unite.ai/feed/",
]

def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "") # strip HTML tags like <p> and <a>
    return html.unescape(text).strip()        # decode entities, e.g. &#8217; -> '

stories = []
now = datetime.now(timezone.utc)

for url in FEEDS:
    feed = feedparser.parse(url)
    source = clean(feed.feed.get("title", url))
    for entry in feed.entries:
        # Skip anything older than 24h so the brief is only today's news.
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        
        if pub:
            published = datetime(*pub[:6], tzinfo=timezone.utc)
            age_hours = (now - published).total_seconds() / 3600
            if age_hours > 24:
                continue

        # Keep the headline and its summary
        title = clean(entry.title)
        summary = clean(entry.get("summary", ""))[:300]   # cap length so prompts stay small
        stories.append({"title": title, "summary": summary, "source": source})



# ========================================
# Write a spoken news script with Gemini
# ========================================
from google import genai

def write_script(stories):
    # Flatten the stories into one text block, one line per story, for the prompt
    headlines = "\n".join(
        f"- {s['title']}: {s['summary']}" for s in stories
    )

    # The prompt to the model
    prompt = f"""You are the anchor of a short daily AI news brief.
        Write a spoken news script of about 550 words, roughly four minutes when read aloud at a calm podcast pace, covering these AI stories.

        Rules:
        - Plain text only. No markdown, no headers, no bullet points.
        - Start with: "Good morning. Here's your AI brief."
        - Calm, clear broadcast tone. Group related stories.
        - Don't invent facts beyond what's given.
        - End with: "That's your AI brief. See you tomorrow."

        Stories:
        {headlines}"""

    # Reads the key set with `export GEMINI_API_KEY=...`
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return resp.text.strip()

script = write_script(stories)


# ========================================
# Convert the script into a voice recording (mp3)
# ========================================
import asyncio
import edge_tts

async def make_voice(text, path):
    communicate = edge_tts.Communicate(text, "en-US-JennyNeural", pitch="+8Hz") 
    await communicate.save(path)

asyncio.run(make_voice(script, "output/podcast.mp3"))
print("\nSaved output/podcast.mp3")



# ========================================
# Build the podcast feed (podcast.xml)
# ========================================
from email.utils import format_datetime

# Your public GitHub Pages address (note the trailing slash)
SITE_URL = "https://emmabertmar.github.io/ai-news-brief/"

def build_feed(audio_filename, title):
    now = datetime.now(timezone.utc)
    pub_date = format_datetime(now)         # podcast dates need this exact format
    audio_url = SITE_URL + audio_filename   # full public link to the mp3

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>My AI Daily Brief</title>
    <link>{SITE_URL}</link>
    <description>A daily 4-minute briefing on the latest AI news.</description>
    <language>en-us</language>
    <itunes:author>Emma</itunes:author>
    <item>
      <title>{title}</title>
      <description>Your AI brief for {now.strftime('%A, %B %d, %Y')}.</description>
      <pubDate>{pub_date}</pubDate>
      <enclosure url="{audio_url}" type="audio/mpeg" length="0"/>
      <guid>{audio_url}</guid>
    </item>
  </channel>
</rss>
"""
    with open("output/podcast.xml", "w") as f:
        f.write(feed)
    print("Saved output/podcast.xml")

build_feed("podcast.mp3", f"AI Brief — {date.today().isoformat()}")



# ========================================
# Build headline cards (images) for the video
# ========================================
from PIL import Image, ImageDraw, ImageFont

# Card colors (R, G, B)
BACKGROUND = (11, 31, 58)      
ACCENT = (200, 16, 46)  # red bar
WHITE = (240, 240, 245)
GREY = (150, 160, 175)

def load_font(size):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS
        "/System/Library/Fonts/Helvetica.ttc",                     # macOS fallback
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",    # Linux (GitHub later)
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def wrap(draw, text, font, max_width):
    # Break the headlines to fit the card
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def make_card(path, kicker, headline, footer):
    img = Image.new("RGB", (1280, 720), BACKGROUND)  # blank 1280x720 canvas
    draw = ImageDraw.Draw(img)                       # the "pen" we draw with

    draw.rectangle([0, 0, 14, 720], fill=ACCENT)     # left accent bar
    draw.text((80, 70), kicker.upper(), font=load_font(34), fill=WHITE)  # small top label

    # Headline: wrap into lines, then center the block vertically.
    font = load_font(60)
    lines = wrap(draw, headline, font, 1280 - 170)
    y = (720 - 74 * len(lines)) // 2
    for line in lines:
        draw.text((80, y), line, font=font, fill=WHITE)
        y += 74   # move down one line height

    draw.text((80, 650), footer, font=load_font(30), fill=GREY)  # source at the bottom
    img.save(path)

# Build one card per story, numbered so ffmpeg can play them in order.
os.makedirs("cards", exist_ok=True)   # create the folder (no error if it exists)

card_files = []
for i, story in enumerate(stories):
    filename = f"cards/card_{i:02d}.png"   # now inside the cards/ folder
    kicker = f"Story {i + 1} of {len(stories)}"
    make_card(filename, kicker, story["title"], story["source"])
    card_files.append(filename)

print(f"Saved {len(card_files)} cards in cards/")


# ========================================
# Combine cards + voice into a video
# ========================================
import subprocess

def audio_length(path):
    # Ask ffprobe (ships with ffmpeg) how many seconds the audio runs.
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())

def build_video(card_files, audio_path, out_path):
    seconds = audio_length(audio_path)
    per_card = seconds / len(card_files)   # show each card for an equal slice

    # Write the list file ffmpeg reads: each image + how long to display it
    with open("cards/list.txt", "w") as f:
        for card in card_files:
            name = os.path.basename(card)          # ffmpeg reads paths relative to the list file
            f.write(f"file '{name}'\n")
            f.write(f"duration {per_card:.3f}\n")
        # The concat format needs the last image repeated once more, or it drops it.
        f.write(f"file '{os.path.basename(card_files[-1])}'\n")

    # Run ffmpeg: images become the video track, voice.mp3 the audio track.
    subprocess.run([
        "ffmpeg", "-y",                            # -y: overwrite without asking
        "-f", "concat", "-safe", "0", "-i", "cards/list.txt",
        "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",  # standard video format players accept
        "-r", "25",                                # 25 frames per second
        "-c:a", "aac",                             # standard audio format
        "-shortest",                               # stop when the audio ends
        out_path,
    ], check=True)

build_video(card_files, "output/podcast.mp3", "output/latest.mp4")
print("Saved output/video.mp4")