import * as vscode from 'vscode';
import { randomBytes, createCipheriv, createDecipheriv } from 'node:crypto';

const SECRET_KEY_NAME = 'aiInsertionDetector.localStore.encryptionKey';

// Marker prefix identifying an encrypted payload. Legacy plaintext stores are a
// JSON object/array (never starting with this prefix), so the formats are
// unambiguous on read.
export const ENC_PREFIX = 'LLENC1:';

/**
 * Resolve (or create) the AES-256 key used to encrypt the local provenance
 * store. The key lives in VS Code SecretStorage, backed by the OS keychain
 * (DPAPI on Windows, Keychain on macOS, libsecret on Linux). A stolen
 * records.json is therefore useless without keychain access.
 *
 * Returns null if the keychain is unavailable, in which case callers fall back
 * to plaintext so the extension still functions.
 */
export async function getOrCreateLocalStoreKey(
  context: vscode.ExtensionContext
): Promise<Buffer | null> {
  try {
    const existing = await context.secrets.get(SECRET_KEY_NAME);
    if (existing) {
      const buf = Buffer.from(existing, 'base64');
      if (buf.length === 32) {
        return buf;
      }
    }
    const key = randomBytes(32);
    await context.secrets.store(SECRET_KEY_NAME, key.toString('base64'));
    return key;
  } catch {
    return null;
  }
}

export function encryptString(key: Buffer, plaintext: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return ENC_PREFIX + Buffer.concat([iv, tag, ciphertext]).toString('base64');
}

export function decryptString(key: Buffer, raw: string): string {
  const payload = Buffer.from(raw.slice(ENC_PREFIX.length).trim(), 'base64');
  const iv = payload.subarray(0, 12);
  const tag = payload.subarray(12, 28);
  const ciphertext = payload.subarray(28);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf8');
}

export function isEncrypted(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith(ENC_PREFIX);
}
