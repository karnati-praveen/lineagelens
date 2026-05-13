import * as os from 'os';
import * as path from 'path';

export type LocalStorageLocation = 'globalState' | 'workspaceFile' | 'customFile';

export type ResolveLocalStoragePathInput = {
  location: LocalStorageLocation;
  customFilePath?: string;
  workspaceRoot?: string;
  defaultWorkspaceRelativePath: string;
};

export function resolveLocalStorageFilePath(
  input: ResolveLocalStoragePathInput
): string | undefined {
  if (input.location === 'globalState') {
    return undefined;
  }

  if (input.location === 'workspaceFile') {
    return input.workspaceRoot
      ? path.join(input.workspaceRoot, input.defaultWorkspaceRelativePath)
      : undefined;
  }

  return resolveCustomFilePath(input.customFilePath, input.workspaceRoot);
}

export function normalizeLocalStorageLocation(value: string | undefined): LocalStorageLocation {
  const normalized = (value ?? '').trim().toLowerCase();

  if (normalized === 'workspacefile') {
    return 'workspaceFile';
  }

  if (normalized === 'customfile') {
    return 'customFile';
  }

  return 'globalState';
}

function resolveCustomFilePath(value: string | undefined, workspaceRoot?: string): string | undefined {
  const trimmed = (value ?? '').trim();
  if (!trimmed) {
    return undefined;
  }

  const withVariables = trimmed
    .replaceAll('${workspaceFolder}', workspaceRoot ?? '')
    .replaceAll('${workspaceRoot}', workspaceRoot ?? '');

  const expanded =
    withVariables === '~' || withVariables.startsWith('~' + path.sep) || withVariables.startsWith('~/')
      ? path.join(os.homedir(), withVariables.slice(1))
      : withVariables;

  if (path.isAbsolute(expanded)) {
    return path.normalize(expanded);
  }

  return path.normalize(workspaceRoot ? path.join(workspaceRoot, expanded) : path.resolve(expanded));
}
