class ChatApp {
    constructor() {
        this.ws = null;
        this.messageHistory = [];
        this.isTyping = false;
        this.currentAssistantMessage = '';
        this.currentAssistantMessageDiv = null;

        // DOM elements
        this.messagesContainer = document.getElementById('chat-messages');
        this.messageInput = document.getElementById('message-input');
        this.sendButton = document.getElementById('send-button');
        this.statusIndicator = document.querySelector('.status');
        this.statusText = this.statusIndicator.querySelector('span');

        // Templates
        this.messageTemplate = document.getElementById('messageTemplate');
        this.loadingTemplate = document.getElementById('loadingTemplate');
        this.errorTemplate = document.getElementById('errorTemplate');

        // Bind methods
        this.sendMessage = this.sendMessage.bind(this);
        this.handleKeyDown = this.handleKeyDown.bind(this);

        // Initialize
        this.initWebSocket();
        this.setupEventListeners();
        console.log('ChatApp initialized');
    }

    initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/chat`;
        console.log(`Connecting to WebSocket: ${wsUrl}`);

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connection established');
            this.sendButton.disabled = false;
            this.updateConnectionStatus(true);
            this.addSystemMessage('Connected to the chat server.');
        };

        this.ws.onclose = (event) => {
            console.log('WebSocket connection closed:', event.code, event.reason);
            this.sendButton.disabled = true;
            this.updateConnectionStatus(false);
            this.addSystemMessage('Connection lost. Attempting to reconnect...');
            setTimeout(() => this.initWebSocket(), 5000);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionStatus(false, 'Error');
            this.addSystemMessage('WebSocket connection error.');
        };

        this.ws.onmessage = (event) => {
            console.debug('WebSocket message received:', event.data);
            try {
                const data = JSON.parse(event.data);

                switch (data.type) {
                    case 'delta':
                        this.updateAssistantMessage(data.content);
                        break;
                    case 'complete':
                        this.finalizeAssistantMessage(data.content);
                        break;
                    case 'error':
                        this.showError(data.content);
                        if (this.isTyping) {
                            this.finalizeAssistantMessage('', true);
                        }
                        break;
                    default:
                        console.warn('Received unknown message type:', data.type);
                }
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
                this.showError('Received malformed data from server.');
            }
        };
    }

    updateConnectionStatus(isConnected, status = null) {
        const statusDot = this.statusIndicator.querySelector('.status-indicator');
        const statusText = this.statusIndicator.querySelector('span');
        
        if (isConnected) {
            statusDot.style.backgroundColor = 'var(--success-color)';
            statusText.textContent = 'Connected';
        } else {
            statusDot.style.backgroundColor = 'var(--error-color)';
            statusText.textContent = status || 'Disconnected';
        }
    }

    setupEventListeners() {
        // Auto-resize textarea
        this.messageInput.addEventListener('input', () => {
            this.messageInput.style.height = 'auto';
            const scrollHeight = this.messageInput.scrollHeight;
            const maxHeight = 150;
            this.messageInput.style.height = `${Math.min(scrollHeight, maxHeight)}px`;
        });

        // Send button click
        this.sendButton.addEventListener('click', this.sendMessage);

        // Keydown listener
        this.messageInput.addEventListener('keydown', this.handleKeyDown);

        // Ensure button is disabled initially until WS connects
        this.sendButton.disabled = true;
    }

    addMessage(content, role, isTyping = false) {
        const messageDiv = this.messageTemplate.content.cloneNode(true).querySelector('.message');
        messageDiv.classList.add(role);
        if (isTyping) messageDiv.classList.add('typing');
        
        const messageContent = messageDiv.querySelector('.message-content');
        messageContent.innerHTML = this.formatMarkdown(content);
        
        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();
        return messageDiv;
    }

    addSystemMessage(content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message system';
        messageDiv.textContent = content;
        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();
    }

    updateAssistantMessage(delta) {
        if (!this.isTyping) {
            this.isTyping = true;
            this.currentAssistantMessage = '';
            this.currentAssistantMessageDiv = this.addMessage('', 'assistant', true);
        }

        this.currentAssistantMessage += delta;
        const messageContent = this.currentAssistantMessageDiv.querySelector('.message-content');
        messageContent.innerHTML = this.formatMarkdown(this.currentAssistantMessage);
        this.scrollToBottom();
    }

    finalizeAssistantMessage(finalContent, isError = false) {
        if (this.isTyping && this.currentAssistantMessageDiv) {
            this.isTyping = false;
            const messageContent = this.currentAssistantMessageDiv.querySelector('.message-content');
            messageContent.innerHTML = this.formatMarkdown(finalContent || this.currentAssistantMessage);
            this.currentAssistantMessageDiv.classList.remove('typing');

            if (!isError) {
                this.messageHistory.push({
                    role: 'assistant',
                    content: finalContent || this.currentAssistantMessage
                });
            }
        }
        this.currentAssistantMessage = '';
        this.currentAssistantMessageDiv = null;
        this.sendButton.disabled = false;
        this.messageInput.disabled = false;
        this.messageInput.focus();
    }

    showError(error) {
        console.error('Chat Error:', error);
        const errorDiv = this.errorTemplate.content.cloneNode(true).querySelector('.error-message');
        errorDiv.querySelector('.error-text').textContent = error;
        this.messagesContainer.appendChild(errorDiv);
        this.scrollToBottom();
    }

    formatMarkdown(content) {
        if (typeof content !== 'string') return '';
        
        // Configure marked options
        marked.setOptions({
            highlight: function(code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return hljs.highlightAuto(code).value;
            },
            breaks: true,
            gfm: true
        });

        try {
            return marked(content);
        } catch (e) {
            console.error('Markdown parsing error:', e);
            return content;
        }
    }

    sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || this.isTyping || this.ws.readyState !== WebSocket.OPEN) {
            if (this.ws.readyState !== WebSocket.OPEN) {
                this.addSystemMessage('Cannot send message: Not connected.');
            }
            return;
        }

        // Add user message to UI and history
        this.addMessage(message, 'user');
        this.messageHistory.push({
            role: 'user',
            content: message
        });

        // Clear and disable input
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto';
        this.sendButton.disabled = true;
        this.messageInput.disabled = true;

        // Send to server
        this.ws.send(JSON.stringify({
            message: message,
            history: this.messageHistory
        }));
    }

    handleKeyDown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            this.sendMessage();
        }
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
}

// Initialize the chat app when the page loads
document.addEventListener('DOMContentLoaded', () => {
    window.chatApp = new ChatApp();
});