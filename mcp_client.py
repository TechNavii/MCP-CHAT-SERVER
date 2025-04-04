from pydantic_ai import RunContext, Tool as PydanticTool
from pydantic_ai.tools import ToolDefinition
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool as MCPTool
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Dict, List, Optional, Set, Tuple, Union, Callable
from dataclasses import dataclass, field
from pathlib import Path
import asyncio
import logging
import shutil
import json
import os
import re
import time
import functools

# Setup a custom formatter to redact sensitive information
class SensitiveDataFilter(logging.Filter):
    """Filter to redact sensitive data from logs"""
    
    def __init__(self, patterns: List[str] = None):
        super().__init__()
        # Extended patterns to catch more potential sensitive data
        self.patterns = patterns or [
            'api_key', 'token', 'secret', 'password', 'credential',
            'access_key', 'auth', 'private_key', 'certificate'
        ]
    
    def filter(self, record):
        if isinstance(record.msg, str):
            # Redact patterns in the format key=value or key: value
            for pattern in self.patterns:
                regex = re.compile(rf'({pattern})\s*[=:]\s*[\'\"](.*?)[\'\"](\s|,|$)', re.IGNORECASE)
                record.msg = regex.sub(r'\1=*REDACTED*\3', record.msg)
                
            # Also redact URLs that might contain tokens as query params
            url_regex = re.compile(r'(https?://[^\s]*[?&][^\s]*=)([^\s&"]*)', re.IGNORECASE)
            record.msg = url_regex.sub(r'\1*REDACTED*', record.msg)
        return True

# Configure logging with sensitive data filtering
logger = logging.getLogger('mcp_client')
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
handler.addFilter(SensitiveDataFilter())
logger.addHandler(handler)
logger.setLevel(logging.ERROR)

# Secure configuration constants
# List of allowed commands that can be executed
ALLOWED_COMMANDS = {
    'npx': True,  # Allow npx for running node packages
    'node': True  # Allow node for running JavaScript
}

# Maximum number of concurrent tool calls to prevent flooding
MAX_CONCURRENT_TOOL_CALLS = 20

# Rate limiting configuration for tool calls
@dataclass
class RateLimiter:
    """Simple rate limiter for API calls"""
    max_calls: int = 100  # Maximum calls per time window
    time_window: float = 60.0  # Time window in seconds
    call_history: List[float] = field(default_factory=list)  # Timestamps of recent calls
    
    def is_allowed(self) -> bool:
        """Check if a new call is allowed based on rate limits"""
        current_time = time.time()
        # Remove timestamps older than the time window
        self.call_history = [t for t in self.call_history if current_time - t <= self.time_window]
        # Check if we've exceeded the maximum calls
        if len(self.call_history) >= self.max_calls:
            return False
        # Record this call attempt
        self.call_history.append(current_time)
        return True

# Create a global rate limiter instance
tool_rate_limiter = RateLimiter()

class MCPClient:
    """Manages connections to one or more MCP servers based on mcp_config.json"""

    def __init__(self) -> None:
        self.servers: List[MCPServer] = []
        self.config: Dict[str, Any] = {}
        self.tools: List[Any] = []
        self.exit_stack = AsyncExitStack()
        self._initialization_lock = asyncio.Lock()
        self._concurrent_tool_calls = 0  # Counter for concurrent tool executions
        self._tool_call_lock = asyncio.Lock()  # Lock for modifying the counter

    def load_servers(self, config_path: str) -> None:
        """Load server configuration from a JSON file (typically mcp_config.json)
        and creates an instance of each server (no active connection until 'start' though).

        Args:
            config_path: Path to the JSON configuration file.
        
        Raises:
            FileNotFoundError: If the config file is not found.
            json.JSONDecodeError: If the config file contains invalid JSON.
            KeyError: If the config file doesn't have the expected structure.
            ValueError: If the config file is empty or invalid.
        """
        try:
            config_path = os.path.expandvars(os.path.expanduser(config_path))
            
            with open(config_path, "r") as config_file:
                self.config = json.load(config_file)
            
            if not self.config or not isinstance(self.config, dict):
                raise ValueError("Invalid config file: empty or not a dictionary")
                
            if "mcpServers" not in self.config:
                raise KeyError("Invalid config: missing 'mcpServers' key")
                
            if not isinstance(self.config["mcpServers"], dict):
                raise ValueError("Invalid config: 'mcpServers' must be a dictionary")
                
            if not self.config["mcpServers"]:
                logger.warning("Config contains no MCP servers")
            
            # Process environment variables in the config
            self._process_env_variables_in_config()
                
            # Create server instances
            self.servers = [MCPServer(name, server_config) 
                           for name, server_config in self.config["mcpServers"].items()]            
        except FileNotFoundError:
            logger.error(f"Config file not found: {config_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            raise
        except (KeyError, ValueError) as e:
            logger.error(f"Invalid config structure: {e}")
            raise