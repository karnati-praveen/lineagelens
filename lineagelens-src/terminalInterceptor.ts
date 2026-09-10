import * as vscode from 'vscode';
import { checkNpmRegistry, checkPypiRegistry, calculateRiskScore } from './registrySandbox';

export function activateTerminalInterceptor(context: vscode.ExtensionContext) {
    // Check if the API is available (requires VS Code 1.93+)
    if (typeof vscode.window.onDidStartTerminalShellExecution !== 'function') {
        console.warn('LineageLens: Terminal shell execution interception requires VS Code 1.93+');
        return;
    }

    context.subscriptions.push(
        vscode.window.onDidStartTerminalShellExecution(async (event) => {
            const commandLine = event.execution.commandLine.value.trim();
            const packages = extractPackages(commandLine);
            
            if (packages.length === 0) return;

            for (const pkg of packages) {
                let result;
                if (pkg.type === 'npm') {
                    result = await checkNpmRegistry(pkg.name);
                } else if (pkg.type === 'pypi') {
                    result = await checkPypiRegistry(pkg.name);
                }

                if (result) {
                    const risk = calculateRiskScore(result);
                    if (risk.isHighRisk) {
                        // Immediately send SIGINT to cancel the potentially malicious command
                        event.terminal.sendText('\x03', false);
                        
                        const action = await vscode.window.showWarningMessage(
                            `🛑 LINEAGELENS SECURITY GATE: Slopsquatting Detected!\n\nPackage '${pkg.name}' has a high hallucination risk score (${risk.hScore.toFixed(2)}).\n${risk.details}\n\nAction: Installation blocked.`,
                            { modal: true },
                            'Override with Justification'
                        );
                        
                        if (action === 'Override with Justification') {
                            // Never replay the raw command line: it may carry a
                            // trailing payload past the package list (`; rm …`).
                            // The user can re-run the install explicitly instead.
                            vscode.window.showInformationMessage(
                                'LineageLens: override acknowledged — re-run the install explicitly if you accept the risk.'
                            );
                        }
                    }
                }
            }
        })
    );
}

export function extractPackages(commandLine: string): { type: 'npm' | 'pypi', name: string }[] {
    const packages: { type: 'npm' | 'pypi', name: string }[] = [];
    
    // Simplistic npm install parsing: `npm install pkg1 pkg2`
    const npmMatch = commandLine.match(/(?:npm|yarn|pnpm)\s+(?:install|add|i)\s+([^&|;]+)/i);
    if (npmMatch && npmMatch[1]) {
        const pkgs = npmMatch[1].split(/\s+/).filter(isInstallPackageToken);
        for (const p of pkgs) {
            // Handle @scoped/package@version correctly
            let name = p;
            if (name.includes('@', 1)) {
                name = name.substring(0, name.indexOf('@', 1));
            }
            if (name) {
                packages.push({ type: 'npm', name });
            }
        }
    }

    // Simplistic pip install parsing: `pip install pkg1 pkg2`
    const pipMatch = commandLine.match(/pip(?:3)?\s+install\s+([^&|;]+)/i);
    if (pipMatch && pipMatch[1]) {
        const pkgs = stripOptionsWithValues(
            pipMatch[1].split(/\s+/),
            new Set(['-r', '--requirement', '-c', '--constraint', '--index-url', '--extra-index-url', '-f', '--find-links'])
        );
        for (const p of pkgs) {
            if (!isInstallPackageToken(p) || p.includes('/') || p.includes('://')) {
                continue;
            }
            // Remove versions e.g. pkg==1.0.0
            const name = p.split(/[=<>~]/)[0];
            if (name) {
                packages.push({ type: 'pypi', name });
            }
        }
    }

    return packages;
}

function isInstallPackageToken(token: string): boolean {
    const trimmed = token.trim();
    return trimmed.length > 0 && !trimmed.startsWith('-');
}

function stripOptionsWithValues(tokens: string[], optionsWithValues: Set<string>): string[] {
    const result: string[] = [];
    for (let i = 0; i < tokens.length; i += 1) {
        const token = tokens[i];
        const optionName = token.includes('=') ? token.slice(0, token.indexOf('=')) : token;
        if (optionsWithValues.has(optionName)) {
            if (!token.includes('=')) {
                i += 1;
            }
            continue;
        }
        result.push(token);
    }
    return result;
}

export async function checkManifestEdits(event: vscode.TextDocumentChangeEvent) {
    const filename = event.document.fileName;
    if (!filename.endsWith('package.json') && !filename.endsWith('requirements.txt')) {
        return;
    }

    for (const change of event.contentChanges) {
        const text = change.text.trim();
        if (!text) continue;

        if (filename.endsWith('package.json')) {
            const dependencyNames = extractPackageJsonDependencyNames(event.document.getText());
            for (const pkgName of dependencyNames) {
                const result = await checkNpmRegistry(pkgName);
                const risk = calculateRiskScore(result);
                if (risk.isHighRisk) {
                    vscode.window.showWarningMessage(
                        `⚠️ LINEAGELENS AST GATE: High Risk Dependency Added!\n\nPackage '${pkgName}' was just inserted into package.json and has a hallucination risk score of ${risk.hScore.toFixed(2)}.\n${risk.details}`
                    );
                }
            }
        }

        // Simplistic requirements.txt check
        if (filename.endsWith('requirements.txt') && text.length > 0) {
            const lines = text.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#'));
            for (const line of lines) {
                const pkgName = line.split(/[=<>~]/)[0].trim();
                if (pkgName) {
                    const result = await checkPypiRegistry(pkgName);
                    if (result) {
                        const risk = calculateRiskScore(result);
                        if (risk.isHighRisk) {
                            vscode.window.showWarningMessage(
                                `⚠️ LINEAGELENS AST GATE: High Risk Dependency Added!\n\nPackage '${pkgName}' was just inserted into requirements.txt and has a hallucination risk score of ${risk.hScore.toFixed(2)}.\n${risk.details}`
                            );
                        }
                    }
                }
            }
        }
    }
}

function extractPackageJsonDependencyNames(documentText: string): string[] {
    try {
        const parsed = JSON.parse(documentText) as Record<string, unknown>;
        const sections = ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'];
        const names = new Set<string>();
        for (const section of sections) {
            const dependencies = parsed[section];
            if (!dependencies || typeof dependencies !== 'object' || Array.isArray(dependencies)) {
                continue;
            }
            for (const name of Object.keys(dependencies)) {
                if (name.trim()) {
                    names.add(name);
                }
            }
        }
        return [...names];
    } catch {
        return [];
    }
}
