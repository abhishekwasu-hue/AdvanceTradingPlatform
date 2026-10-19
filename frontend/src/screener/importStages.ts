/**
 * U2 (flag `screener_builder_u2`): a screen from the server (`POST /api/screener/stages`: its top-level conjuncts, each
 * with its canonical text and AST) -> the funnel's stages. A conjunct becomes an indicator, filter, category or rank
 * block only when that block writes back EXACTLY the server's text (checked here); every other conjunct - offsets,
 * arithmetic, nested groups - is a formula block holding the text. So opening a screen never changes what it means.
 */
import {
  newBlock, OPS, stageText, type FormulaStage, type Op, type Operand, type ParamValue, type Registry, type SeriesRef, type Stage,
  type Timeframe, TIMEFRAMES,
} from "./model";

/** The JSON form of a ScreenQL node (`nodes.to_json` on the server). */
export interface AstNode {
  k: string;
  v?: number | string | boolean;
  name?: string;
  offset?: number;
  tf?: string | null;
  args?: AstNode[];
  kwargs?: Record<string, AstNode>;
  op?: string;
  left?: AstNode;
  right?: AstNode;
  value?: AstNode;
  items?: AstNode[];
  item?: AstNode;
}
export interface ServerStage { text: string; wrapped: string; ast: AstNode }
export interface StagesResponse { name: string | null; base_tf: string; text: string; stages: ServerStage[]; version: string }

const CMP: Record<string, Op> = { ">": ">", ">=": ">=", "<": "<", "<=": "<=" };

function tfOf(node: AstNode): Timeframe | null | undefined {
  if (!node.tf) return null;
  return (TIMEFRAMES as readonly string[]).includes(node.tf) ? (node.tf as Timeframe) : undefined;
}

/** A field or a call with literal arguments and no offset -> a SeriesRef (undefined when the builder cannot hold it). */
function seriesOf(reg: Registry, node: AstNode | undefined): SeriesRef | undefined {
  if (!node || node.offset || (node.k !== "Field" && node.k !== "Call") || !node.name) return undefined;
  const e = reg[node.name];
  const tf = tfOf(node);
  if (!e || tf === undefined || (node.kwargs && Object.keys(node.kwargs).length)) return undefined;
  if (node.k === "Field") return { fn: node.name, params: {}, tf };
  const params: Record<string, ParamValue> = {};
  const args = node.args ?? [];
  if (args.length > e.args.length) return undefined;
  for (let i = 0; i < args.length; i += 1) {
    const a = args[i];
    if (a.k === "Num" && typeof a.v === "number") params[e.args[i]] = a.v;
    else if (a.k === "Str" && typeof a.v === "string") params[e.args[i]] = a.v;
    else if (a.k === "Field" && a.name && !a.offset && !a.tf) params[e.args[i]] = a.name;
    else return undefined;
  }
  return { fn: node.name, params, tf };
}

function operandOf(reg: Registry, node: AstNode | undefined): Operand | undefined {
  if (node?.k === "Num" && typeof node.v === "number") return { kind: "value", value: node.v };
  const ref = seriesOf(reg, node);
  return ref ? { kind: "series", ref } : undefined;
}

function candidate(reg: Registry, ast: AstNode): Stage | undefined {
  if (ast.k === "Compare" && ast.op && ast.op in CMP) {
    const left = seriesOf(reg, ast.left);
    const right = operandOf(reg, ast.right);
    if (left && right && left.fn !== "Rank" && left.fn !== "PercentileRank") {
      return { ...(newBlock(reg, "indicator") as Stage & { kind: "indicator" }), left, op: CMP[ast.op], right };
    }
    if ((ast.left?.name === "Rank" || ast.left?.name === "PercentileRank") && ast.right?.k === "Num" && (ast.op === "<=" || ast.op === ">=")) {
      const of = seriesOf(reg, ast.left.args?.[0]);
      if (of && typeof ast.right.v === "number") return { id: "", kind: "rank", fn: ast.left.name, of, op: ast.op, value: ast.right.v, enabled: true };
    }
  }
  if (ast.k === "Call" && (ast.name === "CrossAbove" || ast.name === "CrossBelow") && !ast.offset && !ast.tf) {
    const left = seriesOf(reg, ast.args?.[0]);
    const right = operandOf(reg, ast.args?.[1]);
    const op = (ast.name === "CrossAbove" ? "crosses above" : "crosses below") as Op;
    if (left && right && OPS.includes(op)) return { ...(newBlock(reg, "indicator") as Stage & { kind: "indicator" }), left, op, right };
  }
  if (ast.k === "Call" && reg[ast.name ?? ""]?.returns === "bool") {
    const call = seriesOf(reg, ast);
    if (call) return { id: "", kind: "filter", call, enabled: true };
  }
  if (ast.k === "Compare" && (ast.op === "==" || ast.op === "!=") && ast.right?.k === "Str") {
    const call = seriesOf(reg, ast.left);
    if (call) return { id: "", kind: "category", call, values: [String(ast.right.v)], negate: ast.op === "!=", enabled: true };
  }
  const inNode = ast.k === "Not" ? ast.item : ast;
  if (inNode?.k === "In" && inNode.items?.every((i) => i.k === "Str")) {
    const call = seriesOf(reg, inNode.value);
    if (call) return { id: "", kind: "category", call, values: inNode.items.map((i) => String(i.v)), negate: ast.k === "Not", enabled: true };
  }
  return undefined;
}

let seq = 0;

/** Stages for the server's conjuncts: a block form when it writes back the same text, else a formula block. */
export function stagesFromServer(reg: Registry, items: ServerStage[]): Stage[] {
  return items.map((item) => {
    seq += 1;
    const id = `imp${Date.now().toString(36)}${seq}`;
    const tried = candidate(reg, item.ast);
    if (tried && stageText(reg, { ...tried, id }) === item.text) return { ...tried, id };
    const formula: FormulaStage = { id, kind: "formula", text: item.text, enabled: true };
    return formula;
  });
}
