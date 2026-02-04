import threading
import time
import os
import wave
import pyaudio
import mss
from PIL import Image
import imagehash

# ================= 纯净版配置区 =================
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHECK_INTERVAL = 2
DIFF_THRESHOLD = 10

class AutoRecorder:
    def __init__(self, course_name):
        self.course_name = course_name
        self.timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.save_dir = f"./data/{self.timestamp}_{self.course_name}"
        self.screenshot_dir = os.path.join(self.save_dir, "screenshots")
        
        os.makedirs(self.screenshot_dir, exist_ok=True)
        
        self.is_recording = False
        self.audio_frames = []
        self.lock = threading.Lock()

    def start(self):
        self.is_recording = True
        print(f"🔴 正在录制：{self.course_name}")
        self.audio_thread = threading.Thread(target=self._record_audio)
        self.screen_thread = threading.Thread(target=self._smart_capture)
        self.audio_thread.start()
        self.screen_thread.start()

    def stop(self):
        if not self.is_recording: return
        self.is_recording = False
        self.audio_thread.join()
        self.screen_thread.join()
        self._save_wav()

    def _record_audio(self):
        p = pyaudio.PyAudio()
        try:
            stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
            while self.is_recording:
                data = stream.read(CHUNK, exception_on_overflow=False)
                with self.lock:
                    self.audio_frames.append(data)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"音频出错: {e}")
        finally:
            p.terminate()

    def _save_wav(self):
        filename = os.path.join(self.save_dir, "lecture.wav")
        try:
            with wave.open(filename, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(pyaudio.PyAudio().get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(self.audio_frames))
        except:
            pass

    def _smart_capture(self):
        last_hash = None
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            while self.is_recording:
                try:
                    sct_img = sct.grab(monitor)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    curr_hash = imagehash.average_hash(img)
                    if last_hash is None or (curr_hash - last_hash) > DIFF_THRESHOLD:
                        now_str = time.strftime("%H-%M-%S")
                        filename = os.path.join(self.screenshot_dir, f"Slide_{now_str}.jpg")
                        img.save(filename, quality=50)
                        last_hash = curr_hash
                    time.sleep(CHECK_INTERVAL)
                except:
                    break