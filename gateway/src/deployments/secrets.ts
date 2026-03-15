/**
 * Deployment Secrets — encryption at rest for environment secrets.
 *
 * Secrets are stored in ~/.bond/deployments/secrets/{env}.yaml
 * Encrypted files use AES-256-GCM with a key derived from ~/.bond/data/.vault_key
 * Magic header: BOND_ENC_V1: followed by base64(iv + ciphertext + authTag)
 *
 * Design Doc 039 §8.4 — Environment Secrets
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { homedir } from "node:os";

const MAGIC_HEADER = "BOND_ENC_V1:";
const ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12;
const AUTH_TAG_LENGTH = 16;

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");
const DEFAULT_VAULT_KEY_PATH = path.join(homedir(), ".bond", "data", ".vault_key");

/**
 * Derive a 256-bit encryption key from the vault key file.
 */
function deriveKey(vaultKeyPath: string): Buffer {
  const raw = fs.readFileSync(vaultKeyPath);
  return crypto.createHash("sha256").update(raw).digest();
}

/**
 * Parse simple YAML key: value format into a flat record.
 */
function parseSimpleYaml(content: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of content.split("\n")) {
    const match = line.match(/^([A-Z_][A-Z0-9_]*):\s*"?([^"#\n]*)"?\s*$/);
    if (match) {
      result[match[1]!] = match[2]!.trim();
    }
  }
  return result;
}

/**
 * Serialize a flat record into simple YAML format.
 */
function serializeSimpleYaml(secrets: Record<string, string>): string {
  return Object.entries(secrets)
    .map(([k, v]) => `${k}: "${v}"`)
    .join("\n") + "\n";
}

/**
 * Encrypt plaintext using AES-256-GCM.
 * Returns: BOND_ENC_V1: + base64(iv + ciphertext + authTag)
 */
function encrypt(plaintext: string, key: Buffer): string {
  const iv = crypto.randomBytes(IV_LENGTH);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);
  const encrypted = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const authTag = cipher.getAuthTag();
  const combined = Buffer.concat([iv, encrypted, authTag]);
  return MAGIC_HEADER + combined.toString("base64");
}

/**
 * Decrypt a BOND_ENC_V1 encrypted string.
 */
function decrypt(encryptedContent: string, key: Buffer): string {
  const b64 = encryptedContent.slice(MAGIC_HEADER.length);
  const combined = Buffer.from(b64, "base64");
  const iv = combined.subarray(0, IV_LENGTH);
  const authTag = combined.subarray(combined.length - AUTH_TAG_LENGTH);
  const ciphertext = combined.subarray(IV_LENGTH, combined.length - AUTH_TAG_LENGTH);
  const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(authTag);
  return decipher.update(ciphertext) + decipher.final("utf8");
}

/**
 * Load secrets for an environment. Handles both plaintext and encrypted files.
 * Returns a flat Record<string, string> of env vars.
 */
export function loadSecrets(env: string, deploymentsDir = DEPLOYMENTS_DIR, vaultKeyPath = DEFAULT_VAULT_KEY_PATH): Record<string, string> {
  const secretsPath = path.join(deploymentsDir, "secrets", `${env}.yaml`);
  if (!fs.existsSync(secretsPath)) return {};

  try {
    let content = fs.readFileSync(secretsPath, "utf8");

    // If encrypted, decrypt first
    if (content.startsWith(MAGIC_HEADER)) {
      if (!fs.existsSync(vaultKeyPath)) {
        console.error(`[secrets] Vault key not found at ${vaultKeyPath} — cannot decrypt secrets for ${env}`);
        return {};
      }
      const key = deriveKey(vaultKeyPath);
      content = decrypt(content, key);
    }

    return parseSimpleYaml(content);
  } catch (err: any) {
    console.error(`[secrets] Failed to load secrets for ${env}:`, err.message);
    return {};
  }
}

/**
 * Encrypt and write secrets for an environment.
 */
export function encryptSecrets(
  env: string,
  secrets: Record<string, string>,
  deploymentsDir = DEPLOYMENTS_DIR,
  vaultKeyPath = DEFAULT_VAULT_KEY_PATH,
): void {
  if (!fs.existsSync(vaultKeyPath)) {
    throw new Error(`Vault key not found at ${vaultKeyPath}`);
  }

  const key = deriveKey(vaultKeyPath);
  const yaml = serializeSimpleYaml(secrets);
  const encrypted = encrypt(yaml, key);

  const secretsDir = path.join(deploymentsDir, "secrets");
  fs.mkdirSync(secretsDir, { recursive: true });
  fs.writeFileSync(path.join(secretsDir, `${env}.yaml`), encrypted, { mode: 0o600 });
}

/**
 * Re-encrypt all secrets files with current vault key.
 * Useful after key rotation.
 */
export function rotateSecretsEncryption(
  deploymentsDir = DEPLOYMENTS_DIR,
  vaultKeyPath = DEFAULT_VAULT_KEY_PATH,
): { rotated: string[]; errors: string[] } {
  const secretsDir = path.join(deploymentsDir, "secrets");
  if (!fs.existsSync(secretsDir)) return { rotated: [], errors: [] };
  if (!fs.existsSync(vaultKeyPath)) {
    throw new Error(`Vault key not found at ${vaultKeyPath}`);
  }

  const key = deriveKey(vaultKeyPath);
  const rotated: string[] = [];
  const errors: string[] = [];

  for (const file of fs.readdirSync(secretsDir)) {
    if (!file.endsWith(".yaml")) continue;
    const env = file.replace(".yaml", "");
    try {
      const content = fs.readFileSync(path.join(secretsDir, file), "utf8");
      let plaintext: string;
      if (content.startsWith(MAGIC_HEADER)) {
        plaintext = decrypt(content, key);
      } else {
        plaintext = content;
      }
      const encrypted = encrypt(plaintext, key);
      fs.writeFileSync(path.join(secretsDir, file), encrypted, { mode: 0o600 });
      rotated.push(env);
    } catch (err: any) {
      errors.push(`${env}: ${err.message}`);
    }
  }

  return { rotated, errors };
}
