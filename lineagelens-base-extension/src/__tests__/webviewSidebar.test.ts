import { CaptureWebviewProvider } from '../webviewSidebar';

describe('CaptureWebviewProvider webview HTML', () => {
  it('emits parseable sidebar JavaScript', () => {
    const provider = new CaptureWebviewProvider({} as any, () => {});
    const html = (provider as any).html({ cspSource: 'vscode-webview://lineagelens-test' });
    const script = html.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/)?.[1];

    expect(script).toBeTruthy();
    expect(() => new Function(script as string)).not.toThrow();
  });
});
