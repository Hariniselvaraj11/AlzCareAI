
/* ═══════════════════════════════════════════════════════════
   AlzCare AI - Main JavaScript
   ═══════════════════════════════════════════════════════════ */

// ─── Chat Functions ──────────────────────────────────────────
function sendChat() {
    const input = document.getElementById('chatInput');
    const msg = input.value.trim();
    if (!msg) return;
    
    const container = document.getElementById('chatMessages');
    
    // Add user message
    container.innerHTML += `
        <div class="chat-bubble user">
            <div class="bubble-avatar"><i class="fas fa-user"></i></div>
            <div class="bubble-content">${escapeHtml(msg)}</div>
        </div>
    `;
    
    input.value = '';
    container.scrollTop = container.scrollHeight;
    
    // Get bot response
    fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ message: msg })
    })
    .then(r => r.json())
    .then(data => {
        container.innerHTML += `
            <div class="chat-bubble bot">
                <div class="bubble-avatar"><i class="fas fa-robot"></i></div>
                <div class="bubble-content">${data.response}</div>
            </div>
        `;
        container.scrollTop = container.scrollHeight;
    });
}

// ─── Reminder Functions ──────────────────────────────────────
function completeReminder(id) {
    fetch(`/api/reminders/${id}/complete`, { method: 'POST' })
        .then(r => r.json())
        .then(() => location.reload());
}

function deleteReminder(id) {
    if (!confirm('Delete this reminder?')) return;
    fetch(`/api/reminders/${id}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(() => {
            const el = document.getElementById('reminder-' + id);
            if (el) el.remove();
        });
}

// ─── People Functions ────────────────────────────────────────
function deletePerson(id) {
    if (!confirm('Remove this person?')) return;
    fetch(`/api/people/${id}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(() => {
            const el = document.getElementById('person-' + id);
            if (el) el.remove();
        });
}

// ─── Face Recognition (Patient) ──────────────────────────────
let patientStream = null;

function startRecognition() {
    navigator.mediaDevices.getUserMedia({ video: true })
        .then(stream => {
            patientStream = stream;
            document.getElementById('recognizeVideo').srcObject = stream;
        })
        .catch(() => {
            document.getElementById('recognitionResult').innerHTML = 
                '<div class="alert alert-danger">Camera access denied.</div>';
        });
}

function captureAndRecognize() {
    const video = document.getElementById('recognizeVideo');
    const canvas = document.getElementById('recognizeCanvas');
    canvas.width = video.videoWidth || 480;
    canvas.height = video.videoHeight || 360;
    canvas.getContext('2d').drawImage(video, 0, 0);
    const imgData = canvas.toDataURL('image/jpeg');
    
    fetch('/api/face/recognize', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ image: imgData })
    })
    .then(r => r.json())
    .then(data => {
        const container = document.getElementById('recognitionResult');
        if (!data.success) {
            container.innerHTML = `<div class="alert alert-warning">${data.error}</div>`;
            return;
        }
        if (data.faces.length === 0) {
            container.innerHTML = '<div class="alert alert-info">No face detected. Please look at the camera.</div>';
            return;
        }
        container.innerHTML = data.faces.map(f => `
            <div class="glass-card-light p-3 mb-2" style="border-left: 4px solid ${f.known ? '#28a745' : '#dc3545'}">
                <h4 class="${f.known ? 'text-success' : 'text-danger'}">${f.name}</h4>
                ${f.relationship ? `<p class="text-primary mb-1"><strong>${f.relationship}</strong></p>` : ''}
                <small class="text-muted">Confidence: ${f.confidence}%</small>
            </div>
        `).join('');
    });
}

// ─── Utility ─────────────────────────────────────────────────
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ─── Reminder Alerts ─────────────────────────────────────────
function checkReminders() {
    fetch('/api/reminders')
        .then(r => r.json())
        .then(reminders => {
            const now = new Date();
            const currentTime = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
            
            reminders.forEach(r => {
                if (r.status === 'pending' && r.time === currentTime) {
                    if (Notification.permission === 'granted') {
                        new Notification('AlzCare Reminder', { body: r.title, icon: '/static/favicon.ico' });
                    }
                    // Play sound
                    try {
                        const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQ==');
                        audio.play().catch(() => {});
                    } catch(e) {}
                }
            });
        });
}

// Request notification permission
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

// Check reminders every minute
setInterval(checkReminders, 60000);


// ═══════════════════════════════════════════════════════════
// Voice-Based Reminder Alert System
// ═══════════════════════════════════════════════════════════

let voiceAlertedIds = new Set();       // IDs already spoken at least once
let voiceAlertInterval = null;
let voiceAlertDismissed = new Set();   // IDs user dismissed (won't repeat)

function startVoiceReminderAlerts() {
    // Only run on patient dashboard
    if (!document.querySelector('.patient-dashboard')) return;
    
    // Check immediately, then every 30 seconds
    checkAndAlertReminders();
    voiceAlertInterval = setInterval(checkAndAlertReminders, 30000);
}

function checkAndAlertReminders() {
    fetch('/api/reminders/pending-now')
        .then(r => r.json())
        .then(reminders => {
            if (!reminders || reminders.length === 0) {
                hideVoiceAlertBanner();
                return;
            }
            
            reminders.forEach(r => {
                if (voiceAlertDismissed.has(r.id)) return;
                
                // Show visual alert
                showVoiceAlertBanner(r);
                
                // Speak the reminder (repeat if not yet alerted or every 60s cycle)
                speakReminder(r);
            });
        })
        .catch(err => console.log('Reminder check error:', err));
}

function speakReminder(reminder) {
    if (!('speechSynthesis' in window)) return;
    
    // Don't queue if already speaking
    window.speechSynthesis.cancel();
    
    const text = "Reminder: It is time for " + reminder.title;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.85;    // Slightly slower for clarity
    utterance.pitch = 1.0;
    utterance.volume = 1.0;
    utterance.lang = 'en-US';
    
    // Try to pick a clear voice
    const voices = window.speechSynthesis.getVoices();
    const preferred = voices.find(v => v.lang.startsWith('en') && v.name.includes('Female')) 
                   || voices.find(v => v.lang.startsWith('en'))
                   || voices[0];
    if (preferred) utterance.voice = preferred;
    
    window.speechSynthesis.speak(utterance);
    voiceAlertedIds.add(reminder.id);
}

function showVoiceAlertBanner(reminder) {
    let banner = document.getElementById('voiceAlertBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'voiceAlertBanner';
        banner.style.cssText = `
            position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
            background: linear-gradient(135deg, #ff6b6b, #ee5a24);
            color: white; padding: 18px 24px; text-align: center;
            font-size: 1.2rem; font-weight: 600;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            display: flex; align-items: center; justify-content: center; gap: 15px;
            animation: pulse-banner 2s infinite;
            font-family: 'Plus Jakarta Sans', sans-serif;
        `;
        
        // Add pulse animation style
        if (!document.getElementById('voiceAlertStyles')) {
            const style = document.createElement('style');
            style.id = 'voiceAlertStyles';
            style.textContent = `
                @keyframes pulse-banner {
                    0%, 100% { opacity: 1; }
                    50% { opacity: 0.85; }
                }
                .voice-alert-btn {
                    background: white; color: #ee5a24; border: none;
                    padding: 8px 20px; border-radius: 25px; cursor: pointer;
                    font-weight: 700; font-size: 1rem;
                    transition: transform 0.2s;
                }
                .voice-alert-btn:hover { transform: scale(1.05); }
            `;
            document.head.appendChild(style);
        }
        
        document.body.prepend(banner);
    }
    
    banner.innerHTML = `
        <i class="fas fa-bell" style="font-size: 1.5rem; animation: pulse-banner 1s infinite;"></i>
        <span>🔔 ${escapeHtml(reminder.title)} — It's time!</span>
        <button class="voice-alert-btn" onclick="markReminderDoneFromAlert(${reminder.id})">
            <i class="fas fa-check"></i> Done
        </button>
        <button class="voice-alert-btn" onclick="dismissVoiceAlert(${reminder.id})" style="background: rgba(255,255,255,0.3); color: white;">
            Later
        </button>
    `;
    banner.style.display = 'flex';
}

function hideVoiceAlertBanner() {
    const banner = document.getElementById('voiceAlertBanner');
    if (banner) banner.style.display = 'none';
}

function markReminderDoneFromAlert(id) {
    fetch(`/api/reminders/${id}/complete`, { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            voiceAlertDismissed.add(id);
            voiceAlertedIds.delete(id);
            window.speechSynthesis.cancel();
            hideVoiceAlertBanner();
            // Refresh reminders section if visible
            location.reload();
        });
}

function dismissVoiceAlert(id) {
    voiceAlertDismissed.add(id);
    window.speechSynthesis.cancel();
    hideVoiceAlertBanner();
}

// Initialize voice alerts when page loads
document.addEventListener('DOMContentLoaded', function() {
    // Load voices (needed for some browsers)
    if ('speechSynthesis' in window) {
        window.speechSynthesis.getVoices();
        window.speechSynthesis.onvoiceschanged = function() {
            window.speechSynthesis.getVoices();
        };
    }
    
    startVoiceReminderAlerts();
});
