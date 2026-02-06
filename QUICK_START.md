# Quick Start Guide

## 5-Minute Setup

### 1. Start the Gateway (Terminal 1)

```bash
cd text_motion_api/
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Edit .env with your remote server
vim .env

./start.sh
```

### 2. Configure Frontend (Terminal 2)

```bash
# In your project root
echo "VITE_TEXT_MOTION_API_URL=http://localhost:8080" > .env

# Copy modified Demo.vue
cp /path/to/output/Demo.vue src/views/Demo.vue

npm run dev
```

### 3. Open Browser

Navigate to `http://localhost:5173` (or your dev server URL)

You should see a new "AI Motion Generation" section in the control panel.

---

## Common Commands

### Gateway

```bash
# Development
./start.sh

# Production
./start_production.sh

# Docker
docker-compose up -d

# Test
curl http://localhost:8080/
```

### Frontend

```bash
# Development
npm run dev

# Production build
npm run build

# Preview build
npm run preview
```

---

## Configuration Checklist

### Gateway (.env)

- [ ] `REMOTE_WS_HOST` - Set to your AI server address
- [ ] `REMOTE_WS_PORT` - Set to your AI server port
- [ ] `ALLOWED_ORIGINS` - Add your frontend URL

### Frontend (.env)

- [ ] `VITE_TEXT_MOTION_API_URL` - Set to gateway URL

---

## Troubleshooting Quick Fixes

| Problem | Solution |
|---------|----------|
| "Not Connected" status | Check gateway is running: `curl http://localhost:8080/` |
| "Server unavailable" | Check SSH tunnel or remote server |
| CORS errors | Update `ALLOWED_ORIGINS` in gateway .env |
| Rate limit | Wait 1 minute or increase `MAX_REQUESTS_PER_MINUTE` |
| Motions not playing | Wait for current motion to finish |

---

## File Locations

```
Your Project/
├── src/
│   └── views/
│       └── Demo.vue          # <- Copy this from output/
├── .env                      # <- Add VITE_TEXT_MOTION_API_URL
└── ...

text_motion_api/              # <- Run this service
├── main.py
├── requirements.txt
├── start.sh
└── .env                      # <- Configure this
```

---

## Need Help?

1. Check `DEPLOYMENT_GUIDE.md` for detailed instructions
2. Check `README_INTEGRATION.md` for architecture overview
3. Check gateway logs for error messages
4. Check browser console for frontend errors
