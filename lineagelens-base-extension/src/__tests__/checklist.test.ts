import { checklistFor } from '../review/checklist';

test('known risk types return non-empty, type-specific items', () => {
  expect(checklistFor('auth').join(' ')).toMatch(/session|authentication/i);
  expect(checklistFor('crypto').join(' ')).toMatch(/crypto|key|secret/i);
  expect(checklistFor('database').join(' ')).toMatch(/parameteriz|quer/i);
  expect(checklistFor('dependencies').length).toBeGreaterThan(0);
});

test('unknown risk type falls back to the generic checklist', () => {
  expect(checklistFor('nonsense')).toEqual(checklistFor('generic'));
});

test('every checklist has at least three actionable items', () => {
  for (const type of ['auth', 'crypto', 'database', 'dependencies', 'ci', 'infra', 'tests', 'migration', 'generic']) {
    expect(checklistFor(type).length).toBeGreaterThanOrEqual(3);
  }
});
