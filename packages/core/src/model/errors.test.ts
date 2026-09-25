import { describe, expect, it } from 'vitest';
import { imageRefusal, ModelError } from './errors';

describe('imageRefusal', () => {
  it('names requests refused for an image, or for their size; anything else is not about images', () => {
    // What claude-opus-5-5 and gpt-6-sol answered through the local proxy (2026-09-25).
    expect(imageRefusal(new ModelError('fatal', '400 Could not process image', 400))).toBe('image');
    expect(imageRefusal(new ModelError('fatal', '400 The image data you provided does not represent a valid image. Please check your input and try again.', 400))).toBe('image');
    expect(imageRefusal(new ModelError('fatal', '413 Request exceeds the maximum size', 413))).toBe('too_large');
    expect(imageRefusal(new ModelError('fatal', '413 status code (no body)', 413))).toBe('too_large');
    expect(imageRefusal(new ModelError('fatal', '400 request body too large', 400))).toBe('too_large');
    expect(imageRefusal(new ModelError('fatal', "400 Invalid 'messages[3].content[1].image_url'. Expected a base64-encoded data URL with an image MIME type.", 400))).toBe('image');
    expect(imageRefusal(new ModelError('fatal', '400 invalid tool schema', 400))).toBeNull();
    // Words that only contain "image" (a tool name, a field) are not about the images sent.
    expect(imageRefusal(new ModelError('fatal', "400 Invalid schema for function 'view_image': 'paths' is not valid", 400))).toBeNull();
    expect(imageRefusal(new ModelError('fatal', '400 tools[12].function.name: view_image_2 is a duplicate', 400))).toBeNull();
    expect(imageRefusal(new ModelError('fatal', '422 unknown field image_count', 422))).toBeNull();
    expect(imageRefusal(new ModelError('fatal', '401 bad key: image', 401))).toBeNull();
    expect(imageRefusal(new ModelError('transient', '500 image service down', 500))).toBeNull();
    expect(imageRefusal(new ModelError('context_overflow', '400 prompt is too long', 400))).toBeNull();
  });
});
