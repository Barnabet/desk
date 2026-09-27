// The `when` language of automation edges (spec 2026-09-26-automations-design §3.2): parsed into an AST and evaluated, never eval'ed.

export type ExprOp = '==' | '!=' | '<' | '<=' | '>' | '>=' | 'contains';

/** A parsed condition. `path` and `exists` hold a checked dotted path, e.g. `steps.fetch.outputs.count`. */
export type Expr =
  | { kind: 'or'; items: Expr[] }
  | { kind: 'and'; items: Expr[] }
  | { kind: 'not'; expr: Expr }
  | { kind: 'cmp'; op: ExprOp; left: Expr; right: Expr }
  | { kind: 'exists'; path: string }
  | { kind: 'path'; path: string }
  | { kind: 'literal'; value: string | number | boolean | null };

/** What a condition reads: the run's inputs, and each step's status, route and outputs. */
export type ExprScope = {
  inputs: Record<string, unknown>;
  steps: Record<string, { status: string; route: string | null; outputs: Record<string, unknown> }>;
};

/** A condition that does not parse. `position` is the 0-based index of the offending character. */
export class ExprError extends Error {
  constructor(
    message: string,
    readonly position: number,
  ) {
    super(message);
    this.name = 'ExprError';
  }
}

export const EXPR_MAX_LENGTH = 500;
/** Parentheses and `not`s nest at most this deep. */
export const EXPR_MAX_DEPTH = 20;

const KEY = '[a-z][a-z0-9_]{0,39}';
const ID = '[a-z][a-z0-9_-]{0,39}';
const PATH = new RegExp(`^(?:inputs\\.${KEY}|steps\\.${ID}\\.(?:outputs\\.${KEY}|route|status))$`);
const KEYWORDS = new Set(['and', 'or', 'not', 'contains', 'exists', 'true', 'false', 'null']);
const WORD = /[A-Za-z_][A-Za-z0-9_.-]*/y;
const NUMBER = /-?\d+(?:\.\d+)?/y;
const OPERATOR = /==|!=|<=|>=|<|>/y;

type Token =
  | { t: 'path' | 'kw' | '(' | ')'; text: string; pos: number }
  | { t: 'op'; text: ExprOp; pos: number }
  | { t: 'str'; text: string; value: string; pos: number }
  | { t: 'num'; text: string; value: number; pos: number };

function match(re: RegExp, src: string, at: number): string | null {
  re.lastIndex = at;
  return re.exec(src)?.[0] ?? null;
}

function readString(src: string, start: number): Extract<Token, { t: 'str' }> {
  const quote = src[start];
  let value = '';
  let i = start + 1;
  while (i < src.length) {
    const c = src[i]!;
    if (c === quote) return { t: 'str', text: src.slice(start, i + 1), value, pos: start };
    if (c === '\\') {
      const n = src[i + 1];
      if (n === undefined) break;
      value += n === 'n' ? '\n' : n === 't' ? '\t' : n;
      i += 2;
      continue;
    }
    value += c;
    i += 1;
  }
  throw new ExprError(`Unterminated string starting at position ${start}`, start);
}

function tokenize(src: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i]!;
    if (/\s/.test(c)) {
      i += 1;
      continue;
    }
    if (c === '(' || c === ')') {
      tokens.push({ t: c, text: c, pos: i });
      i += 1;
      continue;
    }
    if (c === '"' || c === "'") {
      const tok = readString(src, i);
      tokens.push(tok);
      i += tok.text.length;
      continue;
    }
    const word = match(WORD, src, i);
    if (word !== null) {
      if (KEYWORDS.has(word)) tokens.push({ t: 'kw', text: word, pos: i });
      else if (word.startsWith('inputs.') || word.startsWith('steps.')) {
        if (!PATH.test(word)) {
          throw new ExprError(`'${word}' at position ${i} is not a valid path: use inputs.<key>, steps.<id>.outputs.<key>, steps.<id>.route or steps.<id>.status`, i);
        }
        tokens.push({ t: 'path', text: word, pos: i });
      } else throw new ExprError(`Unknown name '${word}' at position ${i}: paths start with inputs. or steps.`, i);
      i += word.length;
      continue;
    }
    const num = match(NUMBER, src, i);
    if (num !== null) {
      const next = src[i + num.length];
      if (next !== undefined && /[A-Za-z0-9_.]/.test(next)) throw new ExprError(`Malformed number at position ${i}`, i);
      tokens.push({ t: 'num', text: num, value: Number(num), pos: i });
      i += num.length;
      continue;
    }
    const op = match(OPERATOR, src, i);
    if (op !== null) {
      tokens.push({ t: 'op', text: op as ExprOp, pos: i });
      i += op.length;
      continue;
    }
    throw new ExprError(`Unexpected character ${JSON.stringify(c)} at position ${i}`, i);
  }
  return tokens;
}

function describe(tok: Token): string {
  if (tok.t === 'str') return 'a string';
  if (tok.t === 'num') return `the number ${tok.text}`;
  return `'${tok.text}'`;
}

function nest(depth: number, pos: number): void {
  if (depth > EXPR_MAX_DEPTH) throw new ExprError(`Nested too deeply at position ${pos} (at most ${EXPR_MAX_DEPTH} levels of parentheses and not)`, pos);
}

/** Recursive descent over the grammar of spec §3.2; `depth` counts the parentheses and `not`s around the current node. */
class Parser {
  private i = 0;

  constructor(
    private readonly tokens: Token[],
    private readonly end: number,
  ) {}

  peek(): Token | undefined {
    return this.tokens[this.i];
  }

  private isKeyword(tok: Token | undefined, kw: string): boolean {
    return tok?.t === 'kw' && tok.text === kw;
  }

  or(depth: number): Expr {
    const items = [this.and(depth)];
    while (this.isKeyword(this.peek(), 'or')) {
      this.i += 1;
      items.push(this.and(depth));
    }
    return items.length === 1 ? items[0]! : { kind: 'or', items };
  }

  private and(depth: number): Expr {
    const items = [this.unary(depth)];
    while (this.isKeyword(this.peek(), 'and')) {
      this.i += 1;
      items.push(this.unary(depth));
    }
    return items.length === 1 ? items[0]! : { kind: 'and', items };
  }

  private unary(depth: number): Expr {
    const tok = this.peek();
    if (tok && this.isKeyword(tok, 'not')) {
      nest(depth + 1, tok.pos);
      this.i += 1;
      return { kind: 'not', expr: this.unary(depth + 1) };
    }
    return this.cmp(depth);
  }

  private cmp(depth: number): Expr {
    const left = this.operand(depth);
    const tok = this.peek();
    if (tok && (tok.t === 'op' || this.isKeyword(tok, 'contains'))) {
      this.i += 1;
      return { kind: 'cmp', op: tok.text as ExprOp, left, right: this.operand(depth) };
    }
    return left;
  }

  private operand(depth: number): Expr {
    const tok = this.tokens[this.i++];
    if (!tok) throw new ExprError(`The condition ends at position ${this.end} where a value is expected`, this.end);
    switch (tok.t) {
      case 'path':
        return { kind: 'path', path: tok.text };
      case 'str':
      case 'num':
        return { kind: 'literal', value: tok.value };
      case '(': {
        nest(depth + 1, tok.pos);
        const inner = this.or(depth + 1);
        this.expect(')');
        return inner;
      }
      case 'kw':
        if (tok.text === 'true' || tok.text === 'false') return { kind: 'literal', value: tok.text === 'true' };
        if (tok.text === 'null') return { kind: 'literal', value: null };
        if (tok.text === 'exists') {
          this.expect('(');
          const arg = this.tokens[this.i++];
          if (arg?.t !== 'path') {
            const pos = arg?.pos ?? this.end;
            throw new ExprError(`exists needs a path at position ${pos}`, pos);
          }
          this.expect(')');
          return { kind: 'exists', path: arg.text };
        }
    }
    throw new ExprError(`Expected a value at position ${tok.pos}, found ${describe(tok)}`, tok.pos);
  }

  private expect(t: '(' | ')'): void {
    const tok = this.tokens[this.i++];
    if (tok?.t !== t) {
      const pos = tok?.pos ?? this.end;
      throw new ExprError(`Expected '${t}' at position ${pos}`, pos);
    }
  }
}

/** Parses a `when` condition. Throws `ExprError` with the position of the first problem. */
export function parseExpr(src: string): Expr {
  if (src.length > EXPR_MAX_LENGTH) throw new ExprError(`A condition has at most ${EXPR_MAX_LENGTH} characters (this one has ${src.length})`, EXPR_MAX_LENGTH);
  const tokens = tokenize(src);
  if (tokens.length === 0) throw new ExprError('The condition is empty', 0);
  const parser = new Parser(tokens, src.length);
  const expr = parser.or(0);
  const extra = parser.peek();
  if (extra) throw new ExprError(`Unexpected ${describe(extra)} at position ${extra.pos}`, extra.pos);
  return expr;
}

/** The dotted paths a condition reads, each once, in first-seen order. */
export function exprPaths(e: Expr): string[] {
  const seen = new Set<string>();
  const walk = (n: Expr): void => {
    switch (n.kind) {
      case 'or':
      case 'and':
        n.items.forEach(walk);
        return;
      case 'not':
        walk(n.expr);
        return;
      case 'cmp':
        walk(n.left);
        walk(n.right);
        return;
      case 'exists':
      case 'path':
        seen.add(n.path);
        return;
      case 'literal':
        return;
    }
  };
  walk(e);
  return [...seen];
}

/** An own property's value, or null. Never reads inherited names such as `constructor`. */
function own(obj: unknown, key: string): unknown {
  if (typeof obj !== 'object' || obj === null || !Object.hasOwn(obj, key)) return null;
  return (obj as Record<string, unknown>)[key] ?? null;
}

/** A checked path's value; null when anything along it is missing. */
function lookup(path: string, scope: ExprScope): unknown {
  const [head, name, field, key] = path.split('.');
  if (head === 'inputs') return own(scope.inputs, name!);
  const step = own(scope.steps, name!);
  return field === 'outputs' ? own(own(step, 'outputs'), key!) : own(step, field!);
}

function truthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === '') return false;
  if (typeof v === 'number') return v !== 0 && !Number.isNaN(v);
  if (Array.isArray(v)) return v.length > 0;
  return true;
}

function kindOf(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  return Array.isArray(v) ? 'list' : typeof v;
}

/** Scalars compare strictly; lists are equal when their items are. */
function same(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => x === b[i]);
  return a === b;
}

function compare(op: ExprOp, a: unknown, b: unknown): boolean {
  if (op === 'contains') {
    if (typeof a === 'string') return typeof b === 'string' && a.includes(b);
    return Array.isArray(a) && a.some((item) => item === b);
  }
  const kind = kindOf(a);
  if (kind !== kindOf(b)) return op === '!=';
  if (op === '==') return same(a, b);
  if (op === '!=') return !same(a, b);
  if (kind !== 'number' && kind !== 'string') return false;
  const x = a as number | string;
  const y = b as number | string;
  const sign = x < y ? -1 : x > y ? 1 : x === y ? 0 : Number.NaN;
  if (op === '<') return sign < 0;
  if (op === '<=') return sign <= 0;
  if (op === '>') return sign > 0;
  return sign >= 0;
}

function valueOf(e: Expr, scope: ExprScope): unknown {
  switch (e.kind) {
    case 'or':
      return e.items.some((item) => truthy(valueOf(item, scope)));
    case 'and':
      return e.items.every((item) => truthy(valueOf(item, scope)));
    case 'not':
      return !truthy(valueOf(e.expr, scope));
    case 'cmp':
      return compare(e.op, valueOf(e.left, scope), valueOf(e.right, scope));
    case 'exists':
      return lookup(e.path, scope) !== null;
    case 'path':
      return lookup(e.path, scope);
    case 'literal':
      return e.value;
  }
}

/**
 * Evaluates a parsed condition. A missing path is null; values of different types are never equal or ordered (`!=` is true);
 * `<`, `<=`, `>`, `>=` order two numbers or two strings and are false otherwise; the result is truth-tested
 * (null, false, 0, '' and [] are false). `and` and `or` short-circuit.
 */
export function evalExpr(e: Expr, scope: ExprScope): boolean {
  return truthy(valueOf(e, scope));
}
