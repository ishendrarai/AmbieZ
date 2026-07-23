# AmbienZ - Technical Analysis & Recommendations Report

## 1. Executive Summary
AmbienZ is a real-time, desktop-based ambient lighting synchronization tool that captures screen content and mirrors the dominant colors to WiZ smart bulbs over a local network. It leverages a low-latency UDP protocol, making it a cloud-free, highly responsive solution. The codebase is well-structured for a single-file application, effectively utilizing `mss` for high-speed screen capture, OpenCV for image processing, and PySide6 for a modern UI. However, as the application scales, it would benefit from decoupling the UI from the processing logic, implementing more rigorous color science math (operating in linear color space), and adding auto-discovery features for WiZ bulbs.

## 2. Project Overview
- **Purpose**: To provide a real-time "Ambilight-style" experience by syncing PC monitor content to local WiZ smart bulbs.
- **How It Works**: The application runs a background `QThread` (`SyncWorker`) that continuously captures the screen at a target FPS using the `mss` library. The frames are resized and cropped to remove black bars. The color is extracted using one of three modes (Dominant Histogram, Average, or Edge-Weighted). A color science pipeline then applies gamma correction, saturation boost, and color temperature adjustments before temporally smoothing the result. If the color change exceeds a threshold, a JSON payload is dispatched over UDP to the bulb(s).
- **Current Features**: Three color extraction algorithms, adaptive smoothing, multi-bulb support, FPS slider, gamma/brightness/saturation/temperature controls, frame-skip optimization, dark UI with PySide6, system tray integration, and configuration persistence.
- **README Consistency**: The implementation perfectly matches the claims in the `README.md`. No discrepancies were found between the documented algorithms, math, UI boundaries (e.g., FPS 10-60, Gamma 0.8-2.2), or system behavior.

## 3. Current Architecture
AmbienZ utilizes a monolithic architecture where a single file (`AmbienZ.py`) contains both the frontend (PySide6 UI) and the backend (capture, processing, and networking loop). 
- **Frontend (`AmbienZUI`)**: Manages widget rendering, layout, system tray logic, config I/O, and UI state. Communicates with the backend using Qt Signals (`preview_signal`).
- **Backend (`SyncWorker`)**: A threaded capture loop that handles `mss` screen grabbing, OpenCV image manipulation, NumPy math operations, and UDP networking.
- **Module Separation**: The color temperature math (`color_temperature.py`) is appropriately separated into its own module, demonstrating a good start towards modularity.

## 4. Code Quality Assessment
### Structure, Readability, and Maintainability
- **Strengths**: Variable naming is explicit, comments clarify the pipeline, and the signal/slot mechanism safely crosses thread boundaries. Type hints are used effectively for the numpy arrays.
- **Weaknesses**: The monolithic file structure (645 lines) reduces maintainability. Configuration saving and loading logic is hardcoded directly into the UI class rather than a standalone `ConfigManager`. UI styling is baked into a massive Python string (`_get_theme`).

### Bugs, Code Smells, and Edge Cases
- **Silenced UDP Exceptions**: In `send_to_wiz`, `self.sock.sendto` catches all exceptions and passes silently (`except Exception: pass`). This masks network errors from the user.
- **Incorrect Color Math Order**: The application currently averages colors in sRGB space (e.g., `np.mean(img_small)`), and *then* converts the resulting single color to linear space. Averaging in sRGB mathematically produces "muddy" or overly dark colors when contrasting colors are on screen. The image should be converted to linear RGB *before* spatial averaging.
- **UI-Worker Coupling**: The UI directly injects data into the `self.worker.params` dictionary. This bypasses thread-safety guarantees.

## 5. Performance Analysis
- **Screen Capture**: `mss` is the fastest cross-platform capture method for Python. This is an excellent choice.
- **Image Resizing**: Resizing to 160x90 via OpenCV is fast, but doing it on the CPU is a slight bottleneck compared to GPU resizing (though negligible for a background app).
- **Black Bar Cropping**: The `crop_black_bars` function runs `cv2.cvtColor`, `cv2.threshold`, and `cv2.findNonZero` on *every single frame*. This is highly wasteful since letterboxing rarely changes during a video or game.
- **Frame-Skip**: The distance check (`np.linalg.norm(final_rgb - self.prev_sent_rgb) > CHANGE_THRESHOLD`) successfully limits UDP spam, preventing network congestion.

## 6. Security & Safety Review
- **Local Execution**: The script operates entirely on the local network (LAN) over UDP, requiring no cloud authentication or inbound port forwarding. This is highly secure.
- **Unvalidated Config**: The `ambienz_config.json` loading blindly accepts values. If a user manually edits the JSON and sets `fps` to a string or `monitor_idx` out of bounds, the app could crash on boot.

## 7. Optimization Recommendations

### Recommendation 1: Cache Black Bar Bounds (Performance)
Since aspect ratios rarely change, cache the crop rectangle and re-evaluate it periodically (e.g., every 60 frames) instead of every frame.
```python
# In SyncWorker.__init__
self.crop_rect = None
self.frame_counter = 0

def crop_black_bars(self, img: np.ndarray) -> np.ndarray:
    if self.frame_counter % 60 == 0 or self.crop_rect is None:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        _, thresh = cv2.threshold(gray, self.params["dark_threshold"], 255, cv2.THRESH_BINARY)
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
```

### Recommendation 2: Correct Color Averaging with LUTs (Quality & Performance)
To fix muddy colors without introducing massive CPU overhead from `np.power`, create a global Look-Up Table (LUT) to convert the 160x90 `uint8` image to linear space *before* averaging.
```python
# Define once globally
LINEAR_LUT = np.power(np.linspace(0, 1.0, 256), 2.2).astype(np.float32)

def to_linear_lut(image_uint8: np.ndarray) -> np.ndarray:
    return LINEAR_LUT[image_uint8]

# Usage in run() for Average mode:
lin_img = to_linear_lut(img_small)
rgb_linear = np.mean(lin_img, axis=(0, 1))
# (Skip the existing to_linear call later down the pipeline)
```

### Recommendation 3: Decouple Configuration (Architecture)
Move configuration logic out of the UI class into a dedicated Data Class or Pydantic model to handle validation and serialization safely.

## 8. Feature Roadmap

| Feature | Priority | Value / Benefit | Implementation Complexity |
| :--- | :---: | :--- | :--- |
| **Auto-Discovery (mDNS / Broadcast UDP)** | **High** | Eliminates the need for users to manually hunt for IP addresses in router settings. | Medium (Broadcast UDP packet on port 38899, listen for replies). |
| **Correct Linear Color Math** | **High** | Vastly improves color accuracy and brightness when scenes have mixed colors. | Low (Implement the LUT mentioned above). |
| **Multi-Zone / Screen Region Support** | **Medium** | True Ambilight experience; assign Left bulb to Left screen edge, Right bulb to Right edge. | High (Requires UI overhaul to map IPs to screen regions, and multiple region extractions). |
| **Scene / Profile Presets** | **Medium** | Users can switch between "Movie" (slow smoothing) and "Gaming" (fast response). | Medium (Store profiles in JSON, populate a UI dropdown). |
| **Audio Reactive Mode** | **Low** | Adds a party/music mode that pulses lights to desktop audio. | High (Requires `pyaudio` or `soundcard` loopback capture, FFT processing). |
| **WLED / Govee Support** | **Low** | Expands market reach beyond WiZ ecosystem. | High (Different network protocols). |

## 9. Action Plan

### Quick Wins (≤1 hour)
- [ ] Implement the global `LINEAR_LUT` for fast sRGB-to-Linear conversion.
- [ ] Apply linear conversion *before* spatial averaging.
- [ ] Add exception logging in `send_to_wiz` (e.g., `print(f"UDP Error: {e}")`) instead of silent pass.

### Short-term Improvements (1–2 days)
- [ ] Implement caching for `crop_black_bars` (re-evaluate every 60 frames).
- [ ] Extract UI styling into an external `style.qss` file.
- [ ] Refactor configuration into a standalone `ConfigManager` class with basic input validation (e.g., ensuring IPs are valid IPv4 strings).

### Medium-term Enhancements (1–2 weeks)
- [ ] Implement UDP broadcast auto-discovery for WiZ bulbs. Add a "Scan for Bulbs" button to the UI.
- [ ] Decouple `SyncWorker` from `AmbienZ.py` into its own `engine.py` module.

### Long-term Features (1+ month)
- [ ] Implement Multi-Zone support: Allow users to draw/select screen regions and assign specific WiZ IPs to those regions.
- [ ] Build Audio Reactive Mode using fast Fourier transforms (FFT) on system audio loopback.

---

## 10. Top 10 Highest-Impact Improvements

Ranked by the best ratio of **Expected Benefit** to **Implementation Effort**:

1. **Cache Black Bar Detection** (Effort: Very Low, Benefit: High) - Huge reduction in CPU usage per frame.
2. **Pre-Linearize Colors with LUT** (Effort: Low, Benefit: High) - Fixes muddy colors in Average mode; physically accurate math.
3. **Log Network Errors** (Effort: Very Low, Benefit: Medium) - Crucial for troubleshooting dropped connections instead of failing silently.
4. **Auto-Discovery of Bulbs** (Effort: Medium, Benefit: Very High) - The biggest usability hurdle (finding IPs) is eliminated.
5. **Decouple Config Validation** (Effort: Low, Benefit: Medium) - Prevents app crashes from malformed JSON files.
6. **Separate QSS Styling** (Effort: Low, Benefit: Low) - Cleans up the main file significantly, making UI code readable.
7. **Thread-Safe Param Injection** (Effort: Medium, Benefit: Medium) - Prevents race conditions by using proper Qt Signals or Mutexes instead of dict injection.
8. **Multi-Zone Region Mapping** (Effort: High, Benefit: Very High) - Transforms the app from a "room light matcher" to a true immersive Ambilight system.
9. **Profile Presets (Gaming/Movie)** (Effort: Medium, Benefit: Medium) - Quality of life feature for rapid context switching.
10. **Audio Reactive Mode** (Effort: High, Benefit: High) - Highly requested feature in this space, adds a completely new dimension to the app.
