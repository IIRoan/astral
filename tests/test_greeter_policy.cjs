const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

let authorize;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
  '../mkosi.extra/usr/share/polkit-1/rules.d/10-astral-noctalia-greeter.rules'), 'utf8'), {
  polkit: {
    addRule(rule) { authorize = rule; },
    Result: { YES: 'yes' },
  },
});

function action(id = 'org.noctalia.greeter.sync-appearance', details = {}) {
  const values = {
    program: '/usr/bin/noctalia-greeter-apply-appearance',
    user: 'root',
    ...details,
  };
  return { id, lookup: key => values[key] };
}

const localAdmin = { local: true, active: true, isInGroup: group => group === 'wheel' };

test('allows constrained appearance sync for an active local administrator', () => {
  assert.equal(authorize(action(), localAdmin), 'yes');
});

for (const id of ['org.noctalia.greeter.apply-appearance', 'org.freedesktop.policykit.exec']) {
  test(`does not authorize ${id}`, () => {
    assert.notEqual(authorize(action(id), localAdmin), 'yes');
  });
}

for (const [name, details] of [
  ['a different program', { program: '/bin/sh' }],
  ['a different target account', { user: 'greeter' }],
]) {
  test(`does not authorize ${name}`, () => {
    assert.notEqual(authorize(action(undefined, details), localAdmin), 'yes');
  });
}

for (const [name, changes] of [
  ['an inactive session', { active: false }],
  ['a remote session', { local: false }],
  ['a non-administrator', { isInGroup: () => false }],
]) {
  test(`does not authorize ${name}`, () => {
    assert.notEqual(authorize(action(), { ...localAdmin, ...changes }), 'yes');
  });
}
