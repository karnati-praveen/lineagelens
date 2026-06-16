import * as http from 'node:http';
import * as https from 'node:https';
import * as vscode from 'vscode';

const EMAIL_REGEX = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{1,63}$/;
const MAX_EMAIL_LENGTH = 254;

// V2 gate — distinct from the old 'lineagelens.welcomeShown' so existing users
// also see the new welcome + get the email prompt once.
const KEY_SEEN_WELCOME = 'lineagelens.hasSeenWelcomeV2';
const KEY_EMAIL_PROMPTED = 'lineagelens.emailPrompted';
const KEY_USER_EMAIL = 'lineagelens.userEmail';

export async function runWelcomeFlow(context: vscode.ExtensionContext): Promise<void> {
    if (context.globalState.get(KEY_SEEN_WELCOME)) {
        return;
    }
    await context.globalState.update(KEY_SEEN_WELCOME, true);
    // Keep old flag in sync so the legacy branch never fires either.
    await context.globalState.update('lineagelens.welcomeShown', true);

    const action = await vscode.window.showInformationMessage(
        'Welcome to LineageLens! Track every line of AI-generated code — model, prompt, and timestamp. All features work locally with no account required.',
        'Show my AI code',
        'Dismiss',
    );
    if (action === 'Show my AI code') {
        void vscode.commands.executeCommand('lineagelens.captures.focus');
    }

    // Non-blocking: show email prompt after welcome closes
    void promptAndSaveEmail(context);
}

export async function promptAndSaveEmail(
    context: vscode.ExtensionContext,
    force = false,
): Promise<void> {
    if (!force && context.globalState.get(KEY_EMAIL_PROMPTED)) {
        return;
    }
    await context.globalState.update(KEY_EMAIL_PROMPTED, true);

    let lastInput = '';
    for (let attempt = 0; attempt < 3; attempt++) {
        const input = await vscode.window.showInputBox({
            prompt:
                'Enter your email to receive LineageLens updates (optional). ' +
                'We store this only to send product updates — remove it anytime with ' +
                '"LineageLens: Remove My Email". Press Esc to skip.',
            placeHolder: 'you@company.com',
            value: lastInput,
            ignoreFocusOut: false,
        });

        if (input === undefined) {
            return; // Esc — skip silently
        }

        const trimmed = input.trim();
        if (trimmed.length === 0) {
            return;
        }

        if (trimmed.length > MAX_EMAIL_LENGTH) {
            void vscode.window.showWarningMessage(
                `Email must be at most ${MAX_EMAIL_LENGTH} characters. Try again or press Esc to skip.`,
            );
            lastInput = trimmed.slice(0, MAX_EMAIL_LENGTH);
            continue;
        }

        if (!EMAIL_REGEX.test(trimmed)) {
            void vscode.window.showWarningMessage(
                "That doesn't look like a valid email. Try again or press Esc to skip.",
            );
            lastInput = trimmed;
            continue;
        }

        await context.globalState.update(KEY_USER_EMAIL, trimmed);
        void fireAndForgetPostLead(trimmed, context);
        void vscode.window.showInformationMessage(
            'LineageLens: Email saved. Use "LineageLens: Remove My Email" to opt out anytime.',
        );
        return;
    }
}

export async function removeEmail(context: vscode.ExtensionContext): Promise<void> {
    const existing = context.globalState.get<string>(KEY_USER_EMAIL);
    await context.globalState.update(KEY_USER_EMAIL, undefined);
    if (existing) {
        void fireAndForgetDeleteLead(existing, context);
        vscode.window.showInformationMessage('LineageLens: Email removed.');
    } else {
        vscode.window.showInformationMessage('LineageLens: No email was saved.');
    }
}

export function getSavedEmail(context: vscode.ExtensionContext): string | undefined {
    return context.globalState.get<string>(KEY_USER_EMAIL);
}

// ── fire-and-forget helpers ───────────────────────────────────────────────────

function fireAndForgetPostLead(email: string, context: vscode.ExtensionContext): void {
    const baseUrl = getBackendBaseUrl();
    if (!baseUrl) { return; } // no backend configured — keep local copy only
    const version = getExtensionVersion(context);
    const body = JSON.stringify({ email, source: 'vscode-base-extension', extension_version: version });
    void rawRequest('POST', `${baseUrl}/leads`, body).catch(() => {});
}

function fireAndForgetDeleteLead(email: string, _context: vscode.ExtensionContext): void {
    const baseUrl = getBackendBaseUrl();
    if (!baseUrl) { return; }
    const encoded = encodeURIComponent(email);
    void rawRequest('DELETE', `${baseUrl}/leads?email=${encoded}`, '').catch(() => {});
}

function getBackendBaseUrl(): string {
    const raw = vscode.workspace
        .getConfiguration('lineagelensBase')
        .get<string>('backendUrl', '') ?? '';
    return raw.trim().replace(/\/$/, '');
}

function getExtensionVersion(context: vscode.ExtensionContext): string {
    const pkg = context.extension?.packageJSON as Record<string, unknown> | undefined;
    return typeof pkg?.version === 'string' ? pkg.version : 'unknown';
}

function rawRequest(method: string, url: string, body: string): Promise<void> {
    return new Promise<void>((resolve) => {
        try {
            const target = new URL(url);
            const transport = target.protocol === 'https:' ? https : http;
            const req = transport.request(
                {
                    protocol: target.protocol,
                    hostname: target.hostname,
                    port: target.port || undefined,
                    method,
                    path: target.pathname + target.search,
                    headers: {
                        'Content-Type': 'application/json',
                        'Content-Length': Buffer.byteLength(body),
                    },
                    timeout: 8000,
                },
                (res) => { res.resume(); resolve(); },
            );
            req.on('error', () => resolve());
            req.on('timeout', () => { req.destroy(); resolve(); });
            if (body) { req.write(body); }
            req.end();
        } catch {
            resolve();
        }
    });
}
