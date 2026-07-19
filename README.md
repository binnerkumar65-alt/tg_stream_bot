# Telegram Video → 24-Hour Streaming Link Bot

## Ye kya karta hai
- Bot ko video forward karo → 24 ghante valid streaming link milta hai.
- Video seedha Telegram se live stream hoti hai, server par save/download nahi hoti.
- VLC ya browser me link kholte hi video play ho jaati hai (download prompt nahi aata).

**Limitation (honestly):** Ye "casual download" rokta hai (Content-Disposition:
attachment nahi bhejta). Lekin agar koi user determined ho aur tools (yt-dlp,
browser dev-tools) use kare, to HTTP stream ko capture kiya ja sakta hai. Bina
DRM ke 100% "un-downloadable" video possible nahi hai — ye sirf simple/casual
misuse ko rokta hai, hard guarantee nahi.

---

## Step 1: Telegram API ID/Hash aur Bot Token lena

1. https://my.telegram.org par login karke **API_ID** aur **API_HASH** le lo
   (aapke paas already hai, waise use kar lena).
2. **@BotFather** se `/newbot` karke ek **BOT_TOKEN** bana lo.

## Step 2: GitHub par code push karna

```bash
git init
git add .
git commit -m "telegram video streaming proxy"
git branch -M main
git remote add origin https://github.com/<aapka-username>/tg-stream-bot.git
git push -u origin main
```

## Step 3: Render par deploy karna

1. https://render.com par login/signup karo (GitHub se sign in kar sakte ho).
2. **New +** → **Web Service** → apna GitHub repo select karo.
3. Render `render.yaml` ko detect kar lega (Blueprint). Agar manually bana rahe ho:
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free
4. Environment variables set karo (Render dashboard → Environment):
   - `API_ID` → aapka Telethon API ID
   - `API_HASH` → aapka Telethon API hash
   - `BOT_TOKEN` → BotFather se mila token
   - `BASE_URL` → deploy hone ke baad Render jo URL dega, wahi daal do
     (e.g. `https://tg-stream-bot.onrender.com`) — pehli deploy ke baad
     URL milega, phir isse update karke redeploy kar dena.
   - `LINK_TTL_SECONDS` → `86400` (24 ghante; already render.yaml me set hai)
5. Deploy karo. Logs me dekho bot successfully connect ho raha hai.

## Step 4: Test karna

1. Apne bot ko Telegram par koi video forward karo.
2. Bot reply karega ek link ke saath, jaise:
   `https://tg-stream-bot.onrender.com/stream/<token>`
3. Us link ko browser ya VLC me kholo — video play ho jaayegi.
4. 24 ghante baad wahi link expire ho jaayega (410 error dega).

---

## Important notes

- **Render free plan** thodi der inactivity ke baad "sleep" ho jaata hai —
  pehli request par 20-30 second ka cold start lag sakta hai.
- **SQLite (`links.db`)** container ke local disk par hai. Agar Render free
  service restart/redeploy hoti hai to ye file reset ho sakti hai (purane
  links invalid ho jaayenge, naye messages se naye links bana lena).
- Bot sirf video files handle karta hai; baaki files ignore/reject hoti hain.
- Agar streaming slow lage, `cryptg` package (requirements me already hai)
  Telethon ke downloads ko fast karta hai.

## Files

- `main.py` — Telethon bot + FastAPI streaming server (ek hi process me).
- `requirements.txt` — Python dependencies.
- `render.yaml` — Render Blueprint config (auto-deploy setup).
- `.gitignore` — session/db files ko git me jaane se rokta hai.
