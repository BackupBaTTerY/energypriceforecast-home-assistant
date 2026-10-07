// Exercise the actual JavaScript from the copy-paste chart, not a rewrite.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const yaml = fs.readFileSync(path.join(__dirname, '../examples/price-chart-comparison.yaml'), 'utf8');
const generators = [...yaml.matchAll(/    data_generator: \|\r?\n((?:      .*\r?\n)+)/g)]
  .map(match => new Function('entity', match[1]));
assert.equal(generators.length, 3);
const timestamp = value => Date.parse(value);
const slot = (start, end, value) => ({ start, end, value });

test('all three lines retain zero and negative prices and draw the full slot', () => {
  const first = slot('2026-08-13T10:00:00Z', '2026-08-13T10:15:00Z', 0);
  const second = slot('2026-08-13T10:15:00Z', '2026-08-13T10:30:00Z', -0.02);
  const entity = { attributes: {
    raw_today: [second, first], raw_forecast: [second, first],
    raw_forecast_reference: [second, first],
  } };
  const expected = [
    [timestamp(first.start), 0], [timestamp(first.end) - 1, 0],
    [timestamp(second.start), -0.02], [timestamp(second.end) - 1, -0.02],
  ];
  generators.forEach(generate => assert.deepEqual(generate(entity), expected));
});

test('missing, null and malformed values are gaps, never invented zeros', () => {
  const first = slot('2026-08-13T10:00:00Z', '2026-08-13T10:15:00Z', 0.1);
  const missing = slot('2026-08-13T10:15:00Z', '2026-08-13T10:30:00Z', null);
  const last = slot('2026-08-13T10:30:00Z', '2026-08-13T10:45:00Z', 0.2);
  const bad = slot('invalid', '2026-08-13T10:15:00Z', 0);
  const entries = [last, missing, bad, first];
  const entity = { attributes: {
    raw_today: entries, raw_forecast: entries, raw_forecast_reference: entries,
  } };
  generators.forEach(generate => {
    const points = generate(entity);
    assert.deepEqual(points.slice(2, 4), [
      [timestamp(first.end), null], [timestamp(last.start) - 1, null],
    ]);
    assert.equal(points.length, 6);
    assert.equal(points.at(-1)[0], timestamp(last.end) - 1);
    assert.ok(points.every((point, i) => i === 0 || point[0] > points[i - 1][0]));
  });
});

test('official values never become forecast anchors; empty attributes stay empty', () => {
  const known = slot('2026-08-13T10:00:00Z', '2026-08-13T10:15:00Z', 0.1);
  const entity = { attributes: { raw_today: [known] } };
  assert.deepEqual(generators[1](entity), []);
  assert.deepEqual(generators[2](entity), []);
  generators.forEach(generate => assert.deepEqual(generate({ attributes: {} }), []));
});

test('saved and current forecasts remain independent of the official line', () => {
  const known = slot('2026-08-13T10:00:00Z', '2026-08-13T10:15:00Z', 0.2);
  const saved = { ...known, value: 0.1, captured_at: '2026-08-12T10:00:00Z' };
  const entity = { attributes: { raw_today: [known], raw_forecast_reference: [saved] } };
  assert.equal(generators[0](entity)[0][1], 0.2);
  assert.deepEqual(generators[1](entity), []);
  assert.equal(generators[2](entity)[0][1], 0.1);
});
