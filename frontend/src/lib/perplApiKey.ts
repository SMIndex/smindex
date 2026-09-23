import * as ed from '@noble/ed25519';
import { hashTypedData, hexToBytes } from 'viem';
import api from '@/lib/api';

// Perpl Ed25519 API-key support (one-click trading).
// The secret key is generated here and NEVER leaves the browser — enrollment
// and key management calls are relayed through our backend (which strips no
// signatures and holds no secrets), trading goes over the WS proxy.

const CHAIN_ID = 143;

export interface StoredApiKey {
  apiKey: string;      // opaque X-API-Key token from Perpl
  privHex: string;     // Ed25519 secret key hex (browser-only)
  pubHex: string;      // 0x-prefixed public key
  address: string;     // wallet it was enrolled for
  label: string;
  scopeMask: number;
  createdAt: number;
}

const storageKey = (address: string) => `perpl_apikey_${address.toLowerCase()}`;

export function getStoredApiKey(address?: string | null): StoredApiKey | null {
  if (!address) return null;
  try {
    const raw = localStorage.getItem(storageKey(address));
    if (!raw) return null;
    const k = JSON.parse(raw) as StoredApiKey;
    // Migrate records written before the nested-ApiKeyEnrollResponse fix:
    // apiKey was stored as the whole ApiKeyInfo object, not the token string.
    const nested = k?.apiKey as unknown as { api_key?: string; label?: string; scope_mask?: number; created_at?: number };
    if (nested && typeof nested === 'object' && typeof nested.api_key === 'string') {
      k.apiKey = nested.api_key;
      if (nested.label) k.label = nested.label;
      if (nested.scope_mask) k.scopeMask = nested.scope_mask;
      if (nested.created_at) k.createdAt = nested.created_at;
      saveApiKey(k);
    }
    return typeof k?.apiKey === 'string' && k.apiKey && k?.privHex ? k : null;
  } catch {
    return null;
  }
}

export function clearStoredApiKey(address: string): void {
  try { localStorage.removeItem(storageKey(address)); } catch {}
}

function saveApiKey(k: StoredApiKey): void {
  localStorage.setItem(storageKey(k.address), JSON.stringify(k));
}

// ---------------------------------------------------------------------------
// Encoding helpers
// ---------------------------------------------------------------------------

const b64url = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

const bytesToHex = (b: Uint8Array): string =>
  Array.from(b).map((x) => x.toString(16).padStart(2, '0')).join('');

const hexToU8 = (hex: string): Uint8Array => hexToBytes((hex.startsWith('0x') ? hex : `0x${hex}`) as `0x${string}`);

const utf8 = (s: string): Uint8Array => new TextEncoder().encode(s);

function randomNonce(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return b64url(b);
}

async function sha256Hex(body: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', utf8(body) as BufferSource);
  return bytesToHex(new Uint8Array(digest));
}

// Clock-skew correction: Perpl requires X-API-Timestamp within ±30s of server
// time. Sync once per session against our backend clock.
let _clockOffsetMs = 0;
let _clockSynced = false;

export async function syncClock(): Promise<void> {
  if (_clockSynced) return;
  try {
    const t0 = Date.now();
    const resp = await api.get('/api/auth/server-time');
    const t1 = Date.now();
    const serverNow = resp.data?.now_ms;
    if (typeof serverNow === 'number') {
      _clockOffsetMs = serverNow - Math.round((t0 + t1) / 2);
      _clockSynced = true;
    }
  } catch {}
}

const nowMs = (): number => Date.now() + _clockOffsetMs;

// ---------------------------------------------------------------------------
// Enrollment
// ---------------------------------------------------------------------------

export interface EnrollParams {
  address: string;
  scopeMask?: number;   // 1 read / 2 trade / 3 both — default 3
  label?: string;
  // wagmi's signTypedDataAsync — the one wallet popup in the whole flow
  signTypedDataAsync: (args: any) => Promise<string>;
}

/**
 * Full enrollment: generate keypair → payload → wallet EIP-712 signature +
 * Ed25519 proof-of-possession → enroll → store locally.
 */
export async function enrollApiKey(params: EnrollParams): Promise<StoredApiKey> {
  const priv = ed.utils.randomSecretKey();
  const pub = await ed.getPublicKeyAsync(priv);
  const pubHex = `0x${bytesToHex(pub)}`;
  const label = params.label || 'SMINDEX';
  const scopeMask = params.scopeMask ?? 3;

  const payloadResp = await api.post('/api/auth/perpl-apikey-payload', {
    public_key: pubHex,
    scope_mask: scopeMask,
    label,
  });
  const { typed_data, mac } = payloadResp.data;

  // Wallet signature (secp256k1, EIP-712). viem/wagmi tolerate the
  // EIP712Domain entry but we strip it to be explicit.
  const { EIP712Domain: _drop, ...types } = typed_data.types;
  const domain = { ...typed_data.domain };
  if (typeof domain.chainId === 'string') domain.chainId = Number(BigInt(domain.chainId));

  const signature = await params.signTypedDataAsync({
    domain,
    types,
    primaryType: typed_data.primaryType,
    message: typed_data.message,
  });

  // Ed25519 proof-of-possession over the EIP-712 digest
  const digest = hashTypedData({
    domain,
    types,
    primaryType: typed_data.primaryType,
    message: typed_data.message,
  });
  const popSig = await ed.signAsync(hexToU8(digest), priv);

  const enrollResp = await api.post('/api/auth/perpl-apikey-enroll', {
    typed_data,
    mac,
    signature,
    pop_signature: `0x${bytesToHex(popSig)}`,
  });

  // ApiKeyEnrollResponse nests the record: { api_key: ApiKeyInfo } and the
  // token itself is ApiKeyInfo.api_key. Accept a flat shape too, defensively.
  const raw = enrollResp.data;
  const info = raw && typeof raw.api_key === 'object' ? raw.api_key : raw;
  if (typeof info?.api_key !== 'string' || !info.api_key) {
    throw new Error('Perpl enroll response did not contain an API-key token');
  }
  const stored: StoredApiKey = {
    apiKey: info.api_key,
    privHex: bytesToHex(priv),
    pubHex,
    address: params.address.toLowerCase(),
    label: info.label ?? label,
    scopeMask: info.scope_mask ?? scopeMask,
    createdAt: info.created_at ?? Date.now(),
  };
  saveApiKey(stored);
  return stored;
}

/** Manual fallback: user created a key at app.perpl.xyz/apikeys and pastes
 * the token + secret here. We derive/verify the public key from the secret. */
export async function importApiKey(address: string, apiKey: string, privHexInput: string): Promise<StoredApiKey> {
  const privHex = privHexInput.trim().replace(/^0x/, '');
  if (!/^[0-9a-fA-F]{64}$/.test(privHex)) throw new Error('Secret key must be 32 bytes of hex');
  const pub = await ed.getPublicKeyAsync(hexToU8(privHex));
  const stored: StoredApiKey = {
    apiKey: apiKey.trim(),
    privHex,
    pubHex: `0x${bytesToHex(pub)}`,
    address: address.toLowerCase(),
    label: 'Imported key',
    scopeMask: 3,
    createdAt: Date.now(),
  };
  saveApiKey(stored);
  return stored;
}

// ---------------------------------------------------------------------------
// Signing — WS sign-in frame and REST canonical
// ---------------------------------------------------------------------------

/** Build the mt:29 ApiKeySignIn frame (first message on the trading WS). */
export async function buildWsSignInFrame(key: StoredApiKey): Promise<Record<string, unknown>> {
  await syncClock();
  const timestamp = String(nowMs());
  const nonce = randomNonce();
  const canonical = [CHAIN_ID, 'trading-ws-signin', timestamp, nonce].join('\n');
  const sig = await ed.signAsync(utf8(canonical), hexToU8(key.privHex));
  return {
    mt: 29,
    chain_id: CHAIN_ID,
    api_key: key.apiKey,
    timestamp,
    nonce,
    signature: b64url(sig),
  };
}

/** Sign a REST request per the Perpl canonical scheme. `target` is the path
 * Perpl sees after /api (e.g. /v1/api-key/list). */
export async function signRestRequest(
  key: StoredApiKey,
  method: string,
  target: string,
  body = '',
): Promise<{ method: string; target: string; body: string; api_key: string; timestamp: string; nonce: string; signature: string }> {
  await syncClock();
  const timestamp = String(nowMs());
  const nonce = randomNonce();
  const bodyHash = await sha256Hex(body);
  const canonical = [CHAIN_ID, method.toUpperCase(), target, timestamp, nonce, bodyHash].join('\n');
  const sig = await ed.signAsync(utf8(canonical), hexToU8(key.privHex));
  return {
    method: method.toUpperCase(),
    target,
    body,
    api_key: key.apiKey,
    timestamp,
    nonce,
    signature: b64url(sig),
  };
}

// ---------------------------------------------------------------------------
// Key management (list / revoke) — relayed through our backend
// ---------------------------------------------------------------------------

export async function listApiKeys(key: StoredApiKey): Promise<any> {
  const signed = await signRestRequest(key, 'GET', '/v1/api-key/list');
  const resp = await api.post('/api/auth/perpl-apikey-proxy', signed);
  return resp.data?.data;
}

/**
 * Revoke the key ON PERPL (deleted server-side, not just forgotten locally),
 * then clear local storage. Perpl's docs route revocation through their web
 * UI; we call the same endpoint it uses (/v1/api-key/delete) — our backend
 * falls back to the stored Perpl web session if the signed call is refused.
 */
export async function revokeApiKey(key: StoredApiKey): Promise<void> {
  const body = JSON.stringify({ api_key: key.apiKey, public_key: key.pubHex });
  const signed = await signRestRequest(key, 'POST', '/v1/api-key/delete', body);
  await api.post('/api/auth/perpl-apikey-proxy', signed);
  clearStoredApiKey(key.address);
}
