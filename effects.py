import time
import math
import random

def apply_effect(r: float, g: float, b: float, brightness: float, effect_name: str):
    """
    Applies mathematical modulation to RGB and brightness for dynamic effects.
    Returns (r, g, b, adjusted_brightness)
    """
    if effect_name == "None" or not effect_name:
        return r, g, b, brightness
        
    t = time.perf_counter()
    
    if effect_name == "Candle Flicker":
        # Flicker adds random high frequency noise combined with a slow drift
        noise = random.uniform(0.7, 1.0)
        drift = (math.sin(t * 3.0) + 1.0) / 2.0 * 0.15 + 0.85
        mod = noise * drift
        return r * mod, g * mod, b * mod, brightness * mod

    elif effect_name == "Pulse":
        # Fast sine wave modulation
        mod = (math.sin(t * 8.0) + 1.0) / 2.0 * 0.8 + 0.2
        return r, g, b, brightness * mod

    elif effect_name == "Breathe":
        # Slow, smooth sine wave interpolation
        mod = (math.sin(t * 2.0) + 1.0) / 2.0 * 0.7 + 0.3
        return r, g, b, brightness * mod

    elif effect_name == "Emergency White Flicker":
        # Harsh strobe of pure white light. 
        # WiZ bulbs drop packets if sent too fast, so we use a 4Hz cycle (2 flashes per sec).
        is_on = (t * 4.0) % 1.0 > 0.5
        
        if is_on:
            return 255.0, 255.0, 255.0, 100.0
        else:
            # Send black (0,0,0) so the bulb actually turns completely dark
            return 0.0, 0.0, 0.0, 10.0
        
    return r, g, b, brightness
