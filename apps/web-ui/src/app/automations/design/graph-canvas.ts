import { afterNextRender, ChangeDetectionStrategy, Component, computed, DestroyRef, ElementRef, inject, input, output, signal, viewChild, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationLayout } from '@desk/protocol';
import {
  bezierPath,
  canConnect,
  CONNECT_RADIUS,
  dotPattern,
  dropTarget,
  edgeGeometry,
  edgeIndex,
  fitViewport,
  graphBounds,
  sourceAnchor,
  toGraph,
  toGraphPoint,
  wheelFactor,
  zoomAt,
  type GraphIssues,
  type GraphNode,
  type GraphRun,
  type GraphSelection,
  type Point,
  type Viewport,
} from '@desk/ui-core';
import { StartNode, StepNode, StubNode } from './nodes';

/** A pointer that went down on a node: a drag once it moves 3 px (a click before that). */
type Drag = { id: string; startX: number; startY: number; origin: Point; position: Point; moved: boolean };
/** A pointer that went down on the background: a pan once it moves 3 px (a pane click before that). */
type Pan = { startX: number; startY: number; origin: Viewport; moved: boolean };
/** A connection being dragged from `from`'s bottom handle; `at` is the pointer in graph coordinates. */
type Link = { from: string; at: Point; target: string | null; valid: boolean };

/** Pointer travel below this is a click, not a drag or a pan. */
const DRAG_THRESHOLD = 3;
let nextPattern = 0;

/**
 * The automation graph, top to bottom (spec §8.2): editable in Design, read-only and lit in a run. The web's own canvas for
 * what React Flow does on the desktop, with React Flow's class names so the shared CSS applies (Plan 21's decisions).
 */
@Component({
  selector: 'div[deskGraphCanvas]',
  imports: [StartNode, StepNode, StubNode],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-canvas' },
  template: `
    @let l = link();
    <div #flow class="react-flow desk-flow" tabindex="0" (keydown)="onKey($event)" (wheel)="onWheel($event)">
      <svg class="react-flow__background" aria-hidden="true">
        <pattern [id]="patternId" patternUnits="userSpaceOnUse" [attr.x]="dots().x" [attr.y]="dots().y" [attr.width]="dots().cell" [attr.height]="dots().cell">
          <circle class="react-flow__background-pattern dots" [attr.cx]="dots().r" [attr.cy]="dots().r" [attr.r]="dots().r" />
        </pattern>
        <rect x="0" y="0" width="100%" height="100%" [attr.fill]="'url(#' + patternId + ')'" />
      </svg>
      <div class="react-flow__renderer">
        <div class="react-flow__pane draggable" [class.dragging]="pan()?.moved ?? false" (pointerdown)="onPointerDown($event)" (click)="onClick($event)">
          <div class="react-flow__viewport" [style.transform]="transform()">
            <div class="react-flow__edges">
              <svg>
                @for (e of edges(); track e.id) {
                  <g class="react-flow__edge react-flow__edge-route" [class.selectable]="e.selectable !== false" [class.selected]="e.selected ?? false" [attr.data-id]="e.id" [attr.aria-label]="'Edge from ' + e.source + ' to ' + e.target">
                    <path class="react-flow__edge-path auto-edge" [class]="'look-' + e.data.look" [class.selected]="e.selected ?? false" [class.invalid]="e.data.error" [attr.d]="e.path" />
                    @if (e.selectable !== false) {
                      <path class="react-flow__edge-interaction" fill="none" stroke-opacity="0" stroke-width="20" [attr.d]="e.path" />
                    }
                  </g>
                }
                @if (linkPath(); as p) {
                  <path class="react-flow__connection-path" [attr.d]="p" />
                }
              </svg>
            </div>
            <div class="react-flow__edgelabel-renderer">
              @for (e of labelled(); track e.id) {
                <div class="auto-edge-label" [class.selected]="e.selected ?? false" [class.invalid]="e.data.error" [style.transform]="'translate(-50%, -50%) translate(' + e.labelX + 'px, ' + e.labelY + 'px)'">{{ e.data.label }}</div>
              }
            </div>
            <div class="react-flow__nodes">
              @for (n of nodes(); track n.id) {
                <div
                  class="react-flow__node"
                  [class]="'react-flow__node-' + n.type"
                  [class.selectable]="n.selectable !== false"
                  [class.draggable]="editable() && n.draggable !== false"
                  [class.selected]="n.selected ?? false"
                  [class.dragging]="drag()?.id === n.id && (drag()?.moved ?? false)"
                  [attr.data-id]="n.id"
                  [style.transform]="'translate(' + n.position.x + 'px, ' + n.position.y + 'px)'"
                >
                  @switch (n.type) {
                    @case ('step') {
                      <div deskStepNode [data]="asStep(n).data" [selected]="n.selected ?? false" [connectable]="editable()" [dropValid]="l !== null && l.valid && l.target === n.id"></div>
                    }
                    @case ('start') {
                      <div deskStartNode [data]="asStart(n).data" [selected]="n.selected ?? false"></div>
                    }
                    @case ('stub') {
                      <div deskStubNode [data]="asStub(n).data"></div>
                    }
                  }
                </div>
              }
            </div>
          </div>
        </div>
      </div>
      <div class="react-flow__panel react-flow__controls vertical bottom left">
        <button type="button" class="react-flow__controls-button react-flow__controls-zoomin" title="Zoom In" aria-label="Zoom In" (click)="zoomBy(1.2)">
          <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" /></svg>
        </button>
        <button type="button" class="react-flow__controls-button react-flow__controls-zoomout" title="Zoom Out" aria-label="Zoom Out" (click)="zoomBy(1 / 1.2)">
          <svg viewBox="0 0 32 5" aria-hidden="true"><path d="M0 0h32v4.2H0z" /></svg>
        </button>
        <button type="button" class="react-flow__controls-button react-flow__controls-fitview" title="Fit View" aria-label="Fit View" (click)="fit()">
          <svg viewBox="0 0 32 30" aria-hidden="true">
            <path
              d="M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z"
            />
          </svg>
        </button>
      </div>
    </div>
  `,
})
export class GraphCanvas {
  readonly def = input.required<AutomationDefinition>();
  readonly layout = input.required<AutomationLayout>();
  readonly startLabel = input.required<string>();
  readonly selection = input.required<GraphSelection>();
  readonly issues = input<GraphIssues | undefined>(undefined);
  readonly run = input<GraphRun | undefined>(undefined);
  readonly editable = input(false);
  /** React's onSelect: a node, an edge, or the pane (`none`). */
  readonly pick = output<GraphSelection>();
  /** Positions of a node the user finished dragging (React's onMove). */
  readonly moved = output<AutomationLayout>();
  readonly connect = output<{ from: string; to: string }>();
  /** Delete or Backspace on the selection: steps and edges (by index) of the same definition (React's onDelete). */
  readonly remove = output<{ steps: string[]; edges: number[] }>();

  private readonly flow = viewChild.required<ElementRef<HTMLElement>>('flow');
  protected readonly patternId = `desk-dots-${++nextPattern}`;
  protected readonly viewport = signal<Viewport>({ x: 0, y: 0, zoom: 1 });
  protected readonly drag = signal<Drag | null>(null);
  protected readonly pan = signal<Pan | null>(null);
  protected readonly link = signal<Link | null>(null);
  /** Set when a pointer gesture ends in a drag, a pan or a connection: the click that follows it is not a selection. */
  private swallowClick = false;

  private readonly graph = computed(() => {
    const issues = this.issues();
    const run = this.run();
    return toGraph(this.def(), this.layout(), { startLabel: this.startLabel(), selection: this.selection(), editable: this.editable(), ...(issues ? { issues } : {}), ...(run ? { run } : {}) });
  });
  /** The graph's nodes, with a node being dragged where the pointer has it. */
  protected readonly nodes = computed(() => {
    const d = this.drag();
    const nodes = this.graph().nodes;
    return d?.moved ? nodes.map((n) => (n.id === d.id ? ({ ...n, position: d.position } as GraphNode) : n)) : nodes;
  });
  protected readonly edges = computed(() => {
    const byId = new Map(this.nodes().map((n) => [n.id, n]));
    return this.graph().edges.flatMap((e) => {
      const g = edgeGeometry(e, byId);
      return g ? [{ ...e, ...g }] : [];
    });
  });
  protected readonly labelled = computed(() => this.edges().filter((e) => e.data.label));
  protected readonly transform = computed(() => {
    const v = this.viewport();
    return `translate(${v.x}px, ${v.y}px) scale(${v.zoom})`;
  });
  protected readonly dots = computed(() => dotPattern(this.viewport()));
  protected readonly linkPath = computed(() => {
    const l = this.link();
    const from = l ? this.nodes().find((n) => n.id === l.from) : undefined;
    return l && from ? bezierPath(sourceAnchor(from), { ...l.at, side: 'top' }).path : null;
  });

  private readonly onMoveListener = (e: PointerEvent) => this.onMove(e);
  private readonly onUpListener = () => this.onUp();

  constructor() {
    // React Flow's fitView, once the canvas has a size.
    afterNextRender(() => this.fit());
    inject(DestroyRef).onDestroy(() => this.untrack());
  }

  protected asStep(n: GraphNode) {
    return n as Extract<GraphNode, { type: 'step' }>;
  }
  protected asStart(n: GraphNode) {
    return n as Extract<GraphNode, { type: 'start' }>;
  }
  protected asStub(n: GraphNode) {
    return n as Extract<GraphNode, { type: 'stub' }>;
  }

  /** The canvas's size; jsdom has none, so specs get 800×600 (the React tests' ResizeObserver shim). */
  private size(): { width: number; height: number } {
    const el = this.flow().nativeElement;
    return { width: el.clientWidth || 800, height: el.clientHeight || 600 };
  }

  /** A pointer event's position relative to the canvas. */
  private local(e: { clientX: number; clientY: number }): Point {
    const r = this.flow().nativeElement.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  protected fit(): void {
    const { width, height } = this.size();
    this.viewport.set(fitViewport(graphBounds(this.graph().nodes), width, height));
  }

  protected zoomBy(factor: number): void {
    const { width, height } = this.size();
    const v = this.viewport();
    this.viewport.set(zoomAt(v, v.zoom * factor, { x: width / 2, y: height / 2 }));
  }

  protected onWheel(e: WheelEvent): void {
    e.preventDefault();
    const v = this.viewport();
    this.viewport.set(zoomAt(v, v.zoom * wheelFactor(e.deltaY, e.deltaMode, e.ctrlKey), this.local(e)));
  }

  protected onKey(e: KeyboardEvent): void {
    if (!this.editable() || (e.key !== 'Delete' && e.key !== 'Backspace')) return;
    const sel = this.selection();
    if (sel.kind === 'step') {
      e.preventDefault();
      this.remove.emit({ steps: [sel.id], edges: [] });
    } else if (sel.kind === 'edge') {
      e.preventDefault();
      this.remove.emit({ steps: [], edges: [sel.index] });
    }
  }

  protected onPointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    this.swallowClick = false;
    const target = e.target as Element;
    const handle = target.closest('.react-flow__handle.connectable');
    const from = handle?.getAttribute('data-nodeid');
    if (from && this.editable()) {
      e.preventDefault();
      this.link.set({ from, at: toGraphPoint(this.viewport(), this.local(e)), target: null, valid: false });
      this.track();
      return;
    }
    if (target.closest('.react-flow__edge')) return;
    const id = target.closest<HTMLElement>('.react-flow__node')?.dataset['id'];
    const node = id ? this.nodes().find((n) => n.id === id) : undefined;
    if (node && this.editable() && node.draggable !== false) {
      this.drag.set({ id: node.id, startX: e.clientX, startY: e.clientY, origin: node.position, position: node.position, moved: false });
    } else {
      this.pan.set({ startX: e.clientX, startY: e.clientY, origin: this.viewport(), moved: false });
    }
    this.track();
  }

  private onMove(e: PointerEvent): void {
    const link = this.link();
    if (link) {
      const v = this.viewport();
      const at = toGraphPoint(v, this.local(e));
      const target = dropTarget(this.nodes(), at, CONNECT_RADIUS / v.zoom);
      this.link.set({ ...link, at, target, valid: target !== null && canConnect(this.def(), link.from, target) === null });
      return;
    }
    const drag = this.drag();
    if (drag) {
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      const zoom = this.viewport().zoom;
      this.drag.set({ ...drag, moved: true, position: { x: drag.origin.x + dx / zoom, y: drag.origin.y + dy / zoom } });
      return;
    }
    const pan = this.pan();
    if (pan) {
      const dx = e.clientX - pan.startX;
      const dy = e.clientY - pan.startY;
      if (!pan.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      this.pan.set({ ...pan, moved: true });
      this.viewport.set({ ...pan.origin, x: pan.origin.x + dx, y: pan.origin.y + dy });
    }
  }

  private onUp(): void {
    this.untrack();
    const link = this.link();
    if (link) {
      this.link.set(null);
      this.swallowClick = true;
      if (link.target && link.valid) this.connect.emit({ from: link.from, to: link.target });
      return;
    }
    const drag = this.drag();
    if (drag) {
      if (drag.moved) {
        this.swallowClick = true;
        this.moved.emit({ [drag.id]: { x: Math.round(drag.position.x), y: Math.round(drag.position.y) } });
      }
      this.drag.set(null);
      return;
    }
    const pan = this.pan();
    if (pan) {
      if (pan.moved) this.swallowClick = true;
      this.pan.set(null);
    }
  }

  protected onClick(e: MouseEvent): void {
    if (this.swallowClick) {
      this.swallowClick = false;
      return;
    }
    const target = e.target as Element;
    const id = target.closest<HTMLElement>('.react-flow__node')?.dataset['id'];
    if (id) {
      const node = this.nodes().find((n) => n.id === id);
      if (node?.type === 'start') this.pick.emit({ kind: 'start' });
      else if (node?.type === 'step') this.pick.emit({ kind: 'step', id: node.id });
      return;
    }
    const edge = target.closest('.react-flow__edge');
    if (edge) {
      const index = edgeIndex(edge.getAttribute('data-id') ?? '');
      if (index !== null) this.pick.emit({ kind: 'edge', index });
      return;
    }
    this.pick.emit({ kind: 'none' });
  }

  /** Moves and releases are followed on the window, so a gesture that leaves the canvas still ends (jsdom has no pointer capture). */
  private track(): void {
    window.addEventListener('pointermove', this.onMoveListener);
    window.addEventListener('pointerup', this.onUpListener);
    window.addEventListener('pointercancel', this.onUpListener);
  }

  private untrack(): void {
    window.removeEventListener('pointermove', this.onMoveListener);
    window.removeEventListener('pointerup', this.onUpListener);
    window.removeEventListener('pointercancel', this.onUpListener);
  }
}
