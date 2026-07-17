// API keys live ONLY in this browser (localStorage) — they are auto-filled into
// the extract form for convenience but are NEVER stored on our servers (they still
// transit per-request to the selected provider, exactly as a typed key would).
export const PROVIDERS = ["openai", "google", "anthropic", "deepseek", "mistral"];

export function getKey(provider) {
  return localStorage.getItem(`metalens_key_${provider}`) || "";
}
export function setKey(provider, value) {
  if (value) localStorage.setItem(`metalens_key_${provider}`, value);
  else localStorage.removeItem(`metalens_key_${provider}`);
}
