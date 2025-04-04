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
ALLOWED_COMMANDS = {
    'npx': True,
    'node': True
}

MAX_CONCURRENT_TOOL_CALLS = 20

@dataclass
class RateLimiter:
    max_calls: int = 100
    time_window: float = 60.0
    call_history: List[float] = field(default_factory=list)
    
    def is_allowed(self) -> bool:
        current_time = time.time()
        self.call_history = [t for t in self.call_history if current_time - t <= self.time_window]
        if len(self.call_history) >= self.max_calls:
            return False
        self.call_history.append(current_time)
        return True

tool_rate_limiter = RateLimiter()

class MCPClient:
    def __init__(self) -> None:
        self.servers: List[MCPServer] = []
        self.config: Dict[str, Any] = {}
        self.tools: List[Any] = []
        self.exit_stack = AsyncExitStack()
        self._initialization_lock = asyncio.Lock()
        self._concurrent_tool_calls = 0
        self._tool_call_lock = asyncio.Lock()

    def load_servers(self, config_path: str) -> None:
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
            
            self._process_env_variables_in_config()
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

    def _process_env_variables_in_config(self) -> None:
        if not self.config or not isinstance(self.config, dict) or "mcpServers" not in self.config:
            return
            
        missing_env_vars = set()
            
        for server_name, server_config in self.config["mcpServers"].items():
            if "env" in server_config and isinstance(server_config["env"], dict):
                if not all(isinstance(k, str) and (isinstance(v, str) or v is None) 
                          for k, v in server_config["env"].items()):
                    logger.warning(f"Invalid 'env' structure for server '{server_name}'")
                
                for key, value in server_config["env"].items():
                    if isinstance(value, str):
                        pattern1 = r'\${([A-Za-z0-9_]+)}'
                        pattern2 = r'\$([A-Za-z0-9_]+)'
                        
                        env_vars = set(re.findall(pattern1, value) + re.findall(pattern2, value))
                        
                        for env_var in env_vars:
                            if env_var not in os.environ:
                                missing_env_vars.add(env_var)
                                logger.warning(f"Environment variable '{env_var}' not set")
                        
                        for match in re.findall(pattern1, value):
                            env_value = os.environ.get(match, '')
                            value = value.replace(f"${{{match}}}", env_value)
                            
                        for match in re.findall(pattern2, value):
                            env_value = os.environ.get(match, '')
                            value = value.replace(f"${match}", env_value)
                            
                        server_config["env"][key] = value
        
        if missing_env_vars:
            logger.error(f"Missing environment variables: {', '.join(missing_env_vars)}")

    async def start(self) -> List[PydanticTool]:
        async with self._initialization_lock:
            self.tools = []
            failed_servers = []
            initialized_servers = []
            
            for server in self.servers:
                try:
                    logger.info(f"Initializing MCP server: {server.name}")
                    await server.initialize()
                    initialized_servers.append(server)
                    tools = await server.create_pydantic_ai_tools()
                    self.tools.extend(tools)
                    logger.info(f"Successfully initialized MCP server: {server.name}")
                except Exception as e:
                    logger.error(f"Failed to initialize server '{server.name}': {str(e)}")
                    failed_servers.append((server.name, str(e)))
            
            if failed_servers:
                error_details = '\n'.join([f"- {name}: {error}" for name, error in failed_servers])
                logger.error(f"Some MCP servers failed to initialize:\n{error_details}")
                
                if initialized_servers:
                    logger.info("Cleaning up successfully initialized servers")
                    await self.cleanup_servers()
                    self.tools = []
                
                if not self.tools:
                    raise RuntimeError(f"Failed to initialize MCP servers: {error_details}")
            
            return self.tools
            
    @asynccontextmanager
    async def session(self):
        try:
            await self.start()
            yield self
        finally:
            await self.cleanup()

    async def cleanup_servers(self) -> None:
        cleanup_tasks = []
        for server in self.servers:
            if hasattr(server, 'cleanup'):
                task = asyncio.create_task(self._safe_server_cleanup(server))
                cleanup_tasks.append(task)
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    
    async def _safe_server_cleanup(self, server: 'MCPServer') -> None:
        try:
            cleanup_task = asyncio.create_task(server.cleanup())
            try:
                await asyncio.wait_for(cleanup_task, timeout=10.0)
                logger.info(f"Successfully cleaned up server: {server.name}")
            except asyncio.TimeoutError:
                logger.warning(f"Cleanup timeout for server {server.name}")
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            logger.warning(f"Error during cleanup of server {server.name}: {str(e)}")

    async def cleanup(self) -> None:
        try:
            logger.info("Starting MCP client cleanup")
            await self.cleanup_servers()
            await self.exit_stack.aclose()
            logger.info("MCP client cleanup completed")
        except Exception as e:
            logger.error(f"Error during final MCP client cleanup: {str(e)}")

class MCPServer:
    _concurrent_tool_calls = 0
    _tool_call_lock = asyncio.Lock()

    def __init__(self, name: str, config: Dict[str, Any]) -> None:
        self.name = name
        self.config = config
        self.stdio_context = None
        self.session = None
        self._cleanup_lock = asyncio.Lock()
        self.exit_stack = AsyncExitStack()
        self._initialized = False
        self._start_time = None
        self._last_activity = None
        self._rate_limiter = RateLimiter()

    async def initialize(self) -> None:
        if self._initialized and self.session is not None:
            logger.debug(f"Server {self.name} already initialized")
            return
            
        self._start_time = time.time()
        self._validate_server_config()
        command = self._resolve_command()
        
        server_params = StdioServerParameters(
            command=command,
            args=self.config.get("args", []),
            env=self.config.get("env") or None,
        )
        
        try:
            connection_task = self._connect_to_server(server_params)
            await asyncio.wait_for(connection_task, timeout=30.0)
            
            elapsed = time.time() - self._start_time
            logger.info(f"Server {self.name} initialized in {elapsed:.2f} seconds")
            self._initialized = True
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout while initializing server {self.name}")
            await self.cleanup()
            raise TimeoutError(f"Timed out while connecting to MCP server '{self.name}'")
        except Exception as e:
            logger.error(f"Error initializing server {self.name}: {str(e)}")
            await self.cleanup()
            if isinstance(e, ValueError):
                raise
            elif "connection" in str(e).lower():
                raise ConnectionError(f"Failed to connect to MCP server '{self.name}': {str(e)}")
            else:
                raise RuntimeError(f"Failed to initialize MCP server '{self.name}': {str(e)}")
    
    def _validate_server_config(self) -> None:
        required_keys = ["command", "args"]
        for key in required_keys:
            if key not in self.config:
                raise ValueError(f"Missing required config key '{key}' for server '{self.name}'")
                
        if not isinstance(self.config["command"], str) or not self.config["command"].strip():
            raise ValueError(f"Invalid command for server '{self.name}'")
            
        if not isinstance(self.config["args"], list):
            raise ValueError(f"Invalid args for server '{self.name}'")
            
        command = self.config["command"]
        if command not in ALLOWED_COMMANDS:
            logger.warning(f"Command '{command}' not in allowed commands list")
            
        for i, arg in enumerate(self.config["args"]):
            if not isinstance(arg, str):
                raise ValueError(f"Argument {i} for server '{self.name}' is not a string")
                
            suspicious_patterns = ['&', '|', ';', '`', '$', '>', '<', '\\', '"', "'", '&&', '||']
            for pattern in suspicious_patterns:
                if pattern in arg:
                    logger.warning(f"Suspicious character '{pattern}' in argument")
        
        if "env" in self.config:
            if not isinstance(self.config["env"], dict):
                raise ValueError(f"Invalid env for server '{self.name}'")
                
            for key, value in self.config["env"].items():
                if not isinstance(key, str):
                    raise ValueError(f"Invalid env key for server '{self.name}'")
                if not (isinstance(value, str) or value is None):
                    raise ValueError(f"Invalid env value for server '{self.name}'")
    
    def _resolve_command(self) -> str:
        command = self.config["command"]
        
        if command not in ALLOWED_COMMANDS:
            logger.error(f"Command '{command}' not in allowed commands list")
            raise ValueError(f"Command '{command}' is not allowed")
        
        if command == "npx":
            resolved_command = shutil.which("npx")
            if resolved_command is None:
                raise ValueError("Could not find 'npx' in PATH")
            logger.debug(f"Resolved 'npx' to {resolved_command}")
            return resolved_command
        
        if os.path.isabs(command):
            if not os.path.exists(command):
                raise ValueError(f"Command not found: {command}")
            if not os.access(command, os.X_OK):
                raise ValueError(f"Command is not executable: {command}")
            logger.debug(f"Using absolute path command: {command}")
        else:
            resolved_command = shutil.which(command)
            if resolved_command is None:
                raise ValueError(f"Command not found in PATH: {command}")
            command = resolved_command
            logger.debug(f"Resolved command to {command}")
            
        return command
            
    async def _connect_to_server(self, server_params: StdioServerParameters) -> None:
        try:
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read, write = stdio_transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            
            init_task = asyncio.create_task(session.initialize())
            await asyncio.wait_for(init_task, timeout=20.0)
            
            self.session = session
            
        except Exception as e:
            logger.error(f"Error connecting to server {self.name}: {str(e)}")
            await self.cleanup()
            raise

    async def create_pydantic_ai_tools(self) -> List[PydanticTool]:
        if not self._initialized or self.session is None:
            raise RuntimeError(f"Server '{self.name}' is not initialized")
            
        try:
            tools_result = await asyncio.wait_for(
                self.session.list_tools(), 
                timeout=10.0
            )
            tools = tools_result.tools
            logger.info(f"Retrieved {len(tools)} tools from server '{self.name}'")
            return [self.create_tool_instance(tool) for tool in tools]
        except asyncio.TimeoutError:
            logger.error(f"Timeout while retrieving tools from server '{self.name}'")
            raise RuntimeError(f"Timed out while retrieving tools")
        except Exception as e:
            logger.error(f"Error retrieving tools from server '{self.name}': {str(e)}")
            raise RuntimeError(f"Failed to retrieve tools: {str(e)}")

    def create_tool_instance(self, tool: MCPTool) -> PydanticTool:
        async def execute_tool(**kwargs: Any) -> Any:
            if not self._initialized or self.session is None:
                raise RuntimeError(f"Server '{self.name}' is not initialized")
            
            if not self._rate_limiter.is_allowed():
                logger.warning(f"Rate limit exceeded for server '{self.name}'")
                raise RuntimeError("Rate limit exceeded")
                
            logger.debug(f"Executing tool '{tool.name}' with params: {kwargs}")
                
            async with MCPServer._tool_call_lock:
                if MCPServer._concurrent_tool_calls >= MAX_CONCURRENT_TOOL_CALLS:
                    logger.warning(f"Too many concurrent tool executions")
                    raise RuntimeError("Too many concurrent tool calls")
                MCPServer._concurrent_tool_calls += 1
                
            try:
                if tool.inputSchema and isinstance(kwargs, dict):
                    self._validate_tool_input(tool.name, tool.inputSchema, kwargs)
                    
                start_time = time.time()
                result = await asyncio.wait_for(
                    self.session.call_tool(tool.name, arguments=kwargs),
                    timeout=60.0
                )
                elapsed = time.time() - start_time
                logger.debug(f"Tool '{tool.name}' executed in {elapsed:.2f} seconds")
                return result
                
            except asyncio.TimeoutError:
                logger.error(f"Timeout executing tool '{tool.name}'")
                raise TimeoutError(f"Tool execution timed out: '{tool.name}'")
            except Exception as e:
                logger.error(f"Error executing tool '{tool.name}': {str(e)}")
                raise RuntimeError(f"Tool execution error: {str(e)}")
            finally:
                async with MCPServer._tool_call_lock:
                    MCPServer._concurrent_tool_calls -= 1
    
    def _validate_tool_input(self, tool_name: str, schema: Dict[str, Any], data: Dict[str, Any]) -> None:
        if not isinstance(schema, dict) or not isinstance(data, dict):
            return
            
        if 'required' in schema and isinstance(schema['required'], list):
            for field in schema['required']:
                if field not in data:
                    logger.warning(f"Missing required field '{field}'")
                    
        if 'properties' in schema and isinstance(schema['properties'], dict):
            for field, field_schema in schema['properties'].items():
                if field in data and 'type' in field_schema:
                    expected_type = field_schema['type']
                    value = data[field]
                    
                    valid = True
                    if expected_type == 'string' and not isinstance(value, str):
                        valid = False
                    elif expected_type == 'number' and not isinstance(value, (int, float)):
                        valid = False
                    elif expected_type == 'integer' and not isinstance(value, int):
                        valid = False
                    elif expected_type == 'boolean' and not isinstance(value, bool):
                        valid = False
                    elif expected_type == 'array' and not isinstance(value, list):
                        valid = False
                    elif expected_type == 'object' and not isinstance(value, dict):
                        valid = False
                        
                    if not valid:
                        logger.warning(f"Type mismatch for field '{field}'")

    async def cleanup(self) -> None:
        async with self._cleanup_lock:
            if not self._initialized:
                logger.debug(f"Server {self.name} was not initialized")
                return
                
            try:
                logger.debug(f"Starting cleanup for server {self.name}")
                
                close_task = asyncio.create_task(self.exit_stack.aclose())
                try:
                    await asyncio.wait_for(close_task, timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout during exit stack cleanup")
                    close_task.cancel()
                    try:
                        await close_task
                    except asyncio.CancelledError:
                        pass
                        
                self.session = None
                self.stdio_context = None
                self._initialized = False
                
                logger.info(f"Successfully cleaned up server {self.name}")
                
            except Exception as e:
                logger.error(f"Error during cleanup: {str(e)}")
            finally:
                self._initialized = False