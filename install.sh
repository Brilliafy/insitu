#!/usr/bin/env bash
# ==============================================================================
# InSitu Installer & Environment Setup Script
# Installs InSitu VS Code extension, MCP bridge, and Dual-Channel AI System Prompt
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}        🏛️  InSitu AI Mentor Setup & Installer        ${NC}"
echo -e "${CYAN}======================================================${NC}"

# 1. Detect Python / Conda environment
CONDA_ENV_PY="/home/Aelfwif/anaconda3/envs/socratic-tutor/bin/python"
if [ -x "$CONDA_ENV_PY" ]; then
    PYTHON_BIN="$CONDA_ENV_PY"
    echo -e "${GREEN}✓ Detected InSitu Python environment:${NC} $PYTHON_BIN"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="$(command -v python3)"
    echo -e "${YELLOW}! Using system python3:${NC} $PYTHON_BIN"
else
    echo -e "${RED}✗ Error: Python 3 not found.${NC}"
    exit 1
fi

# 2. Verify or Install Python Dependencies
echo -e "\n${CYAN}[1/4] Verifying Python Dependencies...${NC}"
"$PYTHON_BIN" -c "from mcp.server.fastmcp import FastMCP; import sounddevice, soundfile, numpy" 2>/dev/null || {
    echo -e "${YELLOW}Installing required Python packages...${NC}"
    "$PYTHON_BIN" -m pip install -q "mcp[cli]" sounddevice soundfile numpy tqdm pyqt6 faster-whisper kokoro
}
echo -e "${GREEN}✓ Python dependencies ready.${NC}"

# 3. Build & Install VS Code / Antigravity Extension
echo -e "\n${CYAN}[2/4] Packaging & Installing VS Code Extension...${NC}"
cd "$SCRIPT_DIR/extension"
if command -v npm &>/dev/null; then
    npm run compile --silent || npm run compile
fi

VSIX_FILE="$SCRIPT_DIR/extension/insitu-0.3.0.vsix"
if [ ! -f "$VSIX_FILE" ]; then
    echo -e "${YELLOW}Packaging VSIX...${NC}"
    npx @vscode/vsce package --no-dependencies --allow-missing-repository --allow-star-activation
fi

if command -v code &>/dev/null; then
    code --install-extension "$VSIX_FILE" --force
    echo -e "${GREEN}✓ InSitu extension installed into VS Code / Antigravity.${NC}"
else
    echo -e "${YELLOW}! 'code' CLI not found. Please manually install: $VSIX_FILE${NC}"
fi

# 4. Check & Update Gemini / Antigravity System Instructions
echo -e "\n${CYAN}[3/4] Configuring AI Mentor System Prompt...${NC}"
GEMINI_MD="/home/Aelfwif/.gemini/GEMINI.md"

DUAL_CHANNEL_PROMPT='
## 7. Dual-Channel Socratic Code Mentor Protocol
When interacting with the user or responding to programming requests:
- Chat / Written Channel: Provide your complete, detailed, and uncompromised technical reasoning, explanations, code blocks, and diagrams in chat. Do NOT truncate or abbreviate written responses.
- Spoken Audio Channel: Always provide a voice companion message for the user:
  * At the very end of your final response, append:
    `[SPEECH] 1-2 punchy, human-like spoken sentences (15-25 words max) summarizing the core answer or asking a guiding pedagogical question [/SPEECH]`
  * OR call the `speak_to_user(message=...)` tool if available.
  * Spoken Audio Rules: Punchy, conversational, and pedagogical. Never read out raw markdown tables, URLs, line numbers, or long code snippets in speech.
'

if [ -f "$GEMINI_MD" ]; then
    if grep -q "Dual-Channel Socratic Code Mentor Protocol" "$GEMINI_MD" || grep -q "Dual-Channel" "$GEMINI_MD"; then
        echo -e "${GREEN}✓ Dual-Channel Socratic Mentor instructions already present in $GEMINI_MD.${NC}"
    else
        echo -e "${YELLOW}Adding Dual-Channel Socratic Mentor instructions to $GEMINI_MD...${NC}"
        echo -e "$DUAL_CHANNEL_PROMPT" >> "$GEMINI_MD"
        echo -e "${GREEN}✓ Successfully added mentor instructions to $GEMINI_MD.${NC}"
    fi
else
    echo -e "${YELLOW}Creating $GEMINI_MD with Dual-Channel Socratic Mentor instructions...${NC}"
    mkdir -p "$(dirname "$GEMINI_MD")"
    echo -e "$DUAL_CHANNEL_PROMPT" > "$GEMINI_MD"
    echo -e "${GREEN}✓ Created $GEMINI_MD.${NC}"
fi

# 5. Configure InSitu MCP Server
echo -e "\n${CYAN}[4/4] Configuring InSitu MCP Server...${NC}"
MCP_CONFIGS=(
    "/home/Aelfwif/.gemini/antigravity/mcp_config.json"
    "/home/Aelfwif/.gemini/antigravity-cli/mcp_config.json"
)

for config_file in "${MCP_CONFIGS[@]}"; do
    if [ -f "$config_file" ]; then
        "$PYTHON_BIN" - <<EOF
import json

path = "$config_file"
try:
    with open(path, "r") as f:
        data = json.load(f)
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"]["insitu"] = {
        "command": "$PYTHON_BIN",
        "args": ["$SCRIPT_DIR/server/tutor_mcp.py"]
    }
    data["mcpServers"]["tutor"] = {
        "command": "$PYTHON_BIN",
        "args": ["$SCRIPT_DIR/server/tutor_mcp.py"]
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  ✓ Updated MCP configuration: {path}")
except Exception as e:
    print(f"  ! Warning: could not update {path}: {e}")
EOF
    fi
done

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}  🎉 InSitu Installation Complete!                     ${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "Quick Shortcuts:"
echo -e "  • ${CYAN}Ctrl+Alt+T${NC} : Open In-Window HUD Overlay"
echo -e "  • ${CYAN}Ctrl+Alt+M${NC} : Toggle Microphone / Voice Dictation"
echo -e "  • ${CYAN}Ctrl+Alt+S${NC} / ${CYAN}Alt+S${NC} : Instant Stop & Silence Speech"
echo -e "  • ${CYAN}Ctrl+Alt+G${NC} : Toggle CPU / GPU CUDA Acceleration"
echo -e "  • ${CYAN}Ctrl+Alt+V${NC} : Choose from 54 Kokoro / Piper voices"
echo -e "  • ${CYAN}Ctrl+Alt+O${NC} : Select or download STT/TTS Hugging Face models"
echo -e "  • ${CYAN}Ctrl+Alt+I${NC} : Inspect & Send Selected Code to Tutor"
echo -e "  • ${CYAN}Ctrl+Alt+C${NC} : Clear Visual Code Highlight"
echo ""
