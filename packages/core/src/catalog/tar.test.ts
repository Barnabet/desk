import { gzipSync } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import { chunked, makeTarball } from '../testing/tarball';
import { extractSubtree } from './tar';

const root = 'skills-0123456789012345678901234567890123456789';
const ok = (entries: Parameters<typeof makeTarball>[1]) => makeTarball(root, entries);
const extract = (buf: Buffer, prefix: string, caps = {}) => extractSubtree(chunked(buf, 700), prefix, caps);
const paths = (files: Array<{ path: string }>) => files.map((f) => f.path).sort();

describe('extractSubtree', () => {
  it('returns the regular files under the prefix, relative to it, and skips the rest', async () => {
    const files = await extract(
      ok([
        { name: 'README.md', content: 'root' },
        { name: 'skills/pdf/', type: '5' },
        { name: 'skills/pdf/SKILL.md', content: '---\nname: pdf\n---\nx' },
        { name: 'skills/pdf/scripts/run.py', content: 'print(1)', mode: 0o755 },
        { name: 'skills/pdfx/SKILL.md', content: 'not me' },
        { name: 'skills/other/SKILL.md', content: 'other' },
        { name: 'skills/other/link', type: '2', linkname: '/etc/passwd' },
      ]),
      'skills/pdf',
    );
    expect(paths(files)).toEqual(['SKILL.md', 'scripts/run.py']);
    expect(files.find((f) => f.path === 'scripts/run.py')!.mode & 0o111).toBeTruthy();
    expect(files.find((f) => f.path === 'SKILL.md')!.content.toString()).toContain('name: pdf');
  });

  it('extracts the whole root with an empty prefix and follows pax and GNU long names', async () => {
    const long = `deep/${'d'.repeat(120)}/file.txt`;
    const files = await extract(
      ok([
        { name: 'SKILL.md', content: 's' },
        { name: 'x', content: 'pax long', pax: { path: `${root}/${long}` } },
        { name: `gnu/${'g'.repeat(110)}.md`, content: 'gnu long', gnuLongName: true },
      ]),
      '',
    );
    expect(paths(files)).toEqual(['SKILL.md', long, `gnu/${'g'.repeat(110)}.md`].sort());
  });

  it('refuses links and special files under the prefix', async () => {
    await expect(extract(ok([{ name: 's/SKILL.md', content: 'x' }, { name: 's/l', type: '2', linkname: '../../x' }]), 's')).rejects.toThrow(/symbolic link/);
    await expect(extract(ok([{ name: 's/h', type: '1', linkname: 's/SKILL.md' }]), 's')).rejects.toThrow(/hard link/);
    await expect(extract(ok([{ name: 's/dev', type: '3' }]), 's')).rejects.toThrow(/special file/);
  });

  it('refuses the archive when any entry is absolute or climbs out', async () => {
    await expect(extract(ok([{ name: '/etc/evil', content: 'x' }]), 's')).rejects.toThrow(/unsafe path/);
    await expect(extract(ok([{ name: 's/../../evil', content: 'x' }]), 's')).rejects.toThrow(/unsafe path/);
  });

  it('enforces the caps', async () => {
    const files = Array.from({ length: 5 }, (_, i) => ({ name: `s/f${i}.txt`, content: 'x'.repeat(100) }));
    await expect(extract(ok(files), 's', { maxFiles: 4 })).rejects.toThrow(/at most 4 files/);
    await expect(extract(ok(files), 's', { maxBytes: 450 })).rejects.toThrow(/larger than/);
    await expect(extract(ok([{ name: 's/big', content: 'x'.repeat(3000) }]), 's', { maxFileBytes: 2000 })).rejects.toThrow(/big is larger/);
    await expect(extract(ok(files), 's', { maxDownload: 50 })).rejects.toThrow(/download is larger/);
  });

  it('refuses a decompression bomb, a truncated archive and garbage', async () => {
    const bomb = makeTarball(root, [{ name: 'other/zeros', content: Buffer.alloc(2_000_000) }, { name: 's/SKILL.md', content: 'x' }]);
    await expect(extract(bomb, 's', { maxDownload: bomb.length + 10 })).rejects.toThrow(/expands/);
    await expect(extract(makeTarball(root, [{ name: 's/SKILL.md', content: 'x' }], { end: false }), 's')).rejects.toThrow(/truncated/);
    const full = makeTarball(root, [{ name: 's/SKILL.md', content: 'x'.repeat(5000) }]);
    await expect(extract(full.subarray(0, full.length - 40), 's')).rejects.toThrow(/could not be read|truncated/);
    await expect(extract(gzipSync(Buffer.alloc(512, 7)), 's')).rejects.toThrow(/checksum/);
  });
});
