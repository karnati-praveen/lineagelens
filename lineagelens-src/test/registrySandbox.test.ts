import * as assert from 'assert';
import { describe, it } from 'node:test';
import { checkNpmRegistry, checkPypiRegistry, calculateRiskScore } from '../registrySandbox';
import { extractPackages } from '../terminalInterceptor';

describe('Slopsquatting & Hallucination Interceptor', () => {
    describe('terminalInterceptor - extractPackages', () => {
        it('should extract packages from npm install', () => {
            const pkgs = extractPackages('npm install express-auth-sanitizer-v2 react');
            assert.deepStrictEqual(pkgs, [
                { type: 'npm', name: 'express-auth-sanitizer-v2' },
                { type: 'npm', name: 'react' }
            ]);
        });

        it('should handle version tags', () => {
            const pkgs = extractPackages('npm i @scoped/pkg@1.2.3 lodash@^4.0.0');
            assert.deepStrictEqual(pkgs, [
                { type: 'npm', name: '@scoped/pkg' },
                { type: 'npm', name: 'lodash' }
            ]);
        });

        it('should extract packages from pip install', () => {
            const pkgs = extractPackages('pip install requests==2.31.0 Flask>=2.0');
            assert.deepStrictEqual(pkgs, [
                { type: 'pypi', name: 'requests' },
                { type: 'pypi', name: 'Flask' }
            ]);
        });

        it('should ignore pip requirement files and index URLs', () => {
            const pkgs = extractPackages('pip install -r requirements.txt --index-url https://example.test/simple urllib3');
            assert.deepStrictEqual(pkgs, [
                { type: 'pypi', name: 'urllib3' }
            ]);
        });
    });

    describe('registrySandbox - Risk Calculation', () => {
        it('should flag non-existent packages as high risk', () => {
            const risk = calculateRiskScore({ exists: false });
            assert.strictEqual(risk.hScore, 1.0);
            assert.strictEqual(risk.isHighRisk, true);
        });

        it('should flag very new packages as high risk', () => {
            // Created 1 hour ago
            const createdAt = new Date(Date.now() - 1 * 60 * 60 * 1000);
            const risk = calculateRiskScore({ exists: true, createdAt });
            assert.strictEqual(risk.hScore, 1.0);
            assert.strictEqual(risk.isHighRisk, true);
        });

        it('should flag slightly new packages as medium risk', () => {
            // Created 3 days ago (72 hours)
            const createdAt = new Date(Date.now() - 72 * 60 * 60 * 1000);
            const risk = calculateRiskScore({ exists: true, createdAt });
            assert.strictEqual(risk.hScore, 0.5);
            assert.strictEqual(risk.isHighRisk, false);
        });

        it('should mark old established packages as safe', () => {
            // Created 1 year ago
            const createdAt = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000);
            const risk = calculateRiskScore({ exists: true, createdAt });
            assert.strictEqual(risk.hScore, 0);
            assert.strictEqual(risk.isHighRisk, false);
        });
    });
});
