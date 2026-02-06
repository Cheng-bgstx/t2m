# Text-to-Motion Integration for Humanoid Policy Viewer

This project integrates text-based motion generation capabilities into the existing browser-based robot simulation system.

## What Was Added

### 1. FastAPI Gateway Service (`text_motion_api/`)

A lightweight Python service that bridges the browser frontend and the remote AI motion generation server.

**Key Features:**
- Multi-user session management with automatic cleanup
- NPZ to JSON format conversion for browser compatibility
- Rate limiting (10 requests/minute per user by default)
- Configurable data retention (30 minutes default)
- CORS support for cross-origin requests

**Files:**
- `main.py` - Main FastAPI application
- `requirements.txt` - Python dependencies
- `start.sh` - Development startup script
- `start_production.sh` - Production startup script
- `.env.example` - Configuration template

### 2. Enhanced Frontend (`Demo.vue`)

Modified Vue component with text-to-motion UI integrated into the existing control panel.

**New Features:**
- Text input area for motion descriptions
- Advanced parameter controls (duration, quality, transition steps)
- Generated motions list with clickable playback
- Status indicators for API connection
- Error handling and user feedback

### 3. Enhanced Tracking Helper (`trackingHelper_enhanced.js`)

Optional enhanced version of TrackingHelper with:
- Adaptive transition steps based on pose difference
- Velocity-aware transitions
- Optional motion smoothing for jittery AI-generated motions
- Configurable min/max transition limits

## Quick Start

### 1. Start the FastAPI Gateway

```bash
cd text_motion_api/

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your remote server settings

# Start the service
./start.sh
```

### 2. Configure the Frontend

```bash
# In your frontend project root
cp .env.example .env

# Set the API URL
# VITE_TEXT_MOTION_API_URL=http://localhost:8080

# Copy the modified Demo.vue
cp /path/to/output/Demo.vue src/views/Demo.vue

# Start development server
npm run dev
```

### 3. Configure Remote Server Connection

Edit `text_motion_api/.env`:

```env
# Option 1: Direct connection
REMOTE_WS_HOST=motion-gen.yourdomain.com
REMOTE_WS_PORT=8000

# Option 2: SSH tunnel (for development)
# First create tunnel: ssh -L 8000:localhost:8000 user@remote-server
REMOTE_WS_HOST=127.0.0.1
REMOTE_WS_PORT=8000
```

## File Structure

```
output/
├── text_motion_api/           # FastAPI gateway service
│   ├── main.py               # Main application
│   ├── requirements.txt      # Dependencies
│   ├── start.sh             # Dev startup
│   ├── start_production.sh  # Production startup
│   └── .env.example         # Config template
│
├── Demo.vue                  # Modified frontend component
├── trackingHelper_enhanced.js # Optional enhanced tracking helper
├── .env.example              # Frontend env template
├── DEPLOYMENT_GUIDE.md       # Detailed deployment instructions
└── README_INTEGRATION.md     # This file
```

## Configuration

### Gateway Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8080 | Gateway HTTP port |
| `HOST` | 0.0.0.0 | Bind address |
| `REMOTE_WS_HOST` | 127.0.0.1 | AI server host |
| `REMOTE_WS_PORT` | 8000 | AI server port |
| `DATA_RETENTION_MINUTES` | 30 | Session data retention |
| `MAX_REQUESTS_PER_MINUTE` | 10 | Rate limit |
| `ALLOWED_ORIGINS` | * | CORS origins |

### Frontend Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_TEXT_MOTION_API_URL` | http://localhost:8080 | Gateway URL |

## How It Works

### Data Flow

1. **User enters text** → Frontend sends POST to `/api/generate`
2. **Gateway creates WebSocket** → Connects to remote AI server
3. **AI generates motion** → Returns NPZ binary data
4. **Gateway converts to JSON** → Arrays of positions/quaternions
5. **Frontend receives motion** → Adds to TrackingHelper
6. **User clicks motion** → TrackingHelper plays with smooth transition

### Session Management

- Each browser session gets a unique `session_id`
- Motion data stored in-memory per session
- Automatic cleanup after 30 minutes of inactivity
- Maximum 10 motions stored per user

### Smooth Transitions

The system uses adaptive transitions:
- Base transition: 100-120 steps (configurable)
- Longer transitions for large pose differences
- Prevents "teleportation" between motions
- Returns to default pose between different motions

## API Endpoints

### Gateway API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/api/session` | POST | Create session |
| `/api/generate` | POST | Generate motion from text |
| `/api/motions` | GET | List user's motions |
| `/api/motions/{id}` | GET | Get motion data |
| `/api/motions/{id}` | DELETE | Delete motion |

### Generate Request Format

```json
{
  "text": "a person walks forward",
  "motion_length": 4.0,
  "num_inference_steps": 10,
  "adaptive_smooth": true,
  "transition_steps": 100
}
```

### Generate Response Format

```json
{
  "success": true,
  "motion_id": "gen_20240205_120000_abc123",
  "motion": {
    "name": "[AI] a person walks forward",
    "fps": 50,
    "joint_pos": [[...], [...], ...],
    "root_pos": [[x, y, z], ...],
    "root_quat": [[w, x, y, z], ...],
    "frame_count": 200,
    "duration": 4.0
  }
}
```

## Troubleshooting

### Gateway can't connect to AI server

```
Error: Motion generation server unavailable
```
- Check SSH tunnel is active (if using)
- Verify AI server is running: `curl http://remote:8000/`
- Check firewall rules

### CORS errors

```
Access blocked by CORS policy
```
- Update `ALLOWED_ORIGINS` in gateway `.env`
- Include your frontend URL exactly (including port)

### Rate limit errors

```
Rate limit exceeded
```
- Wait 1 minute between requests
- Or increase `MAX_REQUESTS_PER_MINUTE`

### Motions not playing

- Wait for current motion to finish (returns to default)
- Check browser console for errors
- Verify motion was added successfully

## Performance Considerations

### Memory Usage

- Per motion: ~100 KB (4 seconds at 50 FPS)
- Per user: ~1 MB (10 motions max)
- 100 concurrent users: ~100 MB RAM

### Network Traffic

- Generate request: ~200 bytes
- Motion response: ~500 KB (JSON)
- Status polling: negligible

### Optimization Tips

1. **Reduce motion length** for faster generation
2. **Lower inference steps** (10 → 5) for quicker results
3. **Enable compression** on reverse proxy
4. **Use HTTP/2** for better multiplexing

## Security

### Current Protections

- Rate limiting prevents abuse
- Session isolation (users can't access others' data)
- Input validation on all parameters
- No persistent storage (data in memory only)

### Production Recommendations

1. Use HTTPS/WSS for all connections
2. Implement authentication if needed
3. Add request signing for AI server
4. Use Redis for session storage (if scaling)
5. Set up monitoring and alerting

## Differences from Original System

### What Changed

1. **Added text input panel** in Demo.vue control section
2. **Added API client** for gateway communication
3. **Added motion storage** for generated motions
4. **Added session management** (automatic)

### What Stayed the Same

1. **All original motion controls** (policy selection, preset motions)
2. **File upload functionality**
3. **Camera controls**
4. **Simulation parameters**
5. **Reset functionality**

### Backward Compatibility

- Existing preset motions work unchanged
- Custom file uploads work unchanged
- No breaking changes to existing features

## Development

### Adding New Features

To extend the text-to-motion functionality:

1. **Add new parameters** in `TextToMotionRequest` (gateway)
2. **Add UI controls** in Demo.vue advanced options
3. **Pass parameters** in the generate request
4. **Handle in AI server** (if needed)

### Testing

```bash
# Test gateway
curl http://localhost:8080/

# Test session creation
curl -X POST http://localhost:8080/api/session

# Test generation
curl -X POST http://localhost:8080/api/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "test motion", "motion_length": 2.0}'
```

## Support

For issues:
1. Check gateway logs
2. Verify all services running
3. Test WebSocket connection to AI server
4. Check browser console for errors

## License

Same as original project.
