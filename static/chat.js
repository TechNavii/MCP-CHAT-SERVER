class ChatApp {
    constructor() {
        this.ws = null;
        this.messageHistory = []; // Stores { role: 'user' | 'assistant', content: string }
        this.isTyping = false;
        this.currentAssistantMessage = '';
        this.currentAssistantMessageDiv = null;

        // DOM elements
        this.messagesContainer = document.getElementById('chat-messages');
        this.messageInput = document.getElementById('message-input');
        this.sendButton = document.getElementById('send-button');

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
            this.addSystemMessage('Connected to the chat server.');
        };

        this.ws.onclose = (event) => {
            console.log('WebSocket connection closed:', event.code, event.reason);
            this.sendButton.disabled = true;
            this.addSystemMessage('Connection lost. Attempting to reconnect...');
            // Attempt to reconnect after 5 seconds
            setTimeout(() => this.initWebSocket(), 5000);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
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
                        this.finalizeAssistantMessage(data.content); // Pass final content
                        break;
                    case 'error':
                        this.showError(data.content);
                        // Potentially end typing indicator here if an error occurs mid-stream
                        if (this.isTyping) {
                            this.finalizeAssistantMessage('', true); // Mark as error state
                        }
                        break;
                    default:
                        console.warn('Received unknown message type:', data.type);
                }
            } catch (e) {
                console.error('Failed to parse WebSocket message or handle data:', e);
                this.showError('Received malformed data from server.');
            }
        };
    }

    setupEventListeners() {
        // Auto-resize textarea
        this.messageInput.addEventListener('input', () => {
            this.messageInput.style.height = 'auto';
            const scrollHeight = this.messageInput.scrollHeight;
            // Limit max height
            const maxHeight = 150;
            this.messageInput.style.height = `${Math.min(scrollHeight, maxHeight)}px`;
        });

        // Send button click
        this.sendButton.addEventListener('click', this.sendMessage);

        // Keydown listener (bound in constructor)
        this.messageInput.addEventListener('keydown', this.handleKeyDown);

         // Ensure button is disabled initially until WS connects
        this.sendButton.disabled = true;
    }

    addMessage(content, role, isTyping = false) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}${isTyping ? ' typing' : ''}`;
        // Use a library like DOMPurify or manually sanitize if content can be malicious
        // For now, assuming markdown conversion handles basic safety
        messageDiv.innerHTML = this.formatMarkdown(content);
        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();
        return messageDiv;
    }

    addSystemMessage(content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message system'; // Add a CSS class for styling system messages
        messageDiv.textContent = content;
        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();
    }

    updateAssistantMessage(delta) {
        if (!this.isTyping) {
            this.isTyping = true;
            this.currentAssistantMessage = '';
            // Create a new div for the assistant's message
            this.currentAssistantMessageDiv = this.addMessage('...', 'assistant', true);
        }

        this.currentAssistantMessage += delta;
        // Update the content of the existing div
        this.currentAssistantMessageDiv.innerHTML = this.formatMarkdown(this.currentAssistantMessage + '...'); // Keep typing indicator
        this.scrollToBottom(); // Keep scrolling as content grows
    }

    finalizeAssistantMessage(finalContent, isError = false) {
        if (this.isTyping && this.currentAssistantMessageDiv) {
            this.isTyping = false;
            this.currentAssistantMessageDiv.innerHTML = this.formatMarkdown(finalContent || this.currentAssistantMessage);
            this.currentAssistantMessageDiv.classList.remove('typing');

            if (!isError) {
                 // Add the complete message to history only if it wasn't an error state
                this.messageHistory.push({
                    role: 'assistant',
                    content: finalContent || this.currentAssistantMessage
                });
            }
        }
        this.currentAssistantMessage = '';
        this.currentAssistantMessageDiv = null;
        this.sendButton.disabled = false; // Re-enable send button
        this.messageInput.disabled = false;
        this.messageInput.focus();
    }

    showError(error) {
        console.error("Chat Error:", error);
        this.addMessage(`Error: ${error}`, 'error');
    }

    formatMarkdown(content) {
        // Basic Markdown to HTML conversion (consider a library like Marked.js for complex cases)
        if (typeof content !== 'string') return '';
        let html = content
            .replace(/```([\s\S]*?)```/g, (match, p1) => `<pre><code>${p1.trim().replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`)
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^\*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^\*]+)\*/g, '<em>$1</em>')
            .replace(/^# (.*$)/g, '<h1>$1</h1>')
            .replace(/^## (.*$)/g, '<h2>$1</h2>')
            .replace(/^### (.*$)/g, '<h3>$1</h3>')
            .replace(/\n/g, '<br>'); // Replace newlines with <br>

        // Naive paragraph wrapping for text not in other blocks
        // This is very basic and might need refinement
        html = html.split('<br><br>').map(p => `<p>${p}</p>`).join('');

        return html;
    }

    sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || this.isTyping || this.ws.readyState !== WebSocket.OPEN) {
             if (this.ws.readyState !== WebSocket.OPEN) {
                this.addSystemMessage("Cannot send message: Not connected.");
             }
            return;
        }

        console.log('Sending message:', message);

        // Add user message to UI and history
        this.addMessage(message, 'user');
        this.messageHistory.push({
            role: 'user',
            content: message
        });

        // Disable input while waiting for response
        this.sendButton.disabled = true;
        this.messageInput.disabled = true;

        // Send message to server
        try {
            this.ws.send(JSON.stringify({
                message: message,
                // Send only the relevant parts of history if needed, or trim if too long
                history: this.messageHistory // Send the current history
            }));
        } catch (e) {
            console.error("Failed to send message:", e);
            this.showError("Failed to send message.");
            this.sendButton.disabled = false; // Re-enable on error
            this.messageInput.disabled = false;
        }

        // Clear input
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto'; // Reset height
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

// Initialize chat app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Make ChatApp instance globally accessible for inline event handlers
    // Consider alternative approaches for larger applications (e.g., event delegation)
    window.chatApp = new ChatApp();

    // Expose sendMessage and handleKeyDown globally for the inline HTML handlers
    window.sendMessage = window.chatApp.sendMessage;
    window.handleKeyDown = window.chatApp.handleKeyDown;
});