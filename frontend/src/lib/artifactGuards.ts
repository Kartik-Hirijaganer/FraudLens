/**
 * Summary: Small fail-closed runtime guards shared by committed research-artifact parsers.
 *
 * Key classes:
 * - ArtifactError: identifies a malformed or drifted aggregate research artifact.
 *
 * Key functions:
 * - recordOf: require a plain object.
 * - exactKeys: reject missing and unrecognized fields.
 * - stringOf: require a non-empty string.
 * - numberOf: require a finite number.
 * - integerOf: require an integer.
 * - booleanOf: require a boolean.
 * - arrayOf: require an array.
 * - nullableNumberOf: require a finite number or null.
 *
 * Notes:
 * - Browser projections contain aggregate synthetic evidence only.
 */
export class ArtifactError extends Error {}

export function recordOf(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ArtifactError(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

export function exactKeys(raw: Record<string, unknown>, expected: string[], path: string): void {
  const actual = Object.keys(raw).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new ArtifactError(`${path} fields do not match the published contract`);
  }
}

export function stringOf(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new ArtifactError(`${path} must be a non-empty string`);
  }
  return value;
}

export function numberOf(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ArtifactError(`${path} must be a finite number`);
  }
  return value;
}

export function integerOf(value: unknown, path: string): number {
  const parsed = numberOf(value, path);
  if (!Number.isInteger(parsed)) {
    throw new ArtifactError(`${path} must be an integer`);
  }
  return parsed;
}

export function booleanOf(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw new ArtifactError(`${path} must be a boolean`);
  }
  return value;
}

export function arrayOf(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new ArtifactError(`${path} must be an array`);
  }
  return value;
}

export function nullableNumberOf(value: unknown, path: string): number | null {
  return value === null ? null : numberOf(value, path);
}
