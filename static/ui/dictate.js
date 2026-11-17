// Mic dictation for a chat box: local /api/transcribe when a whisper engine
// is installed, otherwise the browser's SpeechRecognition. Same two paths
// the old editor chat used.

import { api, toast } from "./dom.js";

const Dictation = window.SpeechRecognition || window.webkitSpeechRecognition;
const LIVE_MS = 500;

function joinText(base, text) {
  return base && !/\s$/.test(base) ? `${base} ${text}` : base + text;
}

function mergePcm(chunks) {
  let length = 0;
  for (const chunk of chunks) length += chunk.length;
  const merged = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; }
  return merged;
}

function downsample(samples, from, to) {
  if (to >= from) return samples;
  const ratio = from / to;
  const length = Math.floor(samples.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.floor((i + 1) * ratio);
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += samples[j];
    out[i] = sum / (end - start);
  }
  return out;
}

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

function bytesToBase64(bytes) {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

// Wire a mic button to a textarea. Returns { cancel, ready } so send can stop
// listening and the caller can hide the mic when neither path works.
export function attachDictate({ button, input }) {
  let listener = null;
  let recorder = null;
  let localReady = false;

  function setListening(on) {
    button.classList.toggle("is-listening", on);
  }

  async function transcribeChunk(rec) {
    if (!rec.chunks.length) return;
    const samples = mergePcm(rec.chunks);
    const wav = encodeWav(downsample(samples, rec.rate, 16000), 16000);
    const data = await api("/api/transcribe", { audio: bytesToBase64(wav) });
    const text = String(data.text || "").trim();
    if (rec.cancelled) return;
    input.value = text ? joinText(rec.base, text) : rec.base;
  }

  async function flushRecording() {
    const rec = recorder;
    if (!rec || rec.busy || !rec.chunks.length) return;
    rec.busy = true;
    try { await transcribeChunk(rec); }
    catch (error) { toast(`Could not transcribe: ${error.message}`, "error"); }
    finally { rec.busy = false; }
  }

  async function stopRecording() {
    const rec = recorder;
    recorder = null;
    setListening(false);
    if (!rec) return;
    clearInterval(rec.timer);
    try {
      if (!rec.cancelled) await transcribeChunk(rec);
    } catch (error) {
      toast(`Could not transcribe: ${error.message}`, "error");
    }
    rec.node.disconnect();
    rec.source.disconnect();
    rec.stream.getTracks().forEach((track) => track.stop());
    await rec.ctx.close();
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const ctx = new AudioContext();
      const source = ctx.createMediaStreamSource(stream);
      const node = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      node.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      source.connect(node);
      node.connect(ctx.destination);
      recorder = {
        stream, ctx, source, node, chunks, rate: ctx.sampleRate,
        base: input.value, timer: null, busy: false, cancelled: false,
      };
      recorder.timer = setInterval(flushRecording, LIVE_MS);
      setListening(true);
      toast("Listening… click the mic again to stop.");
    } catch (error) {
      toast(`Microphone error: ${error.message}`, "error");
    }
  }

  function startBrowser() {
    if (!Dictation) return;
    const speech = new Dictation();
    speech.lang = navigator.language || "en-US";
    speech.continuous = true;
    speech.onresult = (event) => {
      let heard = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) heard += event.results[i][0].transcript;
      }
      if (listener !== speech) return;
      if (heard.trim()) {
        input.value += input.value && !/\s$/.test(input.value) ? ` ${heard.trim()}` : heard.trim();
        input.focus();
      }
    };
    speech.onerror = (event) => {
      const why = event.error === "not-allowed" ? "microphone access was refused" : event.error;
      toast(`Dictation stopped: ${why}`, "error");
    };
    speech.onend = () => { listener = null; setListening(false); };
    speech.start();
    listener = speech;
    setListening(true);
    input.focus();
  }

  function cancel() {
    if (listener) { listener.stop(); listener = null; }
    if (recorder) { recorder.cancelled = true; stopRecording(); }
    setListening(false);
  }

  function toggle() {
    if (localReady) { recorder ? stopRecording() : startRecording(); return; }
    if (listener) { listener.stop(); return; }
    startBrowser();
  }

  button.addEventListener("click", toggle);
  button.hidden = true;
  fetch("/api/transcribe").then((r) => r.json())
    .then((d) => { localReady = Boolean(d.available); })
    .catch(() => { localReady = false; })
    .finally(() => { button.hidden = !(localReady || Dictation); });

  return { cancel };
}
