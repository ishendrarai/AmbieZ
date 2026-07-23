import time
import socket
import json
import numpy as np
import cv2
import mss
from PySide6.QtCore import QThread, Signal

from color_temperature import kelvin_to_multipliers
from effects import apply_effect

BULB_PORT = 38899
CHANGE_THRESHOLD = 5.0

# ---------------------------------------------------------------------------
# COLOR SCIENCE MATH
# ---------------------------------------------------------------------------
# Precomputed LUT for fast sRGB to Linear conversion
LINEAR_LUT = np.power(np.linspace(0, 1.0, 256), 2.2).astype(np.float32)

def to_linear_lut(image_uint8: np.ndarray) -> np.ndarray:
    return LINEAR_LUT[image_uint8]

def to_srgb(linear_color: np.ndarray) -> np.ndarray:
    return np.power(np.clip(linear_color, 0.0, 1.0), 1.0 / 2.2) * 255.0

# ---------------------------------------------------------------------------
# DOMINANT COLOR
# ---------------------------------------------------------------------------
def histogram_dominant(img_uint8: np.ndarray, dark_threshold: int = 20) -> np.ndarray:
    """
    Quantise pixels into 8×8×8 colour bins and return the centre of
    the most-populated non-dark bin.
    """
    pixels = img_uint8.reshape(-1, 3).astype(np.uint16)
    brightness = pixels.sum(axis=1) // 3
    pixels = pixels[brightness > dark_threshold]
    if len(pixels) < 50:
        return np.array([0.0, 0.0, 0.0])

    r_bin = (pixels[:, 0] >> 5).astype(np.uint16)
    g_bin = (pixels[:, 1] >> 5).astype(np.uint16)
    b_bin = (pixels[:, 2] >> 5).astype(np.uint16)

    flat_idx = r_bin * 64 + g_bin * 8 + b_bin
    hist = np.bincount(flat_idx, minlength=512)

    dominant = int(np.argmax(hist))
    r = ((dominant >> 6) & 7) * 32 + 16
    g = ((dominant >> 3) & 7) * 32 + 16
    b = (dominant & 7) * 32 + 16
    
    # Return as linear float (important for smoothing pipeline)
    return LINEAR_LUT[np.array([r, g, b], dtype=np.uint8)]


# ---------------------------------------------------------------------------
# BULB AUTO-DISCOVERY
# ---------------------------------------------------------------------------
def discover_bulbs(timeout=2.0) -> list[str]:
    """Broadcasts a WiZ discovery packet and returns a list of found IPs."""
    discovery_msg = json.dumps({"method": "getSystemConfig", "params": {}}).encode()
    found_ips = set()
    
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        try:
            # Broadcast to 255.255.255.255
            sock.sendto(discovery_msg, ("255.255.255.255", BULB_PORT))
        except Exception:
            pass # Network might not support global broadcast, let's try just listening anyway or specific subnet later
            
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(1024)
                ip = addr[0]
                # WiZ bulbs return JSON. Just checking if it's valid JSON is enough here
                resp = json.loads(data.decode())
                if "result" in resp and "mac" in resp["result"]:
                    found_ips.add(ip)
            except socket.timeout:
                break
            except Exception:
                pass
                
    return list(found_ips)

# ---------------------------------------------------------------------------
# SYNC WORKER (QThread)
# ---------------------------------------------------------------------------
class SyncWorker(QThread):
    preview_signal = Signal(dict)
    log_signal = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # We hold a reference to the main AppConfig
        self.config = config
        
        self.prev_rgb = np.zeros(3, dtype=np.float64)
        self.prev_sent_rgb = np.full(3, -999.0)
        self.prev_sent_bright = -1

        # Caching black bar crop coordinates
        self.crop_rect = None
        self.frame_counter = 0

    def crop_black_bars(self, img: np.ndarray) -> np.ndarray:
        # Re-evaluate crop rect every 60 frames (approx 1-2 seconds)
        if self.frame_counter % 60 == 0 or self.crop_rect is None:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            # Hardcode dark threshold to 20 for simplicity
            _, thresh = cv2.threshold(gray, 20, 255, cv2.THRESH_BINARY)
            coords = cv2.findNonZero(thresh)
            if coords is not None:
                x, y, w, h = cv2.boundingRect(coords)
                if w > 10 and h > 10:
                    self.crop_rect = (x, y, w, h)
            else:
                self.crop_rect = None
                
        self.frame_counter += 1
        
        if self.crop_rect:
            x, y, w, h = self.crop_rect
            return img[y : y + h, x : x + w]
        return img

    def _extract_edge_weighted_linear(self, img_uint8: np.ndarray) -> np.ndarray:
        h, w, _ = img_uint8.shape
        mask = np.ones((h, w), dtype=np.float32)
        cv2.rectangle(
            mask,
            (int(w * 0.2), int(h * 0.2)),
            (int(w * 0.8), int(h * 0.8)),
            0.2,
            -1,
        )
        # Convert to linear space before spatial averaging
        lin_img = to_linear_lut(img_uint8)
        return np.average(lin_img, axis=(0, 1), weights=mask)

    def run(self):
        self.running = True
        self.log_signal.emit("Sync started")
        
        with mss.MSS() as sct:
            while self.running:
                t0 = time.perf_counter()

                mode = self.config.mode
                
                if mode == "Static Color":
                    # Bypass capture and processing
                    hex_code = self.config.static_color
                    hex_code = hex_code.lstrip('#')
                    r_c, g_c, b_c = tuple(int(hex_code[i:i+2], 16) for i in (0, 2, 4))
                    final_rgb = np.array([r_c, g_c, b_c]).astype(int)
                    
                    # Apply effects
                    base_brightness = float(self.config.brightness)
                    effect_name = self.config.effect
                    r, g, b, final_brightness = apply_effect(final_rgb[0], final_rgb[1], final_rgb[2], base_brightness, effect_name)
                    final_rgb = np.array([r, g, b]).astype(int)
                    final_brightness = int(np.clip(final_brightness, 10, 100))
                    
                    img_small = np.zeros((90, 160, 3), dtype=np.uint8)
                    img_small[:] = [r, g, b]
                    
                else:
                    # --- Capture ---
                    m_idx = self.config.monitor_idx
                    if m_idx >= len(sct.monitors):
                        m_idx = 1
                    monitor = sct.monitors[m_idx]
                    
                    try:
                        img = np.array(sct.grab(monitor))
                        img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
                    except Exception as e:
                        self.log_signal.emit(f"Capture error: {e}")
                        time.sleep(0.5)
                        continue
                        
                    img_small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
                    img_small = self.crop_black_bars(img_small)

                    # --- Color extraction ---
                    if mode == "Dominant":
                        lin_rgb = histogram_dominant(img_small, 20)
                    elif mode == "Edge Weighted":
                        lin_rgb = self._extract_edge_weighted_linear(img_small)
                    else: # Average
                        # Linearize first, then average
                        lin_img = to_linear_lut(img_small)
                        lin_rgb = np.mean(lin_img, axis=(0, 1))

                    # --- Color science pipeline ---
                    
                    # Gamma correction (applied in linear space)
                    gamma = float(self.config.gamma) / 10.0
                    if gamma != 1.0:
                        lin_rgb = np.power(np.clip(lin_rgb, 1e-9, 1.0), gamma)

                    # Saturation
                    mean_lin = np.mean(lin_rgb)
                    saturation = self.config.saturation / 10.0
                    lin_rgb = mean_lin + (lin_rgb - mean_lin) * saturation
                    np.clip(lin_rgb, 0.0, 1.0, out=lin_rgb)

                    # Colour temperature white-point adjustment
                    kelvin = float(self.config.kelvin)
                    r_k, g_k, b_k = kelvin_to_multipliers(kelvin)
                    lin_rgb = lin_rgb * np.array([r_k, g_k, b_k])
                    np.clip(lin_rgb, 0.0, 1.0, out=lin_rgb)

                    # Temporal smoothing
                    smooth = self.config.smoothness / 100.0
                    self.prev_rgb = self.prev_rgb * smooth + lin_rgb * (1.0 - smooth)

                    final_rgb = to_srgb(self.prev_rgb)
                    
                    # Dynamic Effects Application
                    base_brightness = float(self.config.brightness)
                    effect_name = self.config.effect
                    r, g, b, final_brightness = apply_effect(final_rgb[0], final_rgb[1], final_rgb[2], base_brightness, effect_name)
                    
                    final_rgb = np.array([r, g, b]).astype(int)
                    final_brightness = int(np.clip(final_brightness, 10, 100))

                # --- Frame-skip optimisation ---
                skipped = False
                color_dist = np.linalg.norm(final_rgb - self.prev_sent_rgb)
                bright_dist = abs(final_brightness - self.prev_sent_bright)
                
                # If there's an effect running, we should update more frequently 
                # (lower threshold) because brightness changes constantly
                threshold = 1.0 if effect_name != "None" else CHANGE_THRESHOLD
                
                if color_dist > threshold or bright_dist > threshold:
                    self.send_to_wiz(final_rgb, final_brightness)
                    self.prev_sent_rgb = final_rgb.copy()
                    self.prev_sent_bright = final_brightness
                else:
                    skipped = True

                elapsed = time.perf_counter() - t0
                self.preview_signal.emit(
                    {"rgb": tuple(final_rgb), "time": elapsed, "skipped": skipped}
                )

                wait = (1.0 / self.config.fps) - (time.perf_counter() - t0)
                if wait > 0:
                    time.sleep(wait)

    def send_to_wiz(self, rgb, brightness: int):
        r, g, b = (int(np.clip(v, 0, 255)) for v in rgb)
        payload = json.dumps(
            {
                "method": "setPilot",
                "params": {
                    "r": r,
                    "g": g,
                    "b": b,
                    "dimming": brightness,
                },
            }
        ).encode()
        
        for ip in self.config.bulb_ips:
            if ip:
                try:
                    self.sock.sendto(payload, (ip, BULB_PORT))
                except Exception as e:
                    self.log_signal.emit(f"UDP Error to {ip}: {e}")
