# MCP Chat Server

A FastAPI-based chat server that integrates with Model Context Protocol (MCP) servers to provide enhanced AI capabilities.

## Features

- Real-time chat using WebSocket connections
- Integration with multiple MCP servers (Brave Search, Google Search, Weather, etc.)
- Modern web interface with responsive design
- Support for streaming responses
- Markdown rendering in chat messages

## Prerequisites

- Python 3.12+
- Node.js and npm (for MCP servers)
- API keys for OpenAI and other services you plan to use

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/TechNavii/MCP-CHAT-SERVER.git
   cd MCP-CHAT-SERVER
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up configuration:
   - Copy `example.env` to `.env` and update with your API keys
   - Copy `example.mcp_config.json` to `mcp_config.json` and configure your MCP servers

5. Start the server:
   ```bash
   python app.py
   ```

6. Open your browser and navigate to:
   ```
   http://localhost:8000
   ```

## Configuration

### Environment Variables (.env)

- `MODEL_CHOICE`: OpenAI model to use (default: gpt-4-turbo-preview)
- `BASE_URL`: OpenAI API base URL
- `LLM_API_KEY`: Your OpenAI API key
- `PORT`: Server port (default: 8000)
- `HOST`: Server host (default: 0.0.0.0)
- `LOG_LEVEL`: Logging level (default: INFO)

### MCP Configuration (mcp_config.json)

Configure your MCP servers in `mcp_config.json`. Each server requires:
- Command to start the server
- Arguments for the command
- Environment variables (API keys, etc.)

See `example.mcp_config.json` for a sample configuration.

## Development

The project structure:
- `app.py`: Main FastAPI application
- `mcp_client.py`: MCP client implementation
- `pydantic_mcp_agent.py`: Pydantic AI agent implementation
- `static/`: Frontend files (HTML, CSS, JavaScript)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

MIT License - see LICENSE file for details