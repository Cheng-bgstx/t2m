# Text-to-Motion Integration - Deployment Guide

This guide explains how to deploy the integrated system that adds text-based motion generation to the existing browser-based robot simulation.

## Architecture Overview

```
┌─────────────────┐      HTTP/REST      ┌──────────────────┐      WebSocket      ┌─────────────────────┐
│   Browser       │ ──────────────────> │  FastAPI Gateway │ ──────────────────> │  Remote AI Server   │
│  (Vue.js)       │                     │  (Python)        │                     │  (Motion Generation)│
│                 │ <────────────────── │                  │ <────────────────── │                     │
└─────────────────┘    JSON Motion      └──────────────────┘    Binary NPZ       └─────────────────────┘
                           Data                │
                                               │ Manages
                                               ▼
                                        ┌──────────────────┐
                                        │  Session Store   │
                                        │  (In-Memory)     │
                                        └──────────────────┘
```

## Components

### 1. FastAPI Gateway Service (`text_motion_api/`)

A Python service that:
- Receives text prompts from the browser via HTTP/REST API
- Forwards requests to the remote WebSocket motion generation server
- Converts NPZ binary responses to JSON format
- Manages user sessions and temporary motion data storage
- Implements rate limiting and automatic cleanup

### 2. Modified Frontend (`Demo.vue`)

Updated Vue component that:
- Adds a text input panel for motion descriptions
- Provides advanced parameter controls (duration, quality, smoothness)
- Displays generated motions as clickable chips
- Integrates with existing motion playback system

## Deployment Steps

### Step 1: Configure the FastAPI Gateway

1. **Navigate to the API directory:**
   ```bash
   cd text_motion_api/
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

   Key configuration options:
   ```env
   # Server settings
   PORT=8080
   HOST=0.0.0.0

   # Remote AI motion generation server (StableMoFusion or similar)
   REMOTE_WS_HOST=your-remote-server.com
   REMOTE_WS_PORT=8000
   REMOTE_WS_PATH=/ws

   # Data management
   DATA_RETENTION_MINUTES=30
   MAX_STORED_MOTIONS_PER_USER=10

   # Rate limiting
   MAX_REQUESTS_PER_MINUTE=10

   # CORS (for production, specify your frontend domain)
   ALLOWED_ORIGINS=http://localhost:5173,https://yourdomain.com
   ```

5. **Start the service:**

   Development mode:
   ```bash
   ./start.sh
   # or
   python main.py
   ```

   Production mode:
   ```bash
   ./start_production.sh
   ```

### Step 2: Configure the Frontend

**重要：** 任何前端源码修改（包括 `src/views/Demo.vue`、`src/simulation/trackingHelper.js` 等）只有在执行 `npm run build` 并部署生成的 **`dist`** 目录后才会在线上生效；仅推送源码不会自动更新用户看到的前端。

1. **Copy the modified Demo.vue:**
   ```bash
   cp /path/to/output/Demo.vue /path/to/your/project/src/views/Demo.vue
   ```

2. **Create environment file:**
   ```bash
   # In your frontend project root
   cp .env.example .env
   ```

3. **Set the API URL:**
   ```env
   VITE_TEXT_MOTION_API_URL=http://localhost:8080
   # For production: VITE_TEXT_MOTION_API_URL=https://api.yourdomain.com
   ```

4. **Install dependencies (if needed):**
   ```bash
   npm install
   ```

5. **Start the development server:**
   ```bash
   npm run dev
   ```

6. **Build for production:**
   ```bash
   npm run build
   ```

### Step 3: Configure Remote AI Server Connection

The FastAPI gateway needs to connect to your remote motion generation server (e.g., StableMoFusion).

**Option A: Direct Connection (if server is publicly accessible)**
```env
REMOTE_WS_HOST=motion-gen.yourdomain.com
REMOTE_WS_PORT=8000
```

**Option B: SSH Tunnel (for development/testing)**
```bash
# On your local machine, create an SSH tunnel to the remote server
ssh -L 8000:localhost:8000 user@remote-server.com

# Then configure the gateway to connect to localhost
REMOTE_WS_HOST=127.0.0.1
REMOTE_WS_PORT=8000
```

**Option C: VPN/Private Network**
```env
REMOTE_WS_HOST=10.0.0.5  # Internal IP
REMOTE_WS_PORT=8000
```

## Configuration Reference

### FastAPI Gateway Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8080 | Local port for the API gateway |
| `HOST` | 0.0.0.0 | Bind address (use 127.0.0.1 for local-only) |
| `REMOTE_WS_HOST` | 127.0.0.1 | Remote motion generation server host |
| `REMOTE_WS_PORT` | 8000 | Remote server WebSocket port |
| `REMOTE_WS_PATH` | /ws | WebSocket endpoint path |
| `DATA_RETENTION_MINUTES` | 30 | How long to keep inactive session data |
| `CLEANUP_INTERVAL_MINUTES` | 5 | How often to run cleanup |
| `MAX_STORED_MOTIONS_PER_USER` | 10 | Max motions per user session |
| `MAX_REQUESTS_PER_MINUTE` | 10 | Rate limit per user |
| `ALLOWED_ORIGINS` | * | CORS allowed origins (comma-separated) |

### Frontend Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_TEXT_MOTION_API_URL` | http://localhost:8080 | URL of the FastAPI gateway |

## API Endpoints

The FastAPI gateway provides these endpoints:

### Health & Info
- `GET /` - Service status
- `GET /health` - Detailed health check
- `GET /api/config` - Service configuration

### Session Management
- `POST /api/session` - Create new session (returns session_id)

### Motion Generation
- `POST /api/generate` - Generate motion from text
  ```json
  {
    "text": "a person walks forward",
    "motion_length": 4.0,
    "num_inference_steps": 10,
    "adaptive_smooth": true,
    "transition_steps": 100
  }
  ```

### Motion Management
- `GET /api/motions` - List user's generated motions
- `GET /api/motions/{motion_id}` - Get specific motion data
- `DELETE /api/motions/{motion_id}` - Delete a motion

## Data Flow

1. **User enters text** in the browser UI
2. **Frontend sends POST /api/generate** with text and parameters
3. **Gateway creates WebSocket connection** to remote AI server
4. **Remote server generates motion** and returns NPZ binary data
5. **Gateway converts NPZ to JSON** (arrays of joint positions, root position, rotation)
6. **Gateway stores motion** in session and returns to frontend
7. **Frontend adds motion** to TrackingHelper via `addMotions()`
8. **User clicks motion chip** to play - calls `requestMotion()`
9. **TrackingHelper handles smooth transition** from current pose to motion start

## Multi-User Support

The gateway supports multiple concurrent users through:

1. **Session-based isolation**: Each user gets a unique `session_id`
2. **In-memory storage**: Motion data is stored per-session in RAM
3. **Automatic cleanup**: Expired sessions are cleaned up periodically
4. **Rate limiting**: Prevents abuse (configurable requests per minute)

## Data Management Strategy

### Storage
- Motion data is stored **in-memory only** (no persistent database)
- Each session has a maximum number of stored motions (default: 10)
- Old motions are automatically removed when limit is reached

### Cleanup
- Sessions inactive for 30+ minutes are removed
- Cleanup runs every 5 minutes
- Maximum session age is 2 hours (regardless of activity)

### Memory Usage Estimate
- Each motion frame: ~500 bytes (29 joints × 4 bytes + root data)
- 4-second motion at 50 FPS: 200 frames × 500 bytes = ~100 KB
- 10 motions per user × 100 KB = ~1 MB per user
- 100 concurrent users: ~100 MB RAM

## Troubleshooting

### Gateway can't connect to remote server
```
[Error] WebSocket error: Connection refused
```
- Check SSH tunnel is active (if using)
- Verify remote server is running: `curl http://remote:8000/`
- Check firewall rules

### CORS errors in browser
```
Access to fetch at 'http://localhost:8080/...' blocked by CORS policy
```
- Update `ALLOWED_ORIGINS` to include your frontend URL
- For development, can use `ALLOWED_ORIGINS=*`

### Rate limit exceeded
```
Rate limit exceeded - max 10 requests per minute
```
- Adjust `MAX_REQUESTS_PER_MINUTE` if needed
- Implement client-side debouncing

### Motion generation timeout
```
Request timeout - generation took too long
```
- Check remote server load
- Reduce `num_inference_steps` for faster generation
- Increase `WS_TIMEOUT` in gateway config

## Production Deployment Checklist

- [ ] Use production startup script (`start_production.sh`)
- [ ] Configure specific `ALLOWED_ORIGINS` (not `*`)
- [ ] Set up reverse proxy (nginx) with SSL
- [ ] Configure firewall rules
- [ ] Set up monitoring and logging
- [ ] Configure appropriate rate limits
- [ ] Test with multiple concurrent users
- [ ] Verify cleanup is working (check memory usage)

## Security Considerations

1. **Never expose the remote AI server directly** - always use the gateway
2. **Use HTTPS/WSS in production** for all connections
3. **Implement authentication** if needed (add to gateway)
4. **Validate all inputs** - gateway validates text length and parameters
5. **Rate limiting** prevents abuse and protects the AI server
6. **Session isolation** ensures users can't access each other's data

## Performance Optimization

### Gateway Optimizations
- Use multiple workers (`--workers 4` in production)
- Enable keep-alive connections
- Consider using Redis for session storage (if scaling beyond single instance)

### Frontend Optimizations
- Debounce text input to prevent accidental double-submissions
- Show loading states during generation
- Cache generated motions in browser (optional)

### Network Optimizations
- Use HTTP/2 or HTTP/3 if possible
- Enable compression (gzip/brotli)
- Consider CDN for static assets

## Support

For issues or questions:
1. Check the gateway logs for detailed error messages
2. Verify all services are running: `curl http://gateway:8080/`
3. Test WebSocket connection to remote server independently
4. Check browser console for frontend errors
