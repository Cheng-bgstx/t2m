"""
Text-to-Motion API Gateway Service

This FastAPI service acts as a bridge between the browser frontend and the remote
WebSocket motion generation server. It handles:
- Multi-user session management
- Data format conversion (NPZ -> JSON)
- Temporary data storage with automatic cleanup
- Concurrent request handling
"""

import asyncio
import io
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Any
import numpy as np
import websockets
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== Configuration ====================

class Config:
    """Service configuration"""
    # Remote WebSocket server settings (the actual motion generation server)
    REMOTE_WS_HOST = os.getenv("REMOTE_WS_HOST", "127.0.0.1")
    REMOTE_WS_PORT = int(os.getenv("REMOTE_WS_PORT", "8000"))
    REMOTE_WS_PATH = os.getenv("REMOTE_WS_PATH", "/ws")
    
    # Connection settings
    WS_MAX_SIZE = 50 * 1024 * 1024  # 50MB for large motion data
    WS_TIMEOUT = 60.0  # 60 seconds timeout for generation
    
    # Data storage settings
    DATA_RETENTION_MINUTES = int(os.getenv("DATA_RETENTION_MINUTES", "30"))
    CLEANUP_INTERVAL_MINUTES = int(os.getenv("CLEANUP_INTERVAL_MINUTES", "5"))
    MAX_STORED_MOTIONS_PER_USER = int(os.getenv("MAX_STORED_MOTIONS_PER_USER", "10"))
    
    # Rate limiting
    MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "10"))
    
    # CORS settings
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")


# ==================== Data Models ====================

class TextToMotionRequest(BaseModel):
    """Request model for text-to-motion generation"""
    text: str = Field(..., min_length=1, max_length=500, description="Text description of the motion")
    motion_length: float = Field(default=4.0, ge=0.1, le=9.8, description="Motion duration in seconds")
    num_inference_steps: int = Field(default=10, ge=1, le=1000, description="Number of denoising steps")
    seed: Optional[int] = Field(default=None, description="Random seed for reproducibility")
    smooth: Optional[bool] = Field(default=None, description="Enable basic smoothing")
    smooth_window: int = Field(default=5, ge=3, description="Smoothing window size")
    adaptive_smooth: bool = Field(default=True, description="Enable adaptive smoothing")
    static_start: bool = Field(default=True, description="Force static start")
    static_frames: int = Field(default=2, ge=0, description="Number of static frames at start")
    blend_frames: int = Field(default=8, ge=0, description="Number of blend frames")
    transition_steps: int = Field(default=100, ge=0, le=300, description="Transition steps for smooth blending")


class MotionData(BaseModel):
    """Motion data response model"""
    name: str
    fps: float
    joint_pos: list  # List of lists, each inner list is a frame of joint positions
    root_pos: list   # List of [x, y, z] positions
    root_quat: list  # List of [w, x, y, z] quaternions
    frame_count: int
    duration: float
    created_at: str


class GenerationResponse(BaseModel):
    """Response model for successful generation"""
    success: bool
    motion_id: str
    motion: MotionData
    message: str


class ErrorResponse(BaseModel):
    """Error response model"""
    success: bool = False
    error: str
    code: str


@dataclass
class UserSession:
    """User session data"""
    session_id: str
    created_at: datetime
    last_activity: datetime
    motions: Dict[str, dict] = field(default_factory=dict)
    request_count: int = 0
    request_window_start: datetime = field(default_factory=datetime.now)


# ==================== Global State ====================

class AppState:
    """Application state manager"""
    def __init__(self):
        self.sessions: Dict[str, UserSession] = {}
        self.lock = asyncio.Lock()
        self.cleanup_task: Optional[asyncio.Task] = None
        
    async def get_or_create_session(self, session_id: Optional[str] = None) -> UserSession:
        """Get existing session or create new one"""
        async with self.lock:
            if session_id and session_id in self.sessions:
                session = self.sessions[session_id]
                session.last_activity = datetime.now()
                return session
            
            # Create new session
            new_session_id = session_id or str(uuid.uuid4())
            now = datetime.now()
            session = UserSession(
                session_id=new_session_id,
                created_at=now,
                last_activity=now
            )
            self.sessions[new_session_id] = session
            logger.info(f"Created new session: {new_session_id}")
            return session
    
    async def cleanup_expired_sessions(self):
        """Remove expired sessions and their data"""
        async with self.lock:
            now = datetime.now()
            expired_sessions = []
            
            for session_id, session in self.sessions.items():
                inactive_duration = now - session.last_activity
                session_age = now - session.created_at
                
                # Remove sessions inactive for too long or too old
                if (inactive_duration > timedelta(minutes=Config.DATA_RETENTION_MINUTES) or
                    session_age > timedelta(hours=2)):
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del self.sessions[session_id]
                logger.info(f"Cleaned up expired session: {session_id}")
    
    def check_rate_limit(self, session: UserSession) -> bool:
        """Check if user has exceeded rate limit"""
        now = datetime.now()
        window_duration = now - session.request_window_start
        
        # Reset window if expired
        if window_duration > timedelta(minutes=1):
            session.request_count = 0
            session.request_window_start = now
        
        # Check limit
        if session.request_count >= Config.MAX_REQUESTS_PER_MINUTE:
            return False
        
        session.request_count += 1
        return True


app_state = AppState()


# ==================== Background Tasks ====================

async def periodic_cleanup():
    """Periodically clean up expired sessions"""
    while True:
        try:
            await asyncio.sleep(Config.CLEANUP_INTERVAL_MINUTES * 60)
            await app_state.cleanup_expired_sessions()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Starting Text-to-Motion API Gateway")
    app_state.cleanup_task = asyncio.create_task(periodic_cleanup())
    yield
    # Shutdown
    logger.info("Shutting down Text-to-Motion API Gateway")
    if app_state.cleanup_task:
        app_state.cleanup_task.cancel()
        try:
            await app_state.cleanup_task
        except asyncio.CancelledError:
            pass


# ==================== FastAPI App ====================

app = FastAPI(
    title="Text-to-Motion API Gateway",
    description="Bridge service between browser frontend and remote motion generation server",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Helper Functions ====================

def convert_npz_to_motion_data(npz_bytes: bytes, motion_name: str) -> dict:
    """
    Convert NPZ binary data to JSON-serializable motion data
    
    The input NPZ contains (38D format):
    - fps: (1,) int32
    - joint_pos: (T, 29) float32 [Isaac order]
    - root_pos: (T, 3) float32
    - root_rot: (T, 4) float32 [w, x, y, z]
    
    Output format matches what TrackingHelper expects:
    - joint_pos: array of arrays (T frames x 29 joints)
    - root_pos: array of [x, y, z]
    - root_quat: array of [w, x, y, z]
    """
    data = np.load(io.BytesIO(npz_bytes))
    
    fps = int(data['fps'][0]) if isinstance(data['fps'], np.ndarray) else int(data['fps'])
    joint_pos = data['joint_pos'].astype(np.float32)
    root_pos = data['root_pos'].astype(np.float32)
    root_rot = data['root_rot'].astype(np.float32)  # [w, x, y, z]
    
    frame_count = joint_pos.shape[0]
    duration = frame_count / fps
    
    # Convert to lists for JSON serialization
    motion_data = {
        'name': motion_name,
        'fps': float(fps),
        'joint_pos': joint_pos.tolist(),
        'root_pos': root_pos.tolist(),
        'root_quat': root_rot.tolist(),  # Already in wxyz format
        'frame_count': frame_count,
        'duration': duration,
        'created_at': datetime.now().isoformat()
    }
    
    return motion_data


async def generate_motion_from_remote(request_data: dict) -> bytes:
    """
    Connect to remote WebSocket server and generate motion
    
    Returns raw NPZ bytes on success
    """
    uri = f"ws://{Config.REMOTE_WS_HOST}:{Config.REMOTE_WS_PORT}{Config.REMOTE_WS_PATH}"
    
    try:
        async with websockets.connect(
            uri,
            max_size=Config.WS_MAX_SIZE,
            open_timeout=10.0
        ) as ws:
            # Send request
            await ws.send(json.dumps(request_data))
            logger.info(f"Sent request to remote server: {request_data.get('text', '')[:50]}...")
            
            # Receive response with timeout
            response = await asyncio.wait_for(
                ws.recv(),
                timeout=Config.WS_TIMEOUT
            )
            
            # Check if error response (JSON string)
            if isinstance(response, str):
                try:
                    error_data = json.loads(response)
                    error_msg = error_data.get('error', 'Unknown error')
                    error_code = error_data.get('code', 'SERVER_ERROR')
                    raise HTTPException(
                        status_code=500,
                        detail={"error": error_msg, "code": error_code}
                    )
                except json.JSONDecodeError:
                    raise HTTPException(
                        status_code=500,
                        detail={"error": "Invalid response from server", "code": "INVALID_RESPONSE"}
                    )
            
            # Response is binary NPZ data
            return response
            
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={"error": "Request timeout - generation took too long", "code": "TIMEOUT"}
        )
    except websockets.exceptions.ConnectionRefused:
        raise HTTPException(
            status_code=503,
            detail={"error": "Motion generation server unavailable", "code": "SERVER_UNAVAILABLE"}
        )
    except websockets.exceptions.WebSocketException as e:
        raise HTTPException(
            status_code=502,
            detail={"error": f"WebSocket error: {str(e)}", "code": "WEBSOCKET_ERROR"}
        )


def enforce_motion_limit(session: UserSession):
    """Enforce maximum number of stored motions per user"""
    if len(session.motions) >= Config.MAX_STORED_MOTIONS_PER_USER:
        # Remove oldest motion
        oldest_id = min(session.motions.keys(), 
                       key=lambda k: session.motions[k].get('created_at', ''))
        del session.motions[oldest_id]
        logger.info(f"Removed oldest motion {oldest_id} for session {session.session_id}")


# ==================== API Endpoints ====================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "Text-to-Motion API Gateway",
        "version": "1.0.0",
        "remote_server": f"{Config.REMOTE_WS_HOST}:{Config.REMOTE_WS_PORT}",
        "active_sessions": len(app_state.sessions)
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "active_sessions": len(app_state.sessions),
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/generate", response_model=GenerationResponse)
async def generate_motion(
    request: TextToMotionRequest,
    background_tasks: BackgroundTasks,
    http_request: Request
):
    """
    Generate motion from text description
    
    This endpoint:
    1. Receives text description and parameters
    2. Forwards to remote WebSocket server
    3. Converts NPZ response to JSON
    4. Stores motion data for the session
    5. Returns motion data to client
    """
    # Get or create session
    session_id = http_request.headers.get("X-Session-ID")
    session = await app_state.get_or_create_session(session_id)
    
    # Check rate limit
    if not app_state.check_rate_limit(session):
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded - max 10 requests per minute", "code": "RATE_LIMIT"}
        )
    
    # Prepare request for remote server
    request_data = {
        "text": request.text,
        "motion_length": request.motion_length,
        "num_inference_steps": request.num_inference_steps,
        "seed": request.seed if request.seed is not None else int(time.time() % 10000),
        "smooth": request.smooth,
        "smooth_window": request.smooth_window,
        "adaptive_smooth": request.adaptive_smooth,
        "static_start": request.static_start,
        "static_frames": request.static_frames,
        "blend_frames": request.blend_frames
    }
    
    # Remove None values
    request_data = {k: v for k, v in request_data.items() if v is not None}
    
    try:
        # Generate motion from remote server
        npz_bytes = await generate_motion_from_remote(request_data)
        
        # Generate motion ID
        motion_id = f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        motion_name = f"[AI] {request.text[:30]}"
        
        # Convert to motion data
        motion_data = convert_npz_to_motion_data(npz_bytes, motion_name)
        motion_data['motion_id'] = motion_id
        motion_data['text_prompt'] = request.text
        motion_data['parameters'] = {
            "motion_length": request.motion_length,
            "num_inference_steps": request.num_inference_steps,
            "adaptive_smooth": request.adaptive_smooth,
            "static_start": request.static_start,
            "transition_steps": request.transition_steps
        }
        
        # Store in session (enforce limit)
        enforce_motion_limit(session)
        session.motions[motion_id] = motion_data
        
        logger.info(f"Generated motion {motion_id} for session {session.session_id}")
        
        return GenerationResponse(
            success=True,
            motion_id=motion_id,
            motion=MotionData(**{k: v for k, v in motion_data.items() 
                                if k in ['name', 'fps', 'joint_pos', 'root_pos', 'root_quat', 
                                        'frame_count', 'duration', 'created_at']}),
            message="Motion generated successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Generation error: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": f"Failed to generate motion: {str(e)}", "code": "GENERATION_FAILED"}
        )


@app.get("/api/motions")
async def list_motions(http_request: Request):
    """List all motions for the current session"""
    session_id = http_request.headers.get("X-Session-ID")
    if not session_id or session_id not in app_state.sessions:
        return {"motions": [], "session_id": None}
    
    session = app_state.sessions[session_id]
    session.last_activity = datetime.now()
    
    motions_list = [
        {
            "motion_id": mid,
            "name": mdata.get("name", "Unknown"),
            "frame_count": mdata.get("frame_count", 0),
            "duration": mdata.get("duration", 0),
            "created_at": mdata.get("created_at", ""),
            "text_prompt": mdata.get("text_prompt", "")[:100]
        }
        for mid, mdata in session.motions.items()
    ]
    
    return {
        "motions": sorted(motions_list, key=lambda x: x["created_at"], reverse=True),
        "session_id": session_id
    }


@app.get("/api/motions/{motion_id}")
async def get_motion(motion_id: str, http_request: Request):
    """Get specific motion data by ID"""
    session_id = http_request.headers.get("X-Session-ID")
    if not session_id or session_id not in app_state.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = app_state.sessions[session_id]
    session.last_activity = datetime.now()
    
    if motion_id not in session.motions:
        raise HTTPException(status_code=404, detail="Motion not found")
    
    return session.motions[motion_id]


@app.delete("/api/motions/{motion_id}")
async def delete_motion(motion_id: str, http_request: Request):
    """Delete a specific motion"""
    session_id = http_request.headers.get("X-Session-ID")
    if not session_id or session_id not in app_state.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = app_state.sessions[session_id]
    
    if motion_id not in session.motions:
        raise HTTPException(status_code=404, detail="Motion not found")
    
    del session.motions[motion_id]
    logger.info(f"Deleted motion {motion_id} from session {session_id}")
    
    return {"success": True, "message": "Motion deleted"}


@app.post("/api/session")
async def create_session():
    """Create a new session"""
    session = await app_state.get_or_create_session()
    return {
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "message": "Session created successfully"
    }


@app.get("/api/config")
async def get_config():
    """Get service configuration (safe values only)"""
    return {
        "max_motion_length": 9.8,
        "min_motion_length": 0.1,
        "max_inference_steps": 1000,
        "min_inference_steps": 1,
        "default_motion_length": 4.0,
        "default_inference_steps": 10,
        "max_stored_motions": Config.MAX_STORED_MOTIONS_PER_USER,
        "data_retention_minutes": Config.DATA_RETENTION_MINUTES
    }


# ==================== Error Handlers ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": exc.detail.get("error", str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail),
            "code": exc.detail.get("code", "UNKNOWN") if isinstance(exc.detail, dict) else "UNKNOWN"
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """General exception handler"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "code": "INTERNAL_ERROR"
        }
    )


# ==================== Main Entry Point ====================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"Starting server on {host}:{port}")
    logger.info(f"Remote WebSocket: {Config.REMOTE_WS_HOST}:{Config.REMOTE_WS_PORT}")
    
    uvicorn.run(app, host=host, port=port)
