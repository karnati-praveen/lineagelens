import * as vscode from 'vscode';

export interface OnboardingResult {
    completed: boolean;
    backendUrl?: string;
    storageMode?: string;
}

export async function runOnboardingWizard(
    context: vscode.ExtensionContext
): Promise<OnboardingResult> {
    // Step 1: Welcome
    const start = await vscode.window.showInformationMessage(
        'Welcome to LineageLens! Let\'s set up your AI code provenance in 3 quick steps.',
        'Get Started',
        'Skip'
    );
    if (start !== 'Get Started') {
        return { completed: false };
    }

    // Step 2: Storage mode selection
    const modeChoice = await vscode.window.showQuickPick(
        [
            {
                label: '$(database) Backend (Plus/Max)',
                description: 'Shared PostgreSQL backend — team features, dashboard, search',
                detail: 'Requires the LineageLens backend to be running (port 8787)',
                value: 'backend',
            },
            {
                label: '$(file) Local (Base)',
                description: 'Store records locally — no server required',
                detail: 'Records saved to VS Code global state or a local JSON file',
                value: 'local',
            },
        ] as Array<vscode.QuickPickItem & { value: string }>,
        { title: 'LineageLens Setup (1/3): Storage Mode', placeHolder: 'Choose how to store provenance records' }
    );
    if (!modeChoice) {
        return { completed: false };
    }

    const storageMode = (modeChoice as vscode.QuickPickItem & { value: string }).value;
    let backendUrl: string | undefined;

    // Step 3a: Backend mode configuration (delegate to connectToServer wizard)
    if (storageMode === 'backend') {
        const connectRes = await vscode.commands.executeCommand<{ backendUrl?: string }>('lineagelens.connectToServer');
        backendUrl = connectRes?.backendUrl || vscode.workspace.getConfiguration('aiInsertionDetector.backend').get<string>('baseUrl');
    } else {
        await vscode.workspace.getConfiguration('aiCodeProvenance').update('mode', 'local', vscode.ConfigurationTarget.Global);
    }

    // Step 3: Proxy setup hint
    const proxyStep = await vscode.window.showInformationMessage(
        'LineageLens Setup (3/3): Point your AI tool\'s API base URL at the proxy on port 8788 to capture completions automatically.\n\nExample: ANTHROPIC_BASE_URL=http://localhost:8788',
        'Open Docs',
        'Done'
    );
    if (proxyStep === 'Open Docs') {
        void vscode.env.openExternal(vscode.Uri.parse('https://github.com/lineagelens/lineagelens#proxy-setup'));
    }

    // Mark onboarding complete
    await context.globalState.update('lineagelens.hasRunBefore', true);
    await context.globalState.update('lineagelens.onboardingVersion', 1);

    void vscode.window.showInformationMessage(
        'LineageLens is ready! AI insertions will now be tracked automatically.',
        'Open Dashboard'
    ).then(action => {
        if (action === 'Open Dashboard') {
            void vscode.commands.executeCommand('aiInsertionDetector.openInsightsDashboard');
        }
    });

    return { completed: true, backendUrl, storageMode };
}
