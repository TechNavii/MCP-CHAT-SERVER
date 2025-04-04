class ChatApp {
    constructor() {
        // Initialize properties
        this.ws = null;
        this.tools = [];
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;

        // Get DOM elements
        this.messageInput = document.getElementById('message-input');
        this.sendButton = document.getElementById('send-button');
        this.chatMessages = document.getElementById('chat-messages');
        this.statusIndicator = document.querySelector('.status-indicator');
        this.statusText = document.querySelector('.status-text');
        this.toolsPanel = document.getElementById('tools-panel');
        this.toolsList = document.getElementById('tools-list');
        this.toggleToolsBtn = document.getElementById('toggle-tools-btn');
        this.closeToolsBtn = document.querySelector('.close-tools-btn');

        // Get templates
        this.messageTemplate = document.getElementById('message-template');
        this.loadingTemplate = document.getElementById('loading-template');
        this.errorTemplate = document.getElementById('error-template');
        this.toolTemplate = document.getElementById('tool-template');

        // Initialize event listeners
        this.initEventListeners();

        // Initialize WebSocket connection
        this.initWebSocket();

        // Configure marked options
        marked.setOptions({
            highlight: function(code, lang) {
                const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                return hljs.highlight(code, { language }).value;
            },
            langPrefix: 'hljs language-'
        });
    }

    initEventListeners() {
        // Message sending
        this.sendButton.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Input handling
        this.messageInput.addEventListener('input', () => {
            this.messageInput.style.height = 'auto';
            this.messageInput.style.height = this.messageInput.scrollHeight + 'px';
            this.sendButton.disabled = !this.messageInput.value.trim();
        });

        // Tools panel
        this.toggleToolsBtn.addEventListener('click', () => this.toggleTools());
        this.closeToolsBtn.addEventListener('click', () => this.toggleTools());

        // Initialize send button as disabled
        this.sendButton.disabled = true;
    }

    initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/chat`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connection established');
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.updateConnectionStatus(true);
        };

        this.ws.onclose = () => {
            console.log('WebSocket connection closed');
            this.isConnected = false;
            this.updateConnectionStatus(false);
            this.handleReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showError('Connection error occurred');
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('Received message:', data);

            switch (data.type) {
                case 'tools':
                    this.handleToolsUpdate(data.content);
                    break;
                case 'delta':
                    this.handleMessageDelta(data.content);
                    break;
                case 'complete':
                    this.finalizeAssistantMessage(data.content);
                    break;
                case 'error':
                    this.showError(data.content);
                    break;
                default:
                    console.warn('Unknown message type:', data.type);
            }
        };
    }

    updateConnectionStatus(connected) {
        this.statusIndicator.classList.toggle('connected', connected);
        this.statusText.textContent = connected ? 'Connected' : 'Disconnected';
        this.sendButton.disabled = !connected || !this.messageInput.value.trim();
    }

    handleReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
            setTimeout(() => this.initWebSocket(), this.reconnectDelay * this.reconnectAttempts);
        } else {
            this.showError('Could not reconnect to the server. Please refresh the page.');
        }
    }

    handleToolsUpdate(tools) {
        console.log('Updating tools:', tools);
        this.tools = tools;
        this.renderTools();
    }

    renderTools() {
        this.toolsList.innerHTML = '';
        this.tools.forEach(tool => {
            const toolElement = this.toolTemplate.content.cloneNode(true);
            toolElement.querySelector('.tool-name').textContent = tool.name;
            toolElement.querySelector('.tool-description').textContent = tool.description;

            if (tool.parameters) {
                const parametersElement = toolElement.querySelector('.tool-parameters');
                parametersElement.textContent = JSON.stringify(tool.parameters, null, 2);
            }

            this.toolsList.appendChild(toolElement);
        });
    }

    toggleTools() {
        this.toolsPanel.classList.toggle('visible');
        const isVisible = this.toolsPanel.classList.contains('visible');
        this.toggleToolsBtn.setAttribute('aria-expanded', isVisible.toString());
    }

    async sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || !this.isConnected) return;

        // Add user message
        this.addMessage(message, 'user');

        // Clear input and reset height
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto';
        this.sendButton.disabled = true;

        // Add loading indicator
        this.addLoadingIndicator();

        try {
            await this.ws.send(JSON.stringify({
                message: message,
                history: [] // Currently not using history
            }));
        } catch (error) {
            console.error('Error sending message:', error);
            this.showError('Failed to send message');
            this.removeLoadingIndicator();
        }
    }

    addMessage(content, role = 'assistant') {
        const messageElement = this.messageTemplate.content.cloneNode(true);
        const messageDiv = messageElement.querySelector('.message');
        messageDiv.classList.add(role);

        const contentDiv = messageElement.querySelector('.message-content');
        contentDiv.innerHTML = role === 'user' ? content : this.formatMarkdown(content);

        this.chatMessages.appendChild(messageElement);
        this.scrollToBottom();
    }

    addLoadingIndicator() {
        const loadingElement = this.loadingTemplate.content.cloneNode(true);
        loadingElement.querySelector('.loading-indicator').id = 'current-loading';
        this.chatMessages.appendChild(loadingElement);
        this.scrollToBottom();
    }

    removeLoadingIndicator() {
        const loadingIndicator = document.getElementById('current-loading');
        if (loadingIndicator) {
            loadingIndicator.remove();
        }
    }

    handleMessageDelta(delta) {
        let currentMessage = document.querySelector('.message.assistant:last-child');
        
        if (!currentMessage) {
            this.removeLoadingIndicator();
            this.addMessage('');
            currentMessage = document.querySelector('.message.assistant:last-child');
        }

        const contentDiv = currentMessage.querySelector('.message-content');
        contentDiv.innerHTML = this.formatMarkdown(contentDiv.textContent + delta);
        this.scrollToBottom();
    }

    finalizeAssistantMessage(message) {
        this.removeLoadingIndicator();
        const lastMessage = document.querySelector('.message.assistant:last-child');
        
        if (!lastMessage) {
            this.addMessage(message);
        } else {
            const contentDiv = lastMessage.querySelector('.message-content');
            contentDiv.innerHTML = this.formatMarkdown(message);
        }
        
        this.scrollToBottom();
    }

    showError(message) {
        const errorElement = this.errorTemplate.content.cloneNode(true);
        errorElement.querySelector('.error-text').textContent = message;
        this.chatMessages.appendChild(errorElement);
        this.scrollToBottom();
    }

    formatMarkdown(text) {
        return marked(text);
    }

    scrollToBottom() {
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }
}

// Initialize the chat application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.chatApp = new ChatApp();
});