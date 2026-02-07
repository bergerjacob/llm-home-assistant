import os
#import requests
from gtts import gTTS
import subprocess
from sys import platform
from time import sleep
from shlex import split

BASE_DIR = os.path.dirname(__file__)
AUDIO_DIR = os.path.join(BASE_DIR, "_audios")
os.makedirs(AUDIO_DIR, exist_ok=True)
TEXTS_DIR = os.path.join(BASE_DIR, "_texts")
os.makedirs(TEXTS_DIR, exist_ok=True)
WHISPER_CPP_PATH = os.path.join(BASE_DIR, "whisper.cpp", "build","bin", "whisper-cli")
MODEL_PATH = os.path.join(BASE_DIR, "whisper.cpp", "models", "ggml-tiny.en-q5_1.bin")

def whisper_model_transcribe(audio_path: str) -> str:
    """
    Transcribe an audio file using whisper.cpp CLI and return text.
    """
    
    # whisper.cpp writes output to "current_request.txt"
    txt_file = os.path.join(TEXTS_DIR, "current_request.txt")


    if not os.path.exists(audio_path):
        _LOGGER.info(f"Audio file not found: {audio_path}")
        return ""

    if not os.path.exists(WHISPER_CPP_PATH):
        _LOGGER.info(f"WHISPER_CPP_PATH: {WHISPER_CPP_PATH}\n does it exist? {os.path.exists(WHISPER_CPP_PATH)}\n")
        _LOGGER.info(f"MODEL_PATH: {MODEL_PATH}\n does it exist? {os.path.exists(MODEL_PATH)}\n")
        return ""
    
    # Run whisper.cpp CLI
    cmd = f"{WHISPER_CPP_PATH} -m {MODEL_PATH} -f {audio_path} -otxt -of {txt_file[:-4]}"
    try:
        try:
            subprocess.run(split(cmd), check=True)
        except subprocess.CalledProcessError as e:
            _LOGGER.warning(f"Error running whisper.cpp: {e}")

        #read text from created file and returns it
        if os.path.exists(txt_file):
            with open(txt_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        else:
            _LOGGER.info(f"whisper.cpp did not produce a txt output for audio file: {audio_path}")
            return ""

    except subprocess.CalledProcessError as e:
        _LOGGER.info(f"Error running whisper.cpp: {e}")
        return ""


from datetime import datetime

#STT - Speech -> Text
def stt_whisper(audio_path: str) -> str:
    if not os.path.exists(audio_path):
        _LOGGER.info(f"Audio file not found: {audio_path}")
        return ""

    _LOGGER.info(f"Transcribing audio from: {audio_path}")
    text = whisper_model_transcribe(audio_path)
    _LOGGER.info(f"Whisper transcription:---{text}---\n")
    
    return text

#TTS - Text -> Speech
def tts_google(text: str, output_path: str = None) -> str:
    """
    convert text to audio using Google TTS
    returns audio file and plays audio
    """
    if output_path is None:
        output_path = os.path.join(AUDIO_DIR, "response_audio")
    else:
        output_path = os.path.join(AUDIO_DIR, output_path)
    
    # Use the global AUDIO_DIR
    output_path = os.path.join(AUDIO_DIR,output_path)

    #clean old file if it exists
    if os.path.exists(output_path):
        os.remove(output_path)

    try:
        #use google's model to read text
        tts = gTTS(text=text, lang='en')
        #saves audio file and plays it
        tts.save(output_path)

        play_audio_tss(output_path, "mp3")
        return output_path
    except Exception as e:
        print(f"Error in tts_google: {e}")
        return ""


def tts_espeak(text: str, output_path="response_audio" , voice="en-US", speed=175, pitch=50) -> str:
    """
    convert text to audio using espeak TTS, runs locally
    returns audio file and plays audio
    """

    # Use the global AUDIO_DIR
    output_path = os.path.join(AUDIO_DIR,output_path)

    #clean old file if it exists
    if os.path.exists(output_path):
        os.remove(output_path)

    #gets command for espeak
    cmd = [
        "espeak",
        "-v", voice,
        "-s", str(speed),
        "-p", str(pitch),
        "-w", output_path,
        text
    ]

    #run command to play audio
    subprocess.run(cmd, check=True)
    play_audio_tss(output_path, "wav")
    return output_path


def play_audio_tss(path: str, type: str):
    """Play audio using system player (ffplay/aplay)."""
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    try:
        if type.lower() == "wav":
            # lightweight WAV player on Linux
            subprocess.run(["aplay", path], check=True)
        else:
            # mp3 or other formats
            subprocess.run(["ffplay", "-nodisp", "-autoexit", path], check=True)
    except FileNotFoundError as e:
        print(f"Audio player not found: {e}")
    except Exception as e:
        print(f"Error playing audio: {e}")



#Recording audio file - saves into my_audio
import logging
_LOGGER = logging.getLogger(__name__)
# Global variables
_process = None
import threading
import subprocess
_process = None
_is_recording = False
FIXED_FILENAME = "current_request.wav"

def start_recording():
    """Start recording with simple subprocess."""
    global _process, _is_recording
    
    _LOGGER.info("=== START RECORDING CALLED ===")
    
    # Check if already recording
    if _is_recording:
        _LOGGER.warning("Already recording")
        return {"status": "already_recording"}
    
    # Create directory if it doesn't exist
    if not os.path.exists(AUDIO_DIR):
        try:
            os.makedirs(AUDIO_DIR, exist_ok=True)
            _LOGGER.info(f"Created directory: {AUDIO_DIR}")
        except Exception as e:
            _LOGGER.error(f"Failed to create directory: {e}")
            return {"status": "error", "error": f"Directory creation failed: {e}"}
    
    filepath = os.path.join(AUDIO_DIR, FIXED_FILENAME)
    _LOGGER.info(f"Will save to: {filepath}")
    
    # Remove old file if exists
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            _LOGGER.info(f"Removed previous file: {FIXED_FILENAME}")
        except Exception as e:
            _LOGGER.warning(f"Could not remove old file: {e}")
    
    # Build ffmpeg command based on OS
    if platform == 'darwin':  # macOS
        cmd = ['ffmpeg', '-f', 'avfoundation', '-i', ':0', '-t', '300', '-y', filepath]
    elif platform == 'win32':  # Windows
        cmd = ['ffmpeg', '-f', 'dshow', '-i', 'audio=Microphone', '-t', '300', '-y', filepath]
    else:  # Linux (Home Assistant)
        cmd = [ "ffmpeg", "-y", "-f", "alsa", "-i", "plughw:3,0", "-ac", "1", "-ar", "16000",  "-sample_fmt", "s16", filepath]
    _LOGGER.info(f"Starting recording command: {' '.join(cmd)}")
    
    try:
        # Start ffmpeg process with proper error handling
        _process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            universal_newlines=False
        )
        _is_recording = True
        
        # Wait a moment to check if ffmpeg started successfully
        sleep(0.5)
        
        # Check if process died immediately
        return_code = _process.poll()
        if return_code is not None:
            # Process died - read error output
            stdout, stderr = _process.communicate()
            error_msg = ""
            if stderr:
                error_msg = stderr.decode('utf-8', errors='ignore')[:500]
            
            _LOGGER.error(f"ffmpeg failed immediately with code {return_code}")
            _LOGGER.error(f"Error: {error_msg}")
            
            _is_recording = False
            _process = None
            
            if "pulse" in error_msg.lower() and "connection refused" in error_msg.lower():
                error_msg += " - PulseAudio not running. Try: sudo apt-get install pulseaudio"
            elif "dshow" in error_msg.lower() or "avfoundation" in error_msg.lower():
                error_msg += " - Check microphone permissions in OS settings"
            
            return {"status": "error", "error": f"ffmpeg failed: {error_msg}"}
        
        _LOGGER.info(f"Recording started successfully (PID: {_process.pid})")
        
        # Start thread to read ffmpeg output (prevents stderr buffer filling)
        def read_output():
            try:
                while _is_recording and _process.poll() is None:
                    line = _process.stderr.readline()
                    if line:
                        line_str = line.decode('utf-8', errors='ignore').strip()
                        if line_str:  # Only log non-empty lines
                            if 'time=' in line_str:
                                _LOGGER.debug(f"Recording: {line_str}")
                            elif 'error' in line_str.lower():
                                _LOGGER.error(f"ffmpeg error: {line_str}")
            except Exception as e:
                _LOGGER.debug(f"Output reader error: {e}")
        
        threading.Thread(target=read_output, daemon=True).start()
        
        return {
            "status": "started", 
            "filename": FIXED_FILENAME,
            "filepath": filepath,
            "pid": _process.pid
        }
        
    except FileNotFoundError:
        _LOGGER.error("ffmpeg not found! Install ffmpeg first.")
        _is_recording = False
        _process = None
        return {"status": "error", "error": "ffmpeg command not found"}
    except Exception as e:
        _LOGGER.error(f"Failed to start recording: {e}")
        import traceback
        _LOGGER.error(traceback.format_exc())
        _is_recording = False
        _process = None
        return {"status": "error", "error": str(e)}

def stop_recording():
    """Stop recording gracefully."""
    global _process, _is_recording
    
    _LOGGER.info("=== STOP RECORDING CALLED ===")
    
    if not _is_recording or _process is None:
        _LOGGER.warning("No recording in progress")
        return {"status": "not_recording"}
    
    filepath = os.path.join(AUDIO_DIR, FIXED_FILENAME)
    pid = _process.pid
    _LOGGER.info(f"Stopping recording (PID: {pid})...")
    
    try:
        # Send 'q' to stdin (graceful stop)
        try:
            if _process.stdin:
                _process.stdin.write(b'q\n')
                _process.stdin.flush()
                _LOGGER.info("Sent 'q' to ffmpeg for graceful stop")
        except (BrokenPipeError, OSError) as e:
            _LOGGER.warning(f"Could not send quit signal: {e}")
        
        # Wait for process to end
        def wait_ffmpeg():
            try:
                _process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                _LOGGER.warning("ffmpeg didn't stop gracefully, terminating...")
                _process.terminate()
                try:
                    _process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    _LOGGER.warning("ffmpeg didn't terminate, killing...")
                    _process.kill()
                    _process.wait()
        
        threading.Thread(target=wait_ffmpeg, daemon=True).start()
        
    except Exception as e:
        _LOGGER.error(f"Error during stop process: {e}")
    
    finally:
        _is_recording = False
        _process = None
        _LOGGER.info(f"Recording stopped (was PID: {pid})")
    
    # Check if file was created
    if os.path.exists(filepath):
        size = os.path.getsize(filepath)
        _LOGGER.info(f"File created: {filepath} ({size} bytes)")
        return {
            "status": "stopped", 
            "filename": FIXED_FILENAME,
            "filepath": filepath,
            "file_size": size,
            "success": True
        }
    else:
        _LOGGER.error(f"File not created: {filepath}")
        return {
            "status": "stopped", 
            "filename": FIXED_FILENAME,
            "filepath": filepath,
            "error": "File not created",
            "success": False
        }
    
def is_recording():
    """Check if recording is active."""
    if _process is None or not _is_recording:
        return False
    return _process.poll() is None