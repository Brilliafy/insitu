import * as vscode from 'vscode';
import * as http from 'http';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { spawn, ChildProcess } from 'child_process';
import { SocraticTutorViewProvider, SocraticState } from './tutorViewProvider';

const CTRL_FILE = '/tmp/insitu_mic_ctrl';
const PYTHON_BIN = '/home/Aelfwif/anaconda3/envs/socratic-tutor/bin/python';
const STT_SCRIPT = '/home/Aelfwif/insitu/server/stt_transcriber.py';
const TTS_SCRIPT = '/home/Aelfwif/insitu/server/tts_player.py';
const MODEL_MANAGER_SCRIPT = '/home/Aelfwif/insitu/server/model_manager.py';
const TTS_MANAGER_SCRIPT = '/home/Aelfwif/insitu/server/tts_manager.py';
const DAEMON_SCRIPT = '/home/Aelfwif/insitu/server/insitu_daemon.py';
const DAEMON_URL = 'http://127.0.0.1:6422';

function queryDaemon(endpoint: string, data?: any, method: string = 'POST', timeoutMs: number = 35000): Promise<any> {
    return new Promise((resolve, reject) => {
        try {
            const u = new URL(`${DAEMON_URL}${endpoint}`);
            const postData = data ? JSON.stringify(data) : undefined;
            const options: http.RequestOptions = {
                hostname: u.hostname,
                port: u.port,
                path: u.pathname,
                method: method,
                headers: postData ? {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(postData)
                } : {},
                timeout: timeoutMs
            };

            const req = http.request(options, (res) => {
                let body = '';
                res.on('data', d => body += d);
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(body));
                    } catch {
                        resolve({ raw: body });
                    }
                });
            });

            req.on('error', reject);
            req.on('timeout', () => {
                req.destroy();
                reject(new Error('Daemon request timed out'));
            });

            if (postData) req.write(postData);
            req.end();
        } catch (err) {
            reject(err);
        }
    });
}

async function isDaemonOnline(): Promise<boolean> {
    try {
        const res = await queryDaemon('/status', undefined, 'GET', 500);
        return res && res.status === 'online';
    } catch {
        return false;
    }
}

async function ensureDaemonRunning() {
    const online = await isDaemonOnline();
    if (online) return;

    if (fs.existsSync(DAEMON_SCRIPT) && fs.existsSync(PYTHON_BIN)) {
        console.log('[InSitu] Starting background persistent CUDA daemon...');
        try {
            const child = spawn(PYTHON_BIN, [DAEMON_SCRIPT], {
                detached: true,
                stdio: 'ignore'
            });
            child.unref();

            for (let i = 0; i < 20; i++) {
                await new Promise(r => setTimeout(r, 250));
                if (await isDaemonOnline()) {
                    console.log('[InSitu] Daemon is now online!');
                    break;
                }
            }
        } catch (e) {
            console.warn('[InSitu] Could not spawn daemon:', e);
        }
    }
}

let highlightDecoration: vscode.TextEditorDecorationType;
let currentMode = 1;
let activeSTTModel = 'moonshine/tiny';
let activeSTTDevice = 'cpu';
let activeTTSModel = 'hexgrad/Kokoro-82M';
let activeTTSVoice = 'af_heart';
let activeTTSEngine = 'kokoro';
let activeTTSChild: ChildProcess | undefined = undefined;
let statusBarItem: vscode.StatusBarItem;
let tutorProvider: SocraticTutorViewProvider;
let micProcess: ChildProcess | undefined = undefined;
let isRecordingCancelled = false;
let silenceAutostop = true;
let currentQuickPick: vscode.QuickPick<any> | undefined = undefined;
let activeSTTQuickPick: vscode.QuickPick<any> | undefined = undefined;
let refreshSTTQuickPickItems: (() => Promise<void>) | undefined = undefined;
let activeTTSQuickPick: vscode.QuickPick<any> | undefined = undefined;
let refreshTTSQuickPickItems: (() => Promise<void>) | undefined = undefined;

const state: SocraticState = {
    mode: 1,
    modeName: 'Section',
    sttModel: 'moonshine/tiny',
    isRecording: false,
    transcriptHistory: []
};

function runPython(args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
        const p = spawn(PYTHON_BIN, args);
        let out = '';
        let err = '';
        p.stdout.on('data', d => out += d.toString());
        p.stderr.on('data', d => err += d.toString());
        p.on('close', code => {
            if (code === 0) resolve(out.trim());
            else reject(new Error(err || `Python command failed with exit code ${code}`));
        });
        p.on('error', reject);
    });
}

async function refreshActiveSTTModel() {
    try {
        const out = await runPython([MODEL_MANAGER_SCRIPT, 'get']);
        const cfg = JSON.parse(out);
        activeSTTModel = cfg.model_id || 'moonshine/tiny';
        activeSTTDevice = cfg.device || 'cpu';
        state.sttModel = activeSTTModel;
        if (tutorProvider) {
            tutorProvider.updateState({ sttModel: activeSTTModel });
        }
        if (currentQuickPick) {
            updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
        }
    } catch (e) {
        console.warn('[InSitu] Failed to refresh active STT model:', e);
    }
}

async function toggleHardwareAcceleration() {
    try {
        const out = await runPython([MODEL_MANAGER_SCRIPT, 'toggle-device']);
        const res = JSON.parse(out);
        activeSTTDevice = res.device || 'cpu';
        const computeType = res.compute_type || (activeSTTDevice === 'cuda' ? 'int8_float32' : 'int8');
        const label = activeSTTDevice === 'cuda' ? `GPU (CUDA / ${computeType.toUpperCase()})` : 'CPU (INT8 Quantized)';
        vscode.window.showInformationMessage(`⚡ Hardware Acceleration switched to: ${label}`);
        if (currentQuickPick) {
            updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
            if ((currentQuickPick as any).refreshMenu) {
                (currentQuickPick as any).refreshMenu();
            }
        }
        if (activeSTTQuickPick && refreshSTTQuickPickItems) {
            await refreshSTTQuickPickItems();
        }
    } catch (e: any) {
        vscode.window.showErrorMessage(`Failed to toggle hardware acceleration: ${e.message}`);
    }
}

async function refreshActiveTTSModel() {
    try {
        const out = await runPython([TTS_MANAGER_SCRIPT, '--status']);
        const cfg = JSON.parse(out);
        activeTTSModel = cfg.model_id || 'hexgrad/Kokoro-82M';
        activeTTSVoice = cfg.voice_id || 'af_heart';
        activeTTSEngine = cfg.engine || 'kokoro';
        if (currentQuickPick) {
            updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
        }
    } catch (e) {
        console.warn('[InSitu] Failed to refresh active TTS model:', e);
    }
}

function getModeName(mode: number): string {
    switch (mode) {
        case 1: return 'Section';
        case 2: return 'Line';
        case 3: return 'Refactor';
        default: return 'Section';
    }
}

function updateMode(mode: number) {
    currentMode = mode;
    state.mode = mode;
    state.modeName = getModeName(mode);
    if (statusBarItem) {
        statusBarItem.text = `$(mortar-board) InSitu: M${mode} (${state.modeName})`;
    }
    if (tutorProvider) {
        tutorProvider.updateState(state);
    }
}

let activeHighlightEditor: vscode.TextEditor | undefined = undefined;
let highlightTimer: NodeJS.Timeout | undefined = undefined;

function clearHighlight() {
    if (activeHighlightEditor && highlightDecoration) {
        try { activeHighlightEditor.setDecorations(highlightDecoration, []); } catch {}
    }
    for (const editor of vscode.window.visibleTextEditors) {
        if (highlightDecoration) {
            try { editor.setDecorations(highlightDecoration, []); } catch {}
        }
    }
    activeHighlightEditor = undefined;
    state.lastHighlight = undefined;
    if (highlightTimer) {
        clearTimeout(highlightTimer);
        highlightTimer = undefined;
    }
    vscode.commands.executeCommand('setContext', 'socraticHighlightActive', false);
    vscode.commands.executeCommand('setContext', 'insituHighlightActive', false);
    if (currentQuickPick) {
        updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
    }
    if (tutorProvider) {
        tutorProvider.postMessageToWebview({ type: 'highlightCleared' });
    }
}

function applyHighlight(editor: vscode.TextEditor, range: vscode.Range, metadata?: { file: string, startLine: number, endLine: number, message?: string }) {
    clearHighlight();
    activeHighlightEditor = editor;
    editor.setDecorations(highlightDecoration, [range]);
    vscode.commands.executeCommand('setContext', 'socraticHighlightActive', true);
    vscode.commands.executeCommand('setContext', 'insituHighlightActive', true);

    if (metadata) {
        state.lastHighlight = metadata;
        if (tutorProvider) {
            tutorProvider.postMessageToWebview({
                type: 'highlight',
                data: metadata
            });
        }
    }

    if (currentQuickPick) {
        updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
    }

    // Auto-clear highlight after 60 seconds of inactivity
    highlightTimer = setTimeout(() => {
        clearHighlight();
    }, 60000);
}

let lastVoicePromptTime = 0;
let injectSocraticGuidance = true;
let autoSpeakResponses = true;
let lastCheckedMtime = Date.now();
let lastProcessedStepByFile = new Map<string, number>();
let lastSpokenText = '';
let lastSpokenTime = 0;
let transcriptWatcherTimer: NodeJS.Timeout | undefined;

async function promptAI(prompt: string, fromVoice: boolean = false) {
    if (!prompt || !prompt.trim()) return;

    if (fromVoice) {
        lastVoicePromptTime = Date.now();
    }

    const cleanPrompt = prompt.trim();
    let textToSend = cleanPrompt;
    if (injectSocraticGuidance) {
        textToSend = `${cleanPrompt}\n\n[InSitu Mentor Mode ${currentMode}: ${state.modeName}]\nFollow dual-channel protocol: IMMEDIATELY output [SPEECH] 1-2 punchy spoken sentences (15-25 words max) directly answering the core question [/SPEECH] at the VERY START of your response (or call speak_to_user via MCP). Then provide your full written explanation and code below.`;
    } else {
        textToSend = `${cleanPrompt}\n\n[InSitu Addon: Mode ${currentMode}]`;
    }

    // 1. Copy prompt to clipboard as universal fallback
    await vscode.env.clipboard.writeText(textToSend);

    // 2. Focus Antigravity native AI Agent chat panel
    try {
        await vscode.commands.executeCommand('antigravity.panel.focus');
    } catch {}

    // 3. Dispatch directly to Antigravity native chat via antigravity.addContext and antigravity.triggerSend
    let sentToAntigravity = false;
    try {
        await vscode.commands.executeCommand('antigravity.addContext', textToSend);
        sentToAntigravity = true;
        // Trigger send in Antigravity chat
        setTimeout(async () => {
            try {
                await vscode.commands.executeCommand('antigravity.triggerSend');
            } catch {}
        }, 150);
    } catch (e) {
        console.warn('[InSitu] antigravity.addContext not available:', e);
    }

    // 4. Fallback for VS Code Chat / Copilot if Antigravity is not active
    if (!sentToAntigravity) {
        try {
            await vscode.commands.executeCommand('workbench.action.chat.open', { query: textToSend });
            setTimeout(async () => {
                try {
                    await vscode.commands.executeCommand('editor.action.clipboardPasteAction');
                } catch {}
            }, 150);
        } catch {}
    }

    vscode.window.showInformationMessage(`Prompt sent to AI Agent: "${cleanPrompt.slice(0, 45)}..."`);
}

async function replayVoice(text?: string, force: boolean = false) {
    const msg = text || state.lastSpoken || 'Replaying last tutor guidance.';
    if (!msg || !msg.trim()) return;

    state.isSpeaking = true;
    notifySpeakingStatus(true);
    if (currentQuickPick) {
        updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
    }

    // 1. Fast Path: InSitu Daemon (prewarmed in RAM, sub-100ms dispatch)
    try {
        if (await isDaemonOnline()) {
            await queryDaemon('/speak', {
                text: msg,
                engine: activeTTSEngine,
                voice_id: activeTTSVoice
            }, 'POST', 15000);
            return;
        }
    } catch (e) {
        console.warn('[InSitu] Daemon speech dispatch error, using fallback process:', e);
    }

    // 2. Fallback Path: Spawning tts_player.py
    if (fs.existsSync(TTS_SCRIPT) && fs.existsSync(PYTHON_BIN)) {
        if (activeTTSChild) {
            try {
                activeTTSChild.kill('SIGTERM');
            } catch {}
            activeTTSChild = undefined;
        }

        const args = force ? [TTS_SCRIPT, '--force', msg] : [TTS_SCRIPT, msg];
        const child = spawn(PYTHON_BIN, args);
        activeTTSChild = child;

        child.on('close', () => {
            if (activeTTSChild === child) activeTTSChild = undefined;
            state.isSpeaking = false;
            notifySpeakingStatus(false);
            if (currentQuickPick) {
                updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
            }
        });
        child.on('error', () => {
            if (activeTTSChild === child) activeTTSChild = undefined;
            state.isSpeaking = false;
            notifySpeakingStatus(false);
            if (currentQuickPick) {
                updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
            }
        });
    }
}

function stopSpeech() {
    queryDaemon('/stop_speech', {}, 'POST', 600).catch(() => {});
    if (activeTTSChild) {
        try {
            activeTTSChild.kill('SIGTERM');
        } catch {}
        activeTTSChild = undefined;
    }
    if (fs.existsSync(TTS_SCRIPT) && fs.existsSync(PYTHON_BIN)) {
        spawn(PYTHON_BIN, [TTS_SCRIPT, '--stop'], { detached: true, stdio: 'ignore' }).unref();
    }
    state.isSpeaking = false;
    notifySpeakingStatus(false);
    if (currentQuickPick) {
        updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
    }
    vscode.window.showInformationMessage('Spoken audio silenced.');
}

function notifySpeakingStatus(isSpeaking: boolean) {
    state.isSpeaking = isSpeaking;
    if (tutorProvider) {
        tutorProvider.postMessageToWebview({ type: 'speakingStatus', isSpeaking });
    }
    if (currentQuickPick) {
        updateQuickPickRecordingUI(currentQuickPick, state.isRecording || false);
    }
}

const spokenStepKeys = new Set<string>();

function initTranscriptWatcherBaseline() {
    const baseDirs = [
        path.join(os.homedir(), '.gemini', 'antigravity', 'brain'),
        path.join(os.homedir(), '.gemini', 'antigravity-cli', 'brain')
    ];

    for (const baseDir of baseDirs) {
        if (!fs.existsSync(baseDir)) continue;
        try {
            const entries = fs.readdirSync(baseDir, { withFileTypes: true });
            for (const entry of entries) {
                if (!entry.isDirectory()) continue;
                const transcriptPath = path.join(baseDir, entry.name, '.system_generated', 'logs', 'transcript.jsonl');
                if (fs.existsSync(transcriptPath)) {
                    try {
                        const stat = fs.statSync(transcriptPath);
                        lastCheckedMtime = Math.max(lastCheckedMtime, stat.mtimeMs);
                        const readSize = Math.min(stat.size, 16384);
                        const buffer = Buffer.alloc(readSize);
                        const fd = fs.openSync(transcriptPath, 'r');
                        fs.readSync(fd, buffer, 0, readSize, stat.size - readSize);
                        fs.closeSync(fd);
                        const lines = buffer.toString('utf-8').trim().split('\n');
                        let maxStep = 0;
                        for (const line of lines) {
                            try {
                                const it = JSON.parse(line.trim());
                                if (typeof it.step_index === 'number') {
                                    maxStep = Math.max(maxStep, it.step_index);
                                    spokenStepKeys.add(`${transcriptPath}:${it.step_index}`);
                                }
                            } catch {}
                        }
                        lastProcessedStepByFile.set(transcriptPath, maxStep);
                    } catch {}
                }
            }
        } catch {}
    }
}

function startTranscriptWatcher() {
    if (transcriptWatcherTimer) return;
    initTranscriptWatcherBaseline();

    transcriptWatcherTimer = setInterval(() => {
        try {
            checkLatestTranscript();
        } catch (e) {
            console.error('[InSitu] Transcript watcher error:', e);
        }
    }, 120);
}

function stopTranscriptWatcher() {
    if (transcriptWatcherTimer) {
        clearInterval(transcriptWatcherTimer);
        transcriptWatcherTimer = undefined;
    }
}

function checkLatestTranscript() {
    if (!autoSpeakResponses) return;
    if (state.isSpeaking) return;

    const baseDirs = [
        path.join(os.homedir(), '.gemini', 'antigravity', 'brain'),
        path.join(os.homedir(), '.gemini', 'antigravity-cli', 'brain')
    ];

    let latestFile: string | null = null;
    let maxMtime = 0;

    for (const baseDir of baseDirs) {
        if (!fs.existsSync(baseDir)) continue;
        try {
            const entries = fs.readdirSync(baseDir, { withFileTypes: true });
            for (const entry of entries) {
                if (!entry.isDirectory()) continue;
                const transcriptPath = path.join(baseDir, entry.name, '.system_generated', 'logs', 'transcript.jsonl');
                try {
                    const stat = fs.statSync(transcriptPath);
                    if (stat.mtimeMs > maxMtime) {
                        maxMtime = stat.mtimeMs;
                        latestFile = transcriptPath;
                    }
                } catch {}
            }
        } catch {}
    }

    if (!latestFile || maxMtime <= lastCheckedMtime) return;
    lastCheckedMtime = maxMtime;

    const now = Date.now();
    // Only process transcripts updated within the last 2 minutes
    if (now - maxMtime > 120000) return;

    const lastStep = lastProcessedStepByFile.get(latestFile) ?? -1;

    try {
        const stat = fs.statSync(latestFile);
        if (stat.size === 0) return;

        // Tail read (last 16KB)
        const readSize = Math.min(stat.size, 16384);
        const buffer = Buffer.alloc(readSize);
        const fd = fs.openSync(latestFile, 'r');
        fs.readSync(fd, buffer, 0, readSize, stat.size - readSize);
        fs.closeSync(fd);

        const lines = buffer.toString('utf-8').trim().split('\n');
        // Drop the first line if sliced in the middle of a line
        const validLines = readSize < stat.size ? lines.slice(1) : lines;

        let maxSeenStep = lastStep;
        let candidateResponse: { step: number; content: string } | null = null;

        for (let i = validLines.length - 1; i >= 0; i--) {
            const line = validLines[i].trim();
            if (!line) continue;
            try {
                const item = JSON.parse(line);
                const stepIndex = typeof item.step_index === 'number' ? item.step_index : 0;
                if (stepIndex > maxSeenStep) {
                    maxSeenStep = stepIndex;
                }

                if (stepIndex <= lastStep) {
                    break;
                }

                if (item.source === 'MODEL' && item.type === 'PLANNER_RESPONSE' && item.status === 'DONE') {
                    const stepKey = `${latestFile}:${stepIndex}`;
                    if (!spokenStepKeys.has(stepKey)) {
                        candidateResponse = { step: stepIndex, content: item.content || '' };
                        break;
                    }
                }
            } catch {}
        }

        // Always advance to highest seen step so older steps are never re-evaluated
        if (maxSeenStep > lastStep) {
            lastProcessedStepByFile.set(latestFile, maxSeenStep);
        }

        if (candidateResponse) {
            const stepKey = `${latestFile}:${candidateResponse.step}`;
            spokenStepKeys.add(stepKey);

            const content = candidateResponse.content;
            if (!content.trim()) return;

            const speechMatch = content.match(/\[SPEECH\]([\s\S]*?)(\[\/SPEECH\]|$)/i);
            if (!speechMatch) return;

            const speechText = speechMatch[1].trim();
            if (!speechText) return;

            // Check if this speech text was already spoken via MCP tool or tts_player within the last 45 seconds
            try {
                const dedupFile = '/tmp/insitu_last_spoken.json';
                if (fs.existsSync(dedupFile)) {
                    const dedupData = JSON.parse(fs.readFileSync(dedupFile, 'utf-8'));
                    if (dedupData.text && (dedupData.text === speechText || speechText.includes(dedupData.text) || dedupData.text.includes(speechText))) {
                        if (Date.now() - (dedupData.time * 1000) < 45000) {
                            console.log('[InSitu] Speech was already spoken via MCP tool / TTS player. Skipping duplicate speech.');
                            return;
                        }
                    }
                }
            } catch {}

            if (lastSpokenText === speechText && now - lastSpokenTime < 15000) {
                return;
            }

            lastSpokenText = speechText;
            lastSpokenTime = now;
            state.lastSpoken = speechText;
            replayVoice(speechText);
        }
    } catch (e) {
        console.error('[InSitu] Error processing transcript:', e);
    }
}

function sendMicControl(command: 'STOP' | 'CANCEL') {
    try {
        fs.writeFileSync(CTRL_FILE, command, 'utf-8');
    } catch (e) {
        console.error('Failed to write mic control:', e);
    }
}

async function startRecording(qp?: vscode.QuickPick<any>) {
    if (state.isRecording) {
        return;
    }

    state.isRecording = true;
    isRecordingCancelled = false;
    notifyRecordingStatus(true);
    if (qp) {
        updateQuickPickRecordingUI(qp, true);
    }

    // 1. Fast Path: InSitu Persistent CUDA Daemon (< 1s turnaround, zero-cold-boot)
    try {
        if (await isDaemonOnline()) {
            console.log('[InSitu] Starting dictation via warm CUDA daemon...');
            const silenceSec = silenceAutostop ? 0.8 : 999.0;
            const res = await queryDaemon('/record', { silence_seconds: silenceSec, max_duration: 30.0 }, 'POST', 35000);

            state.isRecording = false;
            notifyRecordingStatus(false);
            if (qp) updateQuickPickRecordingUI(qp, false);

            if (isRecordingCancelled || res.cancelled) {
                console.log('[InSitu] Dictation was cancelled by user. Discarding output.');
                if (qp) qp.placeholder = "Recording cancelled.";
                return;
            }

            if (res.text && res.text.trim()) {
                notifyTranscription(res.text);
                if (qp) {
                    qp.value = res.text;
                    qp.hide();
                }
                promptAI(res.text, true);
            } else {
                if (qp) qp.placeholder = "No speech detected. Click 🎙 to speak...";
            }
            return;
        }
    } catch (e: any) {
        console.warn('[InSitu] Daemon recording error, using fallback process:', e);
    }

    // 2. Fallback Path: Spawning stt_transcriber.py
    // Remove any stale control file
    try { if (fs.existsSync(CTRL_FILE)) fs.unlinkSync(CTRL_FILE); } catch {}

    const args = ['--mic', '--max-duration', '30.0', '--silence-seconds', '0.8'];
    if (!silenceAutostop) {
        args.push('--no-silence-stop');
    }

    micProcess = spawn(PYTHON_BIN, [STT_SCRIPT, ...args]);
    let stdout = '';
    let stderr = '';

    micProcess.stdout?.on('data', d => stdout += d.toString());
    micProcess.stderr?.on('data', d => stderr += d.toString());

    micProcess.on('close', code => {
        state.isRecording = false;
        notifyRecordingStatus(false);
        micProcess = undefined;

        if (qp) {
            updateQuickPickRecordingUI(qp, false);
        }

        // If user cancelled (e.g. hit Esc or Cancel button), discard audio immediately
        if (isRecordingCancelled || code === 2) {
            console.log('[InSitu] Dictation was cancelled. Discarding audio output.');
            if (qp) qp.placeholder = "Recording cancelled.";
            return;
        }

        if (code === 0 && stdout.trim()) {
            try {
                const res = JSON.parse(stdout.trim());
                if (res.cancelled) {
                    vscode.window.showInformationMessage('Recording cancelled.');
                    if (qp) qp.placeholder = "Recording cancelled.";
                    return;
                }
                if (res.text && res.text.trim()) {
                    notifyTranscription(res.text);
                    if (qp) {
                        qp.value = res.text;
                        qp.hide();
                    }
                    promptAI(res.text, true);
                } else {
                    if (qp) qp.placeholder = "No speech detected. Click 🎙 to speak...";
                }
            } catch {
                const txt = stdout.trim();
                notifyTranscription(txt);
                if (qp) {
                    qp.value = txt;
                    qp.hide();
                }
                promptAI(txt, true);
            }
        } else if (stderr) {
            console.error('STT error:', stderr);
        }
    });

    micProcess.on('error', err => {
        state.isRecording = false;
        notifyRecordingStatus(false);
        micProcess = undefined;
        if (qp) updateQuickPickRecordingUI(qp, false);
        vscode.window.showErrorMessage(`Microphone error: ${err.message}`);
    });
}

function stopRecording() {
    queryDaemon('/stop_mic', {}, 'POST', 600).catch(() => {});
    sendMicControl('STOP');
}

function cancelRecording() {
    isRecordingCancelled = true;
    queryDaemon('/cancel', {}, 'POST', 600).catch(() => {});
    sendMicControl('CANCEL');
    if (micProcess) {
        try {
            micProcess.kill('SIGKILL');
        } catch {}
        micProcess = undefined;
    }
    state.isRecording = false;
    notifyRecordingStatus(false);
    if (currentQuickPick) {
        updateQuickPickRecordingUI(currentQuickPick, false);
        currentQuickPick.placeholder = "Recording cancelled.";
    }
}

function updateQuickPickRecordingUI(qp: vscode.QuickPick<any>, recording: boolean) {
    if (recording) {
        qp.busy = true;
        qp.title = `🔴 Recording... [■ Stop to send, ✕ Cancel] (${silenceAutostop ? '0.8s snappy auto-stop' : 'Manual stop'})`;
        qp.placeholder = "Listening... (Speak your prompt, click ■ Stop to send immediately, ✕ or Esc to cancel)";

        const btnStop: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('primitive-square'),
            tooltip: '■ Stop recording & send prompt to AI'
        };

        const btnCancel: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('close'),
            tooltip: '✕ Cancel recording (discard audio)'
        };

        const btnToggleVAD: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon(silenceAutostop ? 'pulse' : 'circle-slash'),
            tooltip: `2s Silence Autostop: ${silenceAutostop ? 'ON' : 'OFF'}`
        };

        qp.buttons = [btnStop, btnCancel, btnToggleVAD];
    } else {
        qp.busy = false;
        if (state.isSpeaking) {
            qp.title = `🔊 Speaking guidance... [Mode ${currentMode}: ${state.modeName}]`;
        } else {
            qp.title = `🏛️ InSitu Mentor | Mode ${currentMode}: ${state.modeName}`;
        }
        qp.placeholder = "Ask Gemini / InSitu Mentor... (Enter to send, 🎙 to speak)";

        const btnAddContext: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('add'),
            tooltip: '+ Add Active File & Selection to Prompt'
        };

        const btnMode: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('layers'),
            tooltip: `Mode ${currentMode}: ${state.modeName} ▾ (Click to switch mode)`
        };

        const btnDevice: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon(activeSTTDevice === 'cuda' ? 'zap' : 'circuit-board'),
            tooltip: `Acceleration: ${activeSTTDevice.toUpperCase()} (Click to toggle CPU / GPU)`
        };

        const shortModelName = activeSTTModel.split('/').pop() || activeSTTModel;
        const btnSTTModel: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('hubot'),
            tooltip: `STT Model: ${shortModelName} [${activeSTTDevice.toUpperCase()}] ▾ (Click to switch or download from Hugging Face)`
        };

        const shortTTSModel = activeTTSModel.split('/').pop() || activeTTSModel;
        const btnTTSModel: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('symbol-audio'),
            tooltip: `TTS Voice: ${activeTTSVoice} (${shortTTSModel}) ▾ (Click to switch model or voice)`
        };

        const btnMute: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon(state.isSpeaking ? 'mute' : 'bell-slash'),
            tooltip: state.isSpeaking ? '🔇 Stop Speech NOW (Silence Audio)' : '🔇 Silence Audio'
        };

        const btnToggleVAD: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon(silenceAutostop ? 'pulse' : 'circle-slash'),
            tooltip: `Silence Autostop: ${silenceAutostop ? 'ON (2s)' : 'OFF'}`
        };

        const btnReplay: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('unmute'),
            tooltip: '🔊 Replay Last Tutor Speech'
        };

        const btnMic: vscode.QuickInputButton = {
            iconPath: new vscode.ThemeIcon('mic'),
            tooltip: `🎙️ Speak Prompt (${shortModelName} - ${activeSTTDevice.toUpperCase()})`
        };

        const buttons: vscode.QuickInputButton[] = [btnAddContext, btnMode, btnDevice, btnSTTModel, btnTTSModel];
        if (activeHighlightEditor) {
            buttons.push({
                iconPath: new vscode.ThemeIcon('clear-all'),
                tooltip: '✕ Clear Code Highlight (Ctrl+Alt+C)'
            });
        }
        buttons.push(btnMute, btnToggleVAD, btnReplay, btnMic);
        qp.buttons = buttons;
    }
}

async function downloadAndSetModel(modelId: string, modelName: string) {
    return vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: `Downloading STT Model: ${modelName}`,
            cancellable: false
        },
        (progress) => {
            return new Promise<void>((resolve, reject) => {
                let lastPct = 0;
                progress.report({ increment: 0, message: 'Connecting to Hugging Face...' });

                const child = spawn(PYTHON_BIN, [
                    MODEL_MANAGER_SCRIPT,
                    'download',
                    '--model',
                    modelId
                ]);

                child.stdout.on('data', (data) => {
                    const lines = data.toString().split('\n');
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const parsed = JSON.parse(line.trim());
                            if (parsed.type === 'progress' && typeof parsed.progress === 'number') {
                                const diff = Math.max(0, parsed.progress - lastPct);
                                lastPct = parsed.progress;
                                progress.report({
                                    increment: diff,
                                    message: `${parsed.desc || 'Downloading weights'} (${parsed.progress}%)`
                                });
                            } else if (parsed.type === 'complete') {
                                progress.report({ increment: Math.max(0, 100 - lastPct), message: 'Download completed!' });
                            }
                        } catch {}
                    }
                });

                child.stderr.on('data', (err) => {
                    console.error('[Model Download STDERR]:', err.toString());
                });

                child.on('close', (code) => {
                    if (code === 0) {
                        spawn(PYTHON_BIN, [MODEL_MANAGER_SCRIPT, 'set', '--model', modelId]).on('close', () => {
                            vscode.window.showInformationMessage(`✅ STT Model "${modelName}" downloaded and active!`);
                            refreshActiveSTTModel();
                            resolve();
                        });
                    } else {
                        vscode.window.showErrorMessage(`Failed to download model "${modelName}".`);
                        reject(new Error(`Download exited with code ${code}`));
                    }
                });

                child.on('error', (err) => {
                    vscode.window.showErrorMessage(`Download error: ${err.message}`);
                    reject(err);
                });
            });
        }
    );
}

async function deleteSTTCachePrompt() {
    try {
        const out = await runPython([MODEL_MANAGER_SCRIPT, 'list']);
        const data = JSON.parse(out);
        const cachedModels = data.models.filter((m: any) => m.is_cached && m.id !== 'moonshine/tiny');
        if (cachedModels.length === 0) {
            vscode.window.showInformationMessage('No downloadable STT model caches found to delete.');
            return;
        }
        const pick = await vscode.window.showQuickPick(
            cachedModels.map((m: any) => ({
                label: `$(trash) ${m.name}`,
                description: m.disk_size_mb ? `Reclaim ${m.disk_size_mb}` : 'Cached weights',
                detail: m.id,
                modelId: m.id,
                modelName: m.name
            })),
            { title: 'Select Model Cache to Delete & Reclaim Space', placeHolder: 'Pick a cached model to delete' }
        );
        if (pick && (pick as any).modelId) {
            const confirmed = await vscode.window.showWarningMessage(
                `Delete cache for "${(pick as any).modelName}"?`,
                { modal: true },
                'Delete Cache',
                'Cancel'
            );
            if (confirmed === 'Delete Cache') {
                const resStr = await runPython([MODEL_MANAGER_SCRIPT, 'delete', '--model', (pick as any).modelId]);
                const res = JSON.parse(resStr);
                vscode.window.showInformationMessage(`🗑️ ${res.message || 'Model cache deleted.'}`);
                await refreshActiveSTTModel();
                if (refreshSTTQuickPickItems) await refreshSTTQuickPickItems();
            }
        }
    } catch (e: any) {
        vscode.window.showErrorMessage(`Failed to delete model cache: ${e.message}`);
    }
}

async function selectSTTModel() {
    if (activeSTTQuickPick) {
        activeSTTQuickPick.show();
        return;
    }

    const qp = vscode.window.createQuickPick<any>();
    activeSTTQuickPick = qp;
    qp.ignoreFocusOut = false;
    qp.placeholder = 'Select an open-source ASR model or click toggle for GPU acceleration';

    async function loadItems() {
        qp.busy = true;
        try {
            const out = await runPython([MODEL_MANAGER_SCRIPT, 'list']);
            const data = JSON.parse(out);
            const currentModel = data.current_model;
            const device = data.device || 'cpu';
            activeSTTDevice = device;
            activeSTTModel = currentModel;
            state.sttModel = currentModel;

            qp.title = `Speech-to-Text Model Selection [Device: ${device.toUpperCase()}] (Active: ${currentModel})`;

            qp.buttons = [
                {
                    iconPath: new vscode.ThemeIcon(device === 'cuda' ? 'zap' : 'circuit-board'),
                    tooltip: `Toggle Hardware Acceleration (Currently: ${device === 'cuda' ? 'GPU CUDA' : 'CPU INT8'})`
                },
                {
                    iconPath: new vscode.ThemeIcon('symbol-keyword'),
                    tooltip: 'Configure Coding Vocabulary Prompt Bias'
                },
                {
                    iconPath: new vscode.ThemeIcon('trash'),
                    tooltip: 'Delete Cached STT Models (Reclaim Disk Space)'
                },
                {
                    iconPath: new vscode.ThemeIcon('refresh'),
                    tooltip: 'Refresh Model List & Cache Status'
                }
            ];

            const items: (vscode.QuickPickItem & { modelId?: string; modelName?: string; isCached?: boolean; action?: string; disk_size_mb?: string })[] = [];

            // Hardware Acceleration Switcher at the top
            items.push({
                label: `${device === 'cuda' ? '⚡' : '💻'} HARDWARE ACCELERATION: ${device.toUpperCase()}`,
                kind: vscode.QuickPickItemKind.Separator
            });
            items.push({
                label: `${device === 'cuda' ? '$(zap) Active: GPU (CUDA / Float16)' : '$(circuit-board) Active: CPU (INT8 Quantized)'}`,
                description: `Click or press Enter to switch to ${device === 'cuda' ? 'CPU' : 'GPU (CUDA)'}`,
                detail: device === 'cuda'
                    ? 'Using NVIDIA GPU acceleration (with automatic CPU fallback if driver mismatch/OOM occurs).'
                    : 'Using CPU inference with INT8 quantization for minimal memory usage.',
                action: 'toggleDevice'
            });

            // Group 1: Tier 1 Ultra-Lightweight
            items.push({
                label: '⚡ TIER 1: ULTRA-LIGHTWEIGHT (< 500 MB RAM / LOW-END CPU / EDGE)',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of data.models.filter((x: any) => x.tier.includes('Tier 1'))) {
                items.push({
                    label: `${m.is_active ? '$(check) ' : '    '}${m.name}`,
                    description: `RAM: ${m.ram} | Weights: ${m.model_size || m.params} ${m.is_cached ? '$(pass-filled) Cached' + (m.disk_size_mb ? ' (' + m.disk_size_mb + ')' : '') : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached,
                    disk_size_mb: m.disk_size_mb
                });
            }

            // Group 2: Tier 2 (1 GB Models)
            items.push({
                label: '📦 TIER 2: 1 GB MODELS (FAST ON CPU & 1-2 GB ENTRY GPUS)',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of data.models.filter((x: any) => x.tier.includes('Tier 2'))) {
                items.push({
                    label: `${m.is_active ? '$(check) ' : '    '}${m.name}`,
                    description: `RAM: ${m.ram} | Weights: ${m.model_size || m.params} ${m.is_cached ? '$(pass-filled) Cached' + (m.disk_size_mb ? ' (' + m.disk_size_mb + ')' : '') : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached,
                    disk_size_mb: m.disk_size_mb
                });
            }

            // Group 3: Tier 3 (2 GB Models)
            items.push({
                label: '🚀 TIER 3: 2 GB MODELS (DEDICATED 2-3 GB GPUS LIKE GTX 1050/1650 & M-SERIES)',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of data.models.filter((x: any) => x.tier.includes('Tier 3'))) {
                items.push({
                    label: `${m.is_active ? '$(check) ' : '    '}${m.name}`,
                    description: `VRAM: ${m.vram || m.ram} | Weights: ${m.model_size || m.params} ${m.is_cached ? '$(pass-filled) Cached' + (m.disk_size_mb ? ' (' + m.disk_size_mb + ')' : '') : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached,
                    disk_size_mb: m.disk_size_mb
                });
            }

            // Group 4: Tier 4 (3 GB Models)
            items.push({
                label: '👑 TIER 4: 3 GB MODELS (MAXIMUM CODING ACCURACY FOR 3 GB+ GPUS)',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of data.models.filter((x: any) => x.tier.includes('Tier 4'))) {
                items.push({
                    label: `${m.is_active ? '$(check) ' : '    '}${m.name}`,
                    description: `VRAM: ${m.vram || m.ram} | Weights: ${m.model_size || m.params} ${m.is_cached ? '$(pass-filled) Cached' + (m.disk_size_mb ? ' (' + m.disk_size_mb + ')' : '') : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached,
                    disk_size_mb: m.disk_size_mb
                });
            }

            // Group 5: Custom & Space Management
            items.push({
                label: '🌐 CUSTOM REPOSITORY & DISK MANAGEMENT',
                kind: vscode.QuickPickItemKind.Separator
            });

            items.push({
                label: '$(trash) Reclaim Disk Space: Delete Cached Models...',
                description: 'Free up storage by deleting locally downloaded model weights',
                action: 'deleteCache'
            });

            items.push({
                label: '$(plus) Enter Custom Hugging Face Model ID...',
                description: 'Specify any model repo from huggingface.co',
                action: 'customModel'
            });

            items.push({
                label: '$(symbol-keyword) Configure Coding Vocabulary Prompt Bias...',
                description: 'Biases recognition toward syntax: ' + (data.prompt_prefix ? data.prompt_prefix.slice(0, 45) + '...' : 'Default'),
                action: 'editPrefix'
            });

            qp.items = items;
        } catch (e: any) {
            vscode.window.showErrorMessage(`Failed to load STT models: ${e.message}`);
        } finally {
            qp.busy = false;
        }
    }

    refreshSTTQuickPickItems = loadItems;

    qp.onDidTriggerButton(async (btn) => {
        const tooltip = btn.tooltip || '';
        if (tooltip.includes('Toggle Hardware Acceleration')) {
            await toggleHardwareAcceleration();
            await loadItems();
        } else if (tooltip.includes('Coding Vocabulary')) {
            await editPromptPrefix();
            await loadItems();
        } else if (tooltip.includes('Delete Cached STT Models')) {
            await deleteSTTCachePrompt();
            await loadItems();
        } else if (tooltip.includes('Refresh')) {
            await loadItems();
        }
    });

    qp.onDidAccept(async () => {
        const picked: any = qp.selectedItems[0];
        if (!picked) return;

        if (picked.action === 'toggleDevice') {
            await toggleHardwareAcceleration();
            await loadItems();
            return;
        }

        if (picked.action === 'deleteCache') {
            await deleteSTTCachePrompt();
            await loadItems();
            return;
        }

        if (picked.action === 'customModel') {
            const repoId = await vscode.window.showInputBox({
                title: 'Custom Hugging Face Model ID',
                prompt: 'Enter full Hugging Face repo ID (e.g., Systran/faster-whisper-base or distil-whisper/distil-small.en)',
                placeHolder: 'e.g. Systran/faster-distil-whisper-small.en'
            });
            if (repoId && repoId.trim()) {
                await downloadAndSetModel(repoId.trim(), repoId.trim());
                await loadItems();
            }
            return;
        }

        if (picked.action === 'editPrefix') {
            await editPromptPrefix();
            await loadItems();
            return;
        }

        if (picked.modelId) {
            if (picked.isCached) {
                const actions: (vscode.QuickPickItem & { act: string })[] = [
                    { label: '$(check) Set as Active STT Model', description: `Transcribe with ${picked.modelName}`, act: 'activate' }
                ];
                if (picked.modelId !== 'moonshine/tiny') {
                    actions.push({
                        label: `$(trash) Delete Cache & Reclaim Disk Space`,
                        description: picked.disk_size_mb ? `Frees ${picked.disk_size_mb}` : 'Removes downloaded weights from disk',
                        act: 'delete'
                    });
                }
                actions.push({ label: '$(close) Cancel', description: 'Keep current settings', act: 'cancel' });

                const chosen = await vscode.window.showQuickPick(actions, {
                    title: `Manage STT Model: ${picked.modelName}`,
                    placeHolder: 'Choose an action'
                });
                if (!chosen || chosen.act === 'cancel') return;

                if (chosen.act === 'activate') {
                    await runPython([MODEL_MANAGER_SCRIPT, 'set', '--model', picked.modelId]);
                    const cachedDetail = picked.disk_size_mb ? ` (Already cached locally: ${picked.disk_size_mb})` : '';
                    vscode.window.showInformationMessage(`STT Model switched to "${picked.modelName}"${cachedDetail}. Ready to transcribe.`);
                    await refreshActiveSTTModel();
                    await loadItems();
                } else if (chosen.act === 'delete') {
                    const confirm = await vscode.window.showWarningMessage(
                        `Delete cached weights for "${picked.modelName}"?`,
                        { modal: true },
                        'Delete Weights',
                        'Cancel'
                    );
                    if (confirm === 'Delete Weights') {
                        const out = await runPython([MODEL_MANAGER_SCRIPT, 'delete', '--model', picked.modelId]);
                        const res = JSON.parse(out);
                        vscode.window.showInformationMessage(`🗑️ ${res.message || 'Model cache deleted.'}`);
                        await refreshActiveSTTModel();
                        await loadItems();
                    }
                }
            } else {
                const confirm = await vscode.window.showInformationMessage(
                    `Model "${picked.modelName}" will be downloaded from Hugging Face. Proceed with download?`,
                    'Download Now',
                    'Cancel'
                );
                if (confirm === 'Download Now') {
                    await downloadAndSetModel(picked.modelId, picked.modelName || picked.modelId);
                    await loadItems();
                }
            }
        }
    });

    qp.onDidHide(() => {
        activeSTTQuickPick = undefined;
        refreshSTTQuickPickItems = undefined;
        qp.dispose();
    });

    await loadItems();
    qp.show();
}

async function editPromptPrefix(existingPrefix?: string) {
    let current = existingPrefix;
    if (!current) {
        try {
            const out = await runPython([MODEL_MANAGER_SCRIPT, 'get']);
            current = JSON.parse(out).prompt_prefix;
        } catch {}
    }

    const newPrefix = await vscode.window.showInputBox({
        title: 'Programming Vocabulary Prompt Bias',
        prompt: 'Whisper models use this prefix to bias phonetic spelling toward code syntax (snake_case, camelCase, kubectl, async/await):',
        value: current || 'JavaScript, TypeScript, Python, Rust, snake_case, camelCase, kubectl, async/await, fn, def, struct, git',
        valueSelection: [0, 0]
    });

    if (newPrefix !== undefined && newPrefix.trim()) {
        await runPython([MODEL_MANAGER_SCRIPT, 'set-prefix', '--prefix', newPrefix.trim()]);
        vscode.window.showInformationMessage('✅ Programming vocabulary prompt bias updated!');
    }
}

async function downloadAndSetTTSModel(modelId: string, modelName: string) {
    return vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: `Downloading TTS Model: ${modelName}`,
            cancellable: false
        },
        (progress) => {
            return new Promise<void>((resolve, reject) => {
                let lastPct = 0;
                progress.report({ increment: 0, message: 'Connecting to Hugging Face...' });

                const child = spawn(PYTHON_BIN, [
                    TTS_MANAGER_SCRIPT,
                    '--download',
                    modelId
                ]);

                child.stdout.on('data', (data) => {
                    const lines = data.toString().split('\n');
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        if (line.startsWith('PROGRESS:')) {
                            try {
                                const parsed = JSON.parse(line.replace('PROGRESS:', '').trim());
                                const pct = parsed.percent || 0;
                                const diff = Math.max(0, pct - lastPct);
                                lastPct = pct;
                                progress.report({
                                    increment: diff,
                                    message: `${parsed.message || 'Downloading weights'} (${pct}%)`
                                });
                            } catch {}
                        }
                    }
                });

                child.on('close', (code) => {
                    if (code === 0) {
                        spawn(PYTHON_BIN, [TTS_MANAGER_SCRIPT, '--set-model', modelId]).on('close', () => {
                            vscode.window.showInformationMessage(`✅ TTS Model "${modelName}" active!`);
                            refreshActiveTTSModel();
                            resolve();
                        });
                    } else {
                        vscode.window.showErrorMessage(`Failed to download TTS model "${modelName}".`);
                        reject(new Error(`Download exited with code ${code}`));
                    }
                });

                child.on('error', (err) => {
                    vscode.window.showErrorMessage(`Download error: ${err.message}`);
                    reject(err);
                });
            });
        }
    );
}

async function deleteTTSCachePrompt() {
    try {
        const out = await runPython([TTS_MANAGER_SCRIPT, '--list-models']);
        const models: any[] = JSON.parse(out);
        const cachedModels = models.filter((m: any) => m.is_cached);
        if (cachedModels.length === 0) {
            vscode.window.showInformationMessage('No downloadable TTS model caches found to delete.');
            return;
        }
        const pick = await vscode.window.showQuickPick(
            cachedModels.map((m: any) => ({
                label: `$(trash) ${m.name}`,
                description: `Cached weights (${m.ram})`,
                detail: m.id,
                modelId: m.id,
                modelName: m.name
            })),
            { title: 'Select TTS Model Cache to Delete & Reclaim Space', placeHolder: 'Pick a cached TTS model to delete' }
        );
        if (pick && (pick as any).modelId) {
            const confirmed = await vscode.window.showWarningMessage(
                `Delete cache for "${(pick as any).modelName}"?`,
                { modal: true },
                'Delete Cache',
                'Cancel'
            );
            if (confirmed === 'Delete Cache') {
                const resStr = await runPython([TTS_MANAGER_SCRIPT, '--delete-model', (pick as any).modelId]);
                const res = JSON.parse(resStr);
                vscode.window.showInformationMessage(`🗑️ Deleted TTS model cache (${res.freed_mb || 0} MB reclaimed).`);
                await refreshActiveTTSModel();
                if (refreshTTSQuickPickItems) await refreshTTSQuickPickItems();
            }
        }
    } catch (e: any) {
        vscode.window.showErrorMessage(`Failed to delete TTS model cache: ${e.message}`);
    }
}

async function selectTTSModel() {
    if (activeTTSQuickPick) {
        activeTTSQuickPick.show();
        return;
    }

    const qp = vscode.window.createQuickPick<any>();
    activeTTSQuickPick = qp;
    qp.ignoreFocusOut = false;
    qp.placeholder = 'Select an open-source TTS model from Hugging Face or customize voice';

    async function loadItems() {
        qp.busy = true;
        try {
            const out = await runPython([TTS_MANAGER_SCRIPT, '--list-models']);
            const models: any[] = JSON.parse(out);

            qp.title = `Text-to-Speech (TTS) Model Selection (Current: ${activeTTSModel})`;
            qp.buttons = [
                {
                    iconPath: new vscode.ThemeIcon('unmute'),
                    tooltip: `Select Preset Voice (Current: ${activeTTSVoice})`
                },
                {
                    iconPath: new vscode.ThemeIcon('record'),
                    tooltip: 'Set Reference .wav for Voice Cloning'
                },
                {
                    iconPath: new vscode.ThemeIcon('trash'),
                    tooltip: 'Delete Cached TTS Models (Reclaim Disk Space)'
                },
                {
                    iconPath: new vscode.ThemeIcon('refresh'),
                    tooltip: 'Refresh TTS Models & Caches'
                }
            ];

            const items: (vscode.QuickPickItem & { modelId?: string; modelName?: string; isCached?: boolean; action?: string })[] = [];

            items.push({
                label: '⚡ TIER 1: ULTRA-LIGHTWEIGHT (Low CPU / Raspberry Pi / Weak PC)',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of models.filter(x => x.tier.includes('Tier 1'))) {
                items.push({
                    label: `${m.is_current ? '$(check) ' : '    '}${m.name}`,
                    description: `RAM: ${m.ram} | Params: ${m.params} ${m.is_cached ? '$(pass-filled) Cached' : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached
                });
            }

            items.push({
                label: '💻 TIER 2: MID-RANGE CPU / ENTRY GPU (Everyday Laptops & MacBooks)',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of models.filter(x => x.tier.includes('Tier 2'))) {
                items.push({
                    label: `${m.is_current ? '$(check) ' : '    '}${m.name}`,
                    description: `RAM: ${m.ram} | Params: ${m.params} ${m.is_cached ? '$(pass-filled) Cached' : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached
                });
            }

            items.push({
                label: '🚀 TIER 3 & 4: GPU WORKSTATIONS & ZERO-SHOT CLONING',
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const m of models.filter(x => x.tier.includes('Tier 3') || x.tier.includes('Tier 4'))) {
                items.push({
                    label: `${m.is_current ? '$(check) ' : '    '}${m.name}`,
                    description: `RAM: ${m.ram} | Params: ${m.params} ${m.is_cached ? '$(pass-filled) Cached' : '$(cloud-download) Download needed'}`,
                    detail: m.description,
                    modelId: m.id,
                    modelName: m.name,
                    isCached: m.is_cached
                });
            }

            items.push({
                label: '🎙️ VOICE PRESETS & ZERO-SHOT CLONING',
                kind: vscode.QuickPickItemKind.Separator
            });
            items.push({
                label: '$(trash) Reclaim Disk Space: Delete Cached TTS Models...',
                description: 'Free up storage by deleting local TTS models',
                action: 'deleteTTSCache'
            });
            items.push({
                label: '$(unmute) Select Preset Voice (54 Kokoro / 6 Piper Voices)...',
                description: `Current Voice: ${activeTTSVoice}`,
                action: 'selectVoice'
            });
            items.push({
                label: '$(record) Set Reference .wav for Voice Cloning (F5-TTS / XTTS)...',
                description: 'Provide a 3-10s audio sample of any speaker to mimic',
                action: 'setCloneAudio'
            });

            qp.items = items;
        } catch (e: any) {
            vscode.window.showErrorMessage(`Failed to load TTS models: ${e.message}`);
        } finally {
            qp.busy = false;
        }
    }

    refreshTTSQuickPickItems = loadItems;

    qp.onDidTriggerButton(async (btn) => {
        const tooltip = btn.tooltip || '';
        if (tooltip.includes('Select Preset Voice')) {
            await selectTTSVoice();
            await loadItems();
        } else if (tooltip.includes('Voice Cloning')) {
            await setTTSCloneAudio();
            await loadItems();
        } else if (tooltip.includes('Delete Cached TTS Models')) {
            await deleteTTSCachePrompt();
            await loadItems();
        } else if (tooltip.includes('Refresh')) {
            await loadItems();
        }
    });

    qp.onDidAccept(async () => {
        const picked: any = qp.selectedItems[0];
        if (!picked) return;

        if (picked.action === 'deleteTTSCache') {
            await deleteTTSCachePrompt();
            await loadItems();
            return;
        }

        if (picked.action === 'selectVoice') {
            await selectTTSVoice();
            await loadItems();
            return;
        }

        if (picked.action === 'setCloneAudio') {
            await setTTSCloneAudio();
            await loadItems();
            return;
        }

        if (picked.modelId) {
            if (picked.isCached) {
                const actions: (vscode.QuickPickItem & { act: string })[] = [
                    { label: '$(check) Set as Active TTS Model', description: `Synthesize speech with ${picked.modelName}`, act: 'activate' },
                    { label: '$(trash) Delete Cache & Reclaim Disk Space', description: 'Removes downloaded model weights from disk', act: 'delete' },
                    { label: '$(close) Cancel', description: 'Keep current settings', act: 'cancel' }
                ];

                const chosen = await vscode.window.showQuickPick(actions, {
                    title: `Manage TTS Model: ${picked.modelName}`,
                    placeHolder: 'Select an action'
                });
                if (!chosen || chosen.act === 'cancel') return;

                if (chosen.act === 'activate') {
                    await runPython([TTS_MANAGER_SCRIPT, '--set-model', picked.modelId]);
                    await refreshActiveTTSModel();
                    vscode.window.showInformationMessage(`✅ Active TTS model set to: ${picked.modelName}`);
                    await loadItems();
                } else if (chosen.act === 'delete') {
                    const confirm = await vscode.window.showWarningMessage(
                        `Delete cached weights for "${picked.modelName}"?`,
                        { modal: true },
                        'Delete Weights',
                        'Cancel'
                    );
                    if (confirm === 'Delete Weights') {
                        const out = await runPython([TTS_MANAGER_SCRIPT, '--delete-model', picked.modelId]);
                        const res = JSON.parse(out);
                        vscode.window.showInformationMessage(`🗑️ Deleted TTS model cache (${res.freed_mb || 0} MB reclaimed).`);
                        await refreshActiveTTSModel();
                        await loadItems();
                    }
                }
            } else {
                const confirm = await vscode.window.showInformationMessage(
                    `Model "${picked.modelName}" needs to be downloaded from Hugging Face. Proceed with download?`,
                    'Download Now',
                    'Cancel'
                );
                if (confirm === 'Download Now') {
                    await downloadAndSetTTSModel(picked.modelId, picked.modelName || picked.modelId);
                    await loadItems();
                }
            }
        }
    });

    qp.onDidHide(() => {
        activeTTSQuickPick = undefined;
        refreshTTSQuickPickItems = undefined;
        qp.dispose();
    });

    await loadItems();
    qp.show();
}

async function selectTTSVoice() {
    let voices: any[] = [];
    try {
        const out = await runPython([TTS_MANAGER_SCRIPT, '--list-voices']);
        voices = JSON.parse(out);
    } catch (e: any) {
        vscode.window.showErrorMessage(`Failed to load TTS voices: ${e.message}`);
        return;
    }

    const items: (vscode.QuickPickItem & { voiceId?: string; voiceName?: string })[] = [];

    const accents = ['American', 'British', 'Japanese', 'Mandarin'];
    for (const accent of accents) {
        const group = voices.filter(v => v.accent === accent);
        if (group.length > 0) {
            items.push({
                label: `🌐 ${accent.toUpperCase()} VOICES`,
                kind: vscode.QuickPickItemKind.Separator
            });
            for (const v of group) {
                items.push({
                    label: `${v.is_current ? '$(check) ' : '    '}${v.name}`,
                    description: `${v.gender} | ${v.quality} | ID: ${v.id}`,
                    voiceId: v.id,
                    voiceName: v.name
                });
            }
        }
    }

    const other = voices.filter(v => !accents.includes(v.accent));
    if (other.length > 0) {
        items.push({
            label: '🎙️ OTHER PRESET VOICES',
            kind: vscode.QuickPickItemKind.Separator
        });
        for (const v of other) {
            items.push({
                label: `${v.is_current ? '$(check) ' : '    '}${v.name}`,
                description: `${v.gender || ''} | ${v.quality || ''} | ID: ${v.id}`,
                voiceId: v.id,
                voiceName: v.name
            });
        }
    }

    const picked = await vscode.window.showQuickPick(items, {
        title: `Select Preset Tutor Voice (Engine: ${activeTTSEngine.toUpperCase()})`,
        placeHolder: `Choose a voice preset (Current: ${activeTTSVoice})`
    });

    if (picked && picked.voiceId) {
        await runPython([TTS_MANAGER_SCRIPT, '--set-voice', picked.voiceId]);
        await refreshActiveTTSModel();
        vscode.window.showInformationMessage(`✅ Active InSitu voice set to: ${picked.voiceName}`);
        replayVoice(`Hello! InSitu mentor voice is now set to ${picked.voiceName}.`, true);
    }
}

async function setTTSCloneAudio() {
    const uris = await vscode.window.showOpenDialog({
        canSelectMany: false,
        openLabel: 'Select Voice Reference Audio (.wav)',
        filters: { 'Audio Files': ['wav', 'flac', 'mp3', 'ogg'] }
    });

    if (uris && uris.length > 0) {
        const filePath = uris[0].fsPath;
        await runPython([TTS_MANAGER_SCRIPT, '--set-clone-audio', filePath]);
        vscode.window.showInformationMessage(`✅ Voice cloning reference audio configured: ${path.basename(filePath)}`);
    }
}

function openInWindowHUD() {
    if (currentQuickPick) {
        currentQuickPick.show();
        return;
    }

    const qp = vscode.window.createQuickPick();
    currentQuickPick = qp;
    let menuVisible = false;
    (qp as any).menuVisible = false;

    updateQuickPickRecordingUI(qp, state.isRecording || false);

    function getMenuItems() {
        const shortModel = activeSTTModel.split('/').pop() || activeSTTModel;
        return [
            {
                label: `${currentMode === 1 ? '$(check) ' : '    '}Mode 1: Section Scaffold`,
                description: 'Feature flow: builds isolated sections, asks design questions',
                mode: 1
            },
            {
                label: `${currentMode === 2 ? '$(check) ' : '    '}Mode 2: Line-by-Line Step`,
                description: 'Pedagogical: writes one line, drills syntax & invariants',
                mode: 2
            },
            {
                label: `${currentMode === 3 ? '$(check) ' : '    '}Mode 3: Refactor & Doubt`,
                description: 'Deep dive: explains code mechanics & guides refactoring',
                mode: 3
            },
            {
                label: '$(search) Inspect Selection & Ask Doubt',
                description: 'Sends active selection in editor as Mode 3 prompt',
                action: 'inspect'
            },
            {
                label: '$(unmute) Replay Last Tutor Speech',
                description: 'Synthesizes and speaks last tutor guidance aloud',
                action: 'replay'
            },
            {
                label: '$(mute) Stop Speech (Silence Audio)',
                description: 'Instantly cut off any currently playing spoken explanation',
                action: 'stopSpeech'
            },
            {
                label: `$(pulse) Silence Auto-Stop: ${silenceAutostop ? 'ENABLED (2s)' : 'DISABLED'}`,
                description: 'Toggle automatic stop after 2s silence',
                action: 'toggleVAD'
            },
            {
                label: `$(zap) Hardware Acceleration: ${activeSTTDevice.toUpperCase()}`,
                description: `Click to toggle between CPU (INT8) and GPU (CUDA Float16)`,
                action: 'toggleDevice'
            },
            {
                label: `$(hubot) Change STT Model (Current: ${shortModel})`,
                description: 'Select or download open-source ASR models (1GB, 2GB, 3GB) from Hugging Face',
                action: 'changeSTT'
            },
            {
                label: `$(symbol-audio) Change TTS Model (Current: ${activeTTSModel.split('/').pop() || activeTTSModel})`,
                description: `Engine: ${activeTTSEngine.toUpperCase()} | Voice: ${activeTTSVoice}`,
                action: 'changeTTS'
            },
            {
                label: `$(unmute) Select Preset Voice (${activeTTSVoice})`,
                description: 'Pick from 54 Kokoro voices (US/UK/JA/ZH) or Piper voices',
                action: 'selectVoice'
            },
            {
                label: '$(record) Configure Voice Cloning Audio (.wav)',
                description: 'Set reference audio for zero-shot cloning (F5-TTS / XTTS)',
                action: 'setCloneAudio'
            },
            {
                label: '$(symbol-keyword) Configure Coding Vocabulary Bias',
                description: 'Tune terms (snake_case, kubectl, async/await) for higher recognition accuracy',
                action: 'editPrefix'
            },
            {
                label: '$(clear-all) Clear Code Highlight (Ctrl+Alt+C)',
                description: 'Dismiss any active code highlight in editor',
                action: 'clearHighlight'
            },
            {
                label: `$(radio-tower) Dual-Channel Spoken Audio: ${autoSpeakResponses ? 'ENABLED' : 'DISABLED'}`,
                description: 'Automatically speaks [SPEECH] tags or response summaries aloud',
                action: 'toggleAutoSpeak'
            },
            {
                label: `$(comment-discussion) InSitu Guidance Injection: ${injectSocraticGuidance ? 'ENABLED' : 'DISABLED'}`,
                description: 'Appends InSitu mentor mode & speech prompt instructions to chat queries',
                action: 'toggleInjectGuidance'
            }
        ];
    }

    (qp as any).refreshMenu = () => {
        if ((qp as any).menuVisible) {
            qp.items = getMenuItems();
        }
    };

    // Default clean bar with NO drop-down items taking space
    qp.items = [];

    qp.onDidTriggerButton(button => {
        const tooltip = button.tooltip || '';
        if (tooltip.includes('Speak Prompt')) {
            startRecording(qp);
        } else if (tooltip.includes('Stop recording')) {
            stopRecording();
        } else if (tooltip.includes('Cancel recording')) {
            cancelRecording();
            updateQuickPickRecordingUI(qp, false);
        } else if (tooltip.includes('Add Active File')) {
            const editor = vscode.window.activeTextEditor;
            if (editor) {
                const sel = editor.selection;
                const text = editor.document.getText(sel);
                const file = path.basename(editor.document.fileName);
                const prefix = text
                    ? `Regarding ${file}:${sel.start.line + 1}-${sel.end.line + 1}: "${text.slice(0, 60)}..." `
                    : `In ${file}:${sel.start.line + 1}: `;
                qp.value = prefix + (qp.value || '');
            } else {
                vscode.window.showInformationMessage('No active editor selection to add.');
            }
        } else if (tooltip.includes('Mode') || tooltip.includes('switch mode')) {
            menuVisible = !menuVisible;
            (qp as any).menuVisible = menuVisible;
            qp.items = menuVisible ? getMenuItems() : [];
        } else if (tooltip.includes('Acceleration') || tooltip.includes('toggle CPU / GPU')) {
            toggleHardwareAcceleration();
        } else if (tooltip.includes('STT Model')) {
            selectSTTModel();
        } else if (tooltip.includes('TTS Voice') || tooltip.includes('switch model or voice')) {
            selectTTSModel();
        } else if (tooltip.includes('Silence Autostop')) {
            silenceAutostop = !silenceAutostop;
            updateQuickPickRecordingUI(qp, state.isRecording || false);
            if (menuVisible) qp.items = getMenuItems();
            vscode.window.showInformationMessage(`Silence autostop: ${silenceAutostop ? 'ON (2s)' : 'OFF'}`);
        } else if (tooltip.includes('Replay Last Tutor')) {
            replayVoice(undefined, true);
        } else if (tooltip.includes('Clear Code Highlight')) {
            clearHighlight();
            vscode.window.showInformationMessage('InSitu highlight cleared.');
        } else if (tooltip.includes('Stop Speech') || tooltip.includes('Silence Audio')) {
            stopSpeech();
        }
    });

    qp.onDidAccept(() => {
        const val = qp.value.trim();
        if (val && !menuVisible) {
            promptAI(val);
            qp.hide();
            return;
        }

        const selected: any = qp.selectedItems[0];
        if (selected) {
            if (selected.mode) {
                updateMode(selected.mode);
                menuVisible = false;
                qp.items = [];
                updateQuickPickRecordingUI(qp, state.isRecording || false);
            } else if (selected.action === 'inspect') {
                vscode.commands.executeCommand('insitu.sendSelection');
                qp.hide();
            } else if (selected.action === 'replay') {
                replayVoice(undefined, true);
                menuVisible = false;
                qp.items = [];
            } else if (selected.action === 'stopSpeech') {
                stopSpeech();
                menuVisible = false;
                qp.items = [];
            } else if (selected.action === 'clearHighlight') {
                clearHighlight();
                menuVisible = false;
                qp.items = [];
                vscode.window.showInformationMessage('InSitu highlight cleared.');
            } else if (selected.action === 'toggleVAD') {
                silenceAutostop = !silenceAutostop;
                qp.items = getMenuItems();
                updateQuickPickRecordingUI(qp, state.isRecording || false);
                vscode.window.showInformationMessage(`Silence autostop: ${silenceAutostop ? 'ON (2s)' : 'OFF'}`);
            } else if (selected.action === 'toggleDevice') {
                toggleHardwareAcceleration();
            } else if (selected.action === 'changeSTT') {
                qp.hide();
                selectSTTModel();
            } else if (selected.action === 'changeTTS') {
                qp.hide();
                selectTTSModel();
            } else if (selected.action === 'selectVoice') {
                qp.hide();
                selectTTSVoice();
            } else if (selected.action === 'setCloneAudio') {
                qp.hide();
                setTTSCloneAudio();
            } else if (selected.action === 'editPrefix') {
                qp.hide();
                editPromptPrefix();
            } else if (selected.action === 'toggleAutoSpeak') {
                autoSpeakResponses = !autoSpeakResponses;
                qp.items = getMenuItems();
                vscode.window.showInformationMessage(`Dual-Channel Spoken Audio: ${autoSpeakResponses ? 'ENABLED' : 'DISABLED'}`);
            } else if (selected.action === 'toggleInjectGuidance') {
                injectSocraticGuidance = !injectSocraticGuidance;
                qp.items = getMenuItems();
                vscode.window.showInformationMessage(`InSitu Guidance Prompt Injection: ${injectSocraticGuidance ? 'ENABLED' : 'DISABLED'}`);
            }
        } else if (val) {
            promptAI(val);
            qp.hide();
        }
    });

    qp.onDidHide(() => {
        if (state.isRecording) {
            cancelRecording();
        }
        currentQuickPick = undefined;
    });

    qp.show();
}

function notifyRecordingStatus(isRecording: boolean) {
    const msg = { type: 'recordingStatus', isRecording, silenceAutostop };
    if (tutorProvider) tutorProvider.postMessageToWebview(msg);
}

function notifyTranscription(text: string) {
    const msg = { type: 'transcriptionResult', text };
    if (tutorProvider) tutorProvider.postMessageToWebview(msg);
}

export function activate(context: vscode.ExtensionContext) {
    console.log('[InSitu] Activating InSitu extension v0.3.3...');
    ensureDaemonRunning();
    startTranscriptWatcher();

    // 1. Initialize Visual Highlight Decoration (Soft modern cyan tint with left accent line)
    highlightDecoration = vscode.window.createTextEditorDecorationType({
        backgroundColor: 'rgba(56, 189, 248, 0.08)',
        isWholeLine: true,
        borderWidth: '0 0 0 3px',
        borderStyle: 'solid',
        borderColor: 'rgba(56, 189, 248, 0.8)',
        overviewRulerColor: 'rgba(56, 189, 248, 0.6)',
        overviewRulerLane: vscode.OverviewRulerLane ? vscode.OverviewRulerLane.Right : 4
    });

    // Auto-clear highlight on document edit
    context.subscriptions.push(
        vscode.workspace.onDidChangeTextDocument(e => {
            if (activeHighlightEditor && e.document === activeHighlightEditor.document) {
                clearHighlight();
            }
        })
    );

    // 2. Initialize Status Bar Item
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'insitu.openHUD';
    statusBarItem.tooltip = 'Click to open InSitu In-Window Overlay & Mic (Ctrl+Alt+T)';
    context.subscriptions.push(statusBarItem);

    // 3. Initialize Webview Provider (for sidebar view)
    tutorProvider = new SocraticTutorViewProvider(
        context.extensionUri,
        state,
        (mode) => updateMode(mode),
        (prompt) => promptAI(prompt),
        (text) => replayVoice(text),
        () => {
            if (state.isRecording) stopRecording();
            else startRecording();
        }
    );

    // 4. Update initial mode & models
    updateMode(1);
    refreshActiveSTTModel();
    refreshActiveTTSModel();
    statusBarItem.show();

    // 5. Register Sidebar View (both insituView and socraticMentorView)
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            'insituView',
            tutorProvider
        ),
        vscode.window.registerWebviewViewProvider(
            'socraticMentorView',
            tutorProvider
        )
    );

    // 6. Dual Command Registration (supports both insitu.* and socraticMentor.*)
    const registerDual = (id: string, handler: (...args: any[]) => any) => {
        context.subscriptions.push(
            vscode.commands.registerCommand(`insitu.${id}`, handler),
            vscode.commands.registerCommand(`socraticMentor.${id}`, handler)
        );
    };

    registerDual('openHUD', () => openInWindowHUD());
    registerDual('selectSTTModel', () => selectSTTModel());
    registerDual('toggleDevice', () => toggleHardwareAcceleration());
    registerDual('selectTTSModel', () => selectTTSModel());
    registerDual('selectTTSVoice', () => selectTTSVoice());
    registerDual('setTTSCloneAudio', () => setTTSCloneAudio());
    registerDual('editPromptPrefix', () => editPromptPrefix());
    registerDual('stopSpeech', () => stopSpeech());
    registerDual('clearHighlight', () => {
        clearHighlight();
        vscode.window.showInformationMessage('InSitu highlight cleared.');
    });
    registerDual('toggleMic', () => {
        if (state.isRecording) {
            stopRecording();
        } else {
            if (!currentQuickPick) {
                openInWindowHUD();
            }
            if (currentQuickPick) {
                startRecording(currentQuickPick);
            } else {
                startRecording();
            }
        }
    });
    registerDual('stopMic', () => stopRecording());
    registerDual('cancelMic', () => cancelRecording());
    registerDual('toggleVAD', () => {
        silenceAutostop = !silenceAutostop;
        vscode.window.showInformationMessage(`Silence autostop: ${silenceAutostop ? 'ON (2s)' : 'OFF'}`);
    });
    registerDual('setMode1', () => updateMode(1));
    registerDual('setMode2', () => updateMode(2));
    registerDual('setMode3', () => updateMode(3));
    registerDual('sendSelection', () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showWarningMessage('No active editor found to inspect selection.');
            return;
        }
        const selection = editor.selection;
        const text = editor.document.getText(selection);
        const file = editor.document.fileName;
        const startLine = selection.start.line + 1;
        const endLine = selection.end.line + 1;

        updateMode(3);

        const range = new vscode.Range(
            new vscode.Position(selection.start.line, 0),
            new vscode.Position(selection.end.line, 0)
        );
        applyHighlight(editor, range, {
            file,
            startLine,
            endLine,
            message: 'Inspecting code for InSitu guidance'
        });

        const prompt = `[Mode 3 - Refactor / Doubt]: In file ${path.basename(file)} (lines ${startLine}-${endLine}), I have a question about this code:\n\`\`\`\n${text || editor.document.lineAt(selection.start.line).text}\n\`\`\`\nPlease highlight these lines, explain the architectural design or edge cases, and ask me how we should refactor it.`;

        promptAI(prompt);
    });

    // 7. HTTP Bridge Server on 127.0.0.1:6419
    const server = http.createServer(async (req, res) => {
        res.setHeader('Access-Control-Allow-Origin', '*');
        res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
        res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

        if (req.method === 'OPTIONS') {
            res.writeHead(200);
            res.end();
            return;
        }

        // Endpoint: /highlight
        if (req.method === 'POST' && req.url === '/highlight') {
            let body = '';
            req.on('data', chunk => body += chunk);
            req.on('end', async () => {
                try {
                    const { file, startLine, endLine, message, mode } = JSON.parse(body);

                    if (mode && typeof mode === 'number') {
                        updateMode(mode);
                    }

                    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
                    const editor = await vscode.window.showTextDocument(doc, { preview: false });

                    const range = new vscode.Range(
                        new vscode.Position(Math.max(0, startLine - 1), 0),
                        new vscode.Position(Math.max(0, endLine - 1), 0)
                    );

                    editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
                    editor.selection = new vscode.Selection(range.start, range.start);
                    applyHighlight(editor, range, { file, startLine, endLine, message });

                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ status: 'ok', message: 'Viewport centered and code highlighted' }));
                } catch (e: any) {
                    res.writeHead(500, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ status: 'error', error: e.message }));
                }
            });
            return;
        }

        // Endpoint: /clear_highlight
        if (req.method === 'POST' && req.url === '/clear_highlight') {
            clearHighlight();
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ status: 'ok', cleared: true }));
            return;
        }

        // Endpoint: /selection
        if (req.method === 'GET' && req.url === '/selection') {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ active: false }));
                return;
            }
            const selection = editor.selection;
            const text = editor.document.getText(selection);
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                active: true,
                file: editor.document.fileName,
                startLine: selection.start.line + 1,
                endLine: selection.end.line + 1,
                selectedText: text
            }));
            return;
        }

        // Endpoint: /mode
        if (req.url === '/mode') {
            if (req.method === 'GET') {
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    mode: currentMode,
                    modeName: state.modeName
                }));
                return;
            }
            if (req.method === 'POST') {
                let body = '';
                req.on('data', chunk => body += chunk);
                req.on('end', () => {
                    try {
                        const { mode } = JSON.parse(body);
                        updateMode(mode);
                        res.writeHead(200, { 'Content-Type': 'application/json' });
                        res.end(JSON.stringify({ status: 'ok', mode: currentMode }));
                    } catch (e: any) {
                        res.writeHead(400, { 'Content-Type': 'application/json' });
                        res.end(JSON.stringify({ status: 'error', error: e.message }));
                    }
                });
                return;
            }
        }

        // Endpoint: /speak_notify
        if (req.method === 'POST' && req.url === '/speak_notify') {
            let body = '';
            req.on('data', chunk => body += chunk);
            req.on('end', () => {
                try {
                    const data = JSON.parse(body);
                    if (data.message) state.lastSpoken = data.message;
                    state.isSpeaking = (data.speaking !== undefined) ? data.speaking : true;
                    notifySpeakingStatus(state.isSpeaking || false);
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ status: 'ok' }));
                } catch (e: any) {
                    res.writeHead(400, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: e.message }));
                }
            });
            return;
        }

        // Endpoint: /stop_speech
        if (req.method === 'POST' && req.url === '/stop_speech') {
            stopSpeech();
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ status: 'ok', stopped: true }));
            return;
        }

        // Endpoint: /insert_prompt
        if (req.method === 'POST' && req.url === '/insert_prompt') {
            let body = '';
            req.on('data', chunk => body += chunk);
            req.on('end', () => {
                try {
                    const { prompt } = JSON.parse(body);
                    promptAI(prompt);
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ status: 'ok' }));
                } catch (e: any) {
                    res.writeHead(400, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: e.message }));
                }
            });
            return;
        }

        // Endpoint: /stt_models
        if (req.method === 'GET' && req.url === '/stt_models') {
            try {
                const out = await runPython([MODEL_MANAGER_SCRIPT, 'list']);
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(out);
            } catch (e: any) {
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: e.message }));
            }
            return;
        }

        // Endpoint: /set_stt_model
        if (req.method === 'POST' && req.url === '/set_stt_model') {
            let body = '';
            req.on('data', chunk => body += chunk);
            req.on('end', async () => {
                try {
                    const { model } = JSON.parse(body);
                    const out = await runPython([MODEL_MANAGER_SCRIPT, 'set', '--model', model]);
                    await refreshActiveSTTModel();
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(out);
                } catch (e: any) {
                    res.writeHead(400, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: e.message }));
                }
            });
            return;
        }

        // Endpoint: /stt_prefix
        if (req.url === '/stt_prefix') {
            if (req.method === 'GET') {
                try {
                    const out = await runPython([MODEL_MANAGER_SCRIPT, 'get']);
                    const parsed = JSON.parse(out);
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ prompt_prefix: parsed.prompt_prefix }));
                } catch (e: any) {
                    res.writeHead(500, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: e.message }));
                }
                return;
            }
            if (req.method === 'POST') {
                let body = '';
                req.on('data', chunk => body += chunk);
                req.on('end', async () => {
                    try {
                        const { prefix } = JSON.parse(body);
                        const out = await runPython([MODEL_MANAGER_SCRIPT, 'set-prefix', '--prefix', prefix]);
                        res.writeHead(200, { 'Content-Type': 'application/json' });
                        res.end(out);
                    } catch (e: any) {
                        res.writeHead(400, { 'Content-Type': 'application/json' });
                        res.end(JSON.stringify({ error: e.message }));
                    }
                });
                return;
            }
        }

        // Endpoint: /status
        if (req.method === 'GET' && req.url === '/status') {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                status: 'ok',
                port: 6419,
                mode: currentMode,
                modeName: state.modeName
            }));
            return;
        }

        res.writeHead(404).end();
    });

    server.on('error', (err: any) => {
        if (err.code === 'EADDRINUSE') {
            console.warn('[InSitu] Port 6419 already in use, assuming active bridge.');
        }
    });

    server.listen(6419, '127.0.0.1', () => {
        console.log('[InSitu] IDE Bridge server listening on http://127.0.0.1:6419');
    });

    context.subscriptions.push({ dispose: () => server.close() });
}

export function deactivate() {
    stopTranscriptWatcher();
    stopSpeech();
    if (micProcess) {
        try { micProcess.kill(); } catch {}
    }
}
