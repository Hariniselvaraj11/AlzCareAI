# Alzheimer Patient Support System with AI Companion

## 🧠 About
An intelligent healthcare system to support Alzheimer's patients in daily life and help caretakers monitor them. Features AI-powered face recognition, smart chatbot companion, voice-based reminder alerts, routine monitoring, and analytics.

## 📁 Project Structure
```
alzheimer_project/
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── dataset/               # Face image dataset (auto-created)
├── trainer/               # Trained model files (auto-created)
├── data/                  # SQLite database (auto-created)
├── templates/             # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── patient_dashboard.html
│   ├── caretaker_dashboard.html
│   ├── face_register.html
│   ├── face_train.html
│   └── face_recognize.html
└── static/
    ├── css/
    │   └── style.css
    └── js/
        └── app.js
```

## 🚀 Setup & Run Instructions

### 1. Install Python 3.8+ from https://python.org

### 2. Create and activate virtual environment (recommended)
```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Mac/Linux:
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. (Optional) Set up AI Chatbot with Ollama (recommended, free & local)
```bash
# Install Ollama from https://ollama.ai
# Then pull a model:
ollama pull mistral

# Ollama runs on http://localhost:11434 by default
# The chatbot will automatically connect to it
```

### 5. (Alternative) Use OpenAI API instead of Ollama
```bash
# Set environment variables before running:
export CHATBOT_PROVIDER=openai
export OPENAI_API_KEY=your-key-here
export OPENAI_MODEL=gpt-3.5-turbo
```

### 6. Run the application
```bash
python app.py
```

### 7. Open in browser
Go to: **http://localhost:5000**

## 🔧 Features

| Feature | Description |
|---------|------------|
| Role Selection | Patient / Caretaker dashboards |
| Face Registration | Capture face images via webcam |
| Face Training | Train LBPH recognizer |
| Face Recognition | Live recognition with relationship info |
| **AI Chatbot** | **LLM-powered conversational companion with DB context** |
| **Voice Alerts** | **Automatic spoken reminder alerts on patient dashboard** |
| Reminders | Daily task management with alerts |
| Activity Logs | Track all system activities |
| Analytics | Charts and completion statistics |
| Known People | Memory module for patient's contacts |
| Emergency | Quick access to emergency contacts |

## 🤖 Chatbot Details
- Uses Ollama (local LLM) by default — no API key needed
- Falls back to rule-based responses if LLM is unavailable
- Reads from `known_people`, `chatbot_memory`, and `reminders` tables for context
- Supports: greetings, family questions, reminders, confusion/anxiety support, date/time, memory storage

## 🔔 Voice Alert Details
- Automatically checks for due reminders every 30 seconds on the patient dashboard
- Uses browser SpeechSynthesis API to speak reminders aloud
- Shows a visible banner at the top of the screen
- Patient can mark reminder as "Done" or dismiss with "Later"
- Repeats alert every 30 seconds until completed or dismissed

## ⚙️ Environment Variables (all optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `CHATBOT_PROVIDER` | `ollama` | `ollama` or `openai` |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama API endpoint |
| `OLLAMA_MODEL` | `mistral` | Ollama model name |
| `OPENAI_API_KEY` | (empty) | OpenAI API key |
| `OPENAI_MODEL` | `gpt-3.5-turbo` | OpenAI model name |
| `OPENAI_API_URL` | `https://api.openai.com/v1/chat/completions` | OpenAI-compatible endpoint |

## ⚠️ Notes
- Webcam access is required for face features
- Use Chrome or Edge for best webcam + voice support
- Face training needs at least 5 images per person
- If Ollama is not running, the chatbot automatically uses built-in rule-based responses
- Voice alerts require browser audio permission (auto-prompted)
