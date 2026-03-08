import os
import threading
import time
import wave
import shutil

import imagehash
import mss
import numpy as np
import sounddevice as sd
from PIL import Image

from paths import get_recordings_dir

# 配置（支持环境变量覆盖，便于不同机器部署）
CHUNK = int(os.getenv("SKIM_CHUNK", "1024"))
CHANNELS = int(os.getenv("SKIM_CHANNELS", "1"))
RATE = int(os.getenv("SKIM_RATE", "16000"))
SAMPLE_WIDTH = np.dtype(np.int16).itemsize
CHECK_INTERVAL = float(os.getenv("SKIM_CAPTURE_INTERVAL", "2"))
DIFF_THRESHOLD = int(os.getenv("SKIM_DIFF_THRESHOLD", "10"))
THREAD_RETRY_BACKOFF = float(os.getenv("SKIM_THREAD_RETRY_BACKOFF", "1.0"))


def get_available_microphones():
    """返回系统中所有可用的麦克风设备列表，每个元素包含 name 和 index。"""
    devices = sd.query_devices()
    result = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            result.append({"index": i, "name": d["name"]})
    return result


def purge_segment_media(seg_dir):
    """清理单个切片目录中的原始音频与截图路径索引。"""
    audio_p = os.path.join(seg_dir, "audio.wav")
    images_idx = os.path.join(seg_dir, "images.txt")
    img_paths = []
    if os.path.exists(images_idx):
        try:
            with open(images_idx, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if "\t" in line:
                        _, p = line.split("\t", 1)
                        img_paths.append(p)
                    else:
                        img_paths.append(line)
        except Exception:
            pass

    for p in [audio_p, images_idx] + img_paths:
        try:
            if os.path.exists(p) and os.path.isfile(p):
                os.remove(p)
        except Exception:
            continue


def cleanup_old_recordings(retention_hours, exclude_dirs=None):
    """按 TTL 清理历史原始媒体文件。"""
    if retention_hours <= 0:
        return 0
    exclude = set(os.path.abspath(p) for p in (exclude_dirs or []) if p)
    cutoff = time.time() - retention_hours * 3600
    root = get_recordings_dir()
    removed = 0

    if not os.path.exists(root):
        return removed

    for course_dir in os.listdir(root):
        base = os.path.join(root, course_dir)
        if not os.path.isdir(base):
            continue
        if os.path.abspath(base) in exclude:
            continue

        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                full_path = os.path.join(dirpath, name)
                if name == "audio.wav" or name.lower().endswith((".jpg", ".jpeg", ".png", ".wav", ".mp4", ".mov", ".m4a")):
                    try:
                        if os.path.getmtime(full_path) < cutoff:
                            os.remove(full_path)
                            removed += 1
                    except Exception:
                        continue
    return removed


def clear_raw_media(base_dir):
    """一键清除某次课程目录下的原始音频与截图文件。"""
    if not base_dir or not os.path.exists(base_dir):
        return 0

    removed = 0
    for dirpath, _, filenames in os.walk(base_dir):
        for name in filenames:
            full_path = os.path.join(dirpath, name)
            if name == "audio.wav" or name.lower().endswith((".jpg", ".jpeg", ".png", ".wav", ".mp4", ".mov", ".m4a")):
                try:
                    os.remove(full_path)
                    removed += 1
                except Exception:
                    continue

    raw_dir = os.path.join(base_dir, "raw_screenshots")
    if os.path.isdir(raw_dir):
        try:
            shutil.rmtree(raw_dir)
            os.makedirs(raw_dir, exist_ok=True)
        except Exception:
            pass

    return removed


class SegmentRecorder:
    def __init__(
        self,
        course_name,
        interval_minutes=10,
        device_index=None,
        capture_mode="standard",
        audio_enabled=True,
        screen_enabled=True,
        capture_interval=None,
        diff_threshold=None,
    ):
        self.interval = interval_minutes * 60
        self.timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.base_dir = os.path.join(get_recordings_dir(), f"{self.timestamp}_{course_name}")
        os.makedirs(self.base_dir, exist_ok=True)

        self.is_recording = False
        self.is_paused = False
        self.lock = threading.Lock()

        self.device_index = device_index
        self.capture_mode = capture_mode
        self.audio_enabled = bool(audio_enabled)
        self.screen_enabled = bool(screen_enabled)
        self.capture_interval = float(capture_interval) if capture_interval is not None else CHECK_INTERVAL
        self.diff_threshold = int(diff_threshold) if diff_threshold is not None else DIFF_THRESHOLD
        self.segment_index = 0
        self.audio_frames = []
        self.current_images = []

        self.start_time = time.time()
        self.last_segment_time = time.time()

        self.threads = {}
        self.thread_errors = {"audio": 0, "capture": 0, "segment": 0}
        self.last_heartbeat = {"audio": 0.0, "capture": 0.0, "segment": 0.0}
        self.last_error_message = ""

    def start(self):
        self.is_recording = True
        self.is_paused = False
        if self.audio_enabled:
            self.threads["audio"] = threading.Thread(target=self._record_audio, daemon=True, name="audio-thread")
        if self.screen_enabled:
            self.threads["capture"] = threading.Thread(target=self._smart_capture, daemon=True, name="capture-thread")
        if self.audio_enabled or self.screen_enabled:
            self.threads["segment"] = threading.Thread(target=self._segment_monitor, daemon=True, name="segment-thread")
        for t in self.threads.values():
            t.start()
        print(f"🚀 录制开始：{self.base_dir}")

    def stop(self):
        self.is_recording = False
        self.is_paused = False
        self._save_segment(final=True)

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def clear_raw_data(self):
        """清空当前课程会话已落盘的原始媒体，并清理内存缓冲。"""
        removed = clear_raw_media(self.base_dir)
        with self.lock:
            self.audio_frames = []
            self.current_images = []
        return removed

    def _segment_monitor(self):
        while self.is_recording:
            try:
                self.last_heartbeat["segment"] = time.time()
                time.sleep(1)
                if self.is_paused:
                    continue
                if time.time() - self.last_segment_time >= self.interval:
                    self._save_segment()
                    self.last_segment_time = time.time()
            except Exception as e:
                self.thread_errors["segment"] += 1
                self.last_error_message = f"segment monitor error: {e}"
                print(f"❌ 分段线程异常: {e}")
                time.sleep(THREAD_RETRY_BACKOFF)

    def _save_segment(self, final=False):
        with self.lock:
            if not self.audio_frames and not self.current_images:
                if final:
                    print("⚠️ 最后一段未收集到任何数据，跳过保存")
                else:
                    print("⚠️ 当前切片未收集到任何音频或图像数据，跳过保存")
                return

            seg_dir = os.path.join(self.base_dir, f"seg_{self.segment_index}")
            os.makedirs(seg_dir, exist_ok=True)

            if self.audio_frames:
                try:
                    with wave.open(os.path.join(seg_dir, "audio.wav"), "wb") as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(SAMPLE_WIDTH)
                        wf.setframerate(RATE)
                        wf.writeframes(b"".join(self.audio_frames))
                except Exception as e:
                    self.thread_errors["segment"] += 1
                    self.last_error_message = f"save audio error: {e}"
                    print(f"❌ 保存音频失败: {e}")
            else:
                print("⚠️ 本段没有录制到音频，将跳过生成 audio.wav")

            try:
                with open(os.path.join(seg_dir, "images.txt"), "w") as f:
                    for img_info in self.current_images:
                        f.write(f"{img_info['timestamp']}\t{img_info['path']}\n")
            except Exception as e:
                self.thread_errors["segment"] += 1
                self.last_error_message = f"save images index error: {e}"
                print(f"❌ 保存图片索引失败: {e}")

            with open(os.path.join(seg_dir, "ready.flag"), "w") as f:
                f.write("ok")

            print(f"📦 切片 {self.segment_index} 已打包")

            self.audio_frames = []
            self.current_images = []
            self.segment_index += 1

    def _record_audio(self):
        while self.is_recording:
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=RATE,
                    channels=CHANNELS,
                    dtype=np.int16,
                    blocksize=CHUNK,
                    device=self.device_index,
                )
                stream.start()
                while self.is_recording:
                    if self.is_paused:
                        time.sleep(0.2)
                        continue
                    self.last_heartbeat["audio"] = time.time()
                    data, overflowed = stream.read(CHUNK)
                    if overflowed:
                        self.thread_errors["audio"] += 1
                    with self.lock:
                        self.audio_frames.append(data.tobytes())
            except Exception as e:
                self.thread_errors["audio"] += 1
                self.last_error_message = f"audio thread error: {e}"
                print(f"❌ 录音线程异常: {e}")
                time.sleep(THREAD_RETRY_BACKOFF)
            finally:
                try:
                    if stream is not None:
                        stream.stop()
                        stream.close()
                except Exception:
                    pass

    def _smart_capture(self):
        last_hash = None
        raw_dir = os.path.join(self.base_dir, "raw_screenshots")
        os.makedirs(raw_dir, exist_ok=True)

        while self.is_recording:
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    while self.is_recording:
                        if self.is_paused:
                            time.sleep(0.2)
                            continue
                        self.last_heartbeat["capture"] = time.time()
                        sct_img = sct.grab(monitor)
                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                        curr_hash = imagehash.average_hash(img)

                        if last_hash is None or (curr_hash - last_hash) > self.diff_threshold:
                            capture_time = time.time() - self.start_time
                            filename = os.path.join(raw_dir, f"slide_{time.time()}.jpg")
                            img.save(filename, quality=50)
                            last_hash = curr_hash

                            with self.lock:
                                self.current_images.append({"path": filename, "timestamp": round(capture_time, 1)})

                        time.sleep(self.capture_interval)
            except Exception as e:
                self.thread_errors["capture"] += 1
                self.last_error_message = f"capture thread error: {e}"
                print(f"❌ 截图线程异常: {e}")
                time.sleep(max(self.capture_interval, THREAD_RETRY_BACKOFF))

    def get_health_status(self):
        """返回录制线程健康状态，供前端展示与告警。"""
        now = time.time()
        alive = {name: (t.is_alive() if t else False) for name, t in self.threads.items()}
        heartbeat_gap = {
            k: (round(now - v, 1) if v > 0 else None)
            for k, v in self.last_heartbeat.items()
        }
        return {
            "alive": alive,
            "thread_errors": dict(self.thread_errors),
            "heartbeat_gap_seconds": heartbeat_gap,
            "last_error_message": self.last_error_message,
        }

    def get_health_snapshot(self):
        """兼容旧调用：返回简化线程和错误信息。"""
        status = self.get_health_status()
        return {
            "threads": status.get("alive", {}),
            "errors": status.get("last_error_message", ""),
            "is_paused": self.is_paused,
        }
