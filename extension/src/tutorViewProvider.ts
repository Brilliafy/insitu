import * as vscode from 'vscode';

export interface SocraticState {
    mode: number;
    modeName: string;
    sttModel?: string;
    lastHighlight?: {
        file: string;
        startLine: number;
        endLine: number;
        message?: string;
    };
    lastSpoken?: string;
    isRecording?: boolean;
    isSpeaking?: boolean;
    silenceAutostop?: boolean;
    transcriptHistory: Array<{ role: 'tutor' | 'user'; text: string; time: string }>;
}

export class SocraticTutorViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'insituView';
    private _view?: vscode.WebviewView;

    constructor(
        private readonly _extensionUri: vscode.Uri,
        private _state: SocraticState,
        private readonly _onModeChange: (mode: number) => void,
        private readonly _onSendPrompt: (prompt: string) => void,
        private readonly _onReplayVoice: (text: string) => void,
        private readonly _onToggleMic: () => void
    ) {}

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = this.getHtmlContent(webviewView.webview);

        webviewView.webview.onDidReceiveMessage(message => {
            switch (message.type) {
                case 'setMode':
                    this._onModeChange(message.mode);
                    break;
                case 'sendPrompt':
                    this._onSendPrompt(message.prompt);
                    break;
                case 'replayVoice':
                    this._onReplayVoice(message.text || this._state.lastSpoken || '');
                    break;
                case 'toggleMic':
                    this._onToggleMic();
                    break;
                case 'stopMic':
                    vscode.commands.executeCommand('insitu.stopMic');
                    break;
                case 'cancelMic':
                    vscode.commands.executeCommand('insitu.cancelMic');
                    break;
                case 'toggleVAD':
                    vscode.commands.executeCommand('insitu.toggleVAD');
                    break;
                case 'inspectSelection':
                    vscode.commands.executeCommand('insitu.sendSelection');
                    break;
                case 'selectSTTModel':
                    vscode.commands.executeCommand('insitu.selectSTTModel');
                    break;
                case 'stopSpeech':
                    vscode.commands.executeCommand('insitu.stopSpeech');
                    break;
                case 'editPromptPrefix':
                    vscode.commands.executeCommand('insitu.editPromptPrefix');
                    break;
            }
        });

        this.syncState();
    }

    public updateState(newState: Partial<SocraticState>) {
        this._state = { ...this._state, ...newState };
        this.syncState();
    }

    public postMessageToWebview(msg: any) {
        if (this._view) {
            this._view.webview.postMessage(msg);
        }
    }

    public syncState() {
        if (this._view) {
            this._view.webview.postMessage({
                type: 'syncState',
                state: this._state
            });
        }
    }

    public getHtmlContent(_webview: vscode.Webview): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>InSitu Mentor Bar</title>
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            user-select: none;
        }

        body {
            background: transparent;
            padding: 10px 8px;
            display: flex;
            flex-direction: column;
            align-items: center;
            overflow: visible;
        }

        .capsule-bar {
            width: 100%;
            max-width: 580px;
            height: 46px;
            background: #1f1f21;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 23px;
            display: flex;
            align-items: center;
            padding: 0 10px 0 14px;
            gap: 8px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
            position: relative;
        }

        .btn-plus {
            background: none;
            border: none;
            color: #a0a0a5;
            font-size: 20px;
            font-weight: 300;
            cursor: pointer;
            width: 26px;
            height: 26px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            transition: all 0.15s;
        }

        .btn-plus:hover {
            color: #ffffff;
            background: rgba(255, 255, 255, 0.1);
        }

        .input-prompt {
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            color: #e5e5e7;
            font-size: 13px;
            min-width: 80px;
            user-select: text;
        }

        .input-prompt::placeholder {
            color: #71717a;
            font-size: 12.5px;
        }

        .mode-pill {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #d4d4d8;
            font-size: 11.5px;
            font-weight: 500;
            padding: 5px 10px;
            border-radius: 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
            white-space: nowrap;
            transition: all 0.15s;
        }

        .mode-pill:hover {
            background: rgba(255, 255, 255, 0.12);
            color: #ffffff;
            border-color: rgba(255, 255, 255, 0.2);
        }

        .btn-mic {
            background: none;
            border: none;
            color: #a0a0a5;
            font-size: 16px;
            cursor: pointer;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            transition: all 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }

        .btn-mic:hover {
            color: #ffffff;
            background: rgba(255, 255, 255, 0.1);
            transform: scale(1.05);
        }

        .btn-mic.recording {
            background: #dc2626;
            color: #ffffff;
            animation: pulse-ring 1.2s infinite;
        }

        @keyframes pulse-ring {
            0% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); }
            100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); }
        }

        .rec-controls {
            display: none;
            align-items: center;
            gap: 4px;
        }

        .rec-controls.active {
            display: flex;
        }

        .btn-action-sm {
            background: rgba(255, 255, 255, 0.1);
            border: none;
            color: #e5e5e7;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.15s;
        }

        .btn-action-sm:hover {
            background: rgba(255, 255, 255, 0.2);
            color: #fff;
        }

        .btn-action-sm.stop {
            background: #ef4444;
            color: #fff;
        }

        .btn-action-sm.stop-speech {
            background: #4f46e5;
            color: #fff;
            display: none;
            align-items: center;
            gap: 4px;
        }

        .btn-action-sm.stop-speech.active {
            display: flex;
        }

        /* Popup Menus */
        .popup-menu {
            position: absolute;
            top: 52px;
            background: #232326;
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 10px;
            padding: 6px;
            display: none;
            flex-direction: column;
            gap: 2px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
            z-index: 1000;
            min-width: 210px;
        }

        .popup-menu.show { display: flex; }
        .popup-menu.left { left: 10px; }
        .popup-menu.right { right: 40px; }

        .menu-item {
            padding: 7px 12px;
            border-radius: 6px;
            font-size: 12px;
            color: #d4d4d8;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: background 0.15s;
        }

        .menu-item:hover {
            background: rgba(255, 255, 255, 0.1);
            color: #ffffff;
        }

        .menu-item.active {
            color: #e5a93b;
            font-weight: 600;
        }

        .menu-divider {
            height: 1px;
            background: rgba(255, 255, 255, 0.1);
            margin: 4px 0;
        }
    </style>
</head>
<body>

    <div class="capsule-bar">
        <button class="btn-plus" onclick="togglePlusMenu(event)">+</button>

        <input type="text" class="input-prompt" id="prompt-input" placeholder="Ask Tutor or speak..." onkeydown="handleKey(event)" />

        <!-- Recording controls (Manual Stop & Cancel) -->
        <div class="rec-controls" id="rec-controls">
            <button class="btn-action-sm stop" onclick="stopRecording()" title="Stop recording and prompt AI now">■ Stop</button>
            <button class="btn-action-sm" onclick="cancelRecording()" title="Cancel and discard audio">✕ Cancel</button>
        </div>

        <button class="btn-action-sm stop-speech" id="stop-speech-btn" onclick="stopSpeech()" title="Stop voice speech">🔇 Stop Speech</button>

        <div class="mode-pill" id="mode-btn" onclick="toggleModeMenu(event)">
            <span id="mode-label">Mode 1: Section</span>
            <span style="font-size: 9px; opacity: 0.7;">▾</span>
        </div>

        <button class="btn-mic" id="mic-btn" onclick="toggleMic()" title="Tap to speak (Moonshine ONNX)">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="22"/>
            </svg>
        </button>

        <!-- Mode Menu -->
        <div class="popup-menu right" id="mode-menu">
            <div class="menu-item active" id="m1-opt" onclick="setMode(1)">
                <span>Mode 1: Section Scaffold</span>
                <span>●</span>
            </div>
            <div class="menu-item" id="m2-opt" onclick="setMode(2)">
                <span>Mode 2: Line-by-Line</span>
                <span></span>
            </div>
            <div class="menu-item" id="m3-opt" onclick="setMode(3)">
                <span>Mode 3: Refactor / Doubt</span>
                <span></span>
            </div>
        </div>

        <!-- Plus Menu -->
        <div class="popup-menu left" id="plus-menu">
            <div class="menu-item" onclick="inspectSelection()">
                <span>🔍 Ask Doubt on Selection</span>
            </div>
            <div class="menu-item" onclick="replayVoice()">
                <span>🔊 Replay Last Speech</span>
            </div>
            <div class="menu-item" onclick="stopSpeech()">
                <span>🔇 Stop Playing Speech</span>
            </div>
            <div class="menu-divider"></div>
            <div class="menu-item" onclick="toggleVAD()">
                <span id="vad-label">⚡ Silence Autostop: 2s (ON)</span>
            </div>
            <div class="menu-divider"></div>
            <div class="menu-item" onclick="selectSTTModel()">
                <span>🎙️ STT Model: <b id="stt-model-name">Moonshine</b> ▾</span>
            </div>
            <div class="menu-item" onclick="editPromptPrefix()">
                <span>🏷️ Coding Terms Bias...</span>
            </div>
            <div class="menu-item" onclick="clearInput()">
                <span>🗑️ Clear Input</span>
            </div>
        </div>
    </div>

    <script>
        const vscode = acquireVsCodeApi();
        let currentMode = 1;
        let isRecording = false;
        let silenceAutostop = true;

        function toggleModeMenu(e) {
            e.stopPropagation();
            closePlusMenu();
            document.getElementById('mode-menu').classList.toggle('show');
        }

        function togglePlusMenu(e) {
            e.stopPropagation();
            closeModeMenu();
            document.getElementById('plus-menu').classList.toggle('show');
        }

        function closeModeMenu() {
            document.getElementById('mode-menu').classList.remove('show');
        }

        function closePlusMenu() {
            document.getElementById('plus-menu').classList.remove('show');
        }

        window.onclick = function() {
            closeModeMenu();
            closePlusMenu();
        };

        function setMode(m) {
            currentMode = m;
            updateModeUI(m);
            vscode.postMessage({ type: 'setMode', mode: m });
            closeModeMenu();
        }

        function updateModeUI(m) {
            const labels = {
                1: 'Mode 1: Section',
                2: 'Mode 2: Line',
                3: 'Mode 3: Refactor'
            };
            document.getElementById('mode-label').innerText = labels[m] || 'Mode 1: Section';
            for (let i = 1; i <= 3; i++) {
                const el = document.getElementById('m' + i + '-opt');
                if (el) {
                    if (i === m) {
                        el.classList.add('active');
                        el.children[1].innerText = '●';
                    } else {
                        el.classList.remove('active');
                        el.children[1].innerText = '';
                    }
                }
            }
        }

        function inspectSelection() {
            closePlusMenu();
            vscode.postMessage({ type: 'inspectSelection' });
        }

        function replayVoice() {
            closePlusMenu();
            vscode.postMessage({ type: 'replayVoice' });
        }

        function stopSpeech() {
            closePlusMenu();
            vscode.postMessage({ type: 'stopSpeech' });
        }

        function selectSTTModel() {
            closePlusMenu();
            vscode.postMessage({ type: 'selectSTTModel' });
        }

        function editPromptPrefix() {
            closePlusMenu();
            vscode.postMessage({ type: 'editPromptPrefix' });
        }

        function clearInput() {
            document.getElementById('prompt-input').value = '';
            closePlusMenu();
        }

        function toggleVAD() {
            silenceAutostop = !silenceAutostop;
            document.getElementById('vad-label').innerText = silenceAutostop ? '⚡ Silence Autostop: 2s (ON)' : '⚡ Silence Autostop: OFF';
            vscode.postMessage({ type: 'toggleVAD' });
            closePlusMenu();
        }

        function toggleMic() {
            vscode.postMessage({ type: 'toggleMic' });
        }

        function stopRecording() {
            vscode.postMessage({ type: 'stopMic' });
        }

        function cancelRecording() {
            vscode.postMessage({ type: 'cancelMic' });
        }

        function handleKey(e) {
            if (e.key === 'Enter') {
                const text = document.getElementById('prompt-input').value.trim();
                if (text) {
                    vscode.postMessage({ type: 'sendPrompt', prompt: text });
                    document.getElementById('prompt-input').value = '';
                }
            }
        }

        window.addEventListener('message', event => {
            const msg = event.data;
            switch (msg.type) {
                case 'syncState':
                    if (msg.state) {
                        currentMode = msg.state.mode || 1;
                        updateModeUI(currentMode);
                        if (msg.state.sttModel) {
                            const name = msg.state.sttModel.split('/').pop() || msg.state.sttModel;
                            const el = document.getElementById('stt-model-name');
                            if (el) el.innerText = name;
                        }
                        const stopBtn = document.getElementById('stop-speech-btn');
                        if (stopBtn) {
                            if (msg.state.isSpeaking) stopBtn.classList.add('active');
                            else stopBtn.classList.remove('active');
                        }
                    }
                    break;
                case 'speakingStatus':
                    const stopBtn = document.getElementById('stop-speech-btn');
                    if (stopBtn) {
                        if (msg.isSpeaking) stopBtn.classList.add('active');
                        else stopBtn.classList.remove('active');
                    }
                    break;
                case 'recordingStatus':
                    isRecording = msg.isRecording;
                    const micBtn = document.getElementById('mic-btn');
                    const input = document.getElementById('prompt-input');
                    const recControls = document.getElementById('rec-controls');
                    if (isRecording) {
                        micBtn.classList.add('recording');
                        recControls.classList.add('active');
                        input.placeholder = msg.silenceAutostop ? "Listening... (Stops 2s after speech)" : "Listening... (Click ■ Stop when done)";
                    } else {
                        micBtn.classList.remove('recording');
                        recControls.classList.remove('active');
                        input.placeholder = "Ask Tutor or speak...";
                    }
                    break;
                case 'transcriptionResult':
                    if (msg.text) {
                        document.getElementById('prompt-input').value = msg.text;
                        vscode.postMessage({ type: 'sendPrompt', prompt: msg.text });
                        document.getElementById('prompt-input').value = '';
                    }
                    break;
            }
        });

        updateModeUI(1);
    </script>
</body>
</html>`;
    }
}
