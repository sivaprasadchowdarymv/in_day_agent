# Cloud deployment

## Option A – Streamlit Community Cloud (free, simplest)
1. Push the repo to GitHub. It already contains `requirements.txt`, `packages.txt` (installs `tesseract-ocr`) and `.streamlit/config.toml`.
2. Go to https://share.streamlit.io, sign in with GitHub and click **Create app → Deploy a public app from GitHub**.
3. Fill in the form:
   - **Repository:** `<you>/<repo>`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Open **Advanced settings** and set **Python version 3.12**. In **Secrets**, paste:
   ```toml
   BT_DATA_PROVIDER = "yahoo_nse"
   BT_CAPITAL = "30000"
   BT_LLM_PROVIDER = "none"        # or "gemini"
   GEMINI_API_KEY = ""             # optional
   # DATABASE_URL = "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"   # optional, see below
   ```
   The app copies these into environment variables at start-up. Never commit them to Git.
5. Click **Deploy**. The first build takes several minutes.
6. **Make the app private.** It holds your paper trades and settings. In the app's **Share** settings, restrict who can view it.

**If the build fails while installing apt packages** (Community Cloud's Debian sources sometimes break temporarily): empty `packages.txt`, push, and redeploy. Everything works except screenshot OCR, which shows "Tesseract not installed". Restore `tesseract-ocr` later.

## Option B – Docker (Hugging Face Spaces, Render, Railway, Fly.io, any VPS)
```bash
docker build -t bharattrade .
docker run -p 8501:8501 --env-file .env -v $(pwd)/storage:/app/storage bharattrade
```
- **Hugging Face Spaces:** create a Space with SDK **Docker**, push the repo, set `PORT=7860` and add your keys as Space secrets.
- **Render / Railway:** create a new web service from the repo. They detect the Dockerfile automatically. Add your environment variables there.
- On a VPS, put it behind a reverse proxy (Caddy or Nginx) with HTTPS and password protection.

## Keeping data between restarts
Cloud containers usually have **temporary disks**. The SQLite database (paper trades, journal, settings) and the candle cache in `storage/` are lost on restart or redeploy. You have two options:
- **Persistent:** use a free hosted PostgreSQL (for example Neon or Supabase) and set `DATABASE_URL=postgresql+psycopg://…?sslmode=require`. Tables are created automatically on first start.
- **Docker/VPS:** mount `storage/` as a volume, as shown above.

## Data sources in the cloud
- **NSE website endpoints often block cloud/data-centre IP addresses.** Run **Settings → Test all free data sources** after deploying. If NSE shows ❌, the app keeps working on Yahoo only, without bid/ask spread checks, NIFTY 500 breadth or circuit lists. It says this on every affected screen. Running locally from a home connection usually gives better NSE access.
- **Yahoo may rate-limit shared cloud IPs.** If scans fail, raise *Delay between requests* and lower *Max symbols per scan* in Settings.
- **Free tiers sleep when idle.** The first visit after a pause takes about 30 s to wake up.

## Security checklist
- Keys live only in the platform's secrets or environment variables. `.env` and `.streamlit/secrets.toml` are git-ignored.
- Restrict app visibility (private app, platform auth, or reverse-proxy password).
- The app never places orders and stores no broker credentials.
