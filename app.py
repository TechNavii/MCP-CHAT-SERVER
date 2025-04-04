from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import asyncio
import json
import os
import pathlib
import logging
from dotenv import load_dotenv

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
import mcp_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Get the directory where the current script is located
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
# Define the path to the config file relative to the script directory
CONFIG_FILE = SCRIPT_DIR / "mcp_config.json"
# Define the static files directory
STATIC_DIR = SCRIPT_DIR / "static"

class ChatMessage(BaseModel):
    """Represents a chat message."""
    role: str
    content: str
    timestamp: Optional[str] = None

class ChatRequest(BaseModel):
    """Represents a chat request from the frontend."""
    message: str
    history: List[Dict[str, Any]] # Use Dict temporarily for Pydantic AI history format

app = FastAPI(title="Pydantic AI MCP Chat API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_model() -> OpenAIModel:
    """Get the configured OpenAI model."""
    llm = os.getenv('MODEL_CHOICE', 'gpt-4o-mini')
    base_url = os.getenv('BASE_URL', 'https://api.openai.com/v1')
    api_key = os.getenv('LLM_API_KEY', 'no-api-key-provided')

    logger.info(f"Using model: {llm} with base URL: {base_url}")
    return OpenAIModel(
        llm,
        base_url=base_url,
        api_key=api_key
    )

async def get_pydantic_ai_agent() -> tuple[mcp_client.MCPClient, Agent]:
    """Initialize and return the MCP client and agent."""
    logger.info("Initializing MCP client and Pydantic AI agent...")
    client = mcp_client.MCPClient()
    try:
        client.load_servers(str(CONFIG_FILE))
        tools = await client.start()
        agent = Agent(model=get_model(), tools=tools)
        logger.info("MCP client and Pydantic AI agent initialized successfully.")
        return client, agent
    except Exception as e:
        logger.error(f"Failed to initialize agent: {e}", exc_info=True)
        raise

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for chat."""
    await websocket.accept()
    logger.info("WebSocket connection accepted.")
    mcp_agent_client: Optional[mcp_client.MCPClient] = None
    
    try:
        # Initialize the agent for this connection
        mcp_agent_client, mcp_agent = await get_pydantic_ai_agent()
        
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            logger.debug(f"Received data: {data}")
            request_data = json.loads(data)
            request = ChatRequest(**request_data) # Validate incoming data
            
            logger.info(f"Processing message: {request.message}")
            
            # Process the message using Pydantic AI agent
            try:
                # DEBUG: Force empty history to isolate the issue
                message_history = [] 
                logger.info(f"Passing history to agent (FORCED EMPTY): {message_history}")
                
                async with mcp_agent.run_stream(
                    request.message, 
                    message_history=message_history 
                ) as result:
                    curr_message = ""
                    async for message_delta in result.stream_text(delta=True):
                        curr_message += message_delta
                        await websocket.send_text(json.dumps({
                            "type": "delta",
                            "content": message_delta
                        }))
                    
                    logger.info("Streaming complete.")
                    # Send the final complete message
                    await websocket.send_text(json.dumps({
                        "type": "complete",
                        "content": curr_message
                    }))
                    
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": f"An error occurred: {str(e)}"
                }))
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "content": f"A server error occurred: {str(e)}"
            }))
        except Exception:
            pass # Ignore if sending error fails
    finally:
        if mcp_agent_client:
            logger.info("Cleaning up MCP client resources...")
            await mcp_agent_client.cleanup()
        logger.info("WebSocket connection closed.")

# Serve static files (CSS, JS)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def read_root() -> FileResponse:
    """Serve the main chat page (index.html)."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        logger.error("index.html not found in static directory!")
        # Consider returning a 404 or a simple error message
        return FileResponse("path/to/error/page.html", status_code=404) # Placeholder
    return FileResponse(index_path)


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server...")
    uvicorn.run(app, host="0.0.0.0", port=8000) 