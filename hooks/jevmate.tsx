// The jevmate mod: the part of the plugin that lives inside Claude Code's own process.
//
// The Python hooks (jev/hooks.py) do the deciding; this module does what a hook command cannot:
// draw the session's numbers above the prompt and in a pane, put a line under a reply that claims
// a check passed when none ran, ask the person about a destructive command where no permission
// prompt can appear (bypassPermissions), run a reading subagent on a cheaper model, lower the
// effort of a routine turn once it has checked that this keeps the prompt cache, hand Claude Code's
// summarizer the conversation with stale tool results moved out once it has checked that this pays,
// and say what another mod reaches for as it loads. Every call into jev goes through `python3 <plugin>/bin/jev`,
// so the thresholds, the prices and the ledger stay the CLI's.
//
// Needs Claude Code 2.1.287 or later; older versions ignore `modules` and keep the hooks alone.

import type { EngineInterface, Register, SessionMessage, ToolResultSummary, ToolUseSummary } from 'claude-code'

type Api = EngineInterface
// The element constructors a surface draws with ($.ui.resolve), passed to the drawing helpers.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type El = any

// Kept in step with jev/hooks.py: the cheap skip before any process is started.
const SAFE =
  /^\s*(ls|cat|head|tail|wc|grep|rg|find|fd|echo|printf|pwd|which|type|file|stat|du|df|env|printenv|date|whoami|id|uname|tree|less|git\s+(status|log|diff|show|branch|remote|rev-parse|describe|blame|stash\s+list|check-ignore)|python3?\s+-m\s+py_compile|jev|shasum|md5|sha256sum|diff|cmp)\b/
const RISKY =
  /[>|;&`$]|\b(rm|mv|dd|mkfs|chmod|chown|kill|pkill|curl|wget|sudo|truncate|drop|delete|push|reset|rebase|checkout|clean|prune|purge|format|shred)\b|--force|--hard|\s-[a-zA-Z]*f/
const CLAIM =
  /\b(tests?|suite|build|lint|checks?|typecheck|ci)\b[^.\n]{0,60}\b(pass(es|ed|ing)?|green|succeed(s|ed)?|clean|ok)\b|\b(all green|verified|confirmed working|works as expected)\b/i
const RUNNER =
  /\b(pytest|phpunit|npm (run )?test|yarn test|pnpm test|go test|cargo test|make test|jest|vitest|mocha|unittest|composer test|gradlew? test|mvn test|dotnet test|rspec|bundle exec|tsc|mypy|ruff|eslint|flake8|pint|phpstan|psalm|cargo (check|clippy)|npm run (build|lint)|make)\b/i
const CREDENTIAL = /(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY)/i

const PANE = 'jev'
const COMMANDS = ['jevmate', 'jev-session'] as const

// Theme keys, so the colors follow the person's theme, light or dark, terminal or desktop.
const ACCENT = 'claude'
const GOOD = 'success'
const WARN = 'warning'
const BAD = 'error'
const INFO = 'suggestion'
const LABEL = 18 // the pane's label column

const MONEY_FLOOR = 0.5 // below it the band leads with what jev caught, not with cents
const DELEGATE_BAR = 0.85 // how sure jev must be that a subagent's task is reading
const PROBE_CTX = 100_000 // until the cache check has run, effort is only lowered on a context this small
const CACHE_TTL_MS = 240_000 // a step later than this after the previous one may have lost the cache by age

type Safety = { asked: number; pages_flagged: number; files_flagged: number; triaged: number; claims: number; checks: number }
type Compaction = { runs: number; judged: number; moved: number; cut: number; kept: number; freed: number; restored: number; rereads: number; pruned: number; saved_usd: number }
// What `jev compact --messages` hands back for a result it moves or cuts, or a file write it shortens.
type Change = { id: string; kind: 'result' | 'input'; text?: string; input?: Record<string, unknown> }
type Routing = { subagents: number; subagent_saved: number; subagent_spent: number; effort_turns: number; effort_cache: 'keeps' | 'rewrites' | null }
type JevSide = {
  requests: number
  decisions: number
  tokens: number
  cached: number
  reads?: number
  overhead?: number
  trimmed: number
  trim_runs: number
  kept_out: number
  paid: number
  would: number
  saved: number
  saved_total?: number
  asked: number
  once?: number
  reread?: number
  pricing?: 'per-model' | 'flat'
  labels?: Record<string, number>
  hook_labels?: Record<string, number>
  safety?: Safety
  routing?: Routing
  compact?: Compaction
  share?: number
  api?: { reason: string; since?: number | null } | null
}
// A plan window as jev reads it: points used when Claude Code last read it, and the points the kept-out text would have taken.
type PlanWindow = { label?: string; used: number; resets_at: string | null; as_of?: string; rate: number | null; kept_free: number | null }
type Subagents = { count: number; cost: number; models: Record<string, number>; could_save: number; read_only: number }
type Summary = {
  session: string | null
  cwd?: string
  model: { usd: number; turns: number; ctx: number; ctx_size: number; carry?: number; last_model?: string } | null
  jev: JevSide
  plan?: { billing: 'subscription'; windows: Record<string, PlanWindow>; missing?: string[] } | null
  subagents?: Subagents
}
// One styled run of text, and a group of them that the band keeps or drops as a whole.
type Piece = { text: string; color?: string; bold?: true; dim?: true }
type Segment = { key: string; rank: number; pieces: Piece[] }
type Judge = {
  decision: string
  why?: string | null
  p?: number
  outside?: number | null
  requested?: number | null
  ask_at?: number
  deny_at?: number
  reason?: string
}
type Route = { name: string; conf: number }
type Routed = { parent: string; model: string; kind: string }
type Usage = { input_tokens: number; output_tokens: number; cache_read_input_tokens: number; cache_creation_input_tokens: number }

// Module state. A reload starts it over; nothing here is worth keeping past that.
let cfg = { guardMode: 'ask', bandMode: 'on', evidenceLine: 'on', routeMode: 'off', routeConf: 0.8, subagentModel: 'off', trimMode: 'on', compactMode: 'off' }
let optionEnv: Record<string, string> = {}
let sessionId = ''
let cwd = ''
let canAsk = false
let launcher: string[] | null = null
let summary: Summary | null = null
let ctxPct: number | null = null
let ctxTokens: number | null = null
let sessionUsd: number | null = null
let refreshedAt = ''
let collapsed = false
let paneOpen = false
let lastPrompt = ''
let turnCommands: string[] = []
let pendingRoute: Route | null = null
let version = ''
let effortCache: 'keeps' | 'rewrites' | null = null
let compactCache: 'pays' | 'costs' | null = null
let lastStep = { ctx: 0, at: 0 }
let prevLow = false
const routeFor = new Map<string, Route>()
const lowTurns = new Set<string>() // turns running at low effort, decided at their first request
const routedAgents = new Map<string, Routed>()
const flagged = new Set<string>()
let refreshing = false
let refreshAgain = false

const k = (x: number) => (x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : x >= 1000 ? `${Math.round(x / 1000)}k` : String(x))
const n = (x: number) => x.toLocaleString('en-US')
const money = (x: number) => (x >= 100 ? `$${Math.round(x).toLocaleString('en-US')}` : `$${x.toFixed(2)}`)
const pct = (x: number) => `${Math.round(100 * Math.min(1, Math.max(0, x)))}%`
const length = (pieces: Piece[]) => pieces.reduce((sum, p) => sum + p.text.length, 0)
const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`
const clock = (epochSeconds: number) => {
  const d = new Date(epochSeconds * 1000)
  return `${two(d.getHours())}:${two(d.getMinutes())}`
}
// Points of a plan window: 2.1%, <0.1%, or 1.4× past a whole window.
const points = (x: number) => (x >= 100 ? `${(x / 100).toFixed(1)}×` : x < 0.1 ? '<0.1%' : `${x.toFixed(1)}%`)
const two = (x: number) => String(x).padStart(2, '0')
function resets(iso: string | null, withDay: boolean): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]
  return ` · resets ${withDay ? `${day} ` : ''}${two(d.getHours())}:${two(d.getMinutes())}`
}
const shortModel = (m: string) => m.replace(/^claude-/, '').replace(/-\d{8}$/, '')

// Props for a Text from a Piece, with no undefined values in them.
function style(p: Piece): { color?: string; bold?: true; dimColor?: true } {
  const out: { color?: string; bold?: true; dimColor?: true } = {}
  if (p.color) out.color = p.color
  if (p.bold) out.bold = true
  if (p.dim) out.dimColor = true
  return out
}

// One run of styled pieces as a single Text, so a proportional font (the desktop app) wraps it as a sentence.
function spans(Text: El, pieces: Piece[], wrap?: 'truncate-end') {
  return (
    <Text {...(wrap ? { wrap } : {})}>
      {pieces.map(p => (
        <Text {...style(p)}>{p.text}</Text>
      ))}
    </Text>
  )
}

async function launch($: Api): Promise<string[]> {
  if (launcher) return launcher
  const home = (await $.env.get('HOME')) ?? ''
  for (const path of [`${$.plugin.root}/bin/jev`, `${home}/.local/bin/jev`]) {
    if (await $.fs.exists(path)) return (launcher = ['python3', path])
  }
  return (launcher = ['jev'])
}

async function jev($: Api, args: string[], stdin?: string, timeoutMs = 20_000) {
  const argv = [...(await launch($)), ...args]
  const env: Record<string, string> = { ...optionEnv, JEV_SESSION: `session:${sessionId.slice(0, 8)}` }
  const path = await $.env.get('PATH')
  if (path) env.PATH = path
  try {
    return await $.process.run(argv, { stdin, timeoutMs, env, cwd })
  } catch {
    return null
  }
}

// One row in jev's hook log on this module's behalf: what it showed, routed or measured.
async function note($: Api, hook: string, fields: Record<string, unknown> = {}) {
  await jev($, ['hook', 'record'], JSON.stringify({ hook, session_id: sessionId, ...fields }), 8_000)
}

function commandOf(input: unknown): string {
  return String((input as { command?: unknown } | null)?.command ?? '').trim()
}

function lastLine(stdout: string | undefined): string {
  return (stdout ?? '').trim().split('\n').pop() ?? ''
}

// At most one refresh in flight and one queued behind it, however many tool calls finish meanwhile.
async function refresh($: Api) {
  if (refreshing) {
    refreshAgain = true
    return
  }
  refreshing = true
  try {
    await refreshNow($)
  } catch {
    // the session ended or the module reloaded mid-refresh: nothing to draw any more
  } finally {
    refreshing = false
  }
  if (refreshAgain) {
    refreshAgain = false
    void refresh($)
  }
}

async function refreshNow($: Api) {
  const before = summary
  // Claude Code's own figures go along: the session's cost as /cost totals it, and on a subscription the
  // 5-hour and weekly windows, from which jev learns how much of a window a dollar of use takes.
  const args = ['session', '--json', '--compact', '--session', sessionId, '--cwd', cwd]
  try {
    const u = await $.session.usage()
    ctxPct = u.context.percent !== undefined ? Math.round(u.context.percent) : null
    ctxTokens = u.context.tokens ?? null
    sessionUsd = u.cost?.usd ?? null
    if (sessionUsd !== null) args.push('--spent', String(sessionUsd))
    // Every window Claude Code reports: the 5-hour one, the week, and on some plans a week per model.
    for (const w of u.rateLimits) args.push('--plan', `${w.kind}=${w.percentUsed}${w.resetsAt ? `@${w.resetsAt}` : ''}`)
  } catch {
    ctxPct = null
    sessionUsd = null
  }
  const r = await jev($, args)
  if (r && r.exitCode === 0) {
    try {
      summary = JSON.parse(r.stdout) as Summary
    } catch {
      // a partial line: keep the previous numbers
    }
  }
  const now = new Date(await $.clock.now())
  refreshedAt = `${two(now.getHours())}:${two(now.getMinutes())}`
  const trimmed = (summary?.jev.trimmed ?? 0) - (before?.jev.trimmed ?? 0)
  if (before && trimmed > 0) $.ui.log(`jev trimmed ~${k(trimmed)} tokens of that output; the full text is on disk`)
  $.ui.invalidate('ui.render')
}

async function openPane($: Api) {
  paneOpen = true
  await $.ui.open({ id: PANE, title: 'jev', closeOnEscape: true })
  void refresh($)
}

// The line above the prompt never disappears by a press: hide folds it to a chip that brings it back.
function setCollapsed($: Api, value: boolean) {
  collapsed = value
  $.store.set('band_collapsed', value).catch(() => undefined)
  $.ui.invalidate('ui.render')
}

function spent(): { usd: number; estimated: boolean } | null {
  if (sessionUsd !== null && sessionUsd > 0) return { usd: sessionUsd, estimated: false }
  const usd = summary?.model?.usd
  return usd && usd > 0 ? { usd, estimated: true } : null
}

function isIdle(j: JevSide | undefined): boolean {
  return !j || (!j.requests && !j.asked && !j.trimmed && !(j.routing?.subagents ?? 0) && !(j.compact?.runs ?? 0) && !j.api)
}

// Jev failing when a hook last asked it: the band says so for as long as it lasts, the reason in two words.
// The pane and the warning carry it whole.
function shortReason(reason: string): string {
  const status = /^HTTP (\d{3})/.exec(reason)?.[1]
  if (status === '402') return 'no credit'
  if (status === '401' || status === '403') return 'key rejected'
  if (status === '429') return 'rate limited'
  if (status?.startsWith('5')) return 'backend error'
  if (/^no API key/i.test(reason)) return 'no key'
  if (/^network error/i.test(reason)) return 'unreachable'
  return status ? `HTTP ${status}` : reason.slice(0, 24)
}

function failing(j: JevSide | undefined): Piece[] | null {
  if (!j?.api) return null
  const why = shortReason(j.api.reason)
  return [{ text: "Jev can't judge", bold: true, color: BAD }, ...(why ? [{ text: ` · ${why}`, dim: true } as Piece] : [])]
}

const savedTotal = (j: JevSide) => j.saved_total ?? j.saved

// What jev caught this session, in its own units: questions asked, pages and files flagged, runs sorted.
function caught(j: JevSide): Piece[][] {
  const s = j.safety
  const out: Piece[][] = []
  const asked = s?.asked ?? j.asked
  if (asked) out.push([{ text: `guard asked ${asked}×`, color: WARN }])
  if (s?.pages_flagged) out.push([{ text: plural(s.pages_flagged, 'page'), color: WARN }, { text: ' flagged', dim: true }])
  if (s?.files_flagged) out.push([{ text: plural(s.files_flagged, 'skill file'), color: BAD }, { text: ' flagged', dim: true }])
  if (s?.triaged) out.push([{ text: plural(s.triaged, 'red run'), color: INFO }, { text: ' sorted', dim: true }])
  if (s?.claims) out.push([{ text: plural(s.claims, 'unbacked claim'), color: WARN }, { text: ' caught', dim: true }])
  return out
}

// The band's groups in the order they read, each with a rank: the lowest ranks go first when it is narrow.
function segments(): Segment[] {
  const j = summary?.jev
  const out: Segment[] = []
  const down = failing(j)
  if (down) out.push({ key: 'api', rank: 10, pieces: down })
  if (isIdle(j) || !j) {
    out.push({ key: 'idle', rank: 9, pieces: [{ text: 'ready', color: GOOD }, { text: ' · nothing decided yet this session', dim: true }] })
  } else {
    const plan = summary?.plan?.windows
    const shares = [
      { w: plan?.five_hour, name: ' of 5h' },
      { w: plan?.seven_day, name: ' of the week' },
    ].filter(x => x.w && x.w.kept_free !== null && x.w.kept_free !== undefined)
    const worth = savedTotal(j) >= MONEY_FLOOR
    if (worth && plan && shares.length) {
      const pieces: Piece[] = [{ text: 'saved ', dim: true }]
      shares.forEach((x, i) => {
        if (i) pieces.push({ text: ' · ', dim: true })
        pieces.push({ text: points(x.w?.kept_free ?? 0), bold: true, color: GOOD }, { text: x.name, dim: true })
      })
      out.push({ key: 'saved', rank: 9, pieces })
    } else if (worth) {
      out.push({ key: 'saved', rank: 9, pieces: [{ text: `~${money(savedTotal(j))}`, bold: true, color: GOOD }, { text: plan ? ' API-equivalent saved' : ' saved', dim: true }] })
      const s = spent()
      if (s && !plan) out.push({ key: 'share', rank: 4, pieces: [{ text: pct(savedTotal(j) / s.usd), color: GOOD }, { text: ' of the session', dim: true }] })
    }
    if (j.kept_out) out.push({ key: 'kept', rank: worth ? 5 : 8, pieces: [{ text: k(j.kept_out), bold: true, color: INFO }, { text: ' tokens kept out', dim: true }] })
    if (j.routing?.subagents) {
      out.push({ key: 'subagents', rank: 5, pieces: [{ text: plural(j.routing.subagents, 'subagent'), color: INFO }, { text: ' on a cheaper model', dim: true }] })
    }
    caught(j).forEach((pieces, i) => out.push({ key: `caught${i}`, rank: 7 - i, pieces }))
    if (j.compact?.runs) {
      // The count stays ahead of the safety figures; how many results it saved is the first detail to go.
      out.push({ key: 'compact', rank: 7.5, pieces: [{ text: plural(j.compact.runs, 'compaction'), color: INFO }] })
      if (j.compact.judged) {
        out.push({ key: 'compact-saved', rank: 3, pieces: [{ text: `${n(j.compact.judged)} ${j.compact.judged === 1 ? 'result' : 'results'} saved`, dim: true }] })
      }
    }
    if (!worth && !j.kept_out && !caught(j).length && j.decisions) {
      out.push({ key: 'decisions', rank: 6, pieces: [{ text: n(j.decisions), bold: true }, { text: ' decisions', dim: true }] })
    }
  }
  if (ctxPct !== null) {
    const tone: Piece = ctxPct >= 80 ? { text: `${ctxPct}%`, color: BAD, bold: true } : ctxPct >= 60 ? { text: `${ctxPct}%`, color: WARN } : { text: `${ctxPct}%`, dim: true }
    const carry = summary?.model?.carry ?? 0
    out.push({ key: 'ctx', rank: 2, pieces: [{ text: 'ctx ', dim: true }, tone, ...(carry >= 0.05 ? [{ text: ` · ${money(carry)}/turn`, dim: true } as Piece] : [])] })
  }
  return out
}

function fit(segs: Segment[], room: number): Segment[] {
  const kept = [...segs]
  const width = () => kept.reduce((sum, s) => sum + length(s.pieces), 0) + 3 * Math.max(0, kept.length - 1)
  while (kept.length > 1 && width() > room) {
    let low = 0
    for (let i = 1; i < kept.length; i++) if (kept[i].rank < kept[low].rank) low = i
    kept.splice(low, 1)
  }
  return kept
}

async function judge($: Api, command: string): Promise<Judge | null> {
  const payload = { tool_name: 'Bash', tool_input: { command }, cwd, session_id: sessionId, request: lastPrompt, judge: true }
  const r = await jev($, ['hook', 'guard'], JSON.stringify(payload), 15_000)
  const last = lastLine(r?.stdout)
  if (!r || r.exitCode !== 0 || !last) return null
  try {
    return JSON.parse(last) as Judge
  } catch {
    return null
  }
}

async function rate($: Api, prompt: string): Promise<Route | null> {
  const r = await jev($, ['hook', 'route'], JSON.stringify({ prompt, session_id: sessionId, judge: true }), 8_000)
  const last = lastLine(r?.stdout)
  if (!r || r.exitCode !== 0 || !last) return null
  try {
    const read = JSON.parse(last) as { routine: boolean; name: string; conf: number }
    return read.routine && read.conf >= cfg.routeConf ? { name: read.name, conf: read.conf } : null
  } catch {
    return null
  }
}

async function reading($: Api, prompt: string, kind: string): Promise<number | null> {
  const r = await jev($, ['hook', 'delegate'], JSON.stringify({ prompt, subagent_type: kind, session_id: sessionId }), 8_000)
  const last = lastLine(r?.stdout)
  if (!r || r.exitCode !== 0 || !last) return null
  try {
    const p = (JSON.parse(last) as { reading: number | null }).reading
    return typeof p === 'number' ? p : null
  } catch {
    return null
  }
}

// Effort is lowered only where it costs nothing: once the check below showed that this Claude Code build
// keeps the prompt cache across the change, or, before that, on a context small enough that a re-write is cheap.
function effortAllowed(): boolean {
  if (cfg.routeMode !== 'effort' || effortCache === 'rewrites') return false
  return effortCache === 'keeps' || (ctxTokens !== null && ctxTokens <= PROBE_CTX)
}

async function setEffortCache($: Api, verdict: 'keeps' | 'rewrites') {
  if (effortCache === verdict) return
  effortCache = verdict
  $.store.set('effort_cache', { version, verdict }).catch(() => undefined)
  await note($, 'effort-cache', { verdict, version })
  $.ui.log(
    verdict === 'keeps'
      ? 'jev: lowering effort kept the prompt cache on this Claude Code build, so routine turns can run at low effort at any context size'
      : 'jev: lowering effort re-wrote the prompt cache on this Claude Code build, so jev stops doing it',
  )
}

// Does the summarizer pay for its whole input on this build? Read off the first compaction's own request: when it
// read the conversation from the prompt cache, a pruned conversation would cost more than it saves, so pruning stays off.
async function learnCompactCost($: Api, result: unknown) {
  const r = result as { skip?: string; usage?: Usage }
  if (compactCache || r.skip || !r.usage) return
  const u = r.usage
  const total = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
  if (total < 20_000) return
  compactCache = u.cache_read_input_tokens >= 0.5 * total ? 'costs' : 'pays'
  $.store.set('compact_cache', { version, verdict: compactCache }).catch(() => undefined)
  await note($, 'compact-cache', { verdict: compactCache, version, total, cache_read: u.cache_read_input_tokens })
  $.ui.log(
    compactCache === 'pays'
      ? 'jev: the summarizer pays for its whole input on this build, so from the next compaction it reads the conversation with stale tool results moved out'
      : 'jev: the summarizer reads the conversation from the prompt cache on this build, so jev does not prune before it; the block after each compaction stays',
  )
}

// The judged results of this conversation: what to move out or cut before the summarizer reads it.
async function compactPlan($: Api, messages: readonly SessionMessage[]): Promise<Map<string, Change> | null> {
  const payload = JSON.stringify({
    messages: messages.map(m => ({
      role: m.role,
      text: m.text,
      toolUses: m.toolUses.map(u => ({ tool_use_id: u.tool_use_id, tool: u.tool, input: u.input, text: u.text, isError: u.isError })),
      toolResults: (m.toolResults ?? []).map(r => ({ tool_use_id: r.tool_use_id, text: r.text, isError: r.isError })),
    })),
  })
  const args = ['compact', '--messages', '-', '--apply', '--pruned', '--json', '--compact']
  if (summary?.model?.last_model) args.push('--price-model', summary.model.last_model)
  const r = await jev($, args, payload, 120_000)
  if (!r || r.exitCode !== 0) return null
  try {
    const plan = JSON.parse(r.stdout) as { changes?: Change[] }
    return new Map((plan.changes ?? []).map(c => [c.id, c]))
  } catch {
    return null
  }
}

// A message the plan touches is rebuilt without its handle, so the engine builds it from these fields; the rest stay the engine's own.
function pruneMessages(messages: readonly SessionMessage[], changes: Map<string, Change>): SessionMessage[] {
  const use = (u: ToolUseSummary): ToolUseSummary => {
    const c = changes.get(u.tool_use_id)
    const out: ToolUseSummary = { tool_use_id: u.tool_use_id, tool: u.tool, input: c?.kind === 'input' && c.input ? c.input : u.input }
    const text = c?.kind === 'result' && c.text !== undefined ? c.text : u.text
    if (text !== undefined) out.text = text
    if (u.isError) out.isError = true
    if (u.agentId) out.agentId = u.agentId
    return out
  }
  const result = (r: ToolResultSummary): ToolResultSummary => {
    const c = changes.get(r.tool_use_id)
    return { tool_use_id: r.tool_use_id, text: c?.kind === 'result' && c.text !== undefined ? c.text : r.text, isError: r.isError }
  }
  return messages.map(m => {
    const touched = m.toolUses.some(u => changes.has(u.tool_use_id)) || (m.toolResults ?? []).some(r => changes.has(r.tool_use_id))
    if (!touched) return m
    const rebuilt: SessionMessage = { role: m.role, text: m.text, toolUses: m.toolUses.map(use) }
    if (m.toolResults && m.toolResults.length) rebuilt.toolResults = m.toolResults.map(result)
    return rebuilt
  })
}

function question(command: string, j: Judge): string {
  const shown = command.length > 160 ? `${command.slice(0, 157)}…` : command
  if (j.why === 'project-rule') return `\`${shown}\` matches a rule in .jev/guard.json. Run it?`
  const why: string[] = [`looks destructive (p ${(j.p ?? 0).toFixed(2)})`]
  if ((j.outside ?? 0) >= 0.5) why.push(`reaches outside the project (p ${(j.outside ?? 0).toFixed(2)})`)
  if (j.requested !== null && j.requested !== undefined && j.requested < 0.5) why.push(`does not look like part of what you asked (p ${j.requested.toFixed(2)})`)
  return `\`${shown}\` ${why.join(', ')}. Run it?`
}

function inspectUses(e: { name: string; tier: string; uses: { events: readonly string[]; calls: readonly string[]; env?: { reads: readonly string[]; writes: readonly string[] } } }): string | null {
  const reads = (e.uses.env?.reads ?? []).filter(name => CREDENTIAL.test(name))
  const calls = new Set(e.uses.calls)
  const events = new Set(e.uses.events)
  const notes: string[] = []
  if (reads.length && (calls.has('http.fetch') || calls.has('process.run') || calls.has('process.spawn'))) {
    notes.push(`reads ${reads.map(x => `$${x}`).join(', ')} and reaches ${calls.has('http.fetch') ? 'the network' : 'processes'}`)
  }
  if (events.has('tool.check')) notes.push('can answer permission checks')
  if (calls.has('env.set') || (e.uses.env?.writes ?? []).length) notes.push('writes environment variables')
  if (calls.has('settings.read') && calls.has('fs.write')) notes.push('reads settings and writes files')
  return notes.length ? `jev inspect: mod ${e.name} (${e.tier}) ${notes.join('; ')} — worth a look before relying on it` : null
}

// A subagent's task runs on the cheaper model only when the caller left the model to inherit, the
// parent is a costlier tier, and jev reads the task as reading rather than judgment.
function cheaper(parent: string, target: string): boolean {
  const tier = (m: string) => (/haiku/.test(m) ? 1 : /sonnet/.test(m) ? 2 : 3)
  return tier(target) < tier(parent)
}

export const register: Register = (on, options) => {
  const opt = (key: string, fallback: string) => {
    const v = options[key]
    return v === undefined || v === '' ? fallback : String(v)
  }
  const route = opt('route_mode', 'off')
  cfg = {
    guardMode: opt('guard_mode', 'ask'), // ask · strict · deny · off
    bandMode: opt('band_mode', 'on'),
    evidenceLine: opt('evidence_line', 'on'),
    routeMode: route === 'model' ? 'hint' : route, // off · hint · effort; a main turn's model is not switched (its cache would be re-written)
    routeConf: Number(opt('route_conf', '0.80')) || 0.8,
    subagentModel: opt('subagent_model', 'off'), // off · sonnet · haiku
    trimMode: opt('trim_mode', 'on'),
    compactMode: opt('compact_mode', 'off'), // off · on
  }
  // The plugin's options reach the jev process the way Claude Code hands them to a hook command.
  optionEnv = {}
  for (const [key, value] of Object.entries(options)) optionEnv[`CLAUDE_PLUGIN_OPTION_${key.toUpperCase()}`] = String(value)
  // A load starts from nothing, whatever an earlier load of this module left behind.
  sessionId = ''
  cwd = ''
  canAsk = false
  launcher = null
  summary = null
  ctxPct = null
  ctxTokens = null
  sessionUsd = null
  refreshedAt = ''
  collapsed = false
  paneOpen = false
  lastPrompt = ''
  turnCommands = []
  pendingRoute = null
  version = ''
  effortCache = null
  compactCache = null
  lastStep = { ctx: 0, at: 0 }
  prevLow = false
  routeFor.clear()
  lowTurns.clear()
  routedAgents.clear()
  flagged.clear()
  refreshing = false
  refreshAgain = false

  // ------------------------------------------------------------------ session

  on('session.start', async ($, e, next) => {
    sessionId = await $.session.id()
    cwd = e.cwd
    canAsk = e.isInteractive && e.surface !== null
    if (canAsk && (cfg.guardMode === 'ask' || cfg.guardMode === 'strict')) {
      // Tells the PreToolUse guard that, where no prompt can appear, this module asks instead of denying.
      await $.env.set('JEV_GUARD_MOD', '1')
    }
    try {
      version = (await $.session.version()).version
    } catch {
      version = ''
    }
    try {
      // An earlier build hid the line for good, with no way back: whoever pressed that gets it back, open.
      if ((await $.store.get('band_hidden')) !== undefined) await $.store.delete('band_hidden')
      collapsed = (await $.store.get('band_collapsed')) === true
      const seen = (await $.store.get('effort_cache')) as { version?: string; verdict?: 'keeps' | 'rewrites' } | undefined
      effortCache = seen && seen.version === version && seen.verdict ? seen.verdict : null
      const cost = (await $.store.get('compact_cache')) as { version?: string; verdict?: 'pays' | 'costs' } | undefined
      compactCache = cost && cost.version === version && cost.verdict ? cost.verdict : null
    } catch {
      collapsed = false
    }
    // `/jevmate`, or `/jev-session` where something else already has that name; the band's details button
    // and /jevmate:stats show the same numbers either way.
    for (const name of COMMANDS) {
      try {
        await $.command.register({
          name,
          description: 'jev this session in a pane; show or hide the line above the prompt',
          argumentHint: '[show|hide]',
          immediate: true,
        })
        break
      } catch {
        // taken: try the next name
      }
    }
    void refresh($)
    return next(e)
  })

  on('command.run', { command: [...COMMANDS] }, async ($, e) => {
    const arg = String(e.args ?? '').trim().toLowerCase()
    if (arg === 'show' || arg === 'hide') {
      setCollapsed($, arg === 'hide')
      $.ui.toast(arg === 'show' ? 'jev: the line above the prompt is back' : 'jev: line folded · /jevmate show brings it back')
      return {}
    }
    await openPane($)
    return {}
  })

  on('ui.close', async ($, e, next) => {
    if ((e as { id?: string }).id === PANE) paneOpen = false
    return next(e)
  })

  // ------------------------------------------------------------------ the band and the pane

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (cfg.bandMode === 'off' || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    const theirs = await next(e) // what other mods draw here stays, under ours
    const j = summary?.jev
    if (collapsed) {
      const five = summary?.plan?.windows?.five_hour?.kept_free ?? summary?.plan?.windows?.seven_day?.kept_free
      const worth = j && !isIdle(j) && savedTotal(j) >= MONEY_FLOOR
      const first = j && !isIdle(j) ? caught(j)[0] : undefined
      const down = failing(j)
      const chip: Piece[] = down
        ? [{ text: ' ' }, down[0]]
        : !j || isIdle(j)
          ? [{ text: ' ready', dim: true }]
          : worth && five !== null && five !== undefined
            ? [{ text: ` ${points(five)} of ${summary?.plan?.windows?.five_hour?.kept_free !== undefined && summary?.plan?.windows?.five_hour?.kept_free !== null ? '5h' : 'the week'} saved`, color: GOOD }]
            : worth
              ? [{ text: ` ~${money(savedTotal(j))} saved`, color: GOOD }]
              : first
                ? [{ text: ' ' }, ...first]
                : [{ text: ` ${k(j.kept_out)} tokens kept out`, dim: true }]
      return (
        <Box flexDirection="column">
          <Box flexDirection="row">
            {spans(Text, [{ text: '◆ jev', bold: true, color: ACCENT }, ...chip, { text: '  ' }], 'truncate-end')}
            <Button key="show" label="show" plain dimColor onPress={() => setCollapsed($, false)} />
          </Box>
          {theirs}
        </Box>
      )
    }
    // Cells are counted for a monospace grid; the desktop's proportional font fits about a fifth more text in them.
    const room = Math.max(24, Math.floor((e.props.bodyColumns - 26) * (e.surface === 'desktop' ? 1.2 : 1)))
    const pieces: Piece[] = [{ text: '◆ jev', bold: true, color: ACCENT }, { text: '  ' }]
    fit(segments(), room).forEach((seg, i) => {
      if (i) pieces.push({ text: ' · ', dim: true })
      pieces.push(...seg.pieces)
    })
    pieces.push({ text: '   ' })
    return (
      <Box flexDirection="column">
        <Box flexDirection="row">
          {spans(Text, pieces, 'truncate-end')}
          <Button key="details" label="details" plain onPress={() => void openPane($).catch(() => undefined)} />
          <Text dimColor> · </Text>
          <Button key="hide" label="hide" plain dimColor onPress={() => setCollapsed($, true)} />
        </Box>
        {theirs}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const terminal = e.surface === 'terminal' // bars need a monospace grid; the desktop's font is proportional
    const j = summary?.jev
    const project = summary?.cwd ? summary.cwd.split('/').filter(Boolean).pop() : ''
    const row = (label: string, pieces: Piece[]) => (
      <Box flexDirection="row">
        <Box width={LABEL} flexShrink={0}>
          <Text dimColor>{label}</Text>
        </Box>
        <Box flexGrow={1} flexShrink={1}>
          {spans(Text, pieces)}
        </Box>
      </Box>
    )
    const header = spans(Text, [
      { text: '◆ jev', bold: true, color: ACCENT },
      { text: ` · this session${project ? ` · ${project}` : ''}${refreshedAt ? ` · ${refreshedAt}` : ''}`, dim: true },
    ])
    const controls = (
      <Box flexDirection="row" columnGap={2}>
        <Button key="toggle" label={collapsed ? 'show the line above the prompt' : 'fold the line above the prompt'} onPress={() => setCollapsed($, !collapsed)} />
        <Button key="refresh" label="refresh" onPress={() => void refresh($)} />
        <Button key="close" label="close" role="dismiss" onPress={() => void $.ui.close({ id: PANE }).catch(() => undefined)} />
      </Box>
    )
    if (isIdle(j) || !j) {
      return (
        <Box flexDirection="column">
          {header}
          <Text> </Text>
          <Text dimColor>{summary ? 'Nothing went through jev yet this session. `jev sift`, `jev tests` and the hooks show up here.' : 'reading the session…'}</Text>
          <Text> </Text>
          {controls}
        </Box>
      )
    }
    const s = spent()
    const total = savedTotal(j)
    const share = s ? Math.min(1, total / s.usd) : null
    const bar = Math.max(10, Math.min(36, e.props.bodyColumns - LABEL - 8))
    const top = (labels: Record<string, number> | undefined, count: number) =>
      Object.entries(labels ?? {})
        .slice(0, count)
        .map(([name, tokens]) => `${name} ${k(tokens)}`)
        .join(' · ')
    const plan = summary?.plan?.windows
    // A plan window: what is used, then what the kept-out text would have taken on top of it (a bar on the terminal).
    const windowRow = (kind: string, w: PlanWindow) => {
      const used = Math.min(bar, Math.round((w.used / 100) * bar))
      const kept = w.kept_free === null ? 0 : Math.min(bar - used, Math.max(w.kept_free > 0 ? 1 : 0, Math.round((w.kept_free / 100) * bar)))
      const tone = w.used >= 80 ? { color: BAD } : w.used >= 60 ? { color: WARN } : { color: INFO }
      const meter: Piece[] = terminal
        ? [{ text: '█'.repeat(used), ...tone }, { text: '█'.repeat(kept), color: GOOD }, { text: `${'░'.repeat(bar - used - kept)} `, dim: true }]
        : []
      return row(w.label || kind, [
        ...meter,
        { text: `${Math.round(w.used)}% used`, bold: true, ...tone },
        w.kept_free === null ? { text: ' · measuring what a dollar of use takes', dim: true } : { text: ` · jev kept ${points(w.kept_free)} free`, color: GOOD },
        { text: `${resets(w.resets_at, kind !== 'five_hour')}${w.as_of ? ` · as of ${w.as_of}` : ''}`, dim: true },
      ])
    }
    const sf = caught(j)
    const ro = j.routing
    const sub = summary?.subagents
    const filled = share === null ? 0 : Math.round(share * bar)
    const trimOn = cfg.trimMode !== 'off'
    const subagentOn = cfg.subagentModel === 'sonnet' || cfg.subagentModel === 'haiku'
    const effortOn = cfg.routeMode === 'effort'
    const compactOn = cfg.compactMode === 'on'
    const co = j.compact
    const models = sub ? Object.entries(sub.models).map(([m, count]) => `${count} on ${shortModel(m)}`).join(', ') : ''
    const down = failing(j)
    return (
      <Box flexDirection="column">
        {header}
        <Text> </Text>
        {down && j.api
          ? row('jev', [
              { text: "can't judge", bold: true, color: BAD },
              {
                text: ` · ${j.api.reason}${j.api.since ? ` since ${clock(j.api.since)}` : ''} · the hooks let everything through unchecked · jev doctor checks the key and the backend`,
                dim: true,
              },
            ])
          : null}
        {row('saved', [
          { text: `~${money(total)}`, bold: true, color: GOOD },
          { text: plan ? ' at most · a subscription is not billed per token, so this is an API equivalent' : ' at most', dim: true },
          ...(!plan && share !== null && s ? [{ text: ` · ${pct(share)} of the session's ${s.estimated ? '~' : ''}${money(s.usd)}`, dim: true } as Piece] : []),
        ])}
        {!plan && share !== null && s && terminal
          ? row('', [{ text: '█'.repeat(filled), color: GOOD }, { text: '░'.repeat(bar - filled), dim: true }])
          : null}
        {row('  reading', [
          { text: `~${money(j.saved)}`, color: GOOD },
          { text: ` · ${k(j.reads ?? j.kept_out)} tokens judged by jev instead of read · ${money(j.once ?? j.would)} once, ${money(j.reread ?? 0)} re-read until the next compaction`, dim: true },
        ])}
        {row('  trim', [
          { text: trimOn ? 'on' : 'off', bold: true, ...(trimOn ? { color: GOOD } : {}) },
          j.trimmed
            ? { text: ` · ${k(j.trimmed)} tokens of command output kept out in ${plural(j.trim_runs, 'run')}`, dim: true }
            : { text: trimOn ? ' · no command output over ~4k tokens yet' : ' · long command output reaches the context whole', dim: true },
        ])}
        {row('  subagents', [
          { text: subagentOn ? `on, ${cfg.subagentModel}` : 'off', bold: true, ...(subagentOn ? { color: GOOD } : {}) },
          ro && ro.subagents
            ? { text: ` · ${plural(ro.subagents, 'subagent')} moved to a cheaper model · ~${money(ro.subagent_saved)} saved, from their own usage`, dim: true }
            : sub && sub.count
              ? sub.could_save >= 0.01
                ? { text: ` · ${plural(sub.count, 'subagent')} ran (${models}) · ~${money(sub.could_save)} less on Sonnet`, color: WARN }
                : { text: ` · ${plural(sub.count, 'subagent')} ran (${models}) · nothing to gain from a cheaper model`, dim: true }
              : { text: ' · no subagents this session', dim: true },
        ])}
        {row('  low effort', [
          { text: effortOn ? 'on' : 'off', bold: true, ...(effortOn ? { color: GOOD } : {}) },
          {
            text:
              ro && ro.effort_turns
                ? ` · ${plural(ro.effort_turns, 'routine turn')}${ro.effort_cache === 'keeps' ? ' · the prompt cache survives it' : ro.effort_cache === 'rewrites' ? ' · it re-wrote the prompt cache, so it stopped' : ''}`
                : effortOn
                  ? effortCache === 'rewrites'
                    ? ' · stopped: lowering effort re-writes the prompt cache on this build'
                    : ' · waits for a routine prompt on a context under 100k tokens to check the cache'
                  : ' · routine turns run at the session effort',
            dim: true,
          },
        ])}
        {row('  compaction', [
          { text: compactOn ? 'on' : 'off', bold: true, ...(compactOn ? { color: GOOD } : {}) },
          co && co.runs
            ? {
                text: ` · ${plural(co.runs, 'compaction')} judged · ${plural(co.moved, 'result')} moved to disk, ${n(co.cut)} cut · read again later: ${co.rereads}${co.saved_usd ? ` · ~${money(co.saved_usd)} off the summary` : ''}`,
                dim: true,
              }
            : {
                text: compactOn
                  ? compactCache === 'pays'
                    ? ' · at the next compaction the summarizer reads the conversation with stale results moved out'
                    : ' · at the next compaction, large results are judged and saved, and what the work needs comes back after the summary'
                  : ' · Claude Code summarizes on its own',
                dim: true,
              },
        ])}
        {plan ? Object.entries(plan).map(([kind, w]) => windowRow(kind, w)) : null}
        {plan && summary?.plan?.missing?.includes('five_hour')
          ? row('5-hour window', [{ text: 'no reading yet · Claude Code reports it with its next request', dim: true }])
          : null}
        {row(
          'safety',
          sf.length
            ? [
                ...sf.flatMap((p, i) => (i ? [{ text: ' · ', dim: true } as Piece, ...p] : p)),
                { text: ` · ${n(j.safety?.checks ?? 0)} checks`, dim: true },
              ]
            : [{ text: `nothing to flag · ${n(j.safety?.checks ?? 0)} checks`, dim: true }],
        )}
        {ctxPct !== null
          ? row('context', [
              { text: `${ctxPct}%`, bold: true, ...(ctxPct >= 80 ? { color: BAD } : ctxPct >= 60 ? { color: WARN } : {}) },
              { text: ' in use', dim: true },
              ...((summary?.model?.carry ?? 0) >= 0.01
                ? [{ text: ` · each turn re-reads it: ~${money(summary?.model?.carry ?? 0)}${summary?.model?.last_model ? ` on ${shortModel(summary.model.last_model)}` : ''}`, color: WARN } as Piece]
                : []),
            ])
          : null}
        {row('by command', [{ text: top(j.labels, 4) || 'nothing yet: sift, rank, tests, diff and cluster show up here', dim: true }])}
        {top(j.hook_labels, 4) ? row('safety checks', [{ text: `${top(j.hook_labels, 4)} · not text the agent avoided reading`, dim: true }]) : null}
        {row("jev's own cost", [{ text: `${money(j.paid)}`, bold: true }, { text: ` for ${n(j.requests)} requests on your jev backend · nothing from the Claude plan`, dim: true }])}
        <Text> </Text>
        {controls}
        <Text> </Text>
        <Text dimColor>
          {total < MONEY_FLOOR
            ? 'jev pays off on reading-heavy work: large searches, logs, test suites, long diffs, many items to sort. This session had little of it.'
            : 'saved is a ceiling: the band jev was unsure about was read anyway · /jevmate:stats puts this in the chat'}
        </Text>
      </Box>
    )
  })

  // ------------------------------------------------------------------ turns: commands, evidence, effort

  on('turn.start', async ($, e, next) => {
    turnCommands = []
    const route = pendingRoute
    pendingRoute = null
    if (route && effortAllowed()) {
      routeFor.set(e.turnId, route)
      $.ui.log(`jev route: this prompt reads as ${route.name} (confidence ${route.conf.toFixed(2)}) → effort low`)
      void note($, 'effort', { conf: route.conf })
    }
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (!e.agentId) turnCommands.push(e.command)
    const ran = await next(e)
    void refresh($)
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId) {
      // A subagent this module sent to a cheaper model: its own usage prices the saving.
      const routed = routedAgents.get(e.agentId)
      if (routed && e.usage) {
        const u: Usage = e.usage
        await note($, 'subagent', {
          parent: routed.parent,
          model: e.usage.model || routed.model,
          kind: routed.kind,
          usage: {
            input_tokens: u.input_tokens,
            output_tokens: u.output_tokens,
            cache_read_input_tokens: u.cache_read_input_tokens,
            cache_creation_input_tokens: u.cache_creation_input_tokens,
          },
        })
        void refresh($)
      }
      return result
    }
    routeFor.delete(e.turnId)
    lowTurns.delete(e.turnId)
    if (e.isAborted) return result
    void refresh($)
    if (cfg.evidenceLine !== 'off' && e.answer.length >= 40 && CLAIM.test(e.answer) && !turnCommands.some(c => RUNNER.test(c))) {
      await note($, 'evidence')
      return { ...result, text: '⚠ jev: this reply says a test, build or check passed, but no test, build or lint command ran this turn' }
    }
    return result
  })

  on('prompt.submit', async ($, e, next) => {
    if ((e.origin?.kind ?? 'composer') !== 'composer') return next(e) // only what the person typed
    const text = e.text.trim()
    lastPrompt = text.slice(0, 2000)
    if (cfg.routeMode === 'effort' && effortCache !== 'rewrites' && text.length >= 40 && !/^(\/|\[Image:|@"|@\/)/.test(text)) {
      pendingRoute = await rate($, text)
    }
    return next(e)
  })

  // Lower the effort of a routed turn, and watch the first request after each change of effort: when it
  // read the conversation from the cache, the change is free; when it wrote the conversation again, it is not.
  on('turn.step', async function* ($, e, next) {
    if (e.agentId) return yield* next(e)
    // A turn keeps the effort it started with: going back mid-turn would be a second change.
    if (e.index === 0 && routeFor.has(e.turnId) && effortAllowed()) lowTurns.add(e.turnId)
    const low = lowTurns.has(e.turnId)
    const result = yield* next(low ? { ...e, effort: 'low' as const } : e)
    const u = result.usage
    const now = await $.clock.now()
    if (u && e.index === 0) {
      // Only a quick follow-up says anything: after a few idle minutes the cache may have expired on its own.
      const fresh = lastStep.at > 0 && now - lastStep.at < CACHE_TTL_MS
      if (low !== prevLow && fresh && lastStep.ctx > 20_000) {
        if (u.cache_read_input_tokens >= 0.5 * lastStep.ctx) await setEffortCache($, 'keeps')
        else if (u.cache_creation_input_tokens >= 0.5 * lastStep.ctx) await setEffortCache($, 'rewrites')
      }
      prevLow = low
    }
    if (u) lastStep = { ctx: u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens, at: now }
    return result
  })

  // ------------------------------------------------------------------ compaction

  // With compact_mode on, the PreCompact hook judges the large tool results before Claude Code summarizes, and the
  // block after the compaction repeats what the work still needs. This adds one step once it has measured that it
  // pays: the summarizer reads the conversation with the stale results already moved out.
  on('session.compact', async ($, e, next) => {
    if (cfg.compactMode !== 'on' || e.agentId) return next(e)
    if (compactCache !== 'pays') {
      if (e.trigger === 'precompute') return next(e)
      const result = await next(e)
      await learnCompactCost($, result)
      return result
    }
    // A summary computed ahead of time over the unpruned conversation would be thrown away: prune when the compaction comes instead.
    if (e.trigger === 'precompute') return { skip: 'jev prunes the conversation when the compaction comes' }
    const changes = await compactPlan($, e.messages)
    if (!changes || !changes.size) return next(e)
    $.ui.log(`jev compact: the summarizer reads the conversation with ${plural(changes.size, 'tool result')} moved out or cut; all of them are saved on disk`)
    return next({ ...e, messages: pruneMessages(e.messages, changes) })
  })

  // A file a compaction saved, read again: the result was needed after all. `jev compact --report` counts these.
  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    if (/\/compacted\/session-[\w.-]+\/\d{3}-/.test(String((e as { file_path?: unknown }).file_path ?? ''))) await note($, 'compact-reread', { tool: 'Read' })
    return next(e)
  })

  // ------------------------------------------------------------------ subagents on a cheaper model

  on('agent.spawn', async ($, e, next) => {
    const target = cfg.subagentModel
    if (target !== 'sonnet' && target !== 'haiku') return next(e)
    const parent = String(e.parentModel || '')
    const kind = String(e.subagentType || '')
    if (e.model || e.fork || kind.startsWith('jevmate') || !e.prompt || !cheaper(parent, target)) return next(e)
    const p = await reading($, e.prompt, kind)
    if (p === null || p < DELEGATE_BAR) return next(e)
    const result = await next({ ...e, model: target })
    if (!('deny' in result && result.deny) && result.agentId) {
      routedAgents.set(result.agentId, { parent, model: result.model || target, kind })
      $.ui.log(`jev: this subagent's task reads as reading (p ${p.toFixed(2)}) → it runs on ${shortModel(result.model || target)}${parent ? ` instead of ${shortModel(parent)}` : ''}`)
    }
    return result
  })

  // ------------------------------------------------------------------ the guard, where no prompt can appear

  // The PreToolUse hook (Python) knows the permission mode. Where no prompt can appear it holds its verdict
  // for this call instead of denying, and the judge below reads it back: no second model call.
  on('tool.check', { tool: 'Bash' }, async ($, e, next) => {
    const decided = await next(e)
    if (!canAsk || cfg.guardMode === 'off' || cfg.guardMode === 'deny' || decided.decision === 'deny') return decided
    const command = commandOf(e.input)
    if (!command || (SAFE.test(command) && !RISKY.test(command))) return decided
    const j = await judge($, command)
    if (!j || j.decision !== 'ask') return decided
    flagged.add(command)
    let answer: string
    try {
      // Refuse first: a dialog that resolves on its own (the person away from the keyboard) must not run it.
      answer = await $.ui.ask(question(command, j), { header: 'jev guard', options: ['Refuse', 'Run it'] })
    } catch {
      return { decision: 'deny', reason: 'jev guard: the question was dismissed; confirm with the person before running this command' }
    } finally {
      flagged.delete(command)
    }
    if (answer === 'Run it') return decided
    return { decision: 'deny', reason: 'jev guard: the person declined this command. Ask before trying a different approach.' }
  }).catch(async ($, e, next) => {
    // A command this hook was about to ask about never runs unasked because the hook failed.
    if (flagged.has(commandOf(e.input))) {
      return { decision: 'deny', reason: 'jev guard could not ask about this command; confirm with the person before running it' }
    }
    return next(e)
  })

  // ------------------------------------------------------------------ other mods, as they load

  on('plugin.register', async ($, e, next) => {
    if (e.name !== $.plugin.name) {
      const found = inspectUses(e)
      if (found) $.ui.log(found)
    }
    return next(e)
  })
}
