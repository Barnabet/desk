import { readFile } from 'node:fs/promises';
import { Hono, type Context } from 'hono';
import { SkillImportRequest, SkillRestoreRequest, SkillWriteRequest, type SkillScope } from '@desk/protocol';
import { NotFoundError } from '@desk/core';
import type { AppDeps } from '../app';
import { body } from '../http';

/**
 * Global skills under `/skills`, project skills under `/projects/:id/skills`.
 * Listing and reading a project skill resolve like agents do (project first, then global).
 */
export function skillRoutes({ runtime }: AppDeps): Hono {
  const r = new Hono();

  const mount = (prefix: string, scope: SkillScope) => {
    const projectOf = (c: Context) => (scope === 'project' ? c.req.param('id') : undefined);

    r.get(prefix, (c) => c.json(runtime.listSkills(projectOf(c))));

    r.post(`${prefix}/import`, async (c) => {
      const req = await body(c, SkillImportRequest);
      const projectId = projectOf(c);
      const out = runtime.importSkill(req.path, { scope, ...(projectId ? { projectId } : {}), ...(req.name ? { name: req.name } : {}) });
      return c.json(out, 201);
    });

    r.get(`${prefix}/:name`, (c) => {
      const projectId = projectOf(c);
      return c.json(runtime.getSkill(c.req.param('name'), projectId ? { projectId } : { scope: 'global' }));
    });

    r.get(`${prefix}/:name/files/*`, async (c) => {
      const projectId = projectOf(c);
      const skill = runtime.getSkill(c.req.param('name'), projectId ? { projectId } : { scope: 'global' });
      const marker = `/${c.req.param('name')}/files/`;
      const rel = decodeURIComponent(c.req.path.slice(c.req.path.indexOf(marker) + marker.length));
      const file = runtime.skills.filePath(skill, rel);
      return c.body(new Uint8Array(await readFile(file)), 200, { 'content-type': 'application/octet-stream' });
    });

    r.put(`${prefix}/:name`, async (c) => {
      const req = await body(c, SkillWriteRequest);
      const projectId = projectOf(c);
      const out = runtime.saveSkill(
        {
          scope,
          name: c.req.param('name'),
          ...(projectId ? { projectId } : {}),
          ...(req.description ? { description: req.description } : {}),
          ...(req.instructions ? { instructions: req.instructions } : {}),
          files: req.files.map((f) => ({ path: f.path, content: Buffer.from(f.content_base64, 'base64') })),
          removeFiles: req.remove_files,
        },
        { origin: 'user', ...(req.change_note ? { changeNote: req.change_note } : {}) },
      );
      return c.json(out, out.created ? 201 : 200);
    });

    r.delete(`${prefix}/:name`, (c) => {
      runtime.deleteSkill(scope, c.req.param('name'), projectOf(c));
      return c.json({ ok: true });
    });

    r.get(`${prefix}/:name/history`, (c) => {
      const history = runtime.skillHistory(scope, c.req.param('name'), projectOf(c));
      if (!history.length) throw new NotFoundError(`Unknown skill: ${c.req.param('name')}`);
      return c.json(history);
    });

    r.post(`${prefix}/:name/restore`, async (c) => {
      const req = await body(c, SkillRestoreRequest);
      return c.json(runtime.restoreSkill(scope, c.req.param('name'), req.version, projectOf(c)));
    });
  };

  mount('/skills', 'global');
  mount('/projects/:id/skills', 'project');
  return r;
}
