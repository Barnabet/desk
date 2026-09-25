import { useState } from 'react';
import type { CatalogInstall, CatalogItem } from '@desk/protocol';
import { href, skillKey } from '@desk/ui-core';
import { actionFor, BAYS, installRef, runtimeWords, sourceLabel } from './data';
import { RuntimeLine } from './RuntimeLine';

export type Layout = 'cards' | 'list';

/** The Cards / Compact switch, shown with the Skills screen's controls. */
export function LayoutSwitch({ layout, onChange }: { layout: Layout; onChange(l: Layout): void }) {
  return (
    <div className="segmented" role="group" aria-label="Layout">
      <button type="button" aria-pressed={layout === 'cards'} onClick={() => onChange('cards')}>
        Cards
      </button>
      <button type="button" aria-pressed={layout === 'list'} onClick={() => onChange('list')}>
        Compact
      </button>
    </div>
  );
}

/** Cards or compact rows, remembered per viewer. */
export function useCatalogLayout(): [Layout, (l: Layout) => void] {
  const [l, setL] = useState<Layout>(() => {
    try {
      return localStorage.getItem('desk.catalogLayout') === 'list' ? 'list' : 'cards';
    } catch {
      return 'cards';
    }
  });
  return [
    l,
    (next) => {
      setL(next);
      try {
        localStorage.setItem('desk.catalogLayout', next);
      } catch {
        // A convenience only.
      }
    },
  ];
}

/** Where else an entry is installed, e.g. "Also in Thesis". */
function elsewhere(item: CatalogItem, projectNames: Map<string, string>): string | null {
  const names = item.installs.filter((i) => i.scope === 'project' && i.state !== 'name_taken').map((i) => projectNames.get(i.project_id!) ?? 'a project');
  return names.length ? `In ${names.join(', ')}` : null;
}

function Action({ item, install, onReview }: { item: CatalogItem; install: CatalogInstall | undefined; onReview(): void }) {
  const a = actionFor(install);
  if (a.kind === 'installed') {
    return (
      <a className="btn btn-ghost btn-sm catalog-installed" href={href({ name: 'skills', skill: skillKey(installRef(item.id, install!)) })}>
        ✓ Installed
      </a>
    );
  }
  if (a.kind === 'taken') {
    return (
      <span className="chip chip-idle" title={`You already have a skill named ${item.id} that didn't come from the catalog.`}>
        Name taken
      </span>
    );
  }
  return (
    <button type="button" className={`btn btn-sm ${a.kind === 'install' ? 'btn-primary' : 'btn-secondary'}`} onClick={onReview} aria-label={a.kind === 'modified' ? `Review ${item.title} (edited since install)` : `${a.label} ${item.title}`}>
      {a.kind === 'modified' ? 'Modified · review' : a.label}
    </button>
  );
}

function Card({ item, projectNames, onReview }: { item: CatalogItem; projectNames: Map<string, string>; onReview(): void }) {
  const global = item.installs.find((i) => i.scope === 'global');
  const also = elsewhere(item, projectNames);
  return (
    <li className="catalog-card card" aria-label={item.title}>
      <button type="button" className="catalog-card-open" onClick={onReview}>
        <span className="catalog-card-title">{item.title}</span>
        <span className="catalog-card-summary">{item.summary}</span>
      </button>
      <div className="catalog-chips">
        <span className={`chip ${item.source.type === 'builtin' ? 'chip-done' : 'chip-idle'}`}>{sourceLabel(item)}</span>
        <span className="chip chip-idle">{item.license}</span>
        {item.scripts ? <span className="chip chip-wait">{item.scripts === 1 ? '1 script' : `${item.scripts} scripts`}</span> : null}
      </div>
      <p className="catalog-runtime small muted">{runtimeWords(item)}</p>
      {global && global.state !== 'name_taken' ? <RuntimeLine entry={item} install={global} /> : null}
      <div className="catalog-card-foot">
        <span className="small muted grow">{also}</span>
        <Action item={item} install={global} onReview={onReview} />
      </div>
    </li>
  );
}

function Row({ item, projectNames, onReview }: { item: CatalogItem; projectNames: Map<string, string>; onReview(): void }) {
  const global = item.installs.find((i) => i.scope === 'global');
  const also = elsewhere(item, projectNames);
  return (
    <li className="catalog-row">
      <button type="button" className="catalog-row-open" onClick={onReview}>
        <span className="catalog-row-title">{item.title}</span>
        <span className="muted small grow">{item.summary}</span>
      </button>
      <span className="small muted catalog-row-meta">
        {sourceLabel(item)} · {item.license}
        {item.scripts ? ` · ${item.scripts === 1 ? '1 script' : `${item.scripts} scripts`}` : ''}
        {also ? ` · ${also}` : ''}
      </span>
      <Action item={item} install={global} onReview={onReview} />
    </li>
  );
}

/** The catalog in five bays: cards (or a list) with source, licence, scripts, runtime and an action per entry. */
export function CatalogView({ items, layout, projectNames, onReview }: { items: CatalogItem[]; layout: Layout; projectNames: Map<string, string>; onReview(id: string): void }) {
  return (
    <div className="catalog">
      {BAYS.map((bay) => {
        const inBay = items.filter((i) => i.category === bay.category);
        if (!inBay.length) return null;
        return (
          <section key={bay.category} className="catalog-bay" aria-labelledby={`bay-${bay.category}`}>
            <header className="catalog-bay-head">
              <h2 id={`bay-${bay.category}`}>{bay.title}</h2>
              <span className="muted small">
                {bay.blurb} · {inBay.length}
              </span>
            </header>
            {layout === 'cards' ? (
              <ul className="catalog-cards">
                {inBay.map((item) => (
                  <Card key={item.id} item={item} projectNames={projectNames} onReview={() => onReview(item.id)} />
                ))}
              </ul>
            ) : (
              <ul className="catalog-rows">
                {inBay.map((item) => (
                  <Row key={item.id} item={item} projectNames={projectNames} onReview={() => onReview(item.id)} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
