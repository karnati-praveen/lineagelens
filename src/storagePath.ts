import * as vscode from 'vscode';
import { toPortableStoragePath } from './pathUtils';

export function getStoragePathForUri(resource: vscode.Uri): string {
  if (resource.scheme !== 'file') {
    return resource.toString();
  }

  const workspaceFolder = vscode.workspace.getWorkspaceFolder(resource);
  return toPortableStoragePath(resource.fsPath, workspaceFolder?.uri.fsPath);
}
