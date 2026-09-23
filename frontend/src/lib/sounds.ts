let audioCtx: AudioContext | null = null;

function getCtx(): AudioContext {
  if (!audioCtx) audioCtx = new AudioContext();
  return audioCtx;
}

function playTone(freq: number, durationMs: number, type: OscillatorType = 'sine', volume = 0.15) {
  try {
    const ctx = getCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.value = volume;
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + durationMs / 1000);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + durationMs / 1000);
  } catch {}
}

/** Short pleasant beep for order fills */
export function playOrderFill() {
  playTone(440, 100, 'sine', 0.12);
}

/** Two-tone alert for SL/TP triggered */
export function playSlTpTriggered() {
  playTone(660, 120, 'sine', 0.15);
  setTimeout(() => playTone(880, 150, 'sine', 0.15), 130);
}

/** Notification chime for leader trades */
export function playLeaderTrade() {
  playTone(523, 200, 'triangle', 0.12);
}
