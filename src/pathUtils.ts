import * as path from 'path';

export function toPortableStoragePath(filePath: string, workspaceRoot?: string): string {
  const normalizedFilePath = filePath.trim();
  if (!normalizedFilePath) {
    return '';
  }

  if (workspaceRoot) {
    const relativePath = path.relative(workspaceRoot, normalizedFilePath);
    if (relativePath && !relativePath.startsWith('..') && !path.isAbsolute(relativePath)) {
      return toPortablePath(relativePath);
    }
  }

  return toPortablePath(normalizedFilePath);
}

export function pathsReferToSameFile(leftPath: string, rightPath: string): boolean {
  const left = normalizeComparablePath(leftPath);
  const right = normalizeComparablePath(rightPath);

  if (!left || !right) {
    return false;
  }

  if (left === right) {
    return true;
  }

  const leftAbsolute = path.isAbsolute(left);
  const rightAbsolute = path.isAbsolute(right);

  if (leftAbsolute !== rightAbsolute) {
    const absolutePath = leftAbsolute ? left : right;
    const relativePath = leftAbsolute ? right : left;
    return absolutePath.endsWith(path.sep + relativePath);
  }

  return false;
}

function toPortablePath(value: string): string {
  return value.replace(/\\/g, '/');
}

function normalizeComparablePath(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return '';
  }

  return path.normalize(trimmed.replace(/\//g, path.sep)).toLowerCase();
}
