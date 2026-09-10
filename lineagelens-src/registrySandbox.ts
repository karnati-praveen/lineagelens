import * as vscode from 'vscode';

export interface RegistryResult {
    exists: boolean;
    createdAt?: Date;
    downloads?: number;
    error?: string;
    isPypi?: boolean;
}

export interface RiskAssessment {
    hScore: number;
    ageRisk: number;
    downloadRisk: number;
    isHighRisk: boolean;
    details: string;
}

/**
 * Checks the npm registry for the specified package.
 */
export async function checkNpmRegistry(pkgName: string): Promise<RegistryResult> {
    try {
        // Handle scoped packages appropriately
        const encodedPkgName = encodeURIComponent(pkgName);
        const response = await fetch(`https://registry.npmjs.org/${encodedPkgName}`);
        
        if (!response.ok) {
            if (response.status === 404) {
                return { exists: false, error: 'Package not found' };
            }
            return { exists: false, error: `NPM API error: ${response.statusText}` };
        }

        const data: any = await response.json();
        
        // npm registry provides a time object with a created property
        let createdAt: Date | undefined;
        if (data.time && data.time.created) {
            createdAt = new Date(data.time.created);
        }

        return { exists: true, createdAt, downloads: 0 }; // NPM downloads require a separate API call, we'll simplify for now
    } catch (error: any) {
        return { exists: false, error: error.message };
    }
}

/**
 * Checks the PyPI registry for the specified package.
 */
export async function checkPypiRegistry(pkgName: string): Promise<RegistryResult> {
    try {
        const response = await fetch(`https://pypi.org/pypi/${encodeURIComponent(pkgName)}/json`);
        
        if (!response.ok) {
            if (response.status === 404) {
                return { exists: false, error: 'Package not found' };
            }
            return { exists: false, error: `PyPI API error: ${response.statusText}` };
        }

        const data: any = await response.json();
        
        // PyPI provides release information
        let createdAt: Date | undefined;
        if (data.urls && data.urls.length > 0 && data.urls[0].upload_time_iso_8601) {
             createdAt = new Date(data.urls[0].upload_time_iso_8601);
        } else if (data.releases) {
            // Find the earliest release
            const versions = Object.keys(data.releases);
            for (const v of versions) {
                const releases = data.releases[v];
                if (releases && releases.length > 0) {
                    const releaseDate = new Date(releases[0].upload_time_iso_8601);
                    if (!createdAt || releaseDate < createdAt) {
                        createdAt = releaseDate;
                    }
                }
            }
        }

        return { exists: true, createdAt, isPypi: true };
    } catch (error: any) {
        return { exists: false, error: error.message };
    }
}

/**
 * Calculates the Hallucination Risk Score (H_score)
 */
export function calculateRiskScore(result: RegistryResult): RiskAssessment {
    if (!result.exists) {
        return {
            hScore: 1.0,
            ageRisk: 1.0,
            downloadRisk: 1.0,
            isHighRisk: true,
            details: `Package does not exist in the public registry. This is a critical indicator of hallucination.`
        };
    }

    let ageRisk = 0;
    let downloadRisk = 0; // Keeping it 0 for now as we don't fetch download counts

    // If no created date is available but package exists, it's slightly risky
    if (!result.createdAt) {
        ageRisk = 0.5;
    } else {
        const now = new Date();
        const ageInHours = (now.getTime() - result.createdAt.getTime()) / (1000 * 60 * 60);
        
        // Package created < 48 hours ago = 1.0
        if (ageInHours < 48) {
            ageRisk = 1.0;
        } else if (ageInHours < 168) { // < 1 week
            ageRisk = 0.5;
        }
    }

    // H_score formula weighting
    const w1 = 0.7; // Age is very important for slopsquatting
    const w2 = 0.3; // Downloads (if we had them)
    
    // We adjust weights since download risk isn't fully implemented
    const hScore = ageRisk; // Just use ageRisk for now

    return {
        hScore,
        ageRisk,
        downloadRisk,
        isHighRisk: hScore > 0.75,
        details: `Package Age Risk: ${ageRisk.toFixed(2)}.` + 
                 (result.createdAt ? ` Created at ${result.createdAt.toISOString()}.` : ' Creation date unknown.')
    };
}
