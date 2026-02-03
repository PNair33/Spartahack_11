from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import requests
import base64
import speech_recognition as sr
import tempfile
import shutil
from pydub import AudioSegment

app = Flask(__name__)
CORS(app)

# --- 🔧 CRITICAL FIX: FFmpeg Auto-Finder ---
ffmpeg_path = shutil.which("ffmpeg") 
if not ffmpeg_path:
    if os.path.exists("/opt/homebrew/bin/ffmpeg"):
        ffmpeg_path = "/opt/homebrew/bin/ffmpeg"
    elif os.path.exists("/usr/local/bin/ffmpeg"):
        ffmpeg_path = "/usr/local/bin/ffmpeg"
    elif os.path.exists(os.path.join(os.getcwd(), "ffmpeg")):
        ffmpeg_path = os.path.join(os.getcwd(), "ffmpeg")

if ffmpeg_path:
    print(f"✅ Found FFmpeg at: {ffmpeg_path}")
    AudioSegment.converter = ffmpeg_path
else:
    print("❌ WARNING: FFmpeg not found! Audio processing will likely fail.")

# Configuration
ELEVENLABS_API_KEY = os.getenv('ELEVENLABS_API_KEY', '')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')

# Voice IDs
VOICE_IDS = {
    'einstein': 'iNAjkzUboK5rAeoMhym0',
    'lincoln': 'REPLACE_WITH_LINCOLN_VOICE_ID' 
}

# Persona prompts - AUTO-LANGUAGE DETECTION INSTRUCTION
PERSONA_PROMPTS = {
    'einstein': """You are Albert Einstein (1879-1955).
BIOGRAPHICAL INFO: Born 1879, Germany. Nobel Prize 1921. Theory of Relativity.
PERSONALITY: Thoughtful, humble, enthusiastic physicist.
IMPORTANT INSTRUCTION: DETECT THE LANGUAGE OF THE USER'S INPUT.
- If the user speaks English, reply in English.
- If the user speaks Spanish, reply in Spanish.
- If the user speaks Hindi, reply in Hindi.
- If the user speaks ANY other language, reply in that SAME language.
Do not mention that you are switching languages. Just do it naturally.""",
    
    'lincoln': """You are Abraham Lincoln (1809-1865).
BIOGRAPHICAL INFO: 16th US President. Civil War leader.
PERSONALITY: Honest, storyteller, wise, firm.
IMPORTANT INSTRUCTION: DETECT THE LANGUAGE OF THE USER'S INPUT.
- If the user speaks English, reply in English.
- If the user speaks Spanish, reply in Spanish.
- If the user speaks ANY other language, reply in that SAME language.
Do not mention that you are switching languages. Just do it naturally."""
}

class SpeechRecognitionService:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 4000
        self.recognizer.dynamic_energy_threshold = True
    
    def audio_to_text(self, audio_file):
        try:
            with sr.AudioFile(audio_file) as source:
                audio = self.recognizer.record(source)
                # Defaults to auto-detect/global
                text = self.recognizer.recognize_google(audio)
                return text
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(f"Error in speech recognition: {e}")
            return None

def generate_ai_response(question, persona='einstein'):
    try:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5000",
            "X-Title": "Museum AI Exhibit"
        }
        
        payload = {
            "model": "google/gemini-2.0-flash-001",
            "messages": [
                {
                    "role": "system",
                    "content": PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS['einstein'])
                },
                {
                    "role": "user",
                    "content": question
                }
            ],
            "temperature": 0.7,
            "max_tokens": 200
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"❌ OpenRouter Error: {response.status_code}")
            return "I apologize, but I am having difficulty formulating a response."
    except Exception as e:
        print(f"Error generating response: {e}")
        return "I apologize, but I am having difficulty formulating a response."

def synthesize_speech(text, persona='einstein'):
    try:
        voice_id = VOICE_IDS.get(persona, VOICE_IDS['einstein'])
        if 'REPLACE' in voice_id: voice_id = VOICE_IDS['einstein']

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        
        # 'eleven_multilingual_v2' is key for auto-language support
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "use_speaker_boost": True
            }
        }
        
        response = requests.post(url, headers=headers, json=payload)
        return response.content if response.status_code == 200 else None
    except Exception as e:
        print(f"Error in speech synthesis: {e}")
        return None

speech_service = SpeechRecognitionService()

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/process-audio', methods=['POST'])
def process_audio():
    input_path = None
    wav_path = None
    
    try:
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400
        
        audio_file = request.files['audio']
        persona = request.form.get('persona', 'einstein')
        
        fd, input_path = tempfile.mkstemp(suffix='.webm')
        os.close(fd)
        audio_file.save(input_path)
        
        try:
            audio = AudioSegment.from_file(input_path)
            fd, wav_path = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            audio.export(wav_path, format="wav")
        except Exception as e:
            print(f"❌ AUDIO CONVERSION ERROR: {e}")
            return jsonify({'error': 'Audio conversion failed'}), 500

        question = speech_service.audio_to_text(wav_path)
        
        if not question:
            print("❌ Could not understand audio")
            return jsonify({'error': 'Could not understand audio.'}), 400
        
        print(f"✅ Recognized: {question}")
        
        response_text = generate_ai_response(question, persona)
        print(f"✅ Generated response: {response_text}")
        
        audio_bytes = synthesize_speech(response_text, persona)
        
        if not audio_bytes:
            return jsonify({'error': 'Speech synthesis failed'}), 500
        
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        return jsonify({
            'question': question,
            'response': response_text,
            'audio': audio_base64
        })
    
    except Exception as e:
        print(f"❌ GENERAL ERROR: {e}")
        return jsonify({'error': str(e)}), 500
        
    finally:
        if input_path and os.path.exists(input_path): os.remove(input_path)
        if wav_path and os.path.exists(wav_path): os.remove(wav_path)

@app.route('/api/text-to-speech', methods=['POST'])
def text_to_speech():
    try:
        data = request.get_json()
        text = data.get('text', '')
        persona = data.get('persona', 'einstein')
        if not text: return jsonify({'error': 'No text'}), 400
        audio_bytes = synthesize_speech(text, persona)
        if not audio_bytes: return jsonify({'error': 'TTS failed'}), 500
        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
        return jsonify({'audio': audio_base64})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        question = data.get('question', '')
        persona = data.get('persona', 'einstein')
        if not question: return jsonify({'error': 'No question'}), 400
        response_text = generate_ai_response(question, persona)
        return jsonify({'response': response_text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 70)
    print("MUSEUM AI EXHIBIT - AUTO-DETECT SERVER")
    print("=" * 70)
    app.run(debug=True, host='0.0.0.0', port=5000)
