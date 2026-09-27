import { describe, expect, it } from 'vitest';
import type { InputSpec } from '@desk/protocol';
import { initialValues, runInputs } from './automation-inputs';

const inputs: InputSpec[] = [
  { key: 'topic', label: 'Topic', type: 'text', required: true },
  { key: 'tone', label: 'Tone', type: 'choice', required: false, options: ['dry', 'warm'], default: 'dry' },
  { key: 'count', label: 'How many', type: 'number', required: false },
  { key: 'brief', label: 'Brief file', type: 'file', required: false },
  { key: 'deep', label: 'Go deep', type: 'boolean', required: false },
];

describe('run inputs', () => {
  it('starts from defaults, with booleans unticked', () => {
    expect(initialValues(inputs)).toEqual({ topic: '', tone: 'dry', count: '', brief: '', deep: false });
  });

  it('leaves out empty optional inputs, parses numbers and refuses a missing required one', () => {
    expect(runInputs(inputs, { topic: '', tone: 'dry', count: '', brief: '', deep: false })).toBeNull();
    expect(runInputs(inputs, { topic: ' robots ', tone: 'dry', count: '3', brief: '', deep: true })).toEqual({ topic: 'robots', tone: 'dry', count: 3, deep: true });
    expect(runInputs(inputs, { topic: 'x', count: 'many' })).toBeNull();
  });
});
