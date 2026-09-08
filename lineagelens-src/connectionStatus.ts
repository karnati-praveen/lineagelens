import * as vscode from 'vscode';
import { FetchLike, probeServerHealth, normalizeServerUrl, disconnectFromServer } from './connectWizard';
import { SECRET_BACKEND_API_KEY, SECRET_BACKEND_WORKSPACE_ID } from './backendAuth';

export class ConnectionStatusManager implements vscode.Disposable {
  private statusBarItem: vscode.StatusBarItem;
  private pollInterval?: NodeJS.Timeout;
  private isDisposed = false;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly fetchImpl?: FetchLike
  ) {
    this.statusBarItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      98
    );
    this.statusBarItem.command = 'lineagelens.showConnectionMenu';
    this.context.subscriptions.push(this.statusBarItem);

    // Register commands
    this.context.subscriptions.push(
      vscode.commands.registerCommand('lineagelens.refreshConnectionStatus', () => this.updateStatus()),
      vscode.commands.registerCommand('lineagelens.showConnectionMenu', () => this.showConnectionMenu())
    );

    this.updateStatus().catch(() => {});
    this.startPolling();
  }

  public async updateStatus(): Promise<void> {
    if (this.isDisposed) {
      return;
    }

    const mode = vscode.workspace.getConfiguration('aiCodeProvenance').get<string>('mode', 'local');
    if (mode !== 'backend') {
      this.statusBarItem.text = '$(database) LL: local';
      this.statusBarItem.tooltip = 'LineageLens: Running in local storage mode. Click to connect to a server.';
      this.statusBarItem.show();
      return;
    }

    const rawUrl = vscode.workspace.getConfiguration('aiInsertionDetector.backend').get<string>('baseUrl', '');
    if (!rawUrl.trim()) {
      this.statusBarItem.text = '$(cloud-offline) LL ○ unconfigured';
      this.statusBarItem.tooltip = 'LineageLens: Backend mode selected but server URL is empty. Click to configure.';
      this.statusBarItem.show();
      return;
    }

    const serverUrl = normalizeServerUrl(rawUrl);
    const apiKey = await this.context.secrets.get(SECRET_BACKEND_API_KEY);
    const workspaceId = (await this.context.secrets.get(SECRET_BACKEND_WORKSPACE_ID)) || 'workspace';

    const health = await probeServerHealth(serverUrl, this.fetchImpl, 2000);
    if (health.ok) {
      const tier = health.tierLabel || health.productMode || 'connected';
      const authSnippet = apiKey ? ' (API Key)' : '';
      this.statusBarItem.text = `$(plug) LL ● connected (${workspaceId} · ${tier})`;
      this.statusBarItem.tooltip = `LineageLens: Connected to ${serverUrl}${authSnippet}\nWorkspace: ${workspaceId}\nTier: ${tier}\nClick for connection options.`;
    } else {
      this.statusBarItem.text = `$(cloud-offline) LL ○ offline`;
      this.statusBarItem.tooltip = `LineageLens: Server at ${serverUrl} is unreachable.\nClick for connection options.`;
    }

    this.statusBarItem.show();
  }

  public async showConnectionMenu(): Promise<void> {
    const mode = vscode.workspace.getConfiguration('aiCodeProvenance').get<string>('mode', 'local');
    const rawUrl = vscode.workspace.getConfiguration('aiInsertionDetector.backend').get<string>('baseUrl', '');
    const serverUrl = rawUrl.trim() ? normalizeServerUrl(rawUrl) : 'http://localhost:8787';

    interface MenuItem extends vscode.QuickPickItem {
      action: () => Promise<void> | void;
    }

    const items: MenuItem[] = [
      {
        label: '$(refresh) Reconnect / Check Status Now',
        description: 'Probe server health and reload connection state',
        action: async () => {
          await this.updateStatus();
          void vscode.window.showInformationMessage('LineageLens: Connection status refreshed.');
        },
      },
      {
        label: '$(server) Connect to Server / Switch Server...',
        description: 'Run the Connect Wizard to pair with a LineageLens server',
        action: () => {
          void vscode.commands.executeCommand('lineagelens.connectToServer');
        },
      },
    ];

    if (mode === 'backend') {
      items.push({
        label: '$(link-external) Open Server Dashboard / Connect Page',
        description: `Open ${serverUrl}/connect in your browser`,
        action: () => {
          void vscode.env.openExternal(vscode.Uri.parse(`${serverUrl}/connect`));
        },
      });

      items.push({
        label: '$(sign-out) Disconnect from Server',
        description: 'Switch back to local mode and remove saved credentials',
        action: async () => {
          await disconnectFromServer(this.context);
          await this.updateStatus();
        },
      });
    }

    const selected = await vscode.window.showQuickPick(items, {
      title: 'LineageLens: Connection Options',
      placeHolder: 'Select an action',
    });

    if (selected) {
      await selected.action();
    }
  }

  public startPolling(intervalMs = 60_000): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
    }
    this.pollInterval = setInterval(() => {
      this.updateStatus().catch(() => {});
    }, intervalMs);
  }

  public dispose(): void {
    this.isDisposed = true;
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = undefined;
    }
    this.statusBarItem.dispose();
  }
}
